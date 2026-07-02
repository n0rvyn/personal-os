"""video-studio content project scaffold + legacy config migration.

Provides two pure functions (no side-effects beyond file I/O):

* scaffold_content_project(root, book="")
    Creates a new content project at *root*:
    - .video-studio/config.yaml   (content-level skeleton; skipped if exists)
    - .video-studio/syllabus.yaml (skeleton; skipped if exists)
    - .video-studio/cursor.yaml   (skeleton; skipped if exists)
    - CLAUDE.md pointer (appended; idempotent)

* migrate_legacy_config(legacy_dir, machine_config_path, content_root)
    Splits an existing ~/.video-studio/config.yaml into:
    - machine-level (ffmpeg/media/audio/render/budget) → machine_config_path
    - content-level (output_dir/syllabus_path/cursor_path/character_ref/
      book_series/syllabus_source_dir) → content_root/.video-studio/config.yaml
    Also copies syllabus.yaml + cursor.yaml to content_root.
    Raises if any destination already exists (never overwrites; safe).
    Returns a dict of created paths.

Both functions are safe to call from non-interactive contexts. Real migration
(touching ~/.video-studio) is user-gated in the skill layer.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Key partitioning
# ---------------------------------------------------------------------------

# Machine-level keys: production/render tuning knobs that live on the machine.
# IMPORTANT: budget MUST be here — it is a required section in load_config;
# placing it only in content config would break load_config after migrate.
MACHINE_KEYS: frozenset[str] = frozenset({"ffmpeg", "media", "audio", "render", "budget"})

# Content-level keys: per-project paths and identifiers.
CONTENT_KEYS: frozenset[str] = frozenset(
    {"output_dir", "syllabus_path", "cursor_path", "character_ref",
     "book_series", "syllabus_source_dir"}
)


# ---------------------------------------------------------------------------
# Scaffold skeleton templates
# ---------------------------------------------------------------------------

_CONFIG_SKELETON = """\
# Video-studio content project config (content-level keys only).
# Machine-level keys (ffmpeg, media, audio, render, budget) live in
# ~/.claude/video-studio/config.yaml and are merged at load time.

output_dir: ""
syllabus_path: ""
cursor_path: ""
character_ref: ""
book_series: "{book}"
syllabus_source_dir: ""
"""

_SYLLABUS_SKELETON = """\
# Syllabus for video-studio.
# Add module entries here.
modules: []
"""

_CURSOR_SKELETON = """\
# Cursor tracking for video-studio episodes.
episode: 1
"""

_CLAUDE_MD_POINTER = """\

## Video-studio content project

This directory is a video-studio content project.
Config:    .video-studio/config.yaml
Syllabus:  .video-studio/syllabus.yaml
Cursor:    .video-studio/cursor.yaml
"""

_CLAUDE_MD_MARKER = ".video-studio"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scaffold_content_project(root: Path | str, book: str = "") -> None:
    """Create content project structure at *root*.

    Creates:
      <root>/.video-studio/config.yaml   — content-level skeleton (skipped if exists)
      <root>/.video-studio/syllabus.yaml — skeleton (skipped if exists)
      <root>/.video-studio/cursor.yaml   — skeleton (skipped if exists)
      <root>/CLAUDE.md                   — pointer appended (idempotent)

    Args:
        root: Project root directory (must exist or will be created).
        book: Optional book/series identifier to pre-fill in config skeleton.
    """
    root = Path(root)
    vs_dir = root / ".video-studio"
    vs_dir.mkdir(parents=True, exist_ok=True)

    # Write skeletons only if files don't already exist (idempotent)
    _write_if_absent(
        vs_dir / "config.yaml",
        _CONFIG_SKELETON.format(book=book),
    )
    _write_if_absent(vs_dir / "syllabus.yaml", _SYLLABUS_SKELETON)
    _write_if_absent(vs_dir / "cursor.yaml", _CURSOR_SKELETON)

    # Append CLAUDE.md pointer (idempotent — only once per file)
    _append_claude_md_pointer(root / "CLAUDE.md")


def migrate_legacy_config(
    legacy_dir: Path | str,
    machine_config_path: Path | str,
    content_root: Path | str,
) -> dict[str, Path]:
    """Split a legacy ~/.video-studio/config.yaml into machine + content configs.

    Machine-level keys (ffmpeg/media/audio/render/budget) → machine_config_path.
    Content-level keys (output_dir/syllabus_path/...) → content_root/.video-studio/config.yaml.
    syllabus.yaml and cursor.yaml are COPIED (not moved) to content_root.

    Safety rules:
    - Raises if machine_config_path already exists.
    - Raises if content_root/.video-studio/config.yaml already exists.
    - Legacy files are NOT deleted (user confirms cleanup manually).

    Returns:
        dict with keys "machine_config", "content_config", and optionally
        "syllabus", "cursor" for copied files.
    """
    legacy_dir = Path(legacy_dir)
    machine_config_path = Path(machine_config_path)
    content_root = Path(content_root)

    # Safety: refuse to overwrite existing targets
    content_cfg_path = content_root / ".video-studio" / "config.yaml"
    if machine_config_path.exists():
        raise FileExistsError(
            f"machine config already exists — refusing to overwrite: {machine_config_path}"
        )
    if content_cfg_path.exists():
        raise FileExistsError(
            f"content config already exists — refusing to overwrite: {content_cfg_path}"
        )

    # Read legacy config
    legacy_cfg_file = legacy_dir / "config.yaml"
    with legacy_cfg_file.open(encoding="utf-8") as fh:
        legacy_data: dict[str, Any] = yaml.safe_load(fh) or {}

    # Partition into machine and content dicts
    machine_data: dict[str, Any] = {}
    content_data: dict[str, Any] = {}

    for key, value in legacy_data.items():
        if key in MACHINE_KEYS:
            machine_data[key] = value
        elif key in CONTENT_KEYS:
            content_data[key] = value
        else:
            # Unknown key: put in content to avoid dropping it
            content_data[key] = value

    # M1 fix: compute content-project destinations FIRST (under .video-studio/,
    # matching scaffold), and REWRITE the content config's syllabus_path/cursor_path
    # to point at the copies. Without this the migrated config still points into the
    # legacy dir → progress writes back to the old cursor and deleting the legacy
    # dir breaks load_syllabus.
    content_vs_dir = content_root / ".video-studio"
    src_syllabus = legacy_dir / "syllabus.yaml"
    src_cursor = legacy_dir / "cursor.yaml"
    dst_syllabus = content_vs_dir / "syllabus.yaml"
    dst_cursor = content_vs_dir / "cursor.yaml"
    if src_syllabus.exists():
        content_data["syllabus_path"] = str(dst_syllabus)
    if src_cursor.exists():
        content_data["cursor_path"] = str(dst_cursor)

    # Write machine config
    machine_config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(machine_config_path, machine_data)

    # Write content config (pointers already rewritten to the new locations)
    content_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(content_cfg_path, content_data)

    result: dict[str, Path] = {
        "machine_config": machine_config_path,
        "content_config": content_cfg_path,
    }

    # Copy syllabus.yaml + cursor.yaml into content_root/.video-studio/ (the dirs
    # the rewritten pointers reference).
    if src_syllabus.exists():
        shutil.copy2(src_syllabus, dst_syllabus)
        result["syllabus"] = dst_syllabus
    if src_cursor.exists():
        shutil.copy2(src_cursor, dst_cursor)
        result["cursor"] = dst_cursor

    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _write_if_absent(path: Path, content: str) -> None:
    """Write *content* to *path* only if the file does not already exist."""
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def _append_claude_md_pointer(claude_md: Path) -> None:
    """Append video-studio pointer to CLAUDE.md if not already present."""
    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER in existing:
            return  # Already has pointer
        claude_md.write_text(existing + _CLAUDE_MD_POINTER, encoding="utf-8")
    else:
        claude_md.write_text(_CLAUDE_MD_POINTER.lstrip(), encoding="utf-8")
