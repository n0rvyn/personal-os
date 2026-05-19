import unittest, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from minhash_check import shingle_4gram, jaccard_similarity, max_jaccard_against

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

if __name__ == "__main__":
    unittest.main()
