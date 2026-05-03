"""Tests for getnote-intel cursor checkpoint logic.

Per project lesson 2026-05-02-pytest-main-guard-silent-pass.md: never use
`if __name__ == "__main__"` guards in test files — they make pytest a no-op.
Use plain top-level test functions only.
"""
import os, sys, tempfile, datetime
from pathlib import Path
import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import cursor  # noqa: E402

@pytest.fixture
def tmp_cursor(monkeypatch, tmp_path):
    cursor_file = tmp_path / "getnote-intel-state.yaml"
    monkeypatch.setattr(cursor, "cursor_path", lambda: cursor_file)
    return cursor_file

def test_load_returns_empty_when_missing(tmp_cursor):
    state = cursor.load_cursor()
    assert state["seen_blogger_posts"] == []
    assert state["seen_lives"] == []
    assert state["last_topic_id"] is None
    assert state["last_blogger_idx"] == -1
    assert state["last_synced_at"] is None

def test_save_then_load_roundtrip(tmp_cursor):
    cursor.save_cursor({
        "seen_blogger_posts": ["post-1", "post-2"],
        "seen_lives": ["live-a"],
        "last_topic_id": "topic-X",
        "last_blogger_idx": 3,
    })
    state = cursor.load_cursor()
    assert sorted(state["seen_blogger_posts"]) == ["post-1", "post-2"]
    assert state["seen_lives"] == ["live-a"]
    assert state["last_topic_id"] == "topic-X"
    assert state["last_blogger_idx"] == 3
    assert state["last_synced_at"] is not None  # auto-stamped

def test_mark_progress_dedupes_post_ids(tmp_cursor):
    cursor.save_cursor({"seen_blogger_posts": ["p1"], "last_topic_id": "t1", "last_blogger_idx": 0})
    cursor.mark_progress("t1", 1, post_ids=["p1", "p2", "p3"])
    state = cursor.load_cursor()
    assert sorted(state["seen_blogger_posts"]) == ["p1", "p2", "p3"]
    assert state["last_blogger_idx"] == 1

def test_resume_point_after_failure(tmp_cursor):
    """Simulate: prior run wrote cursor at (topic-2, idx 5) then crashed.
    Next run's resume_point should return (topic-2, 5)."""
    cursor.mark_progress("topic-2", 5, post_ids=["p-mid"])
    tid, idx = cursor.resume_point()
    assert tid == "topic-2"
    assert idx == 5

def test_resume_point_fresh_start(tmp_cursor):
    tid, idx = cursor.resume_point()
    assert tid is None and idx == -1

def test_seen_post_cap_at_500(tmp_cursor):
    """Cursor must bound the seen list to avoid unbounded growth."""
    big = [f"p-{i}" for i in range(700)]
    cursor.save_cursor({"seen_blogger_posts": big, "last_topic_id": "t", "last_blogger_idx": 0})
    state = cursor.load_cursor()
    assert len(state["seen_blogger_posts"]) == 500

def test_atomic_write_via_tmp_file(tmp_cursor, tmp_path):
    """save_cursor must use rename-from-tmp so a crash mid-write doesn't corrupt the file."""
    cursor.save_cursor({"seen_blogger_posts": ["only"], "last_topic_id": "t", "last_blogger_idx": 0})
    # No .tmp residue should remain after a successful save
    residue = list(tmp_path.glob("*.tmp"))
    assert residue == []
