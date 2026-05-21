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
        return [
            {"path": "p1.md", "title": "卡拉马佐夫", "tags": ["哲学", "陀思妥耶夫斯基"],
             "created": "2026-05-15", "domain": "philosophy", "excerpt": "..."},
            {"path": "p2.md", "title": "刻意练习", "tags": ["认知", "刻意练习", "ai"],
             "created": "2026-05-10", "domain": "cognition", "excerpt": "..."},
            {"path": "p3.md", "title": "极简管理学", "tags": ["管理", "极简管理"],
             "created": "2026-04-20", "domain": "management", "excerpt": "..."},
            {"path": "p4.md", "title": "Swift 6", "tags": ["swift6", "swiftui"],
             "created": "2026-05-18", "domain": "tech", "excerpt": "..."},
            {"path": "p5.md", "title": "君子不器", "tags": ["君子", "哲学"],
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
            {"path": "older-overlap.md", "title": "Older w/ overlap",
             "tags": ["哲学", "ai"], "created": "2026-04-01",
             "domain": "philosophy", "excerpt": ""},
            {"path": "newer-no-overlap.md", "title": "Newer no overlap",
             "tags": ["哲学", "君子"], "created": "2026-05-15",
             "domain": "philosophy", "excerpt": ""},
        ]
        picked = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes)
        # With overlap is preferred even though older
        self.assertEqual(picked[0]["path"], "older-overlap.md")

    def test_rotation_varies_pick_across_seeds(self):
        # A domain with multiple recent notes — different seeds should be able to
        # surface different notes (cooldown against mechanical repeat).
        notes = [
            {"path": f"p{i}.md", "title": f"note {i}", "tags": ["哲学", "ai"],
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
            {"path": f"p{i}.md", "title": f"note {i}", "tags": ["哲学", "ai"],
             "created": f"2026-05-{10+i:02d}", "domain": "philosophy", "excerpt": ""}
            for i in range(8)
        ]
        a = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes, seed=7)
        b = cross_domain_candidates(["ai"], vault_root=None, n=1, notes=notes, seed=7)
        self.assertEqual(a[0]["path"], b[0]["path"])


class SameTopicPastNotesTests(unittest.TestCase):
    def test_returns_notes_in_window_with_overlap(self):
        # today=2026-05-21 → window 5/22-30d=4/21 to 5/22-7d=5/14
        notes = [
            {"path": "in.md", "title": "in window", "tags": ["ai", "swift"],
             "created": "2026-04-25", "domain": "tech", "excerpt": ""},
            {"path": "too-recent.md", "title": "too recent", "tags": ["ai"],
             "created": "2026-05-19", "domain": "tech", "excerpt": ""},
            {"path": "too-old.md", "title": "too old", "tags": ["ai"],
             "created": "2026-03-01", "domain": "tech", "excerpt": ""},
            {"path": "no-overlap.md", "title": "no overlap", "tags": ["history"],
             "created": "2026-04-25", "domain": "history", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes, days_max=30,
        )
        paths = [nt["path"] for nt in picked]
        self.assertIn("in.md", paths)
        self.assertNotIn("too-recent.md", paths)
        self.assertNotIn("too-old.md", paths)  # 2026-03-01 is outside the 30d window here
        self.assertNotIn("no-overlap.md", paths)

    def test_default_window_is_90_days(self):
        # A note 60 days back is inside the default 90d window but outside a 30d window.
        notes = [
            {"path": "60d.md", "title": "60 days back", "tags": ["ai"],
             "created": "2026-03-22", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual([nt["path"] for nt in picked], ["60d.md"])

    def test_prefers_ideas_dir_over_knowledge(self):
        # Same overlap + recency; 20-Ideas note (a stance) outranks 10-Knowledge excerpt.
        notes = [
            {"path": "10-Knowledge/excerpt.md", "title": "excerpt", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": "20-Ideas/my-opinion.md", "title": "my opinion", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual(picked[0]["path"], "20-Ideas/my-opinion.md")

    def test_dedup_strips_filler_words(self):
        # "X的研究发现" and "X研究发现" differ only by 的 — should collapse.
        notes = [
            {"path": "20-Ideas/a.md", "title": "AI模型的研究发现", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": "10-Knowledge/b.md", "title": "AI模型研究发现", "tags": ["ai"],
             "created": "2026-05-09", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual(len(picked), 1)

    def test_sorted_by_overlap_then_created(self):
        notes = [
            {"path": "low-overlap.md", "title": "1 overlap", "tags": ["ai"],
             "created": "2026-05-10", "domain": "tech", "excerpt": ""},
            {"path": "high-overlap.md", "title": "2 overlap", "tags": ["ai", "swift"],
             "created": "2026-04-25", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai", "swift"], vault_root=None, today="2026-05-21", notes=notes,
        )
        # 2-overlap beats 1-overlap regardless of recency
        self.assertEqual(picked[0]["path"], "high-overlap.md")

    def test_dedups_near_identical_titles(self):
        # Vault holds re-synced near-dupes; same whitespace-normalized title collapses.
        notes = [
            {"path": "a.md", "title": "AI 研究 发现", "tags": ["ai"],
             "created": "2026-05-05", "domain": "tech", "excerpt": ""},
            {"path": "b.md", "title": "AI研究发现", "tags": ["ai"],
             "created": "2026-05-04", "domain": "tech", "excerpt": ""},
        ]
        picked = same_topic_past_notes(
            ["ai"], vault_root=None, today="2026-05-21", notes=notes,
        )
        self.assertEqual(len(picked), 1)


if __name__ == "__main__":
    unittest.main()
