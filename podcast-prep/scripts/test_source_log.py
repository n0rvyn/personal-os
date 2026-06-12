"""Unit tests for source_log helper (cross-period note dedup state).

The source_log is a jsonl file (one line per episode) co-located with the
topic-log, carrying the list of note paths that were OFFERED to the writer
in past episodes. The `check` CLI handler reads the last `window_days` days
of this file and feeds the union into `cross_domain_candidates(exclude_ids=...)`
to prevent the same PKOS note from being offered on consecutive episodes.

Tests cover:
- append_offered writes one line per call, mkdir -p the parent dir
- recent_source_ids collects note_ids from lines in [today-window, today]
- out-of-window dates are excluded; window_days=0 yields empty
- missing file → empty set (no exception)
- corrupt lines are skipped (tolerant jsonl: bad line never aborts the read)
- idempotent: same path appended twice is harmless on read (set dedup)
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from source_log import append_offered, recent_source_ids


class AppendOfferedTests(unittest.TestCase):
    def test_append_writes_one_line_per_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "source_log.jsonl")
            # Sub-directory should be created on demand (parent mkdir -p)
            append_offered(path, "2026-06-07", ["a.md", "b.md"])
            append_offered(path, "2026-06-08", ["c.md"])
            text = Path(path).read_text(encoding="utf-8")
            lines = [l for l in text.splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0]),
                             {"date": "2026-06-07", "note_ids": ["a.md", "b.md"]})
            self.assertEqual(json.loads(lines[1]),
                             {"date": "2026-06-08", "note_ids": ["c.md"]})

    def test_append_empty_note_ids(self):
        # An episode with no cross-domain candidates (e.g. force_domain=None
        # + tiny vault) still writes a line — it carries the date marker so
        # the dedup window boundary is honored on read.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            append_offered(path, "2026-06-07", [])
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertEqual(ids, set())


class RecentSourceIdsTests(unittest.TestCase):
    def test_in_window_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            append_offered(path, "2026-06-01", ["a.md"])
            append_offered(path, "2026-06-07", ["b.md", "c.md"])
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertEqual(ids, {"a.md", "b.md", "c.md"})

    def test_out_of_window_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            append_offered(path, "2026-05-01", ["a.md"])
            append_offered(path, "2026-06-07", ["b.md"])
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            # 2026-05-01 is 37 days before 2026-06-07 → outside 14d window
            self.assertNotIn("a.md", ids)
            self.assertIn("b.md", ids)

    def test_window_days_zero_yields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            append_offered(path, "2026-06-07", ["a.md"])
            ids = recent_source_ids(path, today="2026-06-07", window_days=0)
            self.assertEqual(ids, set())

    def test_missing_file_returns_empty_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nonexistent.jsonl")
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertEqual(ids, set())

    def test_corrupt_line_skipped(self):
        # Tolerant jsonl: skip lines that fail to parse. A bad line in the
        # middle of the file must not abort the
        # whole read.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            Path(path).write_text(
                '{"date": "2026-06-05", "note_ids": ["a.md"]}\n'
                'not-valid-json\n'
                '{"date": "2026-06-07", "note_ids": ["b.md"]}\n',
                encoding="utf-8",
            )
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertIn("a.md", ids)
            self.assertIn("b.md", ids)

    def test_duplicate_path_appears_once(self):
        # Same path appended on 3 different days — set dedup, no count.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            for d in ("2026-06-01", "2026-06-04", "2026-06-07"):
                append_offered(path, d, ["a.md"])
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertEqual(ids, {"a.md"})

    def test_today_line_is_inclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_log.jsonl")
            append_offered(path, "2026-06-07", ["a.md"])
            # today == line date → included
            ids = recent_source_ids(path, today="2026-06-07", window_days=14)
            self.assertIn("a.md", ids)


if __name__ == "__main__":
    unittest.main()
