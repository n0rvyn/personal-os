import unittest, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from minhash_check import shingle_4gram, jaccard_similarity, max_jaccard_against, topic_similarity

class MinHashTests(unittest.TestCase):
    def test_shingle_4gram_short_text(self):
        shingles = shingle_4gram("hello world")
        # "hell" "ello" "llo " "lo w" "o wo" " wor" "worl" "orld"
        self.assertEqual(len(shingles), 8)
        self.assertIn("hell", shingles)
        self.assertIn("orld", shingles)

    def test_jaccard_identical(self):
        a = "the quick brown fox jumps over the lazy dog"
        self.assertAlmostEqual(jaccard_similarity(a, a), 1.0)

    def test_jaccard_disjoint(self):
        sim = jaccard_similarity("abcdefgh", "zyxwvutsrq")
        self.assertLess(sim, 0.05)

    def test_max_jaccard_against_empty_corpus(self):
        sim = max_jaccard_against("any text here long enough for shingles", [])
        self.assertEqual(sim, 0.0)

    def test_max_jaccard_against_identical_in_corpus(self):
        text = "the quick brown fox jumps over the lazy dog"
        sim = max_jaccard_against(text, ["unrelated content here", text, "more unrelated"])
        self.assertGreaterEqual(sim, 0.9)


class MinHashTopicLevelTests(unittest.TestCase):
    """Task 6: topic-level similarity — same topic, different wording must score
    higher than 4-gram character similarity. This catches the '同话题换词'
    (same topic, different wording) repetition that pure 4-gram misses."""

    def test_topic_similarity_importable(self):
        # Sanity: topic_similarity function must exist in minhash_check
        self.assertTrue(callable(topic_similarity))

    def test_topic_similarity_identical_is_one(self):
        a = "the quick brown fox jumps over the lazy dog"
        self.assertAlmostEqual(topic_similarity(a, a), 1.0)

    def test_topic_similarity_higher_than_4gram_for_paraphrase(self):
        # Same topic (AI agent safety), different wording — 4-gram char jaccard
        # is lower than topic-level unigram Jaccard because rephrasing keeps
        # the content vocabulary ('agent', 'systems', 'alignment', 'safety',
        # 'guardrails', 'boundaries') while shuffling/replacing short
        # surface words ('must address' vs 'should address').
        a = (
            "Engineers building AI agent systems in production environments must address safety. "
            "Alignment failures in agent systems could cause real harm to users, and developers "
            "should implement guardrails to constrain agent behavior within well-defined boundaries."
        )
        b = (
            "When engineers build AI agent systems for production environments, they should address safety. "
            "Alignment failures in agent systems can cause real harm to users, and developers "
            "should implement guardrails to constrain agent behavior inside well-defined boundaries."
        )
        char_sim = jaccard_similarity(a, b)
        topic_sim = topic_similarity(a, b)
        self.assertLess(char_sim, 0.85,
                        f"4-gram should be lower than topic-level for this rephrase (got {char_sim:.3f})")
        self.assertGreater(topic_sim, 0.75,
                           f"topic-level should be high for same-topic rephrase (got {topic_sim:.3f})")
        self.assertGreater(topic_sim, char_sim,
                           f"topic_sim ({topic_sim:.3f}) must exceed 4-gram ({char_sim:.3f})")

    def test_topic_similarity_low_for_unrelated(self):
        a = "the safety of agent systems in production environments and alignment"
        b = "the history of medieval architecture and cathedral construction in france"
        sim = topic_similarity(a, b)
        self.assertLess(sim, 0.3,
                        f"unrelated topics should have low topic similarity (got {sim:.3f})")


if __name__ == "__main__":
    unittest.main()
