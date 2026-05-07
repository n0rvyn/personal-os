#!/usr/bin/env python3

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
PARSER = ROOT / "pkos" / "skills" / "getnote" / "scripts" / "getnote.py"
FIXTURES = ROOT / "pkos" / "skills" / "getnote" / "tests" / "fixtures"
DOC_PATHS = [
    ROOT / "pkos" / "skills" / "inbox" / "SKILL.md",
    ROOT / "pkos" / "skills" / "getnote-intel" / "SKILL.md",
    ROOT / "pkos" / "skills" / "getnotes-sync" / "DESIGN.md",
    ROOT / "pkos" / "agents" / "ripple-compiler.md",
    ROOT / "pkos" / "config" / "pkos-config.template.yaml",
]


def read_all_docs():
    return "\n".join(path.read_text(encoding="utf-8") for path in DOC_PATHS)


class DownstreamDocsTests(unittest.TestCase):
    def test_stale_getnote_contract_strings_are_absent(self):
        docs = read_all_docs()
        stale = [
            "Authorization: Bearer",
            "https://api.getnotes.cn",
            "response.notes",
            "response.has_more",
        ]
        for value in stale:
            with self.subTest(value=value):
                self.assertNotIn(value, docs)
        self.assertIsNone(re.search(r"\bn\.id\b|\bn\.created_at\b", docs))

    def test_inline_python_blocks_import_os_when_using_os_environ(self):
        docs = read_all_docs()
        blocks = re.findall(r"```(?:bash|python)?\n(.*?)```", docs, flags=re.DOTALL)
        for block in blocks:
            if "os.environ" not in block:
                continue
            with self.subTest(block=block[:80]):
                has_import = re.search(r"(^|\n)\s*import\s+[^\\n]*\bos\b", block) or "from os import" in block
                self.assertTrue(has_import, block)

    def test_downstream_docs_use_tested_parser_commands(self):
        docs = read_all_docs()
        for command in ["parse-topics", "parse-bloggers", "parse-contents", "parse-lives", "parse-note-tasks"]:
            with self.subTest(command=command):
                self.assertIn(command, docs)
        self.assertIn("GETNOTE_PARSER", docs)

    def test_parser_commands_work_against_downstream_fixtures(self):
        cases = [
            ("parse-topics", "list_topics.json", "topic-001"),
            ("parse-contents", "blogger_contents.json", "post-alias-001"),
            ("parse-lives", "live_detail.json", "live-001"),
            ("parse-save-response", "save_note.json", "note-save-001"),
            ("parse-note-tasks", "async_task.json", "task-001"),
            ("parse-topic-notes", "list_notes.json", "note-001"),
        ]
        for command, fixture_name, expected in cases:
            with self.subTest(command=command):
                proc = subprocess.run(
                    [sys.executable, str(PARSER), command],
                    input=(FIXTURES / fixture_name).read_text(encoding="utf-8"),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertIn(expected, proc.stdout)

    def test_normalized_terms_are_present_in_downstream_docs(self):
        docs = read_all_docs()
        for value in [
            "LAST_CURSOR",
            "data.topics",
            "data.notes",
            "topic_id",
            "post_id_alias",
            "post_title",
            "post_summary",
            "Authorization: {api_key}",
            "tag name -> writable knowledge base",
            "cursor mode stores GetNote cursor",
        ]:
            with self.subTest(value=value):
                self.assertIn(value, docs)


if __name__ == "__main__":
    unittest.main()
