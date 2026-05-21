#!/usr/bin/env python3
"""Tests for migrate.py — external-vault → PKOS migration.

Run: python3 pkos/skills/migrate/tests/test_migrate.py
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate.py"
_spec = importlib.util.spec_from_file_location("migrate", SCRIPT)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


class HelperTests(unittest.TestCase):
    def test_slugify_keeps_cjk_collapses_separators(self):
        self.assertEqual(mig.slugify("Linux SRE"), "linux-sre")
        self.assertEqual(mig.slugify("AI & LLM"), "ai-llm")
        self.assertEqual(mig.slugify("认知科学"), "认知科学")

    def test_clean_title_strips_markdown_noise(self):
        self.assertEqual(mig.clean_title("**# vi configuration**"), "vi configuration")
        self.assertEqual(mig.clean_title("ZPool"), "ZPool")

    def test_mojibake_detection(self):
        self.assertTrue(mig.is_mojibake("è¿™ä¸ªåº" * 30))
        self.assertFalse(mig.is_mojibake("正常的中文内容，完全没有乱码。" * 5))
        self.assertFalse(mig.is_mojibake("Plain English content here."))

    def test_measurable_content_keeps_code(self):
        # A note whose body is only a code block must NOT measure as empty.
        note = "# t\n\n```bash\ncurl -XDELETE localhost:9200/_all\n```\n"
        self.assertGreater(len(mig.measurable_content(note)), 10)

    def test_value_verdict(self):
        self.assertEqual(mig.value_verdict(""), "discard")
        self.assertEqual(mig.value_verdict("---\ntags: [x]\n---\n\n   \n"), "discard")
        self.assertEqual(mig.value_verdict("è¿™ä¸ªåº" * 40), "discard")
        # A one-line command note is kept, never discarded.
        self.assertEqual(
            mig.value_verdict("```bash\ncurl -XDELETE localhost:9200/_all\n```"), "keep")
        # Short unstructured prose → review (still migrated).
        self.assertEqual(mig.value_verdict("一句很短的话。"), "review")
        self.assertEqual(mig.value_verdict("正常长度的知识内容。" * 30), "keep")


class RoutingTests(unittest.TestCase):
    def test_wechat_target(self):
        self.assertIsNone(mig.wechat_target(("Python", "x.md")))
        s, d = mig.wechat_target(("WeChat", "Channel", "跑者学堂", "a.md"))
        self.assertEqual((s, d), ("跑者学堂", "90-Productions/WeChat/跑者学堂"))
        # Source typo 路→跑 is normalized.
        s, _ = mig.wechat_target(("WeChat", "Channel", "丹尼尔斯路步方程式", "a.md"))
        self.assertEqual(s, "丹尼尔斯跑步方程式")
        s, d = mig.wechat_target(("WeChat", "Official Account", "Drafts", "a.md"))
        self.assertEqual(d, "90-Productions/WeChat/公众号随笔")

    def test_route_nests_category(self):
        ntype, dest = mig.route("Linux SRE/cpu.md", "knowledge")
        self.assertEqual((ntype, dest), ("knowledge", "10-Knowledge/linux-sre"))

    def test_route_projects(self):
        ntype, dest = mig.route("WorkSpace/Enflame/x.md", "knowledge")
        self.assertEqual(ntype, "project")
        self.assertTrue(dest.startswith("30-Projects/"))

    def test_route_wechat_is_production(self):
        ntype, dest = mig.route("WeChat/Channel/跑者学堂/a.md", "reference")
        self.assertEqual(ntype, "production")
        self.assertEqual(dest, "90-Productions/WeChat/跑者学堂")

    def test_classify_first_match(self):
        rules = [{"pattern": "WeChat/**", "type": "reference", "source": "wechat-ai"},
                 {"pattern": "**", "type": "knowledge", "source": "external-vault"}]
        self.assertEqual(mig.classify("Python/x.md", rules)[0], "knowledge")

    def test_strip_leading_h1_dedups_title(self):
        # Body's own H1 matching the title is dropped (no double heading).
        self.assertEqual(mig.strip_leading_h1("# systemctl\n\nbody", "systemctl"), "body")
        self.assertEqual(mig.strip_leading_h1("**# vi config**\n\nx", "vi config"), "x")
        # A non-matching H1 is kept.
        self.assertTrue(mig.strip_leading_h1("# Other\n\nx", "filename").startswith("# Other"))
        # An H2 is never stripped.
        self.assertTrue(mig.strip_leading_h1("## Summary\n\nx", "Summary").startswith("## Summary"))

    def test_build_note_valid_frontmatter(self):
        note = mig.build_note("ZPool", "knowledge", "external-vault", 1,
                              ["linux-sre"], "## Summary\n\nZFS pools.")
        self.assertTrue(note.startswith("---\n"))
        self.assertIn("# ZPool", note)
        fm = yaml.safe_load(note[3:note.find("\n---", 3)])
        self.assertEqual(fm["type"], "knowledge")
        self.assertEqual(fm["migrated_from"], "99-Obsidian")
        self.assertIn("linux-sre", fm["tags"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.vault = tempfile.mkdtemp()
        self.state = os.path.join(tempfile.mkdtemp(), "migrate-state.yaml")

        def w(rel, body):
            p = Path(self.src) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")

        w("Linux SRE/ZPool.md", "## Summary\n\n" + "ZFS storage pools. " * 20)
        w("Python/cmd.md", "```bash\npython3 -m http.server\n```\n")
        w("WeChat/Channel/跑者学堂/lesson.md", "心率区间讲解。" * 30)
        w("Untitled.md", "")  # empty → discard
        w("Z-Images/pic.md", "should be skipped")  # SKIP_DIRS

    def test_full_migration(self):
        rc = mig.run(self.src, vault=self.vault, rules=None, state_path=self.state)
        self.assertEqual(rc, 0)
        files = {str(p.relative_to(self.vault)) for p in Path(self.vault).rglob("*.md")
                 if ".trash" not in str(p)}
        self.assertIn("10-Knowledge/linux-sre/zpool.md", files)
        self.assertIn("90-Productions/WeChat/跑者学堂/lesson.md", files)
        self.assertEqual(len(files), 3)  # ZPool, cmd, lesson — Untitled discarded, Z-Images skipped
        # Empty note discarded to .trash, not deleted.
        self.assertTrue(list(Path(self.vault, ".trash", "migrate-discarded").rglob("*.md")))
        # State recorded.
        st = yaml.safe_load(open(self.state, encoding="utf-8"))
        self.assertEqual(len(st["migrated"]), 3)

    def test_scan_only_writes_nothing(self):
        mig.run(self.src, vault=self.vault, rules=None, state_path=self.state,
                scan_only=True)
        self.assertEqual(list(Path(self.vault).rglob("*.md")), [])

    def test_force_relocates_prior_run(self):
        mig.run(self.src, vault=self.vault, rules=None, state_path=self.state)
        before = len(list(Path(self.vault).rglob("*.md")))
        self.assertGreater(before, 0)
        # Re-run with --force: prior output goes to .trash/migrate-prior-run/.
        mig.run(self.src, vault=self.vault, rules=None, state_path=self.state, force=True)
        prior = list(Path(self.vault, ".trash", "migrate-prior-run").rglob("*.md"))
        self.assertEqual(len(prior), 3)
        live = [p for p in Path(self.vault).rglob("*.md") if ".trash" not in str(p)]
        self.assertEqual(len(live), 3)  # re-migrated cleanly


class ValueJudgmentTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.vault = tempfile.mkdtemp()
        self.state = os.path.join(tempfile.mkdtemp(), "migrate-state.yaml")
        p = Path(self.src) / "Python" / "note.md"
        p.parent.mkdir(parents=True)
        p.write_text("# note\n\n" + "real python knowledge content. " * 20, encoding="utf-8")

    def test_run_emits_judgment_queue(self):
        mig.run(self.src, vault=self.vault, rules=None, state_path=self.state)
        queue = os.path.join(os.path.dirname(self.state), "migrate-judgment-queue.jsonl")
        self.assertTrue(os.path.exists(queue))
        import json
        rows = [json.loads(l) for l in open(queue, encoding="utf-8") if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertIn("vault_path", rows[0])
        self.assertIn("excerpt", rows[0])
        self.assertEqual(rows[0]["title"], "note")

    def test_apply_discards_moves_to_trash(self):
        mig.run(self.src, vault=self.vault, rules=None, state_path=self.state)
        st = yaml.safe_load(open(self.state, encoding="utf-8"))
        victim = st["migrated"][0]["vault_path"]
        dfile = os.path.join(self.vault, "discards.txt")
        Path(dfile).write_text(victim + "\n", encoding="utf-8")
        mig.apply_discards(self.vault, dfile, self.state)
        self.assertFalse(os.path.exists(os.path.join(self.vault, victim)))
        self.assertTrue(os.path.exists(
            os.path.join(self.vault, ".trash", "migrate-discarded", victim)))
        st2 = yaml.safe_load(open(self.state, encoding="utf-8"))
        self.assertEqual(len(st2["migrated"]), 0)

    def test_clean_prior_run_sweeps_undocumented_migrated_from(self):
        # A prior-migration file NOT recorded in migrate-state must still be swept,
        # by its `migrated_from` frontmatter (the April-migration duplicate bug).
        old = Path(self.vault) / "10-Knowledge" / "linux-sre" / "old.md"
        old.parent.mkdir(parents=True)
        old.write_text("---\nmigrated_from: 99-Obsidian/Linux SRE/old.md\n---\n\nx",
                       encoding="utf-8")
        moved = mig.clean_prior_run(self.vault, {"migrated": []})
        self.assertEqual(moved, 1)
        self.assertFalse(old.exists())
        self.assertTrue((Path(self.vault) / ".trash" / "migrate-prior-run"
                         / "10-Knowledge" / "linux-sre" / "old.md").exists())


if __name__ == "__main__":
    unittest.main()
