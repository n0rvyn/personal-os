#!/usr/bin/env python3

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
AGENT = ROOT / "pkos" / "agents" / "podcast-writer.md"
SKILL = ROOT / "pkos" / "skills" / "podcast-transcript" / "SKILL.md"
SKILL_README = ROOT / "pkos" / "skills" / "podcast-transcript" / "README.md"
PKOS_README = ROOT / "pkos" / "README.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PodcastWriterContractTests(unittest.TestCase):
    def test_agent_contract_is_read_only_and_grounded(self):
        text = read(AGENT)
        self.assertIn("name: podcast-writer", text)
        self.assertIn("Use this agent when the skill has produced", text)
        self.assertIn("tools: [Read]", text)
        self.assertIn("When to invoke", text)
        for section in ["Opening", "Main stories", "Personal radar", "Closing"]:
            self.assertIn(section, text)
        self.assertIn("No Markdown tables", text)
        self.assertIn("Do not add topics during polish", text)
        self.assertIn("No ungrounded facts outside the topic plan fields", text)
        self.assertIn("topics[].source_excerpts[]", text)
        self.assertIn("topics[].evidence[]", text)
        self.assertIn("topics[].speaker_notes[]", text)
        self.assertIn("# Daily Podcast Transcript: {date}", text)
        self.assertIn("## Source Notes", text)


class PodcastSkillContractTests(unittest.TestCase):
    def test_skill_orchestrates_plan_writer_transcript_manifest_commit(self):
        if not SKILL.exists():
            self.skipTest("Task 3 has not created the skill yet")
        text = read(SKILL)
        self.assertIn("name: podcast-transcript", text)
        self.assertIn("This skill should be used when", text)
        self.assertIn("--date YYYY-MM-DD", text)
        self.assertIn("--dry-run", text)
        self.assertIn("--source-file PATH", text)
        self.assertIn("--source-window-days N", text)
        self.assertIn("--topic-window-days N", text)
        self.assertIn("No new topics for {date}", text)
        self.assertIn("podcast_sources.py", text)
        self.assertRegex(text, re.compile(r"podcast_sources\.py\s+\\\n\s+plan", re.MULTILINE))
        self.assertLess(text.index("plan --date"), text.index("podcast-writer"))
        self.assertLess(text.index("transcript markdown"), text.index("commit --manifest"))
        for forbidden in ["Adam", ".adam", "TTS generation", "channel delivery", "/digest"]:
            self.assertNotIn(forbidden, text)

    def test_documentation_contract(self):
        if not SKILL_README.exists():
            self.skipTest("Task 4 has not created the skill README yet")
        pkos_readme = read(PKOS_README)
        skill_readme = read(SKILL_README)
        self.assertIn("| `/podcast-transcript` |", pkos_readme)
        self.assertIn("TTS-ready transcript", pkos_readme)
        self.assertIn("owns its dedup state", pkos_readme)
        self.assertIn("| Podcast transcript | `/podcast-transcript --type daily`", pkos_readme)
        self.assertIn("{exchange_dir}/domain-intel/", pkos_readme)
        self.assertIn("~/Obsidian/PKOS/60-Digests/Podcast/", pkos_readme)
        self.assertIn("~/Obsidian/PKOS/.state/podcast-transcript/", pkos_readme)
        self.assertIn("Standalone command examples", skill_readme)
        self.assertIn("Input source priority", skill_readme)
        self.assertIn("State file formats", skill_readme)
        self.assertIn("downstream steps consume the final transcript and do not make dedup decisions", skill_readme)
        self.assertIn("does not depend on the runner", skill_readme)
        self.assertNotIn("/tmp", skill_readme)


if __name__ == "__main__":
    unittest.main()
