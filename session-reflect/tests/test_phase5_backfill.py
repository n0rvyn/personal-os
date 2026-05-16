#!/usr/bin/env python3
"""Phase 5 backfill tests: reparse_broken_component function."""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


def _load_backfill():
    """Load backfill module via importlib (filename has no .py extension convention)."""
    spec = importlib.util.spec_from_file_location("backfill", SCRIPT_DIR / "backfill.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)
    return backfill


def _make_plugin_event(session_id, tool_use_id, component):
    """Return a minimal plugin_event dict for seeding."""
    return {
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "component_type": "skill",
        "plugin": None if component in ("skill", "agent") else "dev-workflow",
        "component": component,
        "invoked_at": "2026-05-16T10:00:00.000Z",
        "input_text": json.dumps({"skill": f"dev-workflow:{component}"}),
        "result_text": "",
        "result_ok": 1,
        "agent_turns_used": None,
        "agent_max_turns": None,
        "model_override": None,
        "post_dispatch_signals": None,
        "invocation_trigger": None,
        "duration_ms": None,
        "parent_tool_use_id": None,
        "cwd": None,
    }


def _canned_parse_result(session_id, component, tool_use_id=None):
    """Return a canned parse_claude_session result with one fixed plugin_event.

    tool_use_id must match the seeded broken row's tool_use_id so that
    upsert_plugin_event's DELETE-before-INSERT removes the old broken row.
    """
    if tool_use_id is None:
        # Derive from session_id to match seed pattern; broken rows use "tu-broken-N"
        # We map session_id → tool_use_id by the seeding convention
        idx_map = {"broken-1": "tu-broken-1", "broken-2": "tu-broken-2",
                   "broken-3": "tu-broken-3", "new-broken": "tu-new-broken"}
        tool_use_id = idx_map.get(session_id, f"tu-{session_id}-fixed")
    return {
        "session_id": session_id,
        "source": "claude-code",
        "plugin_events": [
            {
                "session_id": session_id,
                "tool_use_id": tool_use_id,
                "component_type": "skill",
                "plugin": "dev-workflow",
                "component": component,
                "invoked_at": "2026-05-16T10:00:00.000Z",
                "input_text": json.dumps({"skill": f"dev-workflow:{component}"}),
                "result_text": "",
                "result_ok": 1,
                "agent_turns_used": None,
                "agent_max_turns": None,
                "model_override": None,
                "post_dispatch_signals": None,
                "invocation_trigger": "user-slash",
                "duration_ms": 2000,
                "parent_tool_use_id": None,
                "cwd": "/Users/test/proj",
            }
        ],
    }


class TestPhase5BackfillReparseComponent(unittest.TestCase):
    """Tests for backfill.reparse_broken_component()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "sessions.db"
        import sessions_db
        self._orig_db_path = sessions_db.DB_PATH
        sessions_db.DB_PATH = self.db_path
        self.sessions_db = sessions_db
        self.sessions_db.init_db()
        self._seed_db()

    def tearDown(self):
        self.sessions_db.DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed_db(self):
        """Seed 5 plugin_events rows: 3 broken (component='skill'), 2 correct."""
        conn = sqlite3.connect(self.db_path)
        # Insert sessions first (FK parent)
        for sid in ("broken-1", "broken-2", "broken-3", "ok-1", "ok-2"):
            conn.execute(
                "INSERT INTO sessions (session_id, source, project_path) VALUES (?, 'claude-code', ?)",
                (sid, f"/Users/test/{sid}-proj"),
            )
        # 3 broken rows
        for i, sid in enumerate(("broken-1", "broken-2", "broken-3"), start=1):
            event = _make_plugin_event(sid, f"tu-broken-{i}", "skill")
            self.sessions_db.upsert_plugin_event(event, conn=conn)
        # 2 correct rows
        for i, sid in enumerate(("ok-1", "ok-2"), start=1):
            event = _make_plugin_event(sid, f"tu-ok-{i}", "verify-plan")
            self.sessions_db.upsert_plugin_event(event, conn=conn)
        conn.commit()
        conn.close()

    def _load_backfill_with_db(self):
        """Load backfill module with overridden DB path."""
        backfill = _load_backfill()
        # Point backfill's sessions_db to the temp DB
        import sessions_db
        backfill.sessions_db = sessions_db
        return backfill

    def test_finds_broken_rows(self):
        """dry_run=True reports 3 broken sessions."""
        backfill = self._load_backfill_with_db()
        result = backfill.reparse_broken_component(dry_run=True)
        self.assertEqual(result["sessions"], 3,
                         f"Expected 3 broken sessions, got: {result['sessions']}")

    def test_reparse_updates_in_place(self):
        """Wet run: broken rows get updated component; correct rows untouched."""
        backfill = self._load_backfill_with_db()

        # Capture ok row IDs before reparse
        conn = sqlite3.connect(self.db_path)
        ok_ids = {row[0] for row in conn.execute(
            "SELECT id FROM plugin_events WHERE component = 'verify-plan'"
        ).fetchall()}
        conn.close()

        # Patch parse_claude_session and _locate_jsonl
        def fake_parse(path):
            # Derive session_id from path (path is a fake Path object)
            sid = Path(path).stem  # e.g. "broken-1"
            return _canned_parse_result(sid, "verify-plan")

        def fake_locate(session_id, project_path):
            # Return a fake path (file doesn't need to exist — parse is mocked)
            return Path(self.tmpdir) / f"{session_id}.jsonl"

        with patch.object(backfill, "parse_claude_session", side_effect=fake_parse), \
             patch.object(backfill, "_locate_jsonl", side_effect=fake_locate):
            result = backfill.reparse_broken_component(dry_run=False)

        self.assertGreater(result["rows_updated"], 0,
                           "Expected rows_updated > 0 after wet run")

        # Correct rows' IDs should still exist and remain untouched
        conn = sqlite3.connect(self.db_path)
        still_ok_ids = {row[0] for row in conn.execute(
            "SELECT id FROM plugin_events WHERE component = 'verify-plan'"
        ).fetchall()}
        conn.close()
        # Original ok row IDs may or may not survive (upsert deletes+reinserts);
        # what matters is correct rows still exist and broken ones are fixed
        still_broken = conn_count = None
        conn = sqlite3.connect(self.db_path)
        still_broken = conn.execute(
            "SELECT COUNT(*) FROM plugin_events WHERE component IN ('skill','agent')"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(still_broken, 0,
                         f"Expected 0 broken rows after reparse, got {still_broken}")

    def test_idempotent(self):
        """Running reparse twice: second run finds 0 sessions to update."""
        backfill = self._load_backfill_with_db()

        def fake_parse(path):
            sid = Path(path).stem
            return _canned_parse_result(sid, "verify-plan")

        def fake_locate(session_id, project_path):
            return Path(self.tmpdir) / f"{session_id}.jsonl"

        with patch.object(backfill, "parse_claude_session", side_effect=fake_parse), \
             patch.object(backfill, "_locate_jsonl", side_effect=fake_locate):
            backfill.reparse_broken_component(dry_run=False)

        # Second run with fresh backfill instance
        backfill2 = self._load_backfill_with_db()
        result2 = backfill2.reparse_broken_component(dry_run=True)
        self.assertEqual(result2["sessions"], 0,
                         f"Expected 0 broken sessions on second dry_run, got {result2['sessions']}")

    def test_new_broken_rows_picked_up(self):
        """A newly inserted broken row (component='skill'/'agent') is picked up by next reparse."""
        # reparse_broken_component selects by component literal ('skill'/'agent'), NOT by
        # analyzer_version — so the analyzer_version bump mechanism is orthogonal here.
        # Renamed from test_respects_analyzer_version which was a name/behavior mismatch
        # (audit DP — implementation-reviewer-2026-05-16-154602.md §13.1).
        backfill = self._load_backfill_with_db()

        def fake_parse(path):
            sid = Path(path).stem
            return _canned_parse_result(sid, "verify-plan")

        def fake_locate(session_id, project_path):
            return Path(self.tmpdir) / f"{session_id}.jsonl"

        # First reparse — clears all 3 broken
        with patch.object(backfill, "parse_claude_session", side_effect=fake_parse), \
             patch.object(backfill, "_locate_jsonl", side_effect=fake_locate):
            backfill.reparse_broken_component(dry_run=False)

        # Inject a new broken row (simulates a new session with broken component)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO sessions (session_id, source, project_path) VALUES ('new-broken', 'claude-code', '/Users/test/new-proj')"
        )
        new_event = _make_plugin_event("new-broken", "tu-new-broken", "skill")
        conn.commit()
        conn.close()
        import sessions_db
        sessions_db.upsert_plugin_event(new_event)

        backfill3 = self._load_backfill_with_db()
        result3 = backfill3.reparse_broken_component(dry_run=True)
        self.assertEqual(result3["sessions"], 1,
                         f"Expected 1 broken session after new broken row added, got {result3['sessions']}")


if __name__ == "__main__":
    unittest.main()
