"""Unit tests for cross_domain helpers (no filesystem dependency)."""
import unittest
from cross_domain import (
    classify_note_domain,
    cross_domain_candidates,
    same_topic_past_notes,
    _parse_frontmatter,
    _tag_overlap,
    _is_unusable_title,
    _extract_title,
)


class UnusableTitleTests(unittest.TestCase):
    def test_plain_placeholder_is_unusable(self):
        self.assertTrue(_is_unusable_title("无标题"))
        self.assertTrue(_is_unusable_title("无标题笔记"))
        self.assertTrue(_is_unusable_title("未命名"))
        self.assertTrue(_is_unusable_title("untitled"))

    def test_numbered_placeholder_is_unusable(self):
        # getnote captures append a numeric suffix
        self.assertTrue(_is_unusable_title("无标题笔记-1054"))
        self.assertTrue(_is_unusable_title("untitled-3"))

    def test_real_title_is_usable(self):
        self.assertFalse(_is_unusable_title("AI落地的新瓶颈：上下文工程"))
        self.assertFalse(_is_unusable_title("熵蚀与系统衰退"))

    def test_extract_title_falls_back_to_body_when_placeholder(self):
        text = "---\ntitle: 无标题\n---\n\n这是正文第一行讲了一个真实的观点。\n"
        self.assertEqual(_extract_title(text, "无标题笔记-12.md"),
                         "这是正文第一行讲了一个真实的观点。")


class ClassifyDomainTests(unittest.TestCase):
    def test_philosophy_tag_returns_philosophy(self):
        self.assertEqual(classify_note_domain(["哲学", "君子"]), "philosophy")

    def test_cognition_tag_returns_cognition(self):
        self.assertEqual(classify_note_domain(["卡尼曼", "思维"]), "cognition")

    def test_management_tag_returns_management(self):
        self.assertEqual(classify_note_domain(["管理", "极简管理"]), "management")

    def test_tech_only_tags_returns_tech(self):
        self.assertEqual(classify_note_domain(["swift6", "ai-agents"]), "tech")

    def test_empty_tags_returns_general(self):
        self.assertEqual(classify_note_domain([]), "general")

    def test_mixed_philosophy_and_tech_returns_philosophy(self):
        # Non-tech domain wins over tech in priority order
        self.assertEqual(classify_note_domain(["ai", "哲学"]), "philosophy")

    def test_unrecognized_tags_returns_general(self):
        self.assertEqual(classify_note_domain(["random", "miscellaneous"]), "general")


class ContentFallbackTests(unittest.TestCase):
    """The title+excerpt+parent-dir fallback when tags do not classify a note."""

    def test_tags_still_win_when_they_classify(self):
        # A classifying tag short-circuits — content is not consulted.
        self.assertEqual(
            classify_note_domain(["哲学"], title="a tech note about python"),
            "philosophy")

    def test_title_keyword_fallback(self):
        # tags = generic getnote tags that don't classify → title scan finds it
        self.assertEqual(
            classify_note_domain(["得到", "某书名"], title="关于存在与意义的哲学思考"),
            "philosophy")

    def test_excerpt_keyword_fallback(self):
        self.assertEqual(
            classify_note_domain([], title="无标题", excerpt="刻意练习与元认知的关系"),
            "cognition")

    def test_parent_dir_fallback(self):
        # No domain keyword anywhere → parent directory name resolves it
        self.assertEqual(
            classify_note_domain([], title="systemctl", parent_dir="linux-sre"),
            "tech")
        self.assertEqual(
            classify_note_domain([], title="伊凡的独白", parent_dir="卡拉马佐夫兄弟"),
            "literature")

    def test_no_signal_returns_general(self):
        self.assertEqual(
            classify_note_domain([], title="周三的会议记录", parent_dir="misc"),
            "general")

    def test_backward_compatible_tags_only_call(self):
        # The original single-arg signature is unchanged.
        self.assertEqual(classify_note_domain(["ai"]), "tech")
        self.assertEqual(classify_note_domain([]), "general")


class FrontmatterTests(unittest.TestCase):
    def test_block_list_tags(self):
        text = """---
type: knowledge
tags:
  - 哲学
  - 思想
created: 2026-05-01
---
body"""
        fm = _parse_frontmatter(text)
        self.assertEqual(fm["tags"], ["哲学", "思想"])
        self.assertEqual(fm["created"], "2026-05-01")

    def test_inline_list_tags(self):
        text = """---
tags: [认知, 卡尼曼, 思维]
created: 2026-04-15
---"""
        fm = _parse_frontmatter(text)
        self.assertEqual(fm["tags"], ["认知", "卡尼曼", "思维"])

    def test_no_frontmatter_returns_empty(self):
        self.assertEqual(_parse_frontmatter("just body text"), {})


class TagOverlapTests(unittest.TestCase):
    def test_overlap_counts_intersection(self):
        self.assertEqual(_tag_overlap(["ai", "swift"], ["swift", "ml"]), 1)

    def test_case_insensitive(self):
        self.assertEqual(_tag_overlap(["AI", "Swift"], ["ai", "rust"]), 1)


class CrossDomainCandidatesTests(unittest.TestCase):
    def _make_notes(self):
        # cross_domain reads 10-Knowledge/ + 20-Ideas/ per the contract (KL-4).
        return [
            {"path": "10-Knowledge/p1.md", "title": "卡拉马佐夫", "tags": ["哲学", "陀思妥耶夫斯基"],
             "created": "2026-05-15", "domain": "philosophy", "excerpt": "..."},
            {"path": "10-Knowledge/p2.md", "title": "刻意练习", "tags": ["认知", "刻意练习", "ai"],
             "created": "2026-05-10", "domain": "cognition", "excerpt": "..."},
            {"path": "10-Knowledge/p3.md", "title": "极简管理学", "tags": ["管理", "极简管理"],
             "created": "2026-04-20", "domain": "management", "excerpt": "..."},
            {"path": "10-Knowledge/p4.md", "title": "Swift 6", "tags": ["swift6", "swiftui"],
             "created": "2026-05-18", "domain": "tech", "excerpt": "..."},
            {"path": "10-Knowledge/p5.md", "title": "君子不器", "tags": ["君子", "哲学"],
             "created": "2026-05-12", "domain": "philosophy", "excerpt": "..."},
        ]

    def test_returns_one_per_domain_in_priority_order(self):
        notes = self._make_notes()
        picked = cross_domain_candidates(["ai", "swift"], vault_root=None, n=5, notes=notes)
        # Expected order: philosophy (newest = 卡拉马佐夫 5/15) → management (极简管理 4/20)
        #                 → cognition (刻意练习 5/10) overlap with "ai" tag
        # Note: cognition's "刻意练习" has overlap with "ai" tag → with_overlap wins
        domains = [nt["domain"] for nt in picked]
        self.assertEqual(domains[0], "philosophy")  # first by priority
        self.assertIn("cognition", domains)
        self.assertIn("management", domains)
        self.assertNotIn("tech", domains)  # tech excluded

    def test_n_limit_respected(self):
        notes = self._make_notes()
        picked = cross_domain_candidates(["ai"], vault_root=None, n=2, notes=notes)
        self.assertEqual(len(picked), 2)

    def test_empty_notes_returns_empty(self):
        self.assertEqual(cross_domain_candidates(["ai"], vault_root=None, notes=[]), [])

    def test_overlap_preferred_over_recency(self):
        notes = [
            {"path": "10-Knowledge/older-overlap.md", "title": "Older w/ overlap",
             "tags": ["哲学", "ai"], "created": "2026-04-01",
             "domain": "philosophy", "excerpt": ""},
            {"path": "10-Knowledge/newer-no-overlap.md", "title": "Newer no overlap",
             "tags": ["哲学", "君子"], "created": "2026-05-15",
             "domain": "philosophy", "excerpt": ""},
        ]
        picked = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes)
        # With overlap is preferred even though older
        self.assertEqual(picked[0]["path"], "10-Knowledge/older-overlap.md")

    def test_rotation_varies_pick_across_seeds(self):
        # A domain with multiple recent notes — different seeds should be able to
        # surface different notes (cooldown against mechanical repeat).
        notes = [
            {"path": f"10-Knowledge/p{i}.md", "title": f"note {i}", "tags": ["哲学", "ai"],
             "created": f"2026-05-{10+i:02d}", "domain": "philosophy", "excerpt": ""}
            for i in range(8)
        ]
        picks = {
            cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes,
                                    seed=s)[0]["path"]
            for s in range(20)
        }
        # Across 20 seeds, more than one distinct note should appear
        self.assertGreater(len(picks), 1)

    def test_rotation_reproducible_with_same_seed(self):
        notes = [
            {"path": f"10-Knowledge/p{i}.md", "title": f"note {i}", "tags": ["哲学", "ai"],
             "created": f"2026-05-{10+i:02d}", "domain": "philosophy", "excerpt": ""}
            for i in range(8)
        ]
        a = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes, seed=7)
        b = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes, seed=7)
        self.assertEqual(a[0]["path"], b[0]["path"])

    def test_force_domain_returns_only_that_bucket(self):
        # parallel-N perturbation: force_domain pins recall to ONE bucket.
        notes = self._make_notes()
        picked = cross_domain_candidates(
            ["ai", "swift"], vault_root=None, n=5, notes=notes, force_domain="philosophy")
        self.assertGreater(len(picked), 0)
        self.assertTrue(all(nt["domain"] == "philosophy" for nt in picked))
        # _make_notes has 2 philosophy notes → forced bucket returns up to n of them
        self.assertEqual(len(picked), 2)

    def test_force_domain_unknown_raises(self):
        with self.assertRaises(ValueError):
            cross_domain_candidates(["ai"], vault_root=None, notes=[], force_domain="tech")

    def test_force_domain_respects_n_limit(self):
        notes = [
            {"path": f"10-Knowledge/p{i}.md", "title": f"note {i}", "tags": ["哲学", "ai"],
             "created": f"2026-05-{10+i:02d}", "domain": "philosophy", "excerpt": ""}
            for i in range(8)
        ]
        picked = cross_domain_candidates(
            ["ai"], vault_root=None, n=3, notes=notes, force_domain="philosophy")
        self.assertEqual(len(picked), 3)


class SameTopicPastNotesTests(unittest.TestCase):
    # KL-4: self_past reads only 20-Ideas/观点心得/ + 90-Productions/Podcasts/ per the contract.
    IDEAS = "20-Ideas/观点心得"
    POD = "90-Productions/Podcasts"

    def test_returns_notes_in_window_with_overlap(self):
        # today=2026-05-21 → window 5/22-30d=4/21 to 5/22-7d=5/14
        notes = [
            {"path": f"{self.IDEAS}/in.md", "title": "in window", "tags": ["ai", "swift"],
             "created": "2026-04-25", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/too-recent.md", "title": "too recent", "tags": ["ai"],
             "created": "2026-05-19", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/too-old.md", "title": "too old", "tags": ["ai"],
             "created": "2026-03-01", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/no-overlap.md", "title": "no overlap", "tags": ["history"],
             "created": "2026-04-25", "domain": "history", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes, days_max=30,
        )
        paths = [nt["path"] for nt in picked]
        self.assertIn(f"{self.IDEAS}/in.md", paths)
        self.assertNotIn(f"{self.IDEAS}/too-recent.md", paths)
        self.assertNotIn(f"{self.IDEAS}/too-old.md", paths)
        self.assertNotIn(f"{self.IDEAS}/no-overlap.md", paths)

    def test_default_window_is_90_days(self):
        # A note 60 days back is inside the default 90d window but outside a 30d window.
        notes = [
            {"path": f"{self.IDEAS}/60d.md", "title": "60 days back", "tags": ["ai"],
             "created": "2026-03-22", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual([nt["path"] for nt in picked], [f"{self.IDEAS}/60d.md"])

    def test_reads_only_self_past_dirs(self):
        # KL-4: only 20-Ideas/观点心得 and 90-Productions/Podcasts feed self_past; a 10-Knowledge
        # excerpt or a 20-Ideas/产品想法 note is filtered out even with tag overlap.
        notes = [
            {"path": "10-Knowledge/excerpt.md", "title": "excerpt", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": "20-Ideas/产品想法/a-product.md", "title": "product", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/a-viewpoint.md", "title": "viewpoint", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": f"{self.POD}/2026-05-09-past-episode.md", "title": "past ep",
             "tags": ["ai"], "created": "2026-05-09", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        paths = {nt["path"] for nt in picked}
        self.assertIn(f"{self.IDEAS}/a-viewpoint.md", paths)
        self.assertIn(f"{self.POD}/2026-05-09-past-episode.md", paths)
        self.assertNotIn("10-Knowledge/excerpt.md", paths)
        self.assertNotIn("20-Ideas/产品想法/a-product.md", paths)

    def test_dedup_strips_filler_words(self):
        # "X的研究发现" and "X研究发现" differ only by 的 — should collapse.
        notes = [
            {"path": f"{self.IDEAS}/a.md", "title": "AI模型的研究发现", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/b.md", "title": "AI模型研究发现", "tags": ["ai"],
             "created": "2026-05-09", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual(len(picked), 1)

    def test_sorted_by_overlap_then_created(self):
        notes = [
            {"path": f"{self.IDEAS}/low-overlap.md", "title": "1 overlap", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/high-overlap.md", "title": "2 overlap",
             "tags": ["ai", "swift"], "created": "2026-04-25", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai", "swift"], vault_root=None, today="2026-05-21", notes=notes,
        )
        # 2-overlap beats 1-overlap regardless of recency
        self.assertEqual(picked[0]["path"], f"{self.IDEAS}/high-overlap.md")

    def test_dedups_near_identical_titles(self):
        # Vault holds re-synced near-dupes; same whitespace-normalized title collapses.
        notes = [
            {"path": f"{self.IDEAS}/a.md", "title": "AI 研究 发现", "tags": ["ai"],
             "created": "2026-05-05", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/b.md", "title": "AI研究发现", "tags": ["ai"],
             "created": "2026-05-04", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual(len(picked), 1)


if __name__ == "__main__":
    unittest.main()
