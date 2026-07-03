"""video-studio config resolver.

Single source of truth for the video-studio YAML config file. Used by all
pipeline steps and runner scripts via `from lib.config import load_config`.

Fails-closed: missing file / missing required key / nonexistent output_dir all
raise ConfigError naming the offending key. `character_ref` is lenient
(Stage 0 / Phase 6 artifact; not existence-checked at load time).

API credentials (ARK_API_KEY, MINIMAX_API_KEY, etc.) live in shell env,
never in this YAML.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the config is missing, malformed, or fails validation."""


class ConfigNotFound(ConfigError):
    """Raised when no config file is found via discovery (neither user-level nor project-level)."""


VALID_MEDIA_PROVIDERS = frozenset({"doubao", "minimax"})


# ---------------------------------------------------------------------------
# Frozen sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderConfig:
    width: int
    height: int
    fps: int
    subtitle_font: str


@dataclass(frozen=True)
class AudioConfig:
    voice_id: str
    bgm_duck_db: float = 18.0


@dataclass(frozen=True)
class MediaConfig:
    provider: str = "doubao"


@dataclass(frozen=True)
class BudgetConfig:
    video_shots_per_episode: int
    video_motion_threshold: float = 0.6
    video_floor: int = 3


@dataclass(frozen=True)
class FfmpegConfig:
    ffmpeg_bin: str
    ffprobe_bin: str


@dataclass(frozen=True)
class HostConfig:
    """Optional host persona + channel brand (content-level, configurable — never
    hardcoded in the plugin). Feeds digest-writer's fixed greeting/signoff and the
    title card's '频道 · 模块' brand tag. All empty = no fixed host (legacy behaviour)."""
    name: str = ""       # e.g. "卞旸"
    channel: str = ""    # e.g. "跑者学堂"
    tone: str = ""       # e.g. "亲切"


@dataclass(frozen=True)
class VideoStudioConfig:
    render: RenderConfig
    audio: AudioConfig
    media: MediaConfig
    budget: BudgetConfig
    ffmpeg: FfmpegConfig
    character_ref: str
    output_dir: str
    # derived subdirs — computed from output_dir, auto-created on load
    episodes_dir: str
    syllabus_dir: str
    state_dir: str
    # Phase 6: explicit syllabus + cursor paths (optional; Phase-2 mode omits these)
    syllabus_path: str = ""
    cursor_path: str = ""
    # Phase 6: book series identifier (optional; Stage 0 syllabus planner uses this)
    book_series: str = ""
    # Phase 6: syllabus source directory (snapshot of training science materials)
    syllabus_source_dir: str = ""
    # R2: optional host persona + channel brand (configurable, content-level)
    host: HostConfig = HostConfig()


# ---------------------------------------------------------------------------
# Config discovery helpers
# ---------------------------------------------------------------------------


def _find_project_root(start: Path) -> Path | None:
    """Walk up from `start` looking for CLAUDE.md, .git, or .video-studio directory."""
    current = start.resolve()
    while True:
        for marker in ("CLAUDE.md", ".git", ".video-studio"):
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _discover_config_layers(path: str | os.PathLike | None) -> list[Path]:
    """Return ordered list of config Paths to deep-merge (base → override).

    Explicit ``path`` arg or ``$VIDEO_STUDIO_CONFIG``:
        single-layer; file missing → ConfigError (fails-closed, same as before).

    Discovery mode (no explicit path/env):
        Collects [~/.claude/video-studio/config.yaml (user base),
                  <project_root>/.video-studio/config.yaml (project override)]
        keeping only files that exist on disk.
        Empty result → ConfigNotFound.
    """
    if path is not None:
        p = Path(path).expanduser()
        if not p.exists():
            raise ConfigError(
                f"config file not found: {p}. "
                f"解析顺序: 显式 path 参数 → $VIDEO_STUDIO_CONFIG → 发现模式 "
                f"(~/.claude/video-studio/config.yaml + <project>/.video-studio/config.yaml)。"
            )
        return [p]

    env = os.environ.get("VIDEO_STUDIO_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.exists():
            raise ConfigError(
                f"config file not found (VIDEO_STUDIO_CONFIG={env}): {p}."
            )
        return [p]

    # Discovery mode: user-level (base) then project-level (override)
    layers: list[Path] = []

    user_cfg = Path.home() / ".claude" / "video-studio" / "config.yaml"
    if user_cfg.exists():
        layers.append(user_cfg)

    project_root = _find_project_root(Path.cwd())
    if project_root is not None:
        project_cfg = project_root / ".video-studio" / "config.yaml"
        if project_cfg.exists():
            layers.append(project_cfg)

    if not layers:
        raise ConfigNotFound(
            "No video-studio config found. "
            "Expected ~/.claude/video-studio/config.yaml (user-level) and/or "
            "<project>/.video-studio/config.yaml (project-level). "
            "Run /video-make to scaffold a content project or migrate your "
            "existing ~/.video-studio config."
        )

    return layers


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*. Override wins at every leaf level."""
    result = dict(base)
    for key, override_val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(override_val, dict):
            result[key] = _deep_merge(result[key], override_val)
        else:
            result[key] = override_val
    return result


# ---------------------------------------------------------------------------
# YAML reading
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError(f"config {path} root must be a mapping")
        return loaded
    except ImportError:
        raise ConfigError(
            "PyYAML is required: pip install pyyaml"
        )


# ---------------------------------------------------------------------------
# Section validators
# ---------------------------------------------------------------------------


def _validate_render(raw: dict[str, Any]) -> RenderConfig:
    required = ("width", "height", "fps", "subtitle_font")
    for key in required:
        if key not in raw:
            raise ConfigError(f"missing required key: render.{key}")
    return RenderConfig(
        width=int(raw["width"]),
        height=int(raw["height"]),
        fps=int(raw["fps"]),
        subtitle_font=str(raw["subtitle_font"]),
    )


def _validate_audio(raw: dict[str, Any]) -> AudioConfig:
    if "voice_id" not in raw:
        raise ConfigError("missing required key: audio.voice_id")
    return AudioConfig(
        voice_id=str(raw["voice_id"]),
        bgm_duck_db=float(raw.get("bgm_duck_db", 18.0)),
    )


def _validate_media(raw: dict[str, Any]) -> MediaConfig:
    provider = str(raw.get("provider", "doubao"))
    if provider not in VALID_MEDIA_PROVIDERS:
        raise ConfigError(
            f"media.provider must be one of {sorted(VALID_MEDIA_PROVIDERS)}, got: {provider!r}"
        )
    return MediaConfig(provider=provider)


def _validate_budget(raw: dict[str, Any]) -> BudgetConfig:
    if "video_shots_per_episode" not in raw:
        raise ConfigError("missing required key: budget.video_shots_per_episode")
    _cap = int(raw["video_shots_per_episode"])
    _floor = int(raw.get("video_floor", 1))
    # Cross-check: the floor (minimum videos) cannot exceed the cap (maximum). If it
    # did, apply_video_floor would re-promote stills back above the cap that
    # apply_video_budget just enforced — a silent budget violation. Fail loud.
    if _floor > _cap:
        raise ConfigError(
            f"budget.video_floor ({_floor}) 不能大于 budget.video_shots_per_episode "
            f"({_cap})：下限不能高于上限，否则视频数会突破预算 cap。"
        )
    return BudgetConfig(
        video_shots_per_episode=_cap,
        video_motion_threshold=float(raw.get("video_motion_threshold", 0.6)),
        video_floor=_floor,
    )


def _validate_ffmpeg(raw: dict[str, Any]) -> FfmpegConfig:
    for key in ("ffmpeg_bin", "ffprobe_bin"):
        if key not in raw:
            raise ConfigError(f"missing required key: ffmpeg.{key}")
    return FfmpegConfig(
        ffmpeg_bin=str(raw["ffmpeg_bin"]),
        ffprobe_bin=str(raw["ffprobe_bin"]),
    )


# ---------------------------------------------------------------------------
# Top-level loader
# ---------------------------------------------------------------------------


def load_config(path: str | os.PathLike | None = None) -> VideoStudioConfig:
    """Resolve and validate the video-studio config.

    Lookup order:
    1. explicit ``path`` arg (single file; missing → ConfigError)
    2. ``$VIDEO_STUDIO_CONFIG`` env var (single file; missing → ConfigError)
    3. Discovery: [~/.claude/video-studio/config.yaml (base),
                   <project>/.video-studio/config.yaml (override)]
       deep-merged at leaf level; neither exists → ConfigNotFound(ConfigError).

    Fails-closed on missing file, missing required keys, nonexistent output_dir,
    or invalid media.provider. character_ref is lenient (not existence-checked).
    """
    layers = _discover_config_layers(path)

    raw: dict[str, Any] = {}
    for layer_path in layers:
        raw = _deep_merge(raw, _read_yaml(layer_path))

    # --- required sections ---
    for section in ("render", "audio", "media", "budget", "ffmpeg"):
        if section not in raw or not isinstance(raw[section], dict):
            raise ConfigError(f"missing required section: {section}")

    render = _validate_render(raw["render"])
    audio = _validate_audio(raw["audio"])
    media = _validate_media(raw["media"])
    budget = _validate_budget(raw["budget"])
    ffmpeg_cfg = _validate_ffmpeg(raw["ffmpeg"])

    # --- output_dir: required + must exist on disk (fail-closed) ---
    if "output_dir" not in raw:
        raise ConfigError("missing required key: output_dir")
    output_dir_raw = str(raw["output_dir"])
    output_dir_path = Path(os.path.expanduser(output_dir_raw))
    if not output_dir_path.exists():
        raise ConfigError(
            f"output_dir does not exist: {output_dir_path} "
            f"(create it first — video-studio will not auto-create output_dir)"
        )
    if not output_dir_path.is_dir():
        raise ConfigError(f"output_dir is not a directory: {output_dir_path}")
    output_dir = str(output_dir_path)

    # --- derived subdirs: auto-created AFTER output_dir existence check ---
    episodes_dir = str(output_dir_path / "episodes")
    syllabus_dir = str(output_dir_path / "syllabus")
    state_dir = str(output_dir_path / "state")
    for subdir in (episodes_dir, syllabus_dir, state_dir):
        Path(subdir).mkdir(parents=True, exist_ok=True)

    # --- character_ref: lenient, not existence-checked ---
    character_ref = str(raw.get("character_ref", ""))

    # --- Phase 6: explicit syllabus + cursor paths (optional; Phase-2 omits) ---
    syllabus_path = str(raw.get("syllabus_path", ""))
    cursor_path = str(raw.get("cursor_path", ""))

    # --- Phase 6: book_series + syllabus_source_dir: lenient, not required ---
    book_series = str(raw.get("book_series", ""))
    syllabus_source_dir = str(raw.get("syllabus_source_dir", ""))

    # --- R2: host persona (optional; empty = no fixed host) ---
    host_raw = raw.get("host") or {}
    host = HostConfig(
        name=str(host_raw.get("name", "")),
        channel=str(host_raw.get("channel", "")),
        tone=str(host_raw.get("tone", "")),
    )

    return VideoStudioConfig(
        render=render,
        audio=audio,
        media=media,
        budget=budget,
        ffmpeg=ffmpeg_cfg,
        character_ref=character_ref,
        output_dir=output_dir,
        episodes_dir=episodes_dir,
        syllabus_dir=syllabus_dir,
        state_dir=state_dir,
        syllabus_path=syllabus_path,
        cursor_path=cursor_path,
        book_series=book_series,
        syllabus_source_dir=syllabus_source_dir,
        host=host,
    )


# ---------------------------------------------------------------------------
# CLI: python -m lib.config --validate PATH
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="lib.config")
    parser.add_argument(
        "--validate",
        metavar="PATH",
        help="Validate the config at PATH; exit 0 if valid, 1 (with offending key on stderr) otherwise.",
    )
    args = parser.parse_args()

    if args.validate is not None:
        try:
            load_config(args.validate)
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            sys.exit(1)
        print("ok")
        sys.exit(0)

    # Default: load from env/cwd and print summary.
    try:
        c = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"output_dir    = {c.output_dir}")
    print(f"episodes_dir  = {c.episodes_dir}")
    print(f"media.provider = {c.media.provider}")
    print(f"render         = {c.render.width}x{c.render.height}@{c.render.fps}fps")
