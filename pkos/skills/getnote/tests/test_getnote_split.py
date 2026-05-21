"""Tests for contract-aware getnote note splitting (split_getnote_note / write_getnote_split)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import getnote


class SplitGetnoteNoteTests(unittest.TestCase):
    def test_ref_note_with_both_yields_two_parts(self):
        note = {
            "note_type": "ref",
            "ref_content": "引自某书的一段摘抄。",
            "content": "我觉得这段话点出了一个被忽视的问题。",
        }
        parts = getnote.split_getnote_note(note)
        self.assertEqual(len(parts), 2)
        kinds = {p["kind"]: p for p in parts}
        self.assertEqual(kinds["reference"]["subdir"], "50-References")
        self.assertEqual(kinds["reference"]["body"], "引自某书的一段摘抄。")
        self.assertEqual(kinds["idea"]["subdir"], "20-Ideas/观点心得")
        self.assertEqual(kinds["idea"]["body"], "我觉得这段话点出了一个被忽视的问题。")

    def test_pure_highlight_yields_one_reference(self):
        note = {"note_type": "ref", "ref_content": "纯摘抄，没有心得。", "content": ""}
        parts = getnote.split_getnote_note(note)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "reference")
        self.assertEqual(parts[0]["subdir"], "50-References")

    def test_link_note_content_is_reference_not_idea(self):
        # link-type content is GetNote's AI 智能总结 — external distillation, not the user's 心得.
        note = {"note_type": "link", "content": "### 智能总结\n本视频讲了……", "ref_content": ""}
        parts = getnote.split_getnote_note(note)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "reference")

    def test_plain_note_content_is_idea(self):
        note = {"note_type": "plain_text", "content": "我自己记的一个想法。", "ref_content": ""}
        parts = getnote.split_getnote_note(note)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "idea")
        self.assertEqual(parts[0]["subdir"], "20-Ideas/观点心得")

    def test_empty_note_yields_nothing(self):
        self.assertEqual(getnote.split_getnote_note({"note_type": "ref"}), [])


class WriteGetnoteSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_parts_written_and_cross_linked(self):
        note = {
            "note_id": "n1", "title": "AI 煽情警惕", "note_type": "ref",
            "ref_content": "她临终前说：与其苟活，不如主动离开。",
            "content": "这种夸大其词的 AI 鼓吹，我觉得才是最需要警惕的。",
            "tags": [{"name": "得到"}, {"name": "快刀广播站"}],
            "created_at": "2026-05-21 13:16:03",
        }
        counts, processed = getnote.write_getnote_split([note], self.tmp.name)
        self.assertEqual(counts, {"reference": 1, "idea": 1})
        self.assertEqual(processed, ["n1"])
        ref_dir = os.path.join(self.tmp.name, "50-References")
        idea_dir = os.path.join(self.tmp.name, "20-Ideas", "观点心得")
        ref_files = os.listdir(ref_dir)
        idea_files = os.listdir(idea_dir)
        self.assertEqual(len(ref_files), 1)
        self.assertEqual(len(idea_files), 1)
        ref_text = open(os.path.join(ref_dir, ref_files[0]), encoding="utf-8").read()
        idea_text = open(os.path.join(idea_dir, idea_files[0]), encoding="utf-8").read()
        self.assertIn("type: reference", ref_text)
        self.assertIn("type: idea", idea_text)
        self.assertIn("得到", ref_text)  # tags carried
        self.assertIn("created: 2026-05-21", ref_text)
        # cross-linked: each related: list points at the sibling
        self.assertIn("20-Ideas/观点心得", ref_text)
        self.assertIn("50-References", idea_text)

    def test_pure_highlight_writes_only_reference(self):
        note = {"note_id": "n2", "title": "", "note_type": "ref",
                "ref_content": "卡拉马佐夫兄弟的一段引文。", "content": "",
                "created_at": "2026-05-10 09:00:00"}
        counts, processed = getnote.write_getnote_split([note], self.tmp.name)
        self.assertEqual(counts, {"reference": 1, "idea": 0})
        self.assertEqual(processed, ["n2"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "20-Ideas")))


if __name__ == "__main__":
    unittest.main()
