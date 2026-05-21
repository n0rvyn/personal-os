#!/usr/bin/env python3
"""Tests for reorg.py — PKOS vault retag / dedup / cleanup.

Run: python3 pkos/skills/vault-reorg/tests/test_reorg.py
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reorg.py"
_spec = importlib.util.spec_from_file_location("reorg", SCRIPT)
reorg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reorg)


class FrontmatterTests(unittest.TestCase):
    def test_split_frontmatter(self):
        fm, body, had = reorg.split_frontmatter("---\na: 1\n---\n\nbody text")
        self.assertTrue(had)
        self.assertEqual(fm, "a: 1")
        self.assertEqual(body, "body text")
        fm, body, had = reorg.split_frontmatter("no frontmatter")
        self.assertFalse(had)
        self.assertEqual(body, "no frontmatter")

    def test_parse_fm_terms_inline_and_block_and_topics(self):
        self.assertEqual(reorg.parse_fm_terms("tags: [ai, swift]"), ["ai", "swift"])
        self.assertEqual(reorg.parse_fm_terms("topics:\n  - 录音笔记\n  - 君子之道"),
                         ["录音笔记", "君子之道"])
        # tags + topics both collected
        terms = reorg.parse_fm_terms("tags: [得到]\ntopics:\n  - 哲学")
        self.assertIn("得到", terms)
        self.assertIn("哲学", terms)

    def test_write_domain_tag_appends_preserving_existing(self):
        # inline list — domain appended, originals kept
        out = reorg.write_domain_tag("---\ntags: [得到, 某书名]\n---\n\nbody", "philosophy")
        self.assertIn("得到", out)
        self.assertIn("某书名", out)
        self.assertIn("philosophy", out)
        # block list
        out = reorg.write_domain_tag("---\ntags:\n  - 得到\n---\n\nbody", "cognition")
        self.assertIn("- 得到", out)
        self.assertIn("- cognition", out)
        # no tags field
        out = reorg.write_domain_tag("---\nsource: app\n---\n\nbody", "history")
        self.assertIn("tags: [history]", out)
        self.assertIn("source: app", out)
        # no frontmatter at all
        out = reorg.write_domain_tag("just body", "tech")
        self.assertTrue(out.startswith("---\ntags: [tech]\n---"))
        self.assertIn("just body", out)


class DedupTests(unittest.TestCase):
    def test_body_hash_ignores_frontmatter_and_whitespace(self):
        long_a = "same identical body content " * 5   # >80 real chars
        a = reorg._body_hash(f"---\nx: 1\n---\n\n{long_a}")
        b = reorg._body_hash(f"---\nx: 2\n---\n\n{long_a.replace(' ', chr(10))}")
        self.assertEqual(a, b)
        c = reorg._body_hash(f"---\nx: 1\n---\n\n{'a wholly different body text ' * 5}")
        self.assertNotEqual(a, c)

    def test_body_hash_skips_short_bodies(self):
        self.assertEqual(reorg._body_hash("---\nx: 1\n---\n\ntiny body"), "")

    def test_keep_rank_prefers_clean_filename(self):
        ranked = sorted(["10-K/note-a1b2c3.md", "10-K/note.md"], key=reorg._keep_rank)
        self.assertEqual(ranked[0], "10-K/note.md")  # clean name kept


class RunTests(unittest.TestCase):
    def setUp(self):
        self.vault = tempfile.mkdtemp()

        def w(rel, text):
            p = Path(self.vault) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

        # untagged note, philosophy by content
        w("10-Knowledge/a.md", "---\nsource: app\n---\n\n# 关于存在与意义的思考\n\n哲学反思。")
        # already classifiable via topics → must be left alone
        w("10-Knowledge/b.md", "---\ntopics:\n  - 哲学\n---\n\n# b\n\nx")
        # exact-duplicate pair — body ≥80 real chars so it is eligible for dedup
        dup_body = "identical knowledge body content repeated to exceed the length floor " * 3
        w("10-Knowledge/dup1.md", f"---\ntags: [x]\n---\n\n{dup_body}")
        w("50-References/dup2.md", f"---\ntags: [y]\n---\n\n{dup_body}")
        # stale getnote inbox
        w("00-Inbox/getnote/original/old.md", "stale note")

    def test_retag_only_touches_unclassifiable(self):
        reorg.run(self.vault, only="retag")
        a = (Path(self.vault) / "10-Knowledge/a.md").read_text(encoding="utf-8")
        self.assertIn("philosophy", a)        # untagged → got a domain
        b = (Path(self.vault) / "10-Knowledge/b.md").read_text(encoding="utf-8")
        self.assertNotIn("tags:", b)          # already classifiable via topics → untouched

    def test_dedup_moves_one_copy_to_trash(self):
        reorg.run(self.vault, only="dedup")
        live = [p for p in Path(self.vault).rglob("*.md") if ".trash" not in str(p)]
        # one of the identical pair removed
        self.assertEqual(sum(1 for p in live if "identical" in p.read_text(encoding="utf-8")),
                         1)
        self.assertTrue(list((Path(self.vault) / ".trash" / "dedup-removed").rglob("*.md")))

    def test_cleanup_relocates_stale_getnote(self):
        reorg.run(self.vault, only="cleanup")
        self.assertFalse((Path(self.vault) / "00-Inbox" / "getnote").exists())
        self.assertTrue((Path(self.vault) / ".trash" / "stale-getnote-inbox").exists())

    def test_dry_run_writes_nothing(self):
        before = {p: p.read_text(encoding="utf-8") for p in Path(self.vault).rglob("*.md")}
        reorg.run(self.vault, dry_run=True)
        after = {p: p.read_text(encoding="utf-8") for p in Path(self.vault).rglob("*.md")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
