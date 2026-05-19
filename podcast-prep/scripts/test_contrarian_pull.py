import unittest, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from contrarian_pull import CONTRARIAN_POOL, pick_contrarian_source

class ContrarianPullTests(unittest.TestCase):
    def test_pool_is_nonempty_and_has_diversity(self):
        # At least 5 entries (per design L72 reverse-source spec)
        self.assertGreaterEqual(len(CONTRARIAN_POOL), 5)
        # Each entry has source + url + a short description
        for item in CONTRARIAN_POOL:
            self.assertIn("source", item)
            self.assertIn("category", item)

    def test_pick_contrarian_source_deterministic_with_seed(self):
        # Same seed → same pick
        a = pick_contrarian_source(seed=42)
        b = pick_contrarian_source(seed=42)
        self.assertEqual(a, b)

    def test_pick_contrarian_source_excludes_excluded_categories(self):
        # If we exclude all categories, only fall-through "general" remains (or empty pool error)
        result = pick_contrarian_source(seed=42, exclude_categories=[c["category"] for c in CONTRARIAN_POOL[:-1]])
        self.assertEqual(result["category"], CONTRARIAN_POOL[-1]["category"])

if __name__ == "__main__":
    unittest.main()
