"""Stateful helper for source_log.jsonl (cross-period note dedup state).

Per plan 2026-06-07-podcast-source-recurrence-fix-plan Task 3: the CLI `check`
handler reads the last `window_days` days of offered-note paths from this file
and feeds the union into `cross_domain_candidates(exclude_ids=...)` to prevent
the same PKOS note from being offered on consecutive episodes (DP-001 A:
offered-at-check recording — the brief is never empty, and the small-bucket
backfill in `cross_domain_candidates` is the safety net when every candidate
was offered yesterday).

Format: one JSON object per line, append-only.
    {"date": "YYYY-MM-DD", "note_ids": ["a.md", "b.md"]}

Mirrors the style of `topic_log.py` (stdlib only, no PyYAML/other deps).
The corrupt-line tolerance mirrors `podcast_sources.py`'s jsonl handling:
a malformed line is silently skipped so a stray truncated write never
breaks the dedup read path.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path


def _ensure_parent(path) -> None:
    """mkdir -p for the parent dir so first-run writes don't FileNotFoundError."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def append_offered(path, ep_date: str, note_ids) -> None:
    """Append one episode's worth of offered note paths to source_log.jsonl.

    `ep_date` is the ISO date of the episode (e.g. "2026-06-07"). `note_ids`
    is an iterable of path strings (e.g. ["10-Knowledge/foo.md", ...]).
    Each call writes exactly one line. Concurrent appends are safe via
    O_APPEND on POSIX (POSIX guarantees atomic write of <=PIPE_BUF bytes;
    jsonl lines are well under that ceiling).
    """
    _ensure_parent(path)
    record = {"date": ep_date, "note_ids": list(note_ids)}
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def recent_source_ids(path, today: str, window_days: int = 14) -> set:
    """Return the set of note paths offered in the last `window_days` days.

    Window is [today - window_days, today], inclusive of both ends. Days
    outside the window are ignored. Out-of-window lines are simply not
    included in the returned set; we do NOT prune the file (TODO: optional
    in-append prune to keep file size bounded — defer; growth is slow and
    prune is not on the critical path).

    Edge cases:
    - File missing → empty set (no exception)
    - Corrupt JSON line → skipped, not raised
    - Line without `date` / `note_ids` keys → skipped
    - `window_days <= 0` → empty set
    """
    if window_days <= 0:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    try:
        today_d = date.fromisoformat(today)
    except ValueError:
        return set()
    cutoff = today_d - timedelta(days=window_days)
    ids: set = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        d_str = rec.get("date", "")
        try:
            d = date.fromisoformat(d_str)
        except (TypeError, ValueError):
            continue
        if not (cutoff <= d <= today_d):
            continue
        note_ids = rec.get("note_ids", [])
        if not isinstance(note_ids, list):
            continue
        for nid in note_ids:
            if isinstance(nid, str) and nid:
                ids.add(nid)
    return ids
