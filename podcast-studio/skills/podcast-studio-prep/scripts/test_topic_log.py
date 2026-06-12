import unittest, tempfile, os, sys
from pathlib import Path

# Module under test (not yet implemented — import will fail until Task 2-impl)
sys.path.insert(0, str(Path(__file__).parent))
from topic_log import load_topic_log, save_topic_log, append_episode, recent_topic_tags

class TopicLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        self.tmp.close()
        self.path = self.tmp.path = self.tmp.name

    def tearDown(self):
        # Idempotent cleanup: test_load_nonexistent_returns_empty deletes the file
        # in its body to set up the "missing file" scenario, so tearDown must tolerate it.
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_load_nonexistent_returns_empty(self):
        os.unlink(self.path)
        result = load_topic_log(self.path)
        self.assertEqual(result, {"episodes": []})

    def test_save_then_load_roundtrip(self):
        data = {"episodes": [{"date": "2026-05-19", "topics": [{"tag": "swift6", "angle": "技术内核"}]}]}
        save_topic_log(self.path, data)
        loaded = load_topic_log(self.path)
        self.assertEqual(loaded, data)

    def test_append_episode_adds_to_episodes_list(self):
        save_topic_log(self.path, {"episodes": []})
        append_episode(self.path, "2026-05-19", [{"tag": "swift6", "angle": "技术内核"}])
        data = load_topic_log(self.path)
        self.assertEqual(len(data["episodes"]), 1)
        self.assertEqual(data["episodes"][0]["date"], "2026-05-19")

    def test_recent_topic_tags_filters_by_window(self):
        # 10-day window; episodes from 2026-05-09 to 2026-05-19, query window 7
        data = {"episodes": [
            {"date": "2026-05-10", "topics": [{"tag": "old", "angle": "x"}]},
            {"date": "2026-05-15", "topics": [{"tag": "swift6", "angle": "技术内核"}]},
            {"date": "2026-05-19", "topics": [{"tag": "apple-silicon", "angle": "商业影响"}]},
        ]}
        save_topic_log(self.path, data)
        recent = recent_topic_tags(self.path, today="2026-05-19", window_days=7)
        # 7-day window from 2026-05-19 includes 2026-05-13 onwards
        self.assertIn("swift6", recent)
        self.assertIn("apple-silicon", recent)
        self.assertNotIn("old", recent)

if __name__ == "__main__":
    unittest.main()
