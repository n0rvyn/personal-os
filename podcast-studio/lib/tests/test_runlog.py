"""Tests for lib/runlog.py — persistent structured run-log (Phase 2).

Pins:
- run_log_path: <state_dir>/run-log.jsonl
- append_run_log: appends one JSON line; append-only (never truncates);
  NEVER raises (fail-soft) — returns False on failure, True on success
- read_run_log: parses JSONL in order; missing file → []; malformed lines skipped
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def test_run_log_path(tmp_path):
    from lib.runlog import run_log_path
    p = run_log_path(str(tmp_path))
    assert p.name == "run-log.jsonl"
    assert str(p).startswith(str(tmp_path))


def test_append_then_read_roundtrip(tmp_path):
    from lib.runlog import append_run_log, read_run_log
    ok = append_run_log(
        str(tmp_path), date="2026-06-23", show="evening",
        step="coveredground-distill", level="WARNING",
        reason="dispatch failed", ts="2026-06-23T09:00:00",
    )
    assert ok is True
    events = read_run_log(str(tmp_path))
    assert len(events) == 1
    e = events[0]
    assert e["date"] == "2026-06-23"
    assert e["show"] == "evening"
    assert e["step"] == "coveredground-distill"
    assert e["level"] == "WARNING"
    assert e["reason"] == "dispatch failed"
    assert e["ts"] == "2026-06-23T09:00:00"


def test_append_is_append_only(tmp_path):
    """Two appends leave TWO lines in order — the second must not truncate
    the first (append-only, not overwrite)."""
    from lib.runlog import append_run_log, read_run_log
    append_run_log(str(tmp_path), date="2026-06-23", show="morning",
                   step="a", level="INFO", reason="first", ts="t1")
    append_run_log(str(tmp_path), date="2026-06-23", show="evening",
                   step="b", level="WARNING", reason="second", ts="t2")
    events = read_run_log(str(tmp_path))
    assert [e["reason"] for e in events] == ["first", "second"]


def test_append_default_ts_is_iso_seconds(tmp_path):
    """ts=None → an ISO timestamp to seconds precision (no microseconds)."""
    from lib.runlog import append_run_log, read_run_log
    append_run_log(str(tmp_path), date="2026-07-02", show="evening",
                   step="s", level="INFO", reason="r")
    e = read_run_log(str(tmp_path))[0]
    ts = e["ts"]
    # ISO seconds: "YYYY-MM-DDTHH:MM:SS", no dot/microseconds
    assert "T" in ts and "." not in ts
    assert len(ts) == len("2026-07-02T00:00:00")


def test_append_never_raises_on_bad_dir(tmp_path):
    """A nonexistent state_dir must not raise — returns False (fail-soft:
    logging must never crash the run it records)."""
    from lib.runlog import append_run_log
    bogus = tmp_path / "does" / "not" / "exist"
    ok = append_run_log(str(bogus), date="2026-07-02", show="evening",
                        step="s", level="ERROR", reason="r")
    assert ok is False


def test_read_missing_file_returns_empty(tmp_path):
    from lib.runlog import read_run_log
    assert read_run_log(str(tmp_path)) == []


def test_read_skips_malformed_lines(tmp_path):
    """A hand-edited / half-written non-JSON line is skipped, not fatal."""
    from lib.runlog import append_run_log, read_run_log, run_log_path
    append_run_log(str(tmp_path), date="2026-07-02", show="evening",
                   step="s", level="INFO", reason="good", ts="t1")
    # Corrupt the file: add a garbage line + a blank line
    with open(run_log_path(str(tmp_path)), "a", encoding="utf-8") as f:
        f.write("this is not json\n\n")
    append_run_log(str(tmp_path), date="2026-07-02", show="evening",
                   step="s2", level="INFO", reason="good2", ts="t2")
    events = read_run_log(str(tmp_path))
    assert [e["reason"] for e in events] == ["good", "good2"]
