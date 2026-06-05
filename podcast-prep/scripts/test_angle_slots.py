import unittest, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from angle_slots import DEFAULT_ANGLES, pick_unused_angle

class AngleSlotsTests(unittest.TestCase):
    def test_default_angles_are_four_framing_angles(self):
        # Per Phase-4 D-006 (supersedes original 5-angle quota): 反对意见 dropped from
        # the forced rotation — earned via contrarian_source, not a mandatory slot.
        self.assertEqual(len(DEFAULT_ANGLES), 4)
        self.assertIn("技术内核", DEFAULT_ANGLES)
        self.assertNotIn("反对意见", DEFAULT_ANGLES)

    def test_pick_unused_angle_returns_first_unused(self):
        # Topic seen with 技术内核 and 商业影响 → next unused is 用户体验
        result = pick_unused_angle(used_angles=["技术内核", "商业影响"])
        self.assertEqual(result, "用户体验")

    def test_pick_unused_angle_all_used_returns_oldest(self):
        # All angles used → rotate back to first (rotation policy: oldest-first)
        result = pick_unused_angle(used_angles=DEFAULT_ANGLES)
        self.assertEqual(result, DEFAULT_ANGLES[0])

    def test_pick_unused_angle_empty_returns_first(self):
        result = pick_unused_angle(used_angles=[])
        self.assertEqual(result, DEFAULT_ANGLES[0])

if __name__ == "__main__":
    unittest.main()
