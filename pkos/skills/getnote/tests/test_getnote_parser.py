#!/usr/bin/env python3

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = ROOT / "pkos" / "skills" / "getnote" / "scripts" / "getnote.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("getnote", SCRIPT_PATH)
getnote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(getnote)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class GetNoteParserTests(unittest.TestCase):
    def test_load_payload_unwraps_data_and_preserves_unwrapped(self):
        wrapped = getnote.load_payload(fixture("save_note.json"))
        self.assertEqual(wrapped["note_id"], "note-save-001")

        unwrapped = {"notes": [{"id": "legacy-001"}]}
        self.assertEqual(getnote.load_payload(json.dumps(unwrapped)), unwrapped)

    def test_parse_topics_uses_topic_id_and_nested_stats(self):
        topics = getnote.parse_topics(fixture("list_topics.json"))
        self.assertEqual(topics[0]["topic_id"], "topic-001")
        self.assertEqual(topics[0]["name"], "PKOS Knowledge")
        self.assertEqual(topics[0]["description"], "Writable knowledge base for PKOS notes.")
        self.assertEqual(topics[0]["note_count"], 42)

    def test_parse_recall_filters_external_by_default(self):
        filtered = getnote.parse_recall_results(fixture("recall.json"))
        self.assertEqual([item["note_type"] for item in filtered], ["NOTE", "FILE"])

        all_results = getnote.parse_recall_results(fixture("recall.json"), include_external=True)
        self.assertEqual([item["note_type"] for item in all_results], ["NOTE", "FILE", "BLOGGER", "LIVE"])

    def test_parse_blogger_contents_uses_official_post_fields(self):
        contents = getnote.parse_blogger_contents(fixture("blogger_contents.json"))
        self.assertEqual(contents[0]["post_id_alias"], "post-alias-001")
        self.assertEqual(contents[0]["post_title"], "Official blogger post")
        self.assertEqual(contents[0]["post_summary"], "Short blogger summary.")
        self.assertEqual(contents[0]["post_create_time"], "2026-05-06T20:00:00+08:00")

    def test_parse_lives_reads_detail_without_name_title_confusion(self):
        lives = getnote.parse_lives(fixture("live_detail.json"))
        self.assertEqual(lives[0]["live_id"], "live-001")
        self.assertEqual(lives[0]["name"], "Product Strategy Replay")
        self.assertEqual(lives[0]["post_title"], "Live replay title")
        self.assertEqual(lives[0]["post_summary"], "Processed live summary.")

    def test_format_note_summary_prefers_note_id_and_falls_back_to_id(self):
        self.assertEqual(getnote.format_note_summary({"note_id": "official", "id": "legacy"})["note_id"], "official")
        self.assertEqual(getnote.format_note_summary({"id": "legacy"})["note_id"], "legacy")

    def test_parse_save_response_extracts_data_note_id(self):
        self.assertEqual(getnote.parse_save_response(fixture("save_note.json")), "note-save-001")

    def test_parse_note_tasks_extracts_task_fields(self):
        tasks = getnote.parse_note_tasks(fixture("async_task.json"))
        self.assertEqual(tasks[0]["task_id"], "task-001")
        self.assertEqual(tasks[0]["note_id"], "note-save-001")
        self.assertEqual(tasks[0]["status"], "processing")
        self.assertEqual(tasks[0]["progress"], "45")

    def test_parse_note_detail_reads_note_and_original_content_fields(self):
        detail = getnote.parse_note_detail(fixture("note_detail.json"))
        self.assertEqual(detail["note_id"], "note-detail-001")
        self.assertEqual(detail["audio_original"], "https://cdn.biji.com/audio/original.m4a")
        self.assertEqual(detail["audio_transcription"], "Audio transcription text.")
        self.assertEqual(detail["web_page_content"], "Cleaned web page content.")

    def test_parse_upload_token_extracts_first_token_and_rejects_empty_list(self):
        token = getnote.parse_upload_token(fixture("upload_token.json"))
        self.assertEqual(token["host"], "https://oss-upload.example.com")
        self.assertEqual(token["access_url"], "https://cdn.biji.com/images/demo.png")

        with self.assertRaises(ValueError):
            getnote.parse_upload_token(json.dumps({"success": True, "data": {"tokens": []}}))

    def test_top_level_notes_regression_for_internal_callers(self):
        payload = json.dumps({"notes": [{"id": "legacy-note-001", "title": "Legacy", "tags": []}]})
        filtered = getnote.filter_notes_by_tag(payload, "pkos-synced")
        self.assertEqual(filtered[0]["id"], "legacy-note-001")

        proc = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "summarize-notes"],
            input=payload,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn('"note_id": "legacy-note-001"', proc.stdout)

    def test_cli_commands_emit_non_empty_output_for_official_fixtures(self):
        commands = [
            ("parse-topics", "list_topics.json"),
            ("parse-contents", "blogger_contents.json"),
            ("parse-lives", "live_detail.json"),
            ("parse-save-response", "save_note.json"),
            ("parse-note-tasks", "async_task.json"),
            ("parse-note-detail", "note_detail.json"),
            ("parse-upload-token", "upload_token.json"),
        ]
        for command, fixture_name in commands:
            with self.subTest(command=command):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), command],
                    input=fixture(fixture_name),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertTrue(proc.stdout.strip())


if __name__ == "__main__":
    unittest.main()
