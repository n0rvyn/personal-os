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

    # -----------------------------------------------------------------
    # Task 2-tests: cross_domain_candidates signature gains
    # `exclude_ids` (path-string iterable) and applies:
    #   1) near-duplicate title collapse via _title_signature within each
    #      domain pool (keep newest by `created`),
    #   2) exclude_paths filter against the post-collapse pool,
    #   3) small-bucket backfill: in force_domain mode, when the filtered
    #      pool is empty, fall back to the unfiltered pool so the brief is
    #      never empty.
    # Default behavior (no exclude_ids, no near-dup titles) is preserved
    # verbatim — see `test_cross_domain_backward_compat` below.
    # -----------------------------------------------------------------

    def test_cross_domain_title_collapse(self):
        # 3 philosophy notes with the SAME title signature (模拟 编程能力 x 3)
        # + 1 philosophy note with a distinct title. force_domain pins the
        # pool to philosophy so we can count the result precisely. Both
        # title groups carry the "ai" tag so with_overlap is non-empty for
        # both (avoids the pool falling back to the bare domain set and
        # complicating the count).
        notes = [
            {"path": "10-Knowledge/p1.md", "title": "编程能力全球前20",
             "tags": ["ai"], "created": "2026-05-15", "domain": "philosophy",
             "excerpt": ""},
            {"path": "10-Knowledge/p2.md", "title": "编程能力全球前20",
             "tags": ["ai"], "created": "2026-05-16", "domain": "philosophy",
             "excerpt": ""},
            {"path": "10-Knowledge/p3.md", "title": "编程能力全球前20",
             "tags": ["ai"], "created": "2026-05-17", "domain": "philosophy",
             "excerpt": ""},
            {"path": "10-Knowledge/p4.md", "title": "另一篇哲学笔记",
             "tags": ["ai", "哲学"], "created": "2026-05-18",
             "domain": "philosophy", "excerpt": ""},
        ]
        picked = cross_domain_candidates(
            ["ai"], vault_root=None, n=5, notes=notes,
            seed=42, force_domain="philosophy",
        )
        # Collapse rule: same _title_signature → keep newest → 1 survivor.
        # Expect: 编程能力全球前20 (newest = p3, 5/17) + 另一篇哲学笔记 (p4)
        paths = [nt["path"] for nt in picked]
        self.assertEqual(len(picked), 2, f"expected 2 (collapse kept one of 编程能力), got {len(picked)}: {paths}")
        self.assertIn("10-Knowledge/p3.md", paths)
        self.assertNotIn("10-Knowledge/p1.md", paths)
        self.assertNotIn("10-Knowledge/p2.md", paths)

    def test_cross_domain_exclude_ids(self):
        # 5 distinct philosophy notes; exclude 2 of them.
        notes = [
            {"path": f"10-Knowledge/p{i}.md", "title": f"distinct-{i}",
             "tags": ["哲学", "ai"], "created": f"2026-05-{10+i:02d}",
             "domain": "philosophy", "excerpt": ""}
            for i in range(5)
        ]
        exclude = {"10-Knowledge/p1.md", "10-Knowledge/p3.md"}
        picked = cross_domain_candidates(
            ["ai"], vault_root=None, n=5, notes=notes,
            seed=0, force_domain="philosophy", exclude_ids=exclude,
        )
        paths = {nt["path"] for nt in picked}
        self.assertNotIn("10-Knowledge/p1.md", paths)
        self.assertNotIn("10-Knowledge/p3.md", paths)
        # The other 3 should still be selectable (n=5, recent_pool=8 → head=5)
        self.assertTrue(paths.issubset({
            "10-Knowledge/p0.md", "10-Knowledge/p2.md", "10-Knowledge/p4.md",
        }))

    def test_cross_domain_small_bucket_backfill(self):
        # 2 philosophy notes; exclude BOTH → post-filter pool is empty.
        # In force_domain mode, the backfill guard must still return ≥1
        # so the brief is never empty.
        notes = [
            {"path": "10-Knowledge/p0.md", "title": "distinct-0",
             "tags": ["哲学", "ai"], "created": "2026-05-10",
             "domain": "philosophy", "excerpt": ""},
            {"path": "10-Knowledge/p1.md", "title": "distinct-1",
             "tags": ["哲学", "ai"], "created": "2026-05-11",
             "domain": "philosophy", "excerpt": ""},
        ]
        picked = cross_domain_candidates(
            ["ai"], vault_root=None, n=5, notes=notes,
            seed=0, force_domain="philosophy",
            exclude_ids={"10-Knowledge/p0.md", "10-Knowledge/p1.md"},
        )
        # Backfill: drop the exclude filter, return at least 1 (the brief
        # must never be empty when force_domain is set).
        self.assertGreaterEqual(len(picked), 1,
            "force_domain backfill must return ≥1 note even when all are excluded")

    def test_cross_domain_backward_compat(self):
        # Regression shield: no exclude_ids, no near-dup titles, fixed seed
        # → result is identical to the pre-fix behavior (single pick per
        # domain from the with-overlap head).
        notes = [
            {"path": "10-Knowledge/phil.md", "title": "哲学笔记A",
             "tags": ["哲学", "ai"], "created": "2026-05-15",
             "domain": "philosophy", "excerpt": ""},
            {"path": "10-Knowledge/mgmt.md", "title": "管理笔记A",
             "tags": ["管理"], "created": "2026-05-10",
             "domain": "management", "excerpt": ""},
            {"path": "10-Knowledge/cog.md", "title": "认知笔记A",
             "tags": ["认知", "ai"], "created": "2026-05-12",
             "domain": "cognition", "excerpt": ""},
        ]
        picked = cross_domain_candidates(
            ["ai"], vault_root=None, n=5, notes=notes, seed=7,
        )
        # Default: one per domain in priority order — philosophy, management, cognition.
        domains = [nt["domain"] for nt in picked]
        self.assertEqual(domains, ["philosophy", "management", "cognition"])
        # tech should still be excluded
        self.assertNotIn("tech", domains)


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


# ---------------------------------------------------------------------------
# Task 7-tests: vault open-questions ranking helper (used by evening spine
# inversion — same_topic_past_notes and cross_domain_candidates elevate to the
# brief's PRIMARY `open_questions` field, ranked by openness/relevance).
# ---------------------------------------------------------------------------


class OpenQuestionsRankingTests(unittest.TestCase):
    """Phase 2 plan Task 7: evening branch pulls vault notes (same_topic_past_notes
    + cross_domain_candidates) and ranks them as the brief's open-questions spine.
    The ranking helper should prefer (a) notes that look like open questions vs.
    closed stances, and (b) notes that share a tag with today's topic_tags.

    Helper under test: `rank_open_questions(notes, today_tags, n)` — sorts by
    openness_score (presence of question/疑惑/开放/未定 markers in title+excerpt)
    DESC, then by tag overlap with today_tags DESC, then by created DESC.
    Returns the top-n.
    """

    IDEAS = "20-Ideas/观点心得"

    def setUp(self):
        from cross_domain import rank_open_questions  # noqa: F401 — will fail pre-impl

    def test_open_question_marker_ranks_above_closed_stance(self):
        from cross_domain import rank_open_questions
        notes = [
            {"path": f"{self.IDEAS}/closed.md", "title": "我的判断：AI 不会改变组织结构",
             "tags": ["ai", "组织"], "created": "2026-05-15", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/open.md", "title": "AI 改变了什么？开放问题",
             "tags": ["ai"], "created": "2026-05-10", "domain": "tech",
             "excerpt": "我还是没想清楚——这件事到底改变了什么？"},
        ]
        ranked = rank_open_questions(notes, today_tags=["ai"], n=5)
        # The open question ranks first despite being older.
        self.assertEqual(ranked[0]["path"], f"{self.IDEAS}/open.md")

    def test_tag_overlap_breaks_openness_tie(self):
        from cross_domain import rank_open_questions
        notes = [
            {"path": f"{self.IDEAS}/a.md", "title": "开放问题A",
             "tags": ["ai"], "created": "2026-05-15", "domain": "tech", "excerpt": ""},
            {"path": f"{self.IDEAS}/b.md", "title": "开放问题B",
             "tags": ["ai", "swift"], "created": "2026-05-15", "domain": "tech", "excerpt": ""},
        ]
        ranked = rank_open_questions(notes, today_tags=["ai", "swift"], n=5)
        # Same openness score, but B has more overlap → first.
        self.assertEqual(ranked[0]["path"], f"{self.IDEAS}/b.md")

    def test_n_limits_returned_count(self):
        from cross_domain import rank_open_questions
        notes = [
            {"path": f"{self.IDEAS}/q{i}.md", "title": f"开放问题{i}？",
             "tags": ["ai"], "created": f"2026-05-{10+i:02d}",
             "domain": "tech", "excerpt": ""}
            for i in range(8)
        ]
        ranked = rank_open_questions(notes, today_tags=["ai"], n=3)
        self.assertEqual(len(ranked), 3)


class FreshTodayNotesTests(unittest.TestCase):
    """Task 5: surface notes that newly entered the vault TODAY.
    D-025 single-funnel: pure SELECTION over already-loaded vault notes — no new source.
    Helper under test: `fresh_today_notes(notes, today, n)` — filters to created==today."""

    def _note(self, path, created, tags=None, title="t"):
        return {
            "path": path, "title": title, "tags": tags or [],
            "created": created, "domain": "general", "excerpt": "e",
        }

    def test_returns_only_today_notes(self):
        from cross_domain import fresh_today_notes
        notes = [
            self._note("a.md", "2026-06-05", title="today-1"),
            self._note("b.md", "2026-06-04", title="yesterday"),
            self._note("c.md", "2026-05-20", title="old"),
        ]
        out = fresh_today_notes(notes, today="2026-06-05", n=5)
        titles = [n["title"] for n in out]
        self.assertEqual(titles, ["today-1"])
        self.assertNotIn("yesterday", titles)
        self.assertNotIn("old", titles)

    def test_matches_on_date_prefix_with_time_component(self):
        from cross_domain import fresh_today_notes
        notes = [self._note("a.md", "2026-06-05T09:00", title="today-ts")]
        out = fresh_today_notes(notes, today="2026-06-05", n=5)
        self.assertEqual([n["title"] for n in out], ["today-ts"])

    def test_n_limit_respected(self):
        from cross_domain import fresh_today_notes
        notes = [self._note(f"{i}.md", "2026-06-05", title=f"t{i}") for i in range(8)]
        out = fresh_today_notes(notes, today="2026-06-05", n=3)
        self.assertEqual(len(out), 3)

    def test_empty_when_nothing_today(self):
        from cross_domain import fresh_today_notes
        notes = [self._note("a.md", "2026-06-04", title="yesterday")]
        self.assertEqual(fresh_today_notes(notes, today="2026-06-05", n=5), [])


# ---------------------------------------------------------------------------
# Task 1-tests (2026-06-07 source-recurrence fix — 治本 a): domain classifier
# de-pollution. The history bucket has been polluted by:
#   (1) getnote/dedao captures that carry a bare `history` English tag (added
#       as junk by the capturer) on non-history content
#   (2) AI-related 编程稿 that uses "历史性突破" as rhetorical emphasis
#   (3) 方希 解读韩炳哲《倦怠社会》 — actually a philosophy note whose
#       title contains "历史感" + a stray `history` tag
#
# These notes were falling into the `history` bucket and being offered to
# path-C of the morning brief, which is why the same note kept recurring.
# The fix is a regex guard on the CJK keyword `历史` so it does NOT match
# `历史性` (the rhetorical emphasis), and removal of the bare English
# `history` keyword from DOMAIN_KEYWORDS["history"] (it is a getnote junk
# tag with no semantic content). Philosophy bucket gains 思想家/作品词
# (韩炳哲, 倦怠, 倦怠社会, 福柯, 本雅明, 社会批判) so 方希 notes land
# in philosophy rather than drifting through to general.
# ---------------------------------------------------------------------------


class DomainHistoryPollutionTests(unittest.TestCase):
    """Task 1: 域分类器去污染 — 被错分进 history 的三类笔记现在应各归各位。"""

    def test_db2_operations_guide_is_tech_not_history(self):
        # Real vault sample: a DB2 admin note tagged with the getnote junk
        # `history` English tag. With bare `history` removed from
        # DOMAIN_KEYWORDS["history"], the parent_dir name "linux-sre" / tech
        # signal wins.
        self.assertEqual(
            classify_note_domain(
                ['uncatagory', 'history'],
                title='DB2 Operatation Guide',
                excerpt='db2 connect to DATABASE',
            ),
            'tech',
        )

    def test_ai_historic_breakthrough_essay_is_tech_not_history(self):
        # Real vault sample: 编程稿 标题用 "历史性突破" 作修辞强调,
        # 含 junk `history` getnote tag. With the regex guard `历史(?!性)`,
        # the CJK keyword `历史` no longer matches `历史性`; bare `history`
        # is also gone, so the note falls to general and gets rescued by
        # the tech content-signal "AI"/"编程".
        self.assertEqual(
            classify_note_domain(
                ['得到', 'history'],
                title='编程能力进入全球前20AI又迎来历史性突破了',
                excerpt='编程能力进入全球前20，AI又迎来历史性突破了？',
            ),
            'tech',
        )

    def test_fangxi_hanbingzhe_book_note_is_philosophy_not_history(self):
        # Real vault sample: 方希 解读韩炳哲《倦怠社会》. Tags carry
        # `得到` + `《倦怠社会》| 方希解读` + junk `history`. With the
        # regex guard + philosophy gain (`韩炳哲`/`倦怠`/`倦怠社会`),
        # the note lands in philosophy. Title contains "历史感" (not
        # `历史` alone), so the regex guard makes that no-op.
        self.assertEqual(
            classify_note_domain(
                ['得到', '《倦怠社会》| 方希解读', 'history'],
                title='我特别喜欢方希老师的听书系列总觉得她讲书时有一种博古通今的历史感今天的听书课',
                excerpt='一位大学老师在向学生介绍韩炳哲时说...',
            ),
            'philosophy',
        )


class DomainHistoryTruePositivesTests(unittest.TestCase):
    """Task 1: 真历史笔记不应被误伤。每条 family 独立断言（plan-verifier 校正：
    实测这些 family 在 baseline 即为 history，修复后仍必须 history）。"""

    def test_history_research_tag_still_history(self):
        # 历史研究 是既有 history 域信号，必须保留。
        self.assertEqual(
            classify_note_domain(['得到', '历史研究']),
            'history',
        )

    def test_history_program_tag_still_history(self):
        # 历史节目 / 历史频道类笔记
        self.assertEqual(
            classify_note_domain(['得到', '历史节目']),
            'history',
        )

    def test_historical_figure_tag_still_history(self):
        # 历史人物 tag 单用即应入 history 域
        self.assertEqual(
            classify_note_domain(['历史人物']),
            'history',
        )

    def test_history_person_with_wenming_tag_still_history(self):
        # 复合 family: 录音笔记 + 文明之旅节目 + 历史人物（vault 真实 1 条）
        self.assertEqual(
            classify_note_domain(['录音笔记', '文明之旅节目', '历史人物']),
            'history',
        )


class DomainNeighborsUnaffectedTests(unittest.TestCase):
    """Task 1: 修复不应误伤非 history 桶。"""

    def test_philosophy_tag_still_philosophy(self):
        self.assertEqual(classify_note_domain(['哲学', '君子']), 'philosophy')

    def test_karamazov_still_philosophy(self):
        # 卡拉马佐夫 是既有 philosophy 信号
        self.assertEqual(
            classify_note_domain(['哲学', '卡拉马佐夫']),
            'philosophy',
        )

    def test_philosophy_plus_ai_still_philosophy(self):
        # philosophy-first 设计保留：含 ai 的哲学笔记仍 philosophy
        self.assertEqual(classify_note_domain(['哲学', 'ai']), 'philosophy')

    def test_cognition_tag_still_cognition(self):
        self.assertEqual(classify_note_domain(['认知', '心理']), 'cognition')

    def test_kahneman_still_cognition(self):
        self.assertEqual(classify_note_domain(['卡尼曼', '思维']), 'cognition')

    def test_psychology_tag_still_cognition(self):
        self.assertEqual(classify_note_domain(['心理学']), 'cognition')


class DomainHistoryBucketCatastropheFloorTests(unittest.TestCase):
    """Task 1: 桶大小灾难守卫 — history 桶不能从 161 崩到 60（窄词替换式）。

    注意：这是灾难守卫，不是"填满"门。实测修复后 history≈143（baseline 161
    正确剔污收缩 17 条污染 + 1 条真历史因仅靠裸 `history` 标签存活而边缘
    丢失）。门设 135 仅抓窄词替换式崩塌；不得设 ≥150（143 是正确结果）。
    真正的回归保护靠 per-family 断言。
    """

    @classmethod
    def setUpClass(cls):
        import os
        cls.vault_root = os.path.expanduser('~/Obsidian/PKOS')
        cls.vault_present = os.path.isdir(cls.vault_root)

    def test_history_bucket_floor(self):
        if not self.vault_present:
            self.skipTest("PKOS vault not present in this environment")
        from cross_domain import load_pkos_notes
        ns = load_pkos_notes(self.vault_root)
        n_history = sum(1 for n in ns if n['domain'] == 'history')
        # Catastrophe floor: 135 catches narrow-keyword replacement collapse
        # (e.g. 161 → 60). DO NOT set this to 150 — 143 is the correct
        # result of the de-pollution; >150 would invite re-introducing the
        # pollution to satisfy the floor.
        self.assertGreaterEqual(
            n_history, 135,
            f"history bucket {n_history} < 135 (catastrophe floor) — "
            f"possible narrow-keyword replacement collapse",
        )

    def test_philosophy_bucket_not_collapsed(self):
        if not self.vault_present:
            self.skipTest("PKOS vault not present in this environment")
        from cross_domain import load_pkos_notes
        ns = load_pkos_notes(self.vault_root)
        n_phil = sum(1 for n in ns if n['domain'] == 'philosophy')
        # Adding 韩炳哲/倦怠/福柯/本雅明 should NOT shrink philosophy below
        # baseline (≈1112) by a meaningful amount. The pre-fix philosophy
        # count is the source of truth here.
        self.assertGreaterEqual(n_phil, 1100)

    def test_cognition_bucket_not_collapsed(self):
        if not self.vault_present:
            self.skipTest("PKOS vault not present in this environment")
        from cross_domain import load_pkos_notes
        ns = load_pkos_notes(self.vault_root)
        n_cog = sum(1 for n in ns if n['domain'] == 'cognition')
        self.assertGreaterEqual(n_cog, 120)


if __name__ == "__main__":
    unittest.main()
