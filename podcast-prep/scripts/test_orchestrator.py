import unittest, tempfile, json, os, sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from orchestrator import run_check, run_finalize, novelty_score

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
        # 3 matches in 7 days → 1 - 3/7 = 0.571 → 0.3-0.7 → keep + pick_unused_angle
        from topic_log import save_topic_log
        save_topic_log(self.topic_log_path, {
            "episodes": [
                {"date": d, "topics": [{"tag": "ai-agents", "angle": "技术内核"}]}
                for d in ["2026-05-15", "2026-05-16", "2026-05-17"]
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
        # If 达芬奇 forgot to invoke pkos:serendipity, orchestrator returns an error brief
        # so 达芬奇 retries (or 达芬奇 prompt makes it impossible to skip).
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
        archive_dir = os.path.join(self.tmp_dir.name, "90-Podcasts")
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

if __name__ == "__main__":
    unittest.main()
