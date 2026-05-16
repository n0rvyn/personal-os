#!/usr/bin/env python3
"""Phase 5 schema extension + parser population tests."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CLAUDE_INVOCATION_TRIGGER_SAMPLE = os.path.join(
    TEST_DATA_DIR, "claude-invocation-trigger-sample.jsonl"
)


class TestPhase5SchemaExtension(unittest.TestCase):
    """Tests that migrate_schema() adds Phase 5 columns and tables."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "sessions.db"
        import sessions_db
        self._orig_db_path = sessions_db.DB_PATH
        sessions_db.DB_PATH = self.db_path
        self.sessions_db = sessions_db

    def tearDown(self):
        self.sessions_db.DB_PATH = self._orig_db_path
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _init_and_migrate(self):
        self.sessions_db.init_db()
        self.sessions_db.migrate_schema()

    def test_invocation_trigger_column_added(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(plugin_events)").fetchall()}
        conn.close()
        self.assertIn("invocation_trigger", cols,
                      f"Column 'invocation_trigger' not found in plugin_events. Columns: {cols}")

    def test_duration_ms_column_added(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        col_info = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(plugin_events)").fetchall()}
        conn.close()
        self.assertIn("duration_ms", col_info,
                      f"Column 'duration_ms' not found in plugin_events. Columns: {set(col_info)}")
        # Verify type is INTEGER
        self.assertEqual(col_info.get("duration_ms", "").upper(), "INTEGER",
                         f"Expected duration_ms to be INTEGER, got: {col_info.get('duration_ms')}")

    def test_parent_tool_use_id_column_added(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        col_info = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(plugin_events)").fetchall()}
        conn.close()
        self.assertIn("parent_tool_use_id", col_info,
                      f"Column 'parent_tool_use_id' not found in plugin_events. Columns: {set(col_info)}")
        self.assertEqual(col_info.get("parent_tool_use_id", "").upper(), "TEXT",
                         f"Expected parent_tool_use_id to be TEXT, got: {col_info.get('parent_tool_use_id')}")

    def test_cwd_column_added(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        col_info = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(plugin_events)").fetchall()}
        conn.close()
        self.assertIn("cwd", col_info,
                      f"Column 'cwd' not found in plugin_events. Columns: {set(col_info)}")
        self.assertEqual(col_info.get("cwd", "").upper(), "TEXT",
                         f"Expected cwd to be TEXT, got: {col_info.get('cwd')}")

    def test_effort_level_column_added_to_sessions(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        col_info = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        conn.close()
        self.assertIn("effort_level", col_info,
                      f"Column 'effort_level' not found in sessions. Columns: {set(col_info)}")
        self.assertEqual(col_info.get("effort_level", "").upper(), "TEXT",
                         f"Expected effort_level to be TEXT, got: {col_info.get('effort_level')}")

    def test_skill_proactive_triggers_table_created(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_proactive_triggers'"
        ).fetchone()
        self.assertIsNotNone(row, "Table 'skill_proactive_triggers' not found in sqlite_master")

        # Verify required columns exist
        cols = {r[1] for r in conn.execute("PRAGMA table_info(skill_proactive_triggers)").fetchall()}
        conn.close()
        required_cols = {"id", "plugin_event_id", "user_prompt_excerpt",
                         "skill_description_snapshot", "triggered_correctly"}
        self.assertTrue(required_cols.issubset(cols),
                        f"Missing columns in skill_proactive_triggers: {required_cols - cols}")

    def test_skill_proactive_triggers_cascade_on_event_delete(self):
        self._init_and_migrate()
        conn = sqlite3.connect(self.db_path)
        # Must enable FK enforcement on this connection for CASCADE to fire
        conn.execute("PRAGMA foreign_keys = ON")

        # Seed: insert 1 session + 1 plugin_events row + 1 skill_proactive_triggers row
        conn.execute(
            "INSERT INTO sessions (session_id, source) VALUES ('cascade-test-session', 'claude-code')"
        )
        conn.execute("""
            INSERT INTO plugin_events (session_id, tool_use_id, component_type, component)
            VALUES ('cascade-test-session', 'tu-cascade-1', 'skill', 'verify-plan')
        """)
        pe_id = conn.execute(
            "SELECT id FROM plugin_events WHERE tool_use_id='tu-cascade-1'"
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO skill_proactive_triggers (plugin_event_id, user_prompt_excerpt, triggered_correctly)
            VALUES (?, 'test prompt', 1)
        """, (pe_id,))
        conn.commit()

        # Verify trigger row exists
        count_before = conn.execute(
            "SELECT COUNT(*) FROM skill_proactive_triggers WHERE plugin_event_id=?", (pe_id,)
        ).fetchone()[0]
        self.assertEqual(count_before, 1, "Pre-condition: trigger row should exist")

        # DELETE plugin_event row — CASCADE should remove trigger row
        conn.execute("DELETE FROM plugin_events WHERE id=?", (pe_id,))
        conn.commit()

        count_after = conn.execute(
            "SELECT COUNT(*) FROM skill_proactive_triggers WHERE plugin_event_id=?", (pe_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, 0,
                         "Expected skill_proactive_triggers row to be CASCADE deleted with plugin_events row")

    def test_cascade_via_production_write_path(self):
        """Regression: write connections opened by _get_conn() must enable FK enforcement
        so CASCADE fires in production paths (not just test code that sets PRAGMA inline).

        This is the bug discovery from execute-plan: PRAGMA was only set in migrate_schema(),
        so upsert_plugin_event-style write connections didn't enable FK enforcement → CASCADE
        silently no-op'd in production. Fix: _get_conn() sets PRAGMA on every write conn.
        """
        self._init_and_migrate()
        # Use the production helper — NO inline PRAGMA setting in this test.
        conn = self.sessions_db._get_conn()

        # Verify the helper actually set PRAGMA (sanity check)
        fk_state = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(fk_state, 1,
                         "_get_conn() must enable PRAGMA foreign_keys=ON for write connections")

        # Seed via production write path: insert session + plugin_event + trigger
        conn.execute(
            "INSERT INTO sessions (session_id, source) VALUES ('prod-cascade-test', 'claude-code')"
        )
        conn.execute("""
            INSERT INTO plugin_events (session_id, tool_use_id, component_type, component)
            VALUES ('prod-cascade-test', 'tu-prod-1', 'skill', 'verify-plan')
        """)
        pe_id = conn.execute(
            "SELECT id FROM plugin_events WHERE tool_use_id='tu-prod-1'"
        ).fetchone()[0]
        conn.execute("""
            INSERT INTO skill_proactive_triggers (plugin_event_id, user_prompt_excerpt, triggered_correctly)
            VALUES (?, 'prod test prompt', 1)
        """, (pe_id,))
        conn.commit()

        # DELETE the parent plugin_events row — CASCADE should remove the child trigger
        conn.execute("DELETE FROM plugin_events WHERE id=?", (pe_id,))
        conn.commit()

        count_after = conn.execute(
            "SELECT COUNT(*) FROM skill_proactive_triggers WHERE plugin_event_id=?", (pe_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, 0,
                         "Production write path must trigger CASCADE delete; _get_conn() must set PRAGMA")

    def test_migrate_schema_idempotent(self):
        self._init_and_migrate()
        # Call migrate_schema 2 more times (3 total) — should not raise
        try:
            self.sessions_db.migrate_schema()
            self.sessions_db.migrate_schema()
        except Exception as e:
            self.fail(f"migrate_schema() raised on repeated call: {e}")


class TestPhase5ParserPopulation(unittest.TestCase):
    """Tests that parser populates 4 new plugin_events columns + skill_proactive_triggers
    + sessions.effort_level from the claude-invocation-trigger-sample.jsonl fixture."""

    @classmethod
    def setUpClass(cls):
        # Import here so fixture file missing gives a clear error at setup
        from parse_claude_session import parse_claude_session
        cls.result = parse_claude_session(CLAUDE_INVOCATION_TRIGGER_SAMPLE)
        cls.events = cls.result.get("plugin_events", [])

    def _get_event_by_trigger(self, trigger_value):
        return next(
            (e for e in self.events if e.get("invocation_trigger") == trigger_value), None
        )

    def test_user_slash_trigger_detected(self):
        """Pair 1: /command-name syntax → invocation_trigger='user-slash'."""
        event = self._get_event_by_trigger("user-slash")
        self.assertIsNotNone(event,
                             f"No event with invocation_trigger='user-slash'. Events: "
                             f"{[e.get('invocation_trigger') for e in self.events]}")
        self.assertEqual(event.get("component"), "verify-plan")
        self.assertIsNone(event.get("parent_tool_use_id"),
                          f"user-slash event should have NULL parent_tool_use_id")

    def test_claude_proactive_trigger_detected_and_proactive_trigger_row_inserted(self):
        """Pair 2: natural language prompt → invocation_trigger='claude-proactive' +
        skill_proactive_triggers row with triggered_correctly=0 (correction follows)."""
        event = self._get_event_by_trigger("claude-proactive")
        self.assertIsNotNone(event,
                             f"No event with invocation_trigger='claude-proactive'. Events: "
                             f"{[e.get('invocation_trigger') for e in self.events]}")
        proactive_trigger = event.get("_proactive_trigger")
        self.assertIsNotNone(proactive_trigger,
                             "Expected '_proactive_trigger' dict on claude-proactive event")
        self.assertEqual(proactive_trigger.get("triggered_correctly"), 0,
                         "Expected triggered_correctly=0 because correction follows within 3 turns")
        excerpt = proactive_trigger.get("user_prompt_excerpt", "")
        self.assertIsNotNone(excerpt)
        self.assertLessEqual(len(excerpt), 500, "user_prompt_excerpt should be truncated to ≤500 chars")

    def test_nested_skill_trigger_and_parent_id(self):
        """Pair 3: skill inside an Agent dispatch → invocation_trigger='nested-skill'
        + parent_tool_use_id points to outer Agent's tool_use_id."""
        event = self._get_event_by_trigger("nested-skill")
        self.assertIsNotNone(event,
                             f"No event with invocation_trigger='nested-skill'. Events: "
                             f"{[e.get('invocation_trigger') for e in self.events]}")
        self.assertIsNotNone(event.get("parent_tool_use_id"),
                             "nested-skill event should have non-NULL parent_tool_use_id")
        self.assertEqual(event.get("parent_tool_use_id"), "agent_outer",
                         f"Expected parent_tool_use_id='agent_outer', got: {event.get('parent_tool_use_id')}")

    def test_duration_ms_computed(self):
        """At least one event should have duration_ms > 0 (computed from timestamps)."""
        events_with_duration = [e for e in self.events if e.get("duration_ms") is not None]
        self.assertGreater(len(events_with_duration), 0,
                           "Expected at least 1 event with non-NULL duration_ms")
        for e in events_with_duration:
            self.assertGreater(e["duration_ms"], 0,
                               f"duration_ms should be positive, got: {e['duration_ms']}")

    def test_cwd_per_event_captured(self):
        """Pair 4: cwd should be captured per event from record-level cwd."""
        events_with_cwd = [e for e in self.events if e.get("cwd") is not None]
        self.assertGreater(len(events_with_cwd), 0,
                           "Expected at least 1 event with non-NULL cwd")
        # Pair 4 event should show the worktree cwd
        worktree_events = [e for e in self.events if e.get("cwd") == "/Users/test/proj-a/worktree-x"]
        self.assertGreater(len(worktree_events), 0,
                           f"Expected event with cwd='/Users/test/proj-a/worktree-x'. "
                           f"Actual cwds: {[e.get('cwd') for e in self.events]}")

    def test_effort_level_captured_on_session(self):
        """Session-level effort_level is parsed from fixture metadata."""
        # NOTE: If effort.level field is not yet emitted by Claude Code 2.1.143,
        # this test asserts the key is present in result (may be None/null).
        # The fixture has effort.level='high' in user prompt metadata.
        self.assertIn("effort_level", self.result,
                      "Expected 'effort_level' key in parse_claude_session result dict")
        self.assertEqual(self.result.get("effort_level"), "high",
                         f"Expected effort_level='high' from fixture, got: {self.result.get('effort_level')}")

    def test_has_correction_within_3_turns_helper(self):
        """Unit test for _has_correction_within_3_turns helper (4 boundary cases)."""
        # Import inside test method to avoid ImportError at load time
        # (helper doesn't exist until Task 3-impl)
        import parse_claude_session as pcs
        fn = getattr(pcs, "_has_correction_within_3_turns", None)
        self.assertIsNotNone(fn,
                             "_has_correction_within_3_turns not found in parse_claude_session module")

        # Case 1: 0 following turns → no correction → False
        self.assertFalse(fn({}, []))

        # Case 2: 1 turn with correction cue → True
        self.assertTrue(fn({}, [{"text": "wrong, do it differently"}]))

        # Case 3: 3rd turn (index 2) has correction → True (still within window)
        self.assertTrue(fn({}, [
            {"text": "ok thanks"},
            {"text": "looks fine"},
            {"text": "不对，先别"},
        ]))

        # Case 4: 4th turn (index 3) has correction → False (outside 3-turn window)
        self.assertFalse(fn({}, [
            {"text": "ok"},
            {"text": "sure"},
            {"text": "continue"},
            {"text": "wrong, stop"},
        ]))


if __name__ == "__main__":
    unittest.main()
