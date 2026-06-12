import unittest, tempfile, json, os, sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from orchestrator import run_check, run_finalize, novelty_score, _adjusted_novelty, _consecutive_day_penalty

class OrchestratorCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_novelty_score_no_matching_days_returns_one(self):
        # Empty log → score = 1.0 (fully novel)
        score = novelty_score(candidate_tag="swift6", topic_log_path=self.topic_log_path, today="2026-05-19")
        self.assertEqual(score, 1.0)

    def test_novelty_score_three_matching_days_returns_low(self):
        # Build a log with "swift6" present in 3 of the past 7 days
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": "2026-05-15", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
                {"date": "2026-05-16", "topics": [{"tag": "swift6", "angle": "商业影响"}]},
                {"date": "2026-05-17", "topics": [{"tag": "swift6", "angle": "用户体验"}]},
            ],
        })
        score = novelty_score(candidate_tag="swift6", topic_log_path=self.topic_log_path, today="2026-05-19")
        # 1 - 3/7 = 0.571...
        self.assertAlmostEqual(score, 1 - 3/7, places=3)

    def test_run_check_drops_low_novelty_keeps_high(self):
        # 2 candidates: swift6 (low novelty) + new-topic (high novelty).
        # DP-001 A: pkos_note is provided by CALLER (达芬奇), not pulled by orchestrator.
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": d, "topics": [{"tag": "swift6", "angle": "技术内核"}]}
                for d in ["2026-05-14", "2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18"]
            ],
        })
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "test", "category": "test", "url": "test"}
            brief = run_check(
                candidates=["swift6", "new-topic"],
                topic_log_path=self.topic_log_path,
                today="2026-05-19",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            )
        tags = [t["topic_tag"] for t in brief["approved_topics"]]
        self.assertNotIn("swift6", tags)  # 5/7 matches → score 0.28 < 0.3 → drop
        self.assertIn("new-topic", tags)
        # pkos_note is the caller-provided value, propagated to brief verbatim
        self.assertEqual(brief["pkos_note"], {"id": "PKOS/x", "title": "y", "excerpt": "z"})
        self.assertIn("contrarian_source", brief)
        # Insight-density fields present; empty vault → empty candidate lists
        self.assertEqual(brief["cross_domain_candidates"], [])
        self.assertEqual(brief["self_past_candidates"], [])
        self.assertIn("named_concept_prompt", brief)
        self.assertIn("命名", brief["named_concept_prompt"])

    def test_run_check_keeps_medium_novelty_with_angle(self):
        # 3 matches in 7 days → 1 - 3/7 = 0.571 → 0.4-0.7 → keep + pick_unused_angle
        # (Task 5-impl raised the drop threshold from 0.3 to 0.4; the days are
        # also spread out — not consecutive — so the consecutive-day penalty
        # does NOT kick in.)
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": "2026-05-13", "topics": [{"tag": "ai-agents", "angle": "技术内核"}]},
                {"date": "2026-05-15", "topics": [{"tag": "ai-agents", "angle": "技术内核"}]},
                {"date": "2026-05-17", "topics": [{"tag": "ai-agents", "angle": "技术内核"}]},
            ],
        })
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=["ai-agents"],
                topic_log_path=self.topic_log_path,
                today="2026-05-19",
                pkos_note={"id": "PKOS/test", "title": "t", "excerpt": "e"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            )
        topics = brief["approved_topics"]
        self.assertEqual(len(topics), 1)
        # Used angles for "ai-agents" past 7 days: {"技术内核"} → next unused is "商业影响"
        self.assertEqual(topics[0]["required_angle"], "商业影响")

    def test_run_check_rejects_missing_pkos_note(self):
        # DP-001 A enforcement: orchestrator validates pkos_note is provided by caller.
        # If 达芬奇 forgot to select a note from vault.subjective_dir, orchestrator returns
        # an error brief so 达芬奇 retries (or 达芬奇 prompt makes it impossible to skip).
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=["new-topic"],
                topic_log_path=self.topic_log_path,
                today="2026-05-19",
                pkos_note=None,  # caller failed to provide
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            )
        self.assertIn("error", brief)
        self.assertIn("pkos_note", brief["error"])
        # Error brief keeps the full schema so downstream consumers don't KeyError
        self.assertEqual(brief["cross_domain_candidates"], [])
        self.assertEqual(brief["self_past_candidates"], [])
        self.assertIn("named_concept_prompt", brief)

class OrchestratorFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        self.script_path = os.path.join(self.tmp_dir.name, "today.md")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_finalize_accept_when_jaccard_below_threshold(self):
        # Empty log → max Jaccard is 0 → < 0.15 → accept
        Path(self.script_path).write_text("brand new podcast script about quantum computing")
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-19",
            approved_topics=[{"topic_tag": "quantum", "required_angle": "技术内核"}],
        )
        self.assertEqual(result["action"], "accept")
        # topic_log should have been written with today's episode
        from topic_log import load_topic_log
        data = load_topic_log(self.topic_log_path)
        self.assertEqual(len(data["episodes"]), 1)
        self.assertEqual(data["episodes"][0]["date"], "2026-05-19")

    def test_finalize_retry_when_jaccard_exceeds_threshold(self):
        # Log an episode + put its content in a sibling file; new script is identical
        identical_script = "the quick brown fox jumps over the lazy dog " * 20
        # Need to simulate prior-day scripts as corpus; orchestrator reads from configurable scriptArchiveDir
        archive_dir = os.path.join(self.tmp_dir.name, "archive")
        os.makedirs(archive_dir)
        Path(os.path.join(archive_dir, "2026-05-18.md")).write_text(identical_script)
        Path(self.script_path).write_text(identical_script)
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-19",
            approved_topics=[{"topic_tag": "x", "required_angle": "y"}],
            script_archive_dir=archive_dir,
        )
        self.assertEqual(result["action"], "retry")
        self.assertGreaterEqual(result.get("jaccard", 0), 0.15)
        # On retry, topic_log NOT updated
        from topic_log import load_topic_log
        data = load_topic_log(self.topic_log_path)
        self.assertEqual(len(data["episodes"]), 0)

    def test_finalize_archives_episode_when_archive_dir_set(self):
        Path(self.script_path).write_text("# 概率时代的AI可靠性\n\n正文。")
        archive_dir = os.path.join(self.tmp_dir.name, "90-Productions/Podcasts")
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-21",
            approved_topics=[{"topic_tag": "ai-reliability", "required_angle": "技术内核"}],
            archive_dir=archive_dir,
            named_concept="熵蚀",
        )
        self.assertEqual(result["action"], "accept")
        archived = result["archived"]
        self.assertTrue(os.path.exists(archived))
        self.assertTrue(os.path.basename(archived).startswith("2026-05-21-"))
        text = Path(archived).read_text(encoding="utf-8")
        self.assertIn("type: podcast", text)
        self.assertIn("named_concept: 熵蚀", text)
        self.assertIn("ai-reliability", text)

    def test_finalize_skips_archive_when_no_archive_dir(self):
        Path(self.script_path).write_text("# 标题\n\n正文。")
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-21",
            approved_topics=[{"topic_tag": "x", "required_angle": "y"}],
        )
        self.assertEqual(result["action"], "accept")
        self.assertNotIn("archived", result)


class FinalizeTopicLevelTests(unittest.TestCase):
    """Task 6: run_finalize must catch '同话题换词' (same topic, different wording)
    repetition. The current 4-gram jaccard is too literal — paraphrased text on the
    same topic scores low. The new topic-level similarity metric must lift the
    `jaccard` field above zero for paraphrased topics, so a 0 reading is a
    regression of the dedup signal."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        self.script_path = os.path.join(self.tmp_dir.name, "today.md")
        self.archive_dir = os.path.join(self.tmp_dir.name, "archive")
        os.makedirs(self.archive_dir)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_finalize_jaccard_nonzero_for_paraphrased_topic(self):
        # Short scripts sharing technical vocabulary but rephrased — 4-gram
        # stays below topic-level unigram Jaccard. The combined max_jaccard
        # in run_finalize must lift the reported jaccard above the noise
        # floor (0.05).
        prior_script = (
            "AI agent alignment safety in production systems is critical. "
            "Alignment failures can cause harm. Engineers build guardrails. "
            "Monitoring is essential. Boundaries matter."
        )
        today_script = (
            "Production-grade AI agent systems require alignment safety. "
            "Harm may result from failures. Engineers construct guardrails. "
            "Monitoring is vital. Boundaries are essential."
        )
        Path(os.path.join(self.archive_dir, "2026-05-18.md")).write_text(prior_script)
        Path(self.script_path).write_text(today_script)
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-19",
            approved_topics=[{"topic_tag": "ai-agent-safety", "required_angle": "技术内核"}],
            script_archive_dir=self.archive_dir,
        )
        # The jaccard reported back must be non-zero (signal exists).
        # Pure 4-gram on these short rephrased scripts is ~0.39; the new
        # topic-level similarity lifts the combined max to ~0.55.
        self.assertGreater(
            result.get("jaccard", 0.0), 0.4,
            f"run_finalize should report combined topic+4-gram similarity "
            f"above 0.4 for paraphrased same-topic text (got jaccard={result.get('jaccard')})",
        )

    def test_finalize_drops_paraphrased_same_topic_at_default_threshold(self):
        # At the current default threshold (0.4) the paraphrased same-topic
        # script should now trigger retry — this locks the new contract.
        prior_script = (
            "AI agent alignment safety in production systems is critical. "
            "Alignment failures can cause harm. Engineers build guardrails. "
            "Monitoring is essential. Boundaries matter."
        )
        today_script = (
            "Production-grade AI agent systems require alignment safety. "
            "Harm may result from failures. Engineers construct guardrails. "
            "Monitoring is vital. Boundaries are essential."
        )
        Path(os.path.join(self.archive_dir, "2026-05-18.md")).write_text(prior_script)
        Path(self.script_path).write_text(today_script)
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-19",
            approved_topics=[{"topic_tag": "ai-agent-safety", "required_angle": "技术内核"}],
            script_archive_dir=self.archive_dir,
            threshold=0.4,
        )
        self.assertEqual(
            result["action"], "retry",
            f"paraphrased same-topic script should trigger retry at threshold 0.4 "
            f"(got action={result.get('action')}, jaccard={result.get('jaccard')})",
        )

    def test_finalize_accepts_unrelated_topic(self):
        # Sanity: unrelated topic with no shared vocabulary → accept + low jaccard.
        prior_script = (
            "The history of medieval cathedral architecture in france reveals "
            "evolving engineering techniques and shifting theological priorities."
        )
        today_script = (
            "Recent advances in quantum computing promise to revolutionize "
            "cryptography and enable new kinds of optimization problems."
        )
        Path(os.path.join(self.archive_dir, "2026-05-18.md")).write_text(prior_script)
        Path(self.script_path).write_text(today_script)
        result = run_finalize(
            script_path=self.script_path,
            topic_log_path=self.topic_log_path,
            today="2026-05-19",
            approved_topics=[{"topic_tag": "quantum-computing", "required_angle": "技术内核"}],
            script_archive_dir=self.archive_dir,
        )
        self.assertEqual(result["action"], "accept")
        self.assertLess(result.get("jaccard", 0.0), 0.3)


class BriefPerturbationTests(unittest.TestCase):
    """parallel-N perturbation: force_domain + force_contrarian echoed in brief_perturbation."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _check(self, **kw):
        return run_check(
            candidates=["new-topic"],
            topic_log_path=self.topic_log_path,
            today="2026-05-21",
            pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
            vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            **kw,
        )

    def test_brief_perturbation_records_forced_domain_and_contrarian(self):
        brief = self._check(force_domain="philosophy", force_contrarian="lesswrong")
        self.assertEqual(brief["brief_perturbation"]["cross_domain_bucket"], "philosophy")
        self.assertEqual(brief["brief_perturbation"]["contrarian_source"], "lesswrong")
        self.assertEqual(brief["contrarian_source"]["source"], "lesswrong")

    def test_brief_perturbation_bucket_is_none_when_unperturbed(self):
        # Normal daily run — no force_domain → bucket None, contrarian still recorded.
        brief = self._check()
        self.assertIsNone(brief["brief_perturbation"]["cross_domain_bucket"])
        self.assertIsNotNone(brief["brief_perturbation"]["contrarian_source"])

    def test_error_brief_carries_brief_perturbation_key(self):
        # Schema consistency: error brief (missing pkos_note) still has the key.
        brief = run_check(
            candidates=["new-topic"], topic_log_path=self.topic_log_path,
            today="2026-05-21", pkos_note=None)
        self.assertIn("error", brief)
        self.assertIsNone(brief["brief_perturbation"])


# ---------------------------------------------------------------------------
# Task 5-tests: semantic novelty (near-synonym hit), drop threshold tightening,
# consecutive-day penalty
# ---------------------------------------------------------------------------

class NoveltySemanticTests(unittest.TestCase):
    """novelty_score must:
       - hit near-synonyms (ai-agent-safety ≈ ai-agent-security) — not exact string
       - ignore clearly distinct tags (ai-agent-safety vs quantum-computing)
       - score consecutive-day appearances lower than single-day appearances
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _save_log(self, episodes):
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": episodes})

    def test_novelty_hits_near_synonym_tag(self):
        # ai-agent-safety in log; candidate is ai-agent-security.
        # Both share tokens "ai" and "agent" — must be treated as a hit.
        self._save_log([
            {"date": "2026-06-01", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
            {"date": "2026-06-02", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
            {"date": "2026-06-03", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
        ])
        # Raw novelty_score: 3 semantic hits → 0.571 (synonym match works).
        raw = novelty_score(
            candidate_tag="ai-agent-security",
            topic_log_path=self.topic_log_path,
            today="2026-06-04",
        )
        self.assertLess(raw, 1.0, "synonym must count as a hit (raw score < 1.0)")
        # _adjusted_novelty: subtract consecutive-day penalty → below drop threshold
        adj = _adjusted_novelty(
            candidate_tag="ai-agent-security",
            topic_log_path=self.topic_log_path,
            today="2026-06-04",
        )
        self.assertLess(adj, 0.4, "synonym + 3-consecutive-day penalty should drop below 0.4")

    def test_novelty_ignores_unrelated_tag(self):
        # ai-agent-safety in log; candidate is quantum-computing.
        # Tokens share nothing significant — must NOT be a hit.
        self._save_log([
            {"date": d, "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]}
            for d in ["2026-06-01", "2026-06-02", "2026-06-03"]
        ])
        score = novelty_score(
            candidate_tag="quantum-computing",
            topic_log_path=self.topic_log_path,
            today="2026-06-04",
        )
        # No semantic hit → score near 1.0
        self.assertGreater(score, 0.95)

    def test_novelty_consecutive_day_penalty_lowers_score(self):
        # 3 hits in 3 consecutive days (worst case): _adjusted_novelty should be
        # 3 hits spread out (best case): _adjusted_novelty should be higher.
        # raw novelty_score is the same in both cases (1 - 3/7) — the penalty
        # is applied by _adjusted_novelty, not by novelty_score itself (raw API
        # is preserved for callers that want the unpenalized value).
        from topic_log import save_topic_log
        # Case A: 3 consecutive days
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": "2026-06-02", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
                {"date": "2026-06-03", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
                {"date": "2026-06-04", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
            ],
        })
        adj_consecutive = _adjusted_novelty(
            candidate_tag="ai-agent-safety",
            topic_log_path=self.topic_log_path,
            today="2026-06-04",
        )
        # Case B: 3 hits spread across the window (not consecutive)
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": "2026-05-29", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
                {"date": "2026-06-01", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
                {"date": "2026-06-03", "topics": [{"tag": "ai-agent-safety", "angle": "技术内核"}]},
            ],
        })
        adj_spread = _adjusted_novelty(
            candidate_tag="ai-agent-safety",
            topic_log_path=self.topic_log_path,
            today="2026-06-04",
        )
        # Consecutive pattern (3 in a row) must score LOWER than spread-out pattern
        self.assertLess(
            adj_consecutive, adj_spread,
            f"consecutive={adj_consecutive} should be < spread={adj_spread}",
        )

    def test_run_check_drops_tightened_threshold(self):
        # With the new drop threshold (0.4) + consecutive-day penalty, a topic
        # whose hits include any 2-day consecutive run gets an extra -0.10.
        # 4 hits across 7 days = base 0.429. If even 2 are consecutive, the
        # adjusted score = 0.329 < 0.4 → drop. This locks the new contract:
        # the "no consecutive penalty" case was the old behavior; the new
        # behavior treats 2-in-a-row as a signal worth penalizing.
        from topic_log import save_topic_log
        # 4-of-7 with 2 consecutive (5-29 → 5-30): base 0.429 - 0.10 penalty = 0.329 < 0.4 → drop
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": "2026-05-29", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
                {"date": "2026-05-30", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
                {"date": "2026-06-01", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
                {"date": "2026-06-03", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
            ],
        })
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "test", "category": "test", "url": "test"}
            brief = run_check(
                candidates=["swift6"],
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            )
        # 2-consecutive days ⇒ penalty kicks in ⇒ drop
        tags = [t["topic_tag"] for t in brief["approved_topics"]]
        self.assertNotIn("swift6", tags)


# ---------------------------------------------------------------------------
# Task 6-tests: --show-type {morning,evening} branch + morning quota routing
# ---------------------------------------------------------------------------


class ShowTypeMorningQuotaRoutingTests(unittest.TestCase):
    """Phase 2 plan Task 6: orchestrator `check` gains `--show-type {morning,evening}`.
    Morning branch must route the candidate list through select_with_domain_quota
    so the brief enforces cross-domain coverage in code (not LLM trust).

    `required_domains` is a parameter to the python layer (caller-passed → fork-safe),
    matching the literal `domain` key on candidates (∈ tech/market/science/geo/culture).
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_morning_show_type_routes_through_domain_quota(self):
        # 8 AI-only candidates dressed as dicts with `domain` key — morning
        # branch must cap them and surface missing domains via the brief.
        candidates = [
            {"id": f"t{i}", "domain": "tech", "topic_tag": f"ai-thing-{i}"}
            for i in range(8)
        ]
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=candidates,
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
                show_type="morning",
                required_domains=["tech", "market", "science", "geo", "culture"],
            )
        # The brief must carry a domain_selection diagnostic so the writer step
        # knows what was filtered/missing.
        self.assertIn("domain_selection", brief)
        ds = brief["domain_selection"]
        self.assertIn("missing_domains", ds["diagnostic"])
        self.assertIn("market", ds["diagnostic"]["missing_domains"])
        # Tech capped: not all 8 tech items propagated.
        tech_selected = sum(1 for s in ds["selected"] if s["domain"] == "tech")
        self.assertLess(tech_selected, 8)

    def test_morning_with_balanced_domains_covers_all(self):
        # 1 candidate per required domain → no missing.
        candidates = [
            {"id": "a", "domain": "tech", "topic_tag": "ai-x"},
            {"id": "b", "domain": "market", "topic_tag": "market-y"},
            {"id": "c", "domain": "science", "topic_tag": "science-z"},
            {"id": "d", "domain": "geo", "topic_tag": "geo-w"},
            {"id": "e", "domain": "culture", "topic_tag": "culture-v"},
        ]
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=candidates,
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
                show_type="morning",
                required_domains=["tech", "market", "science", "geo", "culture"],
            )
        self.assertEqual(brief["domain_selection"]["diagnostic"]["missing_domains"], [])

    def test_default_show_type_preserves_legacy_string_candidates(self):
        # Regression shield: omitting show_type must NOT change behavior.
        # Existing callers pass `candidates` as a list of bare topic_tag strings
        # (see `test_run_check_drops_low_novelty_keeps_high`). The default
        # branch keeps that contract — no domain_selection key emitted.
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=["new-topic"],
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=os.path.join(self.tmp_dir.name, "nonexistent_vault"),
            )
        self.assertNotIn("domain_selection", brief)
        # Original schema unchanged.
        self.assertIn("approved_topics", brief)
        self.assertIn("new-topic", [t["topic_tag"] for t in brief["approved_topics"]])


# ---------------------------------------------------------------------------
# Task 7-tests: --show-type=evening brief spine inversion (vault open questions
# elevated to PRIMARY field, news demoted to secondary `evidence`)
# ---------------------------------------------------------------------------


class ShowTypeEveningSpineInversionTests(unittest.TestCase):
    """Phase 2 plan Task 7: evening branch flips the brief — vault open questions
    (same_topic_past_notes + cross_domain_candidates) become the PRIMARY field
    `open_questions` / `belief_spine`; news candidates demote to secondary
    `evidence`. Morning brief schema must NOT regress.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})
        # Build a mock vault with one note per directory used by self_past +
        # cross_domain (so both sources can populate the open-questions main field).
        self.vault_root = os.path.join(self.tmp_dir.name, "vault")
        ideas = os.path.join(self.vault_root, "20-Ideas", "观点心得")
        os.makedirs(ideas)
        Path(os.path.join(ideas, "viewpoint.md")).write_text(
            "---\ntype: idea\ntags: [ai, 哲学]\ncreated: 2026-05-15\n"
            "---\n\n关于 AI 的开放问题：意义如何被构建？\n",
            encoding="utf-8",
        )
        cross = os.path.join(self.vault_root, "10-Knowledge")
        os.makedirs(cross)
        Path(os.path.join(cross, "philosophy-note.md")).write_text(
            "---\ntype: knowledge\ntags: [哲学, ai]\ncreated: 2026-05-20\n"
            "---\n\n关于意义与价值的哲学思考。\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_evening_brief_open_questions_is_primary(self):
        candidates = [
            {"id": "n1", "domain": "tech", "topic_tag": "ai-new-thing"},
        ]
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=candidates,
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=self.vault_root,
                show_type="evening",
                required_domains=["tech", "market", "science", "geo", "culture"],
            )
        # PRIMARY field: open_questions / belief_spine elevated from vault.
        self.assertIn("open_questions", brief,
                      "evening brief must expose open_questions as a top-level primary field")
        self.assertIsInstance(brief["open_questions"], list)
        # Non-empty: vault has matching notes seeded in setUp.
        self.assertGreater(len(brief["open_questions"]),
                           0,
                           "open_questions must be populated from vault recall")
        # SECONDARY field: news demoted to `evidence`.
        self.assertIn("evidence", brief,
                      "evening brief must put news candidates under secondary `evidence` field")
        self.assertNotIn(
            "approved_topics", brief,
            "evening brief must NOT carry `approved_topics` as primary (that is morning's spine)",
        )

    def test_evening_brief_surfaces_fresh_today_notes(self):
        # Task 5: a note created TODAY must appear in the evening brief's
        # ADDITIONAL `fresh_today` field — without displacing open_questions.
        today = "2026-05-20"  # matches philosophy-note.md created date seeded in setUp
        candidates = [{"id": "n1", "domain": "tech", "topic_tag": "ai-new-thing"}]
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=candidates,
                topic_log_path=self.topic_log_path,
                today=today,
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=self.vault_root,
                show_type="evening",
                required_domains=["tech", "market", "science", "geo", "culture"],
            )
        self.assertIn("fresh_today", brief,
                      "evening brief must expose fresh_today as an additional field")
        self.assertIsInstance(brief["fresh_today"], list)
        created_dates = {n.get("created", "")[:10] for n in brief["fresh_today"]}
        self.assertTrue(brief["fresh_today"], "today-created vault note must surface")
        self.assertEqual(created_dates, {today},
                         "fresh_today must contain ONLY notes created today")
        # Did NOT displace the primary spine.
        self.assertIn("open_questions", brief)

    def test_morning_schema_does_not_regress(self):
        # The same call with show_type="morning" must keep the event-centric
        # schema: approved_topics + cross_domain_candidates / self_past_candidates
        # as secondary fields, NOT a top-level open_questions primary.
        candidates = [
            {"id": "n1", "domain": "tech", "topic_tag": "ai-new-thing"},
            {"id": "n2", "domain": "market", "topic_tag": "market-y"},
        ]
        with patch("orchestrator._contrarian_pull") as mc:
            mc.return_value = {"source": "x", "category": "y", "url": "z"}
            brief = run_check(
                candidates=candidates,
                topic_log_path=self.topic_log_path,
                today="2026-06-04",
                pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
                vault_root=self.vault_root,
                show_type="morning",
                required_domains=["tech", "market", "science", "geo", "culture"],
            )
        self.assertIn("approved_topics", brief)
        # Morning must NOT promote open_questions to PRIMARY (that is evening's spine).
        self.assertNotIn("open_questions", brief,
                         "morning brief must not regress into evening spine")
        # Morning still carries the morning quota-selection diagnostic.
        self.assertIn("domain_selection", brief)


class EarnedNamingAndAngleTests(unittest.TestCase):
    """Task 1: naming/反方 moved from forced quota to 'earned only'.
    - 反对意见 dropped from the forced angle rotation (earned via contrarian_source).
    - NAMED_CONCEPT_PROMPT reworded to 'earned, not forced': omission is correct."""

    def test_pick_unused_angle_never_returns_contrarian(self):
        from angle_slots import pick_unused_angle, DEFAULT_ANGLES
        # 反对意见 is no longer part of the forced rotation.
        self.assertNotIn("反对意见", DEFAULT_ANGLES)
        # Even when all 4 framing angles are used (saturation), rotation never
        # surfaces 反对意见 — it falls back to a framing angle, not the contrarian slot.
        all_used = ["技术内核", "商业影响", "用户体验", "历史类比"]
        self.assertNotEqual(pick_unused_angle(all_used), "反对意见")
        self.assertEqual(pick_unused_angle([]), "技术内核")
        # Walk the rotation: no path yields 反对意见.
        used = []
        for _ in range(6):
            a = pick_unused_angle(used)
            self.assertNotEqual(a, "反对意见")
            if a not in used:
                used.append(a)

    def test_default_angles_are_the_four_framing_angles(self):
        from angle_slots import DEFAULT_ANGLES
        self.assertEqual(DEFAULT_ANGLES, ["技术内核", "商业影响", "用户体验", "历史类比"])

    def test_named_concept_prompt_is_earned_not_forced(self):
        from orchestrator import NAMED_CONCEPT_PROMPT
        # Mandatory-ritual framing must be gone.
        self.assertNotIn("命名任务", NAMED_CONCEPT_PROMPT)
        self.assertNotIn("命名仪式", NAMED_CONCEPT_PROMPT)
        # Earned-omission language must be present: omitting when nothing merits
        # naming is the CORRECT choice, not a penalized one.
        self.assertIn("省略", NAMED_CONCEPT_PROMPT)


# ---------------------------------------------------------------------------
# Task 3-tests (2026-06-07 source-recurrence fix — 治本 b IO 侧): the CLI
# `check` handler must:
#   1) read source_log.jsonl (co-located with --topic-log) for the last
#      14 days of offered note paths, pass them to run_check as
#      `exclude_source_ids`,
#   2) write the brief's cross_domain_candidates paths back to
#      source_log.jsonl after the brief is generated,
#   3) NOT do any IO when called via run_check directly (pure-function
#      contract — preserves the existing 102 tests).
# ---------------------------------------------------------------------------


class SourceLogWiringTests(unittest.TestCase):
    """Task 3: CLI `check` wires source_log read+write around run_check."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})
        # Build a minimal vault so cross_domain_candidates has something to pick
        self.vault_root = os.path.join(self.tmp_dir.name, "vault")
        cross = os.path.join(self.vault_root, "10-Knowledge")
        os.makedirs(cross)
        # 5 distinct philosophy notes — small enough to avoid triggering
        # small-bucket backfill; distinct titles so title-collapse is a no-op.
        for i in range(5):
            Path(os.path.join(cross, f"phil-{i}.md")).write_text(
                "---\ntype: knowledge\ntags: [哲学, ai]\ncreated: 2026-05-15\n"
                f"---\n\n哲学笔记{i} 实体内容。\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _check(self, exclude_source_ids=None, vault_root=None):
        # Pass candidates as DICT list so the morning quota selector accepts
        # them — bare-string candidates under show_type="morning" are filtered
        # out (they lack a `domain` key) and the downstream `notes` walk
        # never runs. The dict shape mirrors the morning producer/consumer
        # contract that Adam templates use in production.
        candidates = [
            {"id": "n1", "domain": "tech", "topic_tag": "new-topic"},
        ]
        return run_check(
            candidates=candidates,
            topic_log_path=self.topic_log_path,
            today="2026-06-07",
            pkos_note={"id": "PKOS/x", "title": "y", "excerpt": "z"},
            vault_root=vault_root or self.vault_root,
            force_domain="philosophy",
            show_type="morning",
            required_domains=["tech", "philosophy"],
            exclude_source_ids=exclude_source_ids,
        )

    def test_run_check_accepts_exclude_source_ids(self):
        # The pure function signature must accept `exclude_source_ids` and
        # filter cross_domain_candidates accordingly.
        from cross_domain import _title_signature
        all_brief = self._check()
        all_paths = [nt["path"] for nt in all_brief["cross_domain_candidates"]]
        self.assertEqual(len(all_paths), 5)
        # Now exclude the first 2 — they should drop out of the result.
        exclude = set(all_paths[:2])
        filtered = self._check(exclude_source_ids=exclude)
        filtered_paths = [nt["path"] for nt in filtered["cross_domain_candidates"]]
        for excluded in exclude:
            self.assertNotIn(excluded, filtered_paths)

    def test_run_check_pure_no_write(self):
        # run_check (direct call, not the CLI handler) must NOT touch any
        # source_log file. The IO is in the CLI handler only.
        self._check()
        # No source_log.jsonl should appear next to --topic-log (we did not
        # route through main()'s `check` branch).
        source_log_path = os.path.join(self.tmp_dir.name, "source_log.jsonl")
        self.assertFalse(os.path.exists(source_log_path),
                         "run_check direct call must not create source_log.jsonl")

    def test_run_check_default_exclude_is_none(self):
        # Calling run_check with no exclude_source_ids must behave like
        # the pre-fix contract — no filtering.
        brief_default = self._check()
        brief_explicit_none = self._check(exclude_source_ids=None)
        # Both should return the same set of paths (deterministic on the
        # fixed topic_log + vault).
        d1 = sorted(nt["path"] for nt in brief_default["cross_domain_candidates"])
        d2 = sorted(nt["path"] for nt in brief_explicit_none["cross_domain_candidates"])
        self.assertEqual(d1, d2)


class SourceLogCLIRoundtripTests(unittest.TestCase):
    """Task 3: CLI `check` roundtrip — running main(['check', ...]) must
    read exclude + write offered into source_log.jsonl. Tests via
    subprocess to exercise the real main() entry point."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.topic_log_path = os.path.join(self.tmp_dir.name, "topic_log.yaml")
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {"episodes": []})
        self.vault_root = os.path.join(self.tmp_dir.name, "vault")
        cross = os.path.join(self.vault_root, "10-Knowledge")
        os.makedirs(cross)
        # 8 distinct philosophy notes — large enough that excluding 5
        # (typical first-run offered) still leaves 3 in the filtered pool,
        # so the small-bucket backfill never triggers. The plan's step 3
        # explicitly says: "用足够大的桶避免兜底".
        for i in range(8):
            Path(os.path.join(cross, f"phil-{i}.md")).write_text(
                "---\ntype: knowledge\ntags: [哲学, ai]\ncreated: 2026-05-15\n"
                f"---\n\n哲学笔记{i}。\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _run_check_cli(self, today="2026-06-07", candidates=None):
        """Drive main() with the same args Adam uses for the morning check."""
        import subprocess
        import sys as _sys
        # Dict shape mirrors the morning producer/consumer contract that
        # Adam templates use in production. Bare strings under
        # show_type="morning" would be filtered by select_with_domain_quota
        # (no `domain` key) and the cross_domain_candidates walk would never
        # run, defeating the test's purpose.
        cands = candidates or [
            {"id": "n1", "domain": "tech", "topic_tag": "new-topic"},
        ]
        cands_json = json.dumps(cands)
        # --pkos-note is REQUIRED by run_check (DP-001 A). Without it the
        # brief comes back as an error brief with cross_domain_candidates=[]
        # and source_log.jsonl never gets written. Adam templates always
        # supply this — the test must mirror that.
        pkos_note_json = json.dumps({"id": "PKOS/x", "title": "y", "excerpt": "z"})
        result = subprocess.run(
            [
                _sys.executable, "orchestrator.py", "check",
                "--candidates", cands_json,
                "--date", today,
                "--topic-log", self.topic_log_path,
                "--vault-root", self.vault_root,
                "--force-domain", "philosophy",
                "--show-type", "morning",
                "--required-domains", "tech,philosophy",
                "--pkos-note", pkos_note_json,
            ],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__)),
        )
        return result

    def test_check_writes_offered_to_source_log(self):
        result = self._run_check_cli()
        self.assertEqual(result.returncode, 0, f"check CLI failed: {result.stderr}")
        source_log_path = os.path.join(self.tmp_dir.name, "source_log.jsonl")
        self.assertTrue(os.path.exists(source_log_path),
                        "check CLI must create source_log.jsonl next to --topic-log")
        # The file must contain at least one jsonl line with note_ids.
        from source_log import recent_source_ids
        ids = recent_source_ids(source_log_path, today="2026-06-07", window_days=14)
        self.assertGreater(len(ids), 0,
                           "source_log.jsonl must record the offered note paths")

    def test_check_excludes_recent_offered_on_next_run(self):
        # First run: writes offered paths to source_log.
        first = self._run_check_cli(today="2026-06-06")
        self.assertEqual(first.returncode, 0, f"first run failed: {first.stderr}")
        from source_log import recent_source_ids
        offered = recent_source_ids(
            os.path.join(self.tmp_dir.name, "source_log.jsonl"),
            today="2026-06-07", window_days=14,
        )
        self.assertGreater(len(offered), 0)
        # Second run: those offered paths should be EXCLUDED from the brief.
        second = self._run_check_cli(today="2026-06-07")
        self.assertEqual(second.returncode, 0, f"second run failed: {second.stderr}")
        brief = json.loads(second.stdout)
        result_paths = {nt["path"] for nt in brief.get("cross_domain_candidates", [])}
        for prev in offered:
            self.assertNotIn(prev, result_paths,
                             f"path {prev!r} offered yesterday must be excluded today")


if __name__ == "__main__":
    unittest.main()
