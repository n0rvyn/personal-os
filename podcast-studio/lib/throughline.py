"""podcast-studio throughline portfolio mechanics.

Owns the recurring-theme ("throughline") tracking that lets the show
deepen 3–5 obsessions across days instead of rediscovering them:

  - `mine_candidates(cards, window_days, today)`: aggregate `topics`
    across prior stance cards within a recency window; rank recurring
    themes by frequency. Returns a list of {topic, count} dicts,
    highest-frequency first.
  - `load_obsessions(output_dir)`: read `{output_dir}/throughline.yaml`
    (list of {id, theme, confirmed_at}). Returns [] if the file does
    not exist or output_dir is missing. Raises naming the file on a
    malformed store (fail-closed — silently treating as 'empty' would
    let a corrupted store lose the user's confirmed obsessions).
  - `save_obsessions(output_dir, obsessions)`: atomic write of the
    user-curated obsession list. Overwrites the prior file (the
    throughline is user-curated, not append-only). Temp file is
    created in output_dir + `os.replace`; on any error the temp is
    removed (no orphan). Validates schema on save so a corrupted
    store cannot be persisted.
  - `pick_to_deepen(obsessions, cards)`: choose the confirmed obsession
    least-recently appearing in card topics. Returns the obsession
    dict augmented with a `new_angle` flag (True if no prior card has
    mentioned the theme). Returns None if obsessions is empty.

The obsession store is YAML (PyYAML); see requirements.txt.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml


# Fixed filename (single source of truth for the throughline store).
_THROUGHLINE_FILENAME = "throughline.yaml"

# Default anchor date for recency windows: when not supplied to
# mine_candidates, the function uses the latest card's episode date
# so that windows are always relative to the most recent episode.


# ---------- internal helpers -------------------------------------------------

def _throughline_path(output_dir: str | os.PathLike) -> Path:
    """Return the canonical throughline path: `<output_dir>/throughline.yaml`.

    Realpath-asserted: the resolved path must stay inside `output_dir`.
    Does NOT create the file or any directories.
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    candidate = out_dir / _THROUGHLINE_FILENAME
    real = os.path.realpath(str(candidate))
    if not real.startswith(str(out_dir) + os.sep) and real != str(out_dir):
        raise ValueError(
            f"throughline path escapes output_dir: {candidate} (realpath: {real})"
        )
    return candidate


def _parse_card_date(card: dict[str, Any]) -> str | None:
    """Extract the ISO date string from a card's episode block."""
    ep = card.get("episode")
    if not isinstance(ep, dict):
        return None
    d = ep.get("date")
    if not isinstance(d, str):
        return None
    try:
        _date.fromisoformat(d)
    except ValueError:
        return None
    return d


def _validate_obsessions(obsessions: Any) -> list[dict[str, Any]]:
    """Validate a parsed obsession list. Returns the list on success;
    raises ValueError on the first malformed item (caller names the file)."""
    if not isinstance(obsessions, list):
        raise ValueError(
            f"throughline store must be a list, got {type(obsessions).__name__}"
        )
    for i, item in enumerate(obsessions):
        if not isinstance(item, dict):
            raise ValueError(
                f"obsessions[{i}] must be a dict, got {type(item).__name__}"
            )
        # Required fields
        for field in ("id", "theme", "confirmed_at"):
            if field not in item:
                raise ValueError(
                    f"obsessions[{i}] missing required field: {field}"
                )
        # Types
        if not isinstance(item["id"], str) or not item["id"]:
            raise ValueError(f"obsessions[{i}].id must be a non-empty string")
        if not isinstance(item["theme"], str) or not item["theme"]:
            raise ValueError(f"obsessions[{i}].theme must be a non-empty string")
        if not isinstance(item["confirmed_at"], str):
            raise ValueError(
                f"obsessions[{i}].confirmed_at must be a string, got "
                f"{type(item['confirmed_at']).__name__}"
            )
        # confirmed_at must parse as an ISO date
        try:
            _date.fromisoformat(item["confirmed_at"])
        except ValueError as e:
            raise ValueError(
                f"obsessions[{i}].confirmed_at not a valid ISO date: "
                f"{item['confirmed_at']!r} ({e})"
            ) from e
    return obsessions


# ---------- public API -------------------------------------------------------

def mine_candidates(
    cards: list[dict[str, Any]],
    window_days: int = 7,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate `topics` from prior stance cards within a recency window
    and rank recurring themes by frequency.

    Args:
        cards: list of stance cards (from `lib.stance.load_cards`).
        window_days: include cards whose `episode.date` is within
            `window_days` of `today` (or the latest card's date when
            `today` is not supplied).
        today: ISO date string for the recency anchor. When None,
            the latest card's `episode.date` is used (so an empty
            window-anchor degrades to "all cards in the input").

    Returns:
        list of `{"topic": str, "count": int}` dicts, sorted by
        count desc, then by topic name asc (deterministic ordering
        for ties). Empty list if no cards or no topics.
    """
    if not cards:
        return []
    if window_days <= 0:
        return []

    # Determine the anchor date.
    if today is not None:
        try:
            anchor = _date.fromisoformat(today)
        except ValueError as e:
            raise ValueError(
                f"today {today!r} not a valid ISO date: {e}"
            ) from e
    else:
        # Default: latest card's episode date.
        latest = ""
        for c in cards:
            d = _parse_card_date(c)
            if d is not None and d > latest:
                latest = d
        if not latest:
            return []
        anchor = _date.fromisoformat(latest)

    # Aggregate topic counts across cards within the window.
    counts: dict[str, int] = {}
    for c in cards:
        d = _parse_card_date(c)
        if d is None:
            continue
        try:
            card_date = _date.fromisoformat(d)
        except ValueError:
            continue
        # Within window? (card_date in [anchor - window_days, anchor])
        delta = (anchor - card_date).days
        if delta < 0 or delta > window_days:
            continue
        topics = c.get("topics", [])
        if not isinstance(topics, list):
            continue
        for t in topics:
            if isinstance(t, str) and t:
                counts[t] = counts.get(t, 0) + 1

    # Sort by count desc, then topic name asc (deterministic ties).
    ranked = sorted(
        ({"topic": t, "count": n} for t, n in counts.items()),
        key=lambda x: (-x["count"], x["topic"]),
    )
    return ranked


def load_obsessions(output_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Load the user's confirmed obsession list from
    `{output_dir}/throughline.yaml`.

    - Returns `[]` if the file does not exist OR if `output_dir` does
      not exist (first-run path; the pipeline proceeds without a
      throughline and prompts the user next run).
    - Raises ValueError naming the file on a malformed store
      (fail-closed: silently treating as 'empty' would let a
      corrupted store lose the user's confirmed obsessions).
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    if not out_dir.exists():
        return []
    if not out_dir.is_dir():
        return []

    target = _throughline_path(out_dir)
    if not target.exists():
        return []

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(
            f"failed to read throughline store {target}: {e}"
        ) from e

    if not raw.strip():
        return []

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(
            f"malformed throughline store {target.name}: YAML parse error: {e}"
        ) from e

    try:
        return _validate_obsessions(parsed)
    except ValueError as e:
        raise ValueError(
            f"malformed throughline store {target.name}: {e}"
        ) from e


def save_obsessions(
    output_dir: str | os.PathLike,
    obsessions: list[dict[str, Any]],
) -> Path:
    """Atomic write of the user-curated obsession list to
    `{output_dir}/throughline.yaml`.

    - Overwrites the prior file (the throughline is user-curated, not
      append-only like stance cards).
    - Validates the schema on save (raises on malformed items; this
      prevents a corrupted store from being persisted in the first
      place).
    - Atomic write: temp in `output_dir` + `os.replace`; on any error
      the temp is removed (no orphan).
    - Realpath-asserted: the resolved path stays inside `output_dir`.

    Returns the path written.
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    if not out_dir.exists():
        raise FileNotFoundError(f"output_dir does not exist: {out_dir}")
    if not out_dir.is_dir():
        raise NotADirectoryError(f"output_dir is not a directory: {out_dir}")

    # Validate first so a bad obsessions list never produces a temp file.
    _validate_obsessions(obsessions)

    target = _throughline_path(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(out_dir),
    )
    tmp_p = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                obsessions, f, allow_unicode=True, sort_keys=False
            )
        os.replace(str(tmp_p), str(target))
    except Exception:
        # On any error, remove the temp file (no orphan).
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise

    return target


def pick_to_deepen(
    obsessions: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the confirmed obsession least-recently appearing in card topics.

    Algorithm:
      1. For each obsession, find the most recent card date that
         mentions the theme in `topics` (None if never mentioned).
      2. Sort by "most stale" (oldest last-mentioned date, or
         `confirmed_at` if never mentioned) ascending → pick first.
      3. Mark `new_angle=True` if the theme has never appeared in any
         prior card (i.e. fresh — a new lens on this obsession).
      4. Returns None if `obsessions` is empty (no obsessions yet).

    Deterministic: ties broken by `confirmed_at` asc, then by `id` asc.

    Returns the obsession dict augmented with `new_angle: bool`.
    """
    if not obsessions:
        return None

    # For each obsession, find the most recent card date mentioning
    # its theme in `topics`. None if never mentioned.
    last_seen: dict[str, str | None] = {}
    for o in obsessions:
        theme = o["theme"]
        latest = None
        for c in cards:
            topics = c.get("topics", [])
            if not isinstance(topics, list):
                continue
            if not any(isinstance(t, str) and t == theme for t in topics):
                continue
            d = _parse_card_date(c)
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d
        last_seen[o["id"]] = latest

    # Sort: never-seen first (their effective last_seen is None, treated
    # as "older than any real date"), then by last_seen asc, then by
    # confirmed_at asc, then by id asc (deterministic).
    def _sort_key(o: dict[str, Any]) -> tuple:
        ls = last_seen[o["id"]]
        # None → "" (sorts first alphabetically, but we'll treat None as
        # "infinitely old" so it sorts first; using "" works because
        # all real dates are non-empty).
        return (
            ls if ls is not None else "",
            o.get("confirmed_at", ""),
            o.get("id", ""),
        )

    # The sort above puts unrecent-or-never-seen first; for ties among
    # never-seen obsessions, the earliest-confirmed (oldest promise)
    # comes first. But the contract says "least-recently-deepened" →
    # never-seen is the most stale (deepest in priority). Good.
    sorted_obs = sorted(obsessions, key=_sort_key)
    chosen = sorted_obs[0]
    new_angle = last_seen[chosen["id"]] is None
    return {**chosen, "new_angle": new_angle}
