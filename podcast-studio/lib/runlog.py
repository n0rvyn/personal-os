"""podcast-studio run-log — persistent structured run events (Phase 2).

Post-publish distillation stations (covered-ground / stance / — later —
thesis-ledger) are fail-SOFT: a failure there must never halt an
already-published episode. But the pre-Phase-2 behavior only `print(...)`-ed
the skip to stderr — nothing landed on disk — so a silently-skipped
distillation left the cross-episode memory with a hole no operator could see
after the run (the real 6/23 + 6/25–27 covered-ground store gaps). This module
gives those events a durable home: an append-only JSONL at
`<state_dir>/run-log.jsonl`, one JSON object per line.

Design:
- One line per event: `{"ts", "date", "show", "step", "level", "reason"}`.
- Append-only (open "a"); the pipeline runs one episode at a time, so a
  single-writer append is safe. No overwrite, no truncation.
- **Logging is itself fail-soft: `append_run_log` NEVER raises.** A logging
  failure must not crash the run it is trying to record — it returns False.
- `read_run_log` skips malformed lines (a hand-edited / partially-written
  line must not blow up a reader).
- `ts` is injectable so tests are deterministic; the runner passes None →
  `datetime.now().isoformat(timespec="seconds")`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Fixed filename under `state_dir`.
_RUN_LOG_FILENAME = "run-log.jsonl"

# Recognized levels (free-form is tolerated; these are the ones the runner
# emits). Kept as a tuple for documentation, not enforced (a logger must not
# reject an event because of an unexpected level string).
LEVELS = ("INFO", "WARNING", "ERROR")


def run_log_path(state_dir: str | os.PathLike) -> Path:
    """Return `<state_dir>/run-log.jsonl` (does not create anything)."""
    return Path(str(state_dir)) / _RUN_LOG_FILENAME


def append_run_log(
    state_dir: str | os.PathLike,
    *,
    date: str,
    show: str,
    step: str,
    level: str,
    reason: str,
    ts: Optional[str] = None,
) -> bool:
    """Append one structured event to `<state_dir>/run-log.jsonl`.

    NEVER raises — a logging failure must not crash the run being recorded.
    Returns True on a successful append, False on any failure (missing dir,
    permission error, serialization error, …).

    `ts` defaults to `datetime.now().isoformat(timespec="seconds")` when None.
    """
    try:
        if ts is None:
            # Local import so a module-load-time clock probe is avoided and the
            # module stays import-light; runner already uses datetime elsewhere.
            from datetime import datetime as _dt
            ts = _dt.now().isoformat(timespec="seconds")
        entry = {
            "ts": ts,
            "date": date,
            "show": show,
            "step": step,
            "level": level,
            "reason": reason,
        }
        line = json.dumps(entry, ensure_ascii=False)
        path = run_log_path(state_dir)
        # Append mode; create if absent. State dir is expected to exist (config
        # fail-closed-creates it); if it doesn't, this raises and we return
        # False (fail-soft) rather than crash the run.
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001 — logging must never crash the run
        return False


def read_run_log(state_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Return the parsed events in append order.

    - Missing file → `[]` (no raise).
    - Malformed / partial lines are skipped (a hand-edited or half-written
      line must not blow up a reader).
    """
    path = run_log_path(state_dir)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
