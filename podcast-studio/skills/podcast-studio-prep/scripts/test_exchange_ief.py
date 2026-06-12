"""Unit tests for exchange_ief (IEF reader).

The reader parses the markdown ITSELF (frontmatter split + yaml.safe_load) —
no cross-plugin imports. It scans {exchange_dir}/**/*.md recursively, skips
the podcast-studio self-producer dir, normalizes the candidate shape, and
applies the exclude-today window [today-14, today-1] (D-3).

Test fixtures replicate the REAL sample frontmatter shape (9 required
fields + nested youtube_scoring + inline tags) and inject dates relative
to a test `today` so window tests are deterministic.
"""
import os
import tempfile
import unittest
from pathlib import Path

from exchange_ief import (
    parse_ief_file,
    load_ief_candidates,
    _REQUIRED_IEF_KEYS,
    _SELF_PRODUCER_DIRS,
)


# Minimal real-shape IEF body — 9 required fields + nested youtube_scoring
# + inline tags. The shape is locked to the real domain-intel/youtube output.
def _ief_text(ief_id, date, source="youtube", significance=4, tags=None,
              extra_fm=None, body_text="Sample fact from this IEF."):
    tags = tags or ["ai", "agents", "inference"]
    fm_lines = [
        "---",
        f"id: \"{ief_id}\"",
        f"source: \"{source}\"",
        "url: \"https://example.com/abcdef\"",
        f"title: \"Sample title for {ief_id}\"",
        f"significance: {significance}",
        f"tags: [{', '.join(tags)}]",
        "category: \"framework\"",
        "domain: \"ai-ml\"",
        f"date: {date}",
        "read: false",
    ]
    if extra_fm:
        for k, v in extra_fm.items():
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(f"# {ief_id}")
    fm_lines.append("")
    fm_lines.append(body_text)
    return "\n".join(fm_lines) + "\n"


def _write_ief(directory, subdir, filename, content):
    full = Path(directory) / subdir / filename
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


class ParseIefFileTests(unittest.TestCase):
    """parse_ief_file: frontmatter split + required-field check + fail-closed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exchange = self.tmp.name

    def _write_minimal(self, ief_id="2026-06-07-youtube-001", **kw):
        return _write_ief(
            self.exchange, "domain-intel/2026-06",
            f"{ief_id}.md",
            _ief_text(ief_id, "2026-06-07", **kw),
        )

    def test_happy_path_returns_normalized_candidate(self):
        path = self._write_minimal()
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNotNone(cand)
        self.assertEqual(diag["status"], "ok")
        self.assertEqual(cand["id"], "2026-06-07-youtube-001")
        self.assertEqual(cand["title"], "Sample title for 2026-06-07-youtube-001")
        self.assertEqual(cand["tags"], ["ai", "agents", "inference"])
        self.assertEqual(cand["significance"], 4)
        self.assertEqual(cand["created"], "2026-06-07")
        self.assertEqual(cand["domain"], "ai-ml")
        self.assertEqual(cand["source"], "youtube")
        self.assertEqual(cand["category"], "framework")
        self.assertEqual(cand["url"], "https://example.com/abcdef")
        # path is RELATIVE to exchange_dir
        self.assertEqual(cand["path"],
                         "domain-intel/2026-06/2026-06-07-youtube-001.md")
        # excerpt = first non-empty body line
        self.assertIn("Sample fact", cand["excerpt"])

    def test_required_keys_constant_matches_ief_spec(self):
        # 9 required per docs/ief-format.md + the consumption flag `read`.
        self.assertEqual(
            set(_REQUIRED_IEF_KEYS),
            {"id", "source", "url", "title", "significance", "tags",
             "category", "domain", "date", "read"},
        )

    def test_nested_youtube_scoring_dict_does_not_break_parse(self):
        # Real domain-intel IEFs carry a nested `youtube_scoring:` block —
        # the parser must ignore unknown fields and still return the
        # normalized candidate. Inject the block via raw text so the
        # nested-dict shape is preserved (PyYAML auto-types it).
        path = self._write_minimal()
        text = Path(path).read_text(encoding="utf-8")
        # Insert a nested block right before the closing `---`.
        text = text.replace(
            "read: false\n---",
            "read: false\nchannel: \"Some Channel\"\n"
            "youtube_scoring:\n  relevance: 0.92\n  novelty: 0.81\n---",
        )
        Path(path).write_text(text, encoding="utf-8")
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNotNone(cand)
        self.assertEqual(diag["status"], "ok")
        self.assertEqual(cand["significance"], 4)

    def test_missing_required_field_returns_diagnostic(self):
        # Drop the `title` key.
        text = _ief_text("2026-06-07-youtube-001", "2026-06-07")
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("title:")
        )
        path = _write_ief(self.exchange, "domain-intel/2026-06",
                          "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertEqual(diag["status"], "skipped")
        self.assertIn("title", diag["reason"])

    def test_missing_domain_field_returns_diagnostic(self):
        text = _ief_text("2026-06-07-youtube-001", "2026-06-07")
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("domain:")
        )
        path = _write_ief(self.exchange, "domain-intel/2026-06",
                          "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertIn("domain", diag["reason"])

    def test_missing_date_field_returns_diagnostic(self):
        text = _ief_text("2026-06-07-youtube-001", "2026-06-07")
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("date:")
        )
        path = _write_ief(self.exchange, "domain-intel/2026-06",
                          "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertIn("date", diag["reason"])

    def test_bad_yaml_returns_diagnostic(self):
        # Malformed YAML inside the frontmatter fences.
        text = (
            "---\n"
            "id: \"2026-06-07-youtube-001\"\n"
            "title: : : bad yaml [[[ \n"
            "source: \"youtube\"\n"
            "url: \"x\"\n"
            "significance: 4\n"
            "tags: [a, b]\n"
            "category: \"x\"\n"
            "domain: \"x\"\n"
            "date: 2026-06-07\n"
            "read: false\n"
            "---\n\n"
            "body\n"
        )
        path = _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertEqual(diag["status"], "skipped")
        self.assertIn("frontmatter", diag["reason"])

    def test_no_frontmatter_returns_diagnostic(self):
        text = "Just a plain markdown note. No frontmatter here.\n"
        path = _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertEqual(diag["status"], "skipped")

    def test_empty_file_returns_diagnostic(self):
        path = _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", "")
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertEqual(diag["status"], "skipped")

    def test_non_integer_significance_returns_diagnostic(self):
        # significance as a string "3.5" or word must fail.
        text = _ief_text("2026-06-07-youtube-001", "2026-06-07")
        text = text.replace("significance: 4", "significance: \"3.5\"")
        path = _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertIn("significance", diag["reason"])

    def test_unparseable_date_returns_diagnostic(self):
        text = _ief_text("2026-06-07-youtube-001", "2026-06-07")
        text = text.replace("date: 2026-06-07", "date: \"not-a-date\"")
        path = _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", text)
        cand, diag = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertIsNone(cand)
        self.assertIn("date", diag["reason"])

    def test_excerpt_truncates_long_first_line(self):
        long_body = "x" * 500
        path = self._write_minimal(body_text=long_body)
        cand, _ = parse_ief_file(path, exchange_dir=self.exchange)
        self.assertEqual(len(cand["excerpt"]), 200)

    def test_nonexistent_file_returns_diagnostic(self):
        cand, diag = parse_ief_file(
            os.path.join(self.exchange, "nope.md"),
            exchange_dir=self.exchange,
        )
        self.assertIsNone(cand)
        self.assertEqual(diag["status"], "skipped")


class LoadIefCandidatesTests(unittest.TestCase):
    """load_ief_candidates: rglob discovery, self-producer skip, window,
    exclude_ids, sort. All test fixtures use dates relative to a fixed
    `today` so the window math is deterministic."""

    TODAY = "2026-06-12"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exchange = self.tmp.name

    def _write(self, relpath, date, ief_id=None, **kw):
        parts = relpath.split("/")
        subdir = "/".join(parts[:-1])
        fname = parts[-1]
        # Default id derives from the filename (drop .md) so the on-disk
        # filename and the in-content `id:` agree. Tests that need a
        # specific id pass it explicitly.
        if ief_id is None:
            ief_id = fname.removesuffix(".md")
        text = _ief_text(ief_id, date, **kw)
        return _write_ief(self.exchange, subdir, fname, text)

    def test_returns_empty_on_none_exchange_dir(self):
        cands, diags = load_ief_candidates(None, today=self.TODAY)
        self.assertEqual(cands, [])
        self.assertEqual(diags, [])

    def test_returns_empty_on_missing_exchange_dir(self):
        cands, diags = load_ief_candidates(
            os.path.join(self.tmp.name, "nonexistent"),
            today=self.TODAY,
        )
        self.assertEqual(cands, [])
        self.assertEqual(diags, [])

    def test_returns_empty_on_invalid_today(self):
        cands, diags = load_ief_candidates(self.exchange, today="not-a-date")
        self.assertEqual(cands, [])
        self.assertEqual(diags, [])

    def test_returns_empty_on_window_days_zero(self):
        self._write("domain-intel/2026-06/ief.md", date="2026-06-11")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=0)
        self.assertEqual(cands, [])

    def test_happy_path_in_window(self):
        # 1 in-window IEF.
        self._write("domain-intel/2026-06/ief1.md", date="2026-06-11",
                    ief_id="2026-06-11-youtube-001")
        cands, diags = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        self.assertEqual(len(cands), 1)
        self.assertEqual(diags, [])
        c = cands[0]
        self.assertEqual(c["id"], "2026-06-11-youtube-001")
        self.assertEqual(c["created"], "2026-06-11")
        self.assertEqual(c["significance"], 4)

    def test_recursive_glob_finds_signal_subdir(self):
        # signal_YYYY-MM-DD is the non-uniform subdir name observed in
        # real samples — a {YYYY-MM} monthly glob would silently skip it.
        self._write("domain-intel/signal_2026-06-06/ief.md",
                    date="2026-06-06", ief_id="2026-06-06-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["path"],
                         "domain-intel/signal_2026-06-06/ief.md")

    def test_recursive_glob_finds_both_month_and_signal_dirs(self):
        # Both subdir layouts coexist in real data — the reader must
        # find them all (it does, by rglob).
        self._write("domain-intel/2026-05/ief1.md",
                    date="2026-06-08", ief_id="2026-06-08-youtube-001")
        self._write("domain-intel/signal_2026-06-07/ief2.md",
                    date="2026-06-07", ief_id="2026-06-07-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = sorted(c["id"] for c in cands)
        self.assertEqual(ids, [
            "2026-06-07-youtube-001",
            "2026-06-08-youtube-001",
        ])

    def test_self_producer_dir_is_skipped(self):
        # podcast-studio's own exchange output must not feed back.
        self._write("podcast-prep/x.md", date="2026-06-11",
                    ief_id="2026-06-11-podcast-prep-001")
        self._write("domain-intel/2026-06/ief1.md",
                    date="2026-06-11", ief_id="2026-06-11-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = [c["id"] for c in cands]
        self.assertIn("2026-06-11-youtube-001", ids)
        self.assertNotIn("2026-06-11-podcast-prep-001", ids)

    def test_exclude_today_filter_d3_correctness_fix(self):
        # D-3 correctness fix: IEF with date==today is EXCLUDED from the
        # window. This is the key fix that makes check-A / B / C see the
        # same pool of candidates (the pipeline runs up to 3 check
        # invocations per /podcast run for parallel-N perturbation).
        self._write("domain-intel/2026-06/today.md", date="2026-06-12",
                    ief_id="2026-06-12-youtube-001")
        self._write("domain-intel/2026-06/yesterday.md", date="2026-06-11",
                    ief_id="2026-06-11-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = [c["id"] for c in cands]
        self.assertNotIn("2026-06-12-youtube-001", ids,
                         "today's IEF must be excluded from the window")
        self.assertIn("2026-06-11-youtube-001", ids,
                      "yesterday's IEF must be in the window")

    def test_window_boundary_inclusive_at_far_end(self):
        # Window is [today-14, today-1]. today-1 is INCLUSIVE.
        self._write("domain-intel/2026-05/edge.md", date="2026-05-29",
                    ief_id="2026-05-29-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = [c["id"] for c in cands]
        self.assertIn("2026-05-29-youtube-001", ids)

    def test_window_boundary_inclusive_at_near_end(self):
        # Window is [today-14, today-1]. today-14 is INCLUSIVE.
        self._write("domain-intel/2026-05/edge.md", date="2026-05-29",
                    ief_id="2026-05-29-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = [c["id"] for c in cands]
        # 2026-06-12 - 14d = 2026-05-29 → inclusive on the near end
        self.assertIn("2026-05-29-youtube-001", ids)

    def test_out_of_window_far_excluded(self):
        # Window is [today-14, today-1]. today-15 is OUT.
        self._write("domain-intel/2026-05/old.md", date="2026-05-28",
                    ief_id="2026-05-28-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        ids = [c["id"] for c in cands]
        self.assertNotIn("2026-05-28-youtube-001", ids)

    def test_exclude_ids_dedup_by_id(self):
        # D-4: dedup key is IEF `id`, not file path.
        self._write("domain-intel/2026-06/a.md", date="2026-06-11",
                    ief_id="2026-06-11-youtube-001")
        self._write("domain-intel/2026-06/b.md", date="2026-06-10",
                    ief_id="2026-06-10-youtube-001")
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14,
            exclude_ids={"2026-06-11-youtube-001"},
        )
        ids = [c["id"] for c in cands]
        self.assertNotIn("2026-06-11-youtube-001", ids)
        self.assertIn("2026-06-10-youtube-001", ids)

    def test_sort_significance_desc_then_date_desc(self):
        # significance desc + date desc — verify both keys are honored.
        # 5 items, mixed sigs and dates.
        self._write("domain-intel/2026-06/a.md", date="2026-06-10",
                    ief_id="2026-06-10-youtube-001", significance=2)
        self._write("domain-intel/2026-06/b.md", date="2026-06-11",
                    ief_id="2026-06-11-youtube-001", significance=4)
        self._write("domain-intel/2026-06/c.md", date="2026-06-08",
                    ief_id="2026-06-08-youtube-001", significance=4)
        self._write("domain-intel/2026-06/d.md", date="2026-06-09",
                    ief_id="2026-06-09-youtube-001", significance=5)
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        sigs = [c["significance"] for c in cands]
        # 5 first; then 4, 4; then 2. Within the two 4s, newer (2026-06-11) first.
        self.assertEqual(sigs, [5, 4, 4, 2])
        # Within the two sig=4 candidates, 2026-06-11 is newer than 2026-06-08.
        sig4 = [c for c in cands if c["significance"] == 4]
        self.assertEqual(sig4[0]["created"], "2026-06-11")
        self.assertEqual(sig4[1]["created"], "2026-06-08")

    def test_n_truncates_after_sort(self):
        self._write("domain-intel/2026-06/a.md", date="2026-06-11",
                    ief_id="2026-06-11-youtube-001", significance=5)
        self._write("domain-intel/2026-06/b.md", date="2026-06-10",
                    ief_id="2026-06-10-youtube-001", significance=4)
        self._write("domain-intel/2026-06/c.md", date="2026-06-09",
                    ief_id="2026-06-09-youtube-001", significance=3)
        cands, _ = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14, n=2)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["significance"], 5)
        self.assertEqual(cands[1]["significance"], 4)

    def test_malformed_ief_does_not_break_others(self):
        # fail-closed: one bad file in the directory must not abort the
        # whole load — good files still come back.
        good_path = self._write("domain-intel/2026-06/good.md",
                                date="2026-06-11",
                                ief_id="2026-06-11-youtube-001")
        # Drop a `title:` line to make it malformed.
        text = _ief_text("2026-06-10-youtube-001", "2026-06-10")
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith("title:"))
        _write_ief(self.exchange, "domain-intel/2026-06", "bad.md", text)
        cands, diags = load_ief_candidates(
            self.exchange, today=self.TODAY, window_days=14)
        self.assertEqual(len(cands), 1, "good IEF must still be returned")
        self.assertEqual(cands[0]["id"], "2026-06-11-youtube-001")
        self.assertEqual(len(diags), 1, "bad IEF must be reported")
        self.assertEqual(diags[0]["status"], "skipped")

    def test_self_producer_dirs_constant_lists_podcast_prep(self):
        self.assertIn("podcast-prep", _SELF_PRODUCER_DIRS)


if __name__ == "__main__":
    unittest.main()
