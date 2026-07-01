"""Syllabus + cursor data model for video-studio Stage 0.

Provides dataclasses (Syllabus / Module / Episode / Cursor) and I/O functions
(load_syllabus / save_syllabus / load_cursor / save_cursor) plus game-loop
helpers (next_pending / advance / derive_seq_slug).

Fails-closed: missing files, missing required keys, and malformed YAML all
raise named errors (SyllabusError / CursorError).  Mirrors lib/config.py
style.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import yaml


class SyllabusError(Exception):
    """Raised when syllabus YAML is missing, malformed, or fails validation."""


class CursorError(Exception):
    """Raised when cursor YAML is missing, malformed, or fails validation."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    title: str
    doc_refs: List[str]
    source: str
    verify_status: str
    learning_goal: str
    status: str  # "pending" | "done"


@dataclass
class Module:
    name: str
    style: str
    episodes: List[Episode]


@dataclass
class Syllabus:
    book: str
    character_ref: str
    modules: List[Module]


@dataclass
class Cursor:
    book: str
    module_idx: int
    episode_idx: int


# ---------------------------------------------------------------------------
# Internal slug helper
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """Return an ASCII-safe kebab-case slug from *title*.

    For Latin/accented text: NFKD decompose then strip non-ASCII.
    For CJK/non-ASCII that survives: convert to pinyin via pypinyin if
    available; fall back to "ep" if pypinyin is not installed.
    """
    # Decompose and strip non-ASCII (handles accented Latin chars)
    nfkd = unicodedata.normalize("NFKD", title)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")

    # For purely CJK/non-ASCII titles, convert to pinyin
    if not ascii_text.strip():
        try:
            from pypinyin import lazy_pinyin  # optional dependency

            ascii_text = "-".join(lazy_pinyin(title))
        except ImportError:
            ascii_text = "ep"

    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "ep"


# ---------------------------------------------------------------------------
# Internal parse helpers
# ---------------------------------------------------------------------------


def _parse_episode(raw: dict, idx: int) -> Episode:
    required_keys = ("title", "doc_refs", "source", "verify_status", "learning_goal", "status")
    for key in required_keys:
        if key not in raw:
            raise KeyError(f"Episode[{idx}] missing required key: {key!r}")
    return Episode(
        title=str(raw["title"]),
        doc_refs=list(raw["doc_refs"]),
        source=str(raw["source"]),
        verify_status=str(raw["verify_status"]),
        learning_goal=str(raw["learning_goal"]),
        status=str(raw["status"]),
    )


def _parse_module(raw: dict, idx: int) -> Module:
    if "name" not in raw:
        raise KeyError(f"Module[{idx}] missing required key: 'name'")
    episodes = [
        _parse_episode(ep, i) for i, ep in enumerate(raw.get("episodes", []))
    ]
    return Module(
        name=str(raw["name"]),
        style=str(raw.get("style", "")),
        episodes=episodes,
    )


# ---------------------------------------------------------------------------
# I/O: Syllabus
# ---------------------------------------------------------------------------


def load_syllabus(path: Path) -> Syllabus:
    """Load and validate a syllabus YAML file.

    Raises:
        FileNotFoundError / OSError  — file missing or unreadable.
        SyllabusError                — YAML parse error or top-level not a mapping.
        KeyError                     — required key absent.
    """
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")  # raises FileNotFoundError if absent

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SyllabusError(f"YAML parse error in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SyllabusError(f"Expected mapping at top level in {path}")

    if "book" not in data:
        raise KeyError("Syllabus missing required key: 'book'")

    modules = [_parse_module(m, i) for i, m in enumerate(data.get("modules", []))]

    return Syllabus(
        book=str(data["book"]),
        character_ref=str(data.get("character_ref", "")),
        modules=modules,
    )


def save_syllabus(syllabus: Syllabus, path: Path) -> None:
    """Serialize *syllabus* to YAML at *path* (using yaml.safe_dump)."""
    path = Path(path)
    data = {
        "book": syllabus.book,
        "character_ref": syllabus.character_ref,
        "modules": [
            {
                "name": m.name,
                "style": m.style,
                "episodes": [
                    {
                        "title": ep.title,
                        "doc_refs": ep.doc_refs,
                        "source": ep.source,
                        "verify_status": ep.verify_status,
                        "learning_goal": ep.learning_goal,
                        "status": ep.status,
                    }
                    for ep in m.episodes
                ],
            }
            for m in syllabus.modules
        ],
    }
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# I/O: Cursor
# ---------------------------------------------------------------------------


def load_cursor(path: Path) -> Cursor:
    """Load a cursor YAML file.

    Raises:
        FileNotFoundError / OSError  — file missing or unreadable.
        CursorError                  — YAML parse error or top-level not a mapping.
        KeyError                     — required key absent.
    """
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")  # raises FileNotFoundError if absent

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise CursorError(f"YAML parse error in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise CursorError(f"Expected mapping at top level in {path}")

    for key in ("book", "module_idx", "episode_idx"):
        if key not in data:
            raise KeyError(f"Cursor missing required key: {key!r}")

    return Cursor(
        book=str(data["book"]),
        module_idx=int(data["module_idx"]),
        episode_idx=int(data["episode_idx"]),
    )


def save_cursor(cursor: Cursor, path: Path) -> None:
    """Serialize *cursor* to YAML at *path* (using yaml.safe_dump)."""
    path = Path(path)
    data = {
        "book": cursor.book,
        "module_idx": cursor.module_idx,
        "episode_idx": cursor.episode_idx,
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# derive_seq_slug
# ---------------------------------------------------------------------------


def derive_seq_slug(
    syllabus: Syllabus, module_idx: int, episode_idx: int
) -> Tuple[str, str]:
    """Return ``(seq, slug)`` for the episode at *(module_idx, episode_idx)*.

    *seq*  — 1-based global episode number across all modules, zero-padded
              to 3 digits (e.g. ``"001"``).
    *slug* — ASCII-safe kebab-case version of the episode title.

    Raises ValueError if the indices are out of bounds.
    """
    global_idx = 0
    for m_i, module in enumerate(syllabus.modules):
        for e_i, episode in enumerate(module.episodes):
            if m_i == module_idx and e_i == episode_idx:
                seq = f"{global_idx + 1:03d}"
                slug = _slugify(episode.title)
                return seq, slug
            global_idx += 1
    raise ValueError(
        f"Episode not found: module_idx={module_idx}, episode_idx={episode_idx}"
    )


# ---------------------------------------------------------------------------
# next_pending
# ---------------------------------------------------------------------------


def next_pending(
    syllabus: Syllabus, cursor: Cursor
) -> Optional[Tuple[Episode, str, str, Module]]:
    """Return ``(episode, seq, slug, module)`` for the first pending episode at or after cursor.

    Returns ``None`` if the cursor is past all episodes or no pending episode
    exists from the cursor position onward.
    """
    # Compute the cursor's global flat index
    cursor_global: Optional[int] = None
    global_idx = 0
    for m_i, module in enumerate(syllabus.modules):
        for e_i in range(len(module.episodes)):
            if m_i == cursor.module_idx and e_i == cursor.episode_idx:
                cursor_global = global_idx
            global_idx += 1

    if cursor_global is None:
        # Cursor points past all known episodes
        return None

    # Walk from cursor_global onward and return the first pending episode
    global_idx = 0
    for m_i, module in enumerate(syllabus.modules):
        for e_i, episode in enumerate(module.episodes):
            if global_idx >= cursor_global and episode.status == "pending":
                seq, slug = derive_seq_slug(syllabus, m_i, e_i)
                return episode, seq, slug, module
            global_idx += 1

    return None


# ---------------------------------------------------------------------------
# Selection — entry-level result dataclass (returned by select_next)
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    """The next pending episode resolved at Stage-1 entry.

    All fields are consumed either by the driver (seq/slug → prepare_render) or
    by step_cursor_read (doc_refs/source/verify_status/topic/learning_goal/
    character_ref → ctx). character_ref is series-level (same face across the
    whole column); carried so doc-mode extract can provision ref_url.
    style carries the module-level look name (empty string = use config default).
    """
    episode: Episode
    seq: str
    slug: str
    doc_refs: List[str]
    source: str
    verify_status: str
    learning_goal: str
    topic: str
    character_ref: str
    style: str = ""


# ---------------------------------------------------------------------------
# select_next — Stage-1 entry helper (reads cfg.syllabus_path / cursor_path)
# ---------------------------------------------------------------------------


def select_next(cfg: Any) -> Optional[Selection]:
    """Return the next pending episode as a Selection, or None.

    Returns None in two cases:
    - No syllabus configured (cfg.syllabus_path is empty) — Phase-2 fixture mode.
    - Syllabus fully done (all episodes status=done).

    Never raises; any I/O / parse failures propagate naturally (the caller,
    runner._cli, will crash loudly — intentional fail-closed behaviour).
    """
    syllabus_path = getattr(cfg, "syllabus_path", "")
    cursor_path = getattr(cfg, "cursor_path", "")

    if not syllabus_path or not cursor_path:
        return None

    syllabus = load_syllabus(Path(syllabus_path))
    cursor = load_cursor(Path(cursor_path))

    result = next_pending(syllabus, cursor)
    if result is None:
        return None

    episode, seq, slug, module = result
    return Selection(
        episode=episode,
        seq=seq,
        slug=slug,
        doc_refs=episode.doc_refs,
        source=episode.source,
        verify_status=episode.verify_status,
        learning_goal=episode.learning_goal,
        topic=episode.title,
        character_ref=syllabus.character_ref,
        style=module.style,
    )


# ---------------------------------------------------------------------------
# promote_draft
# ---------------------------------------------------------------------------


def promote_draft(
    draft_path: Path,
    syllabus_path: Path,
    cursor_path: Path,
    book: str,
) -> Syllabus:
    """Promote a planner draft YAML to a locked syllabus + initialized cursor.

    Strips an optional ```yaml ... ``` fence, validates all required episode
    fields (source / verify_status / doc_refs + title / learning_goal / status),
    then atomically writes *syllabus_path* and *cursor_path* only when
    validation passes (fail-closed — no partial files on error).

    Args:
        draft_path:   Path to the draft YAML produced by the planner persona.
        syllabus_path: Destination for the locked syllabus YAML.
        cursor_path:   Destination for the initialized cursor YAML.
        book:          Book identifier written into the cursor.

    Returns:
        The validated Syllabus object.

    Raises:
        SyllabusError  — YAML parse error, not a mapping, or no modules.
        KeyError       — Required field absent in a module or episode.
        FileNotFoundError / OSError  — draft_path missing or unreadable.
    """
    draft_path = Path(draft_path)
    syllabus_path = Path(syllabus_path)
    cursor_path = Path(cursor_path)

    raw_text = draft_path.read_text(encoding="utf-8")

    # Strip ```yaml … ``` or ``` … ``` fence if present
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        # Drop first line (e.g. "```yaml") and last line ("```")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner)

    # Parse YAML
    try:
        data = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise SyllabusError(f"YAML parse error in draft {draft_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SyllabusError(f"Expected mapping at top level in draft {draft_path}")

    if "book" not in data:
        raise KeyError("Draft missing required key: 'book'")

    # Validate modules exist and are non-empty before writing any files
    if not data.get("modules"):
        raise SyllabusError(f"Draft {draft_path} has no modules")

    # Validate each module and episode (raises KeyError on missing required keys)
    modules = [_parse_module(m, i) for i, m in enumerate(data["modules"])]

    syllabus = Syllabus(
        book=str(data["book"]),
        character_ref=str(data.get("character_ref", "")),
        modules=modules,
    )

    # All validation passed — write files atomically
    save_syllabus(syllabus, syllabus_path)
    save_cursor(Cursor(book=book, module_idx=0, episode_idx=0), cursor_path)

    return syllabus


# ---------------------------------------------------------------------------
# advance
# ---------------------------------------------------------------------------


def advance(syllabus: Syllabus, cursor: Cursor) -> Tuple[Syllabus, Cursor]:
    """Mark the current episode done and move cursor to the next pending episode.

    Pure-function style: returns new ``(Syllabus, Cursor)`` objects; the
    originals are not mutated.

    If there is no subsequent pending episode, the cursor is parked past the
    last module (``module_idx = len(modules)``, ``episode_idx = 0``).
    """
    # Deep-copy to guarantee no mutation of originals
    s2: Syllabus = copy.deepcopy(syllabus)
    c2: Cursor = copy.deepcopy(cursor)

    # Mark the current episode done in the copy
    if cursor.module_idx < len(s2.modules):
        module = s2.modules[cursor.module_idx]
        if cursor.episode_idx < len(module.episodes):
            module.episodes[cursor.episode_idx].status = "done"

    # Determine the current episode's global flat index in s2
    cursor_global: Optional[int] = None
    global_idx = 0
    for m_i, module in enumerate(s2.modules):
        for e_i in range(len(module.episodes)):
            if m_i == cursor.module_idx and e_i == cursor.episode_idx:
                cursor_global = global_idx
            global_idx += 1

    # Find the next pending episode after cursor_global
    if cursor_global is not None:
        global_idx = 0
        for m_i, module in enumerate(s2.modules):
            for e_i, episode in enumerate(module.episodes):
                if global_idx > cursor_global and episode.status == "pending":
                    c2.module_idx = m_i
                    c2.episode_idx = e_i
                    return s2, c2
                global_idx += 1

    # No subsequent pending episode — park cursor past end
    c2.module_idx = len(s2.modules)
    c2.episode_idx = 0
    return s2, c2
