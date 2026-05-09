#!/usr/bin/env python3

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = ROOT / "pkos" / "skills" / "podcast-transcript" / "scripts" / "podcast_sources.py"

spec = importlib.util.spec_from_file_location("podcast_sources", SCRIPT_PATH)
podcast_sources = importlib.util.module_from_spec(spec)
sys.modules["podcast_sources"] = podcast_sources
spec.loader.exec_module(podcast_sources)


@contextmanager
def temp_env(**updates):
    old = {key: os.environ.get(key) for key in updates}
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def write_md(path: Path, meta: dict, body: str = "# Body\nEvidence text about agents and product direction.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(podcast_sources.render_markdown_frontmatter(meta, body), encoding="utf-8")


class PodcastSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.exchange = self.root / "exchange"
        self.scratch = self.root / "scratch"
        self.vault = self.root / "vault"
        self.home.mkdir()
        self.exchange.mkdir()
        self.scratch.mkdir()
        self.vault.mkdir()
        self.config = self.home / ".claude" / "personal-os.yaml"
        self.pkos_config = self.home / ".claude" / "pkos" / "config.yaml"

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, extra: str = "") -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        text = f"""
exchange_dir: {self.exchange}
scratch_dir: {self.scratch}
pkos:
  vault:
    path: {self.vault}
{extra}
"""
        self.config.write_text(text.strip() + "\n", encoding="utf-8")

    def roots(self):
        return podcast_sources.resolve_roots(self.config, self.pkos_config)

    def test_root_resolution_creates_missing_config_and_default_dirs(self):
        missing_config = self.home / ".claude" / "personal-os.yaml"
        with temp_env(HOME=self.home):
            roots = podcast_sources.resolve_roots(missing_config, self.pkos_config)
        self.assertTrue(missing_config.exists())
        self.assertTrue(roots.exchange_dir.exists())
        self.assertTrue(roots.scratch_dir.exists())
        self.assertEqual(roots.exchange_dir, (self.home / "Obsidian" / "PKOS" / ".exchange").resolve())

    def test_nested_pkos_vault_path_parsing(self):
        self.write_config()
        roots = self.roots()
        self.assertEqual(roots.vault, self.vault.resolve())

    def test_yaml_subset_supports_comments_inline_and_indented_lists(self):
        data = podcast_sources.parse_yaml_subset(
            """
# comment
exchange_dir: "~/Exchange" # trailing
tags: [ai, "agent ops", 3]
pkos:
  vault:
    path: ~/Vault
sources:
  - domain-intel
  - session-reflect
enabled: true
"""
        )
        self.assertEqual(data["tags"], ["ai", "agent ops", 3])
        self.assertEqual(data["sources"], ["domain-intel", "session-reflect"])
        self.assertTrue(data["enabled"])
        self.assertEqual(data["pkos"]["vault"]["path"], "~/Vault")

    def test_frontmatter_parse_and_write_without_pyyaml(self):
        raw = podcast_sources.render_markdown_frontmatter(
            {"title": "Agent Notes", "tags": ["ai", "ops"], "score": 4},
            "# Agent Notes\nBody.",
        )
        meta, body = podcast_sources.parse_markdown_frontmatter(raw)
        self.assertEqual(meta["title"], "Agent Notes")
        self.assertEqual(meta["tags"], ["ai", "ops"])
        self.assertIn("Body.", body)

    def test_identity_derivation_prefers_canonical_url(self):
        path = self.vault / "10-Knowledge" / "note.md"
        identity = podcast_sources.derive_identity({"url": "HTTPS://Example.com/a/?utm=1#frag"}, path, "domain-intel")
        self.assertEqual(identity, "source:url:https://example.com/a")

    def test_product_lens_mapping_uses_intent_decision_evidence_and_confidence(self):
        self.write_config()
        path = self.exchange / "product-lens" / "reprioritize" / "item.md"
        write_md(
            path,
            {
                "producer": "product-lens",
                "intent": "repo_reprioritize",
                "project": "Adam",
                "decision": "focus",
                "confidence": "high",
                "created": "2026-05-09",
                "targets": ["Adam"],
                "source_refs": ["commit:abc"],
            },
            "Main blocker is demand validation.",
        )
        candidate, diagnostic = podcast_sources.normalize_candidate(path, self.roots())
        self.assertIsNone(diagnostic)
        self.assertEqual(candidate.producer, "product-lens")
        self.assertEqual(candidate.significance, 4.0)
        self.assertIn("commit:abc", candidate.evidence)
        self.assertIn("decision: focus", candidate.speaker_notes)

    def test_topic_key_normalization_is_deterministic(self):
        topic = podcast_sources.slugify_topic(["AI", "Agents"], "The AI Agent Platform UX!", "the")
        self.assertEqual(topic, "ai-agents-agent-platform-ux")

    def test_source_file_validation_rejects_outside_markdown(self):
        self.write_config()
        outside = self.root / "outside.md"
        outside.write_text("---\ntitle: Outside\n---\nBody", encoding="utf-8")
        with self.assertRaises(podcast_sources.PodcastSourceError):
            podcast_sources.validate_source_file(outside, self.roots())

    def test_source_window_boundary_keeps_thirty_days_inclusive(self):
        self.write_config()
        sdir = self.vault / ".state" / "podcast-transcript"
        sdir.mkdir(parents=True)
        (sdir / "source-index.jsonl").write_text(
            json.dumps({"source_identity": "source:id:domain:1", "episode_date": "2026-04-09"}) + "\n",
            encoding="utf-8",
        )
        path = self.exchange / "domain-intel" / "2026-05" / "item.md"
        write_md(path, {"id": "1", "source": "domain", "title": "AI Ops", "date": "2026-05-09", "significance": 4})
        candidate, _ = podcast_sources.normalize_candidate(path, self.roots())
        selected, diagnostics, duplicate_count = podcast_sources.select_topics(
            [candidate],
            self.roots(),
            podcast_sources.parse_iso_date("2026-05-09"),
            4,
            30,
            14,
        )
        self.assertEqual(selected, [])
        self.assertEqual(duplicate_count, 1)
        self.assertEqual(diagnostics[0]["reason"], "recent_source_duplicate")

    def test_topic_window_boundary_marks_update_for_new_source(self):
        self.write_config()
        sdir = self.vault / ".state" / "podcast-transcript"
        sdir.mkdir(parents=True)
        (sdir / "topic-index.jsonl").write_text(
            json.dumps({"topic_key": "ai-ops", "episode_date": "2026-04-25"}) + "\n",
            encoding="utf-8",
        )
        path = self.exchange / "domain-intel" / "2026-05" / "new.md"
        write_md(path, {"id": "2", "source": "domain", "title": "AI Ops", "date": "2026-05-09", "significance": 4})
        candidate, _ = podcast_sources.normalize_candidate(path, self.roots())
        candidate.topic_key = "ai-ops"
        selected, _, _ = podcast_sources.select_topics(
            [candidate],
            self.roots(),
            podcast_sources.parse_iso_date("2026-05-09"),
            4,
            30,
            14,
        )
        self.assertEqual(selected[0].novelty, "update")

    def test_bm25_nearest_history_ranks_relevant_episode_first(self):
        rows = [
            {"episode_id": "old-ai", "transcript_body": "agent platform orchestration and permissions"},
            {"episode_id": "old-health", "transcript_body": "nutrition sleep workout energy"},
        ]
        matches = podcast_sources.bm25_nearest("agent permissions platform", rows)
        self.assertEqual(matches[0]["episode_id"], "old-ai")

    def test_corrupt_jsonl_tolerance_and_commit_mode(self):
        self.write_config()
        sdir = self.vault / ".state" / "podcast-transcript"
        sdir.mkdir(parents=True)
        (sdir / "episodes.jsonl").write_text("{broken\n", encoding="utf-8")
        rows, corrupt = podcast_sources.read_jsonl(sdir / "episodes.jsonl")
        self.assertEqual(rows, [])
        self.assertEqual(corrupt, 1)

        transcript = self.vault / "60-Digests" / "Podcast" / "2026-05" / "2026-05-09-daily-podcast.md"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("# Daily Podcast Transcript: 2026-05-09\n\nAgent platform update.", encoding="utf-8")
        manifest = sdir / "manifests" / "2026-05" / "2026-05-09-daily.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "date": "2026-05-09",
                    "type": "daily",
                    "episode_id": "daily-2026-05-09",
                    "transcript_path": str(transcript),
                    "topic_plan": {
                        "topics": [
                            {
                                "topic_key": "agent-platform",
                                "source_identities": ["source:id:domain:2"],
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        with temp_env(PERSONAL_OS_CONFIG=self.config, PKOS_CONFIG=self.pkos_config):
            result = podcast_sources.commit_manifest(manifest)
        self.assertEqual(result["status"], "committed")
        self.assertIn("transcript_hash", json.loads(manifest.read_text(encoding="utf-8")))
        self.assertIn("source:id:domain:2", (sdir / "source-index.jsonl").read_text(encoding="utf-8"))

    def test_cli_plan_source_file_outputs_topic_plan(self):
        self.write_config()
        path = self.exchange / "domain-intel" / "2026-05" / "item.md"
        write_md(path, {"id": "1", "source": "domain-intel", "title": "Agent Platform UX", "date": "2026-05-09", "significance": 4})
        output = self.scratch / "topic-plan.json"
        with temp_env(PERSONAL_OS_CONFIG=self.config, PKOS_CONFIG=self.pkos_config):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "plan",
                    "--date",
                    "2026-05-09",
                    "--type",
                    "daily",
                    "--source-file",
                    str(path),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
        self.assertIn('"topics"', proc.stdout)
        plan = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(plan["topics"][0]["source_identities"], ["source:id:domain-intel:1"])

    def test_weekly_type_is_rejected_until_supported(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            podcast_sources.parse_args(["plan", "--date", "2026-05-09", "--type", "weekly"])

    def test_default_scratch_is_cleaned_and_keep_scratch_preserves_it(self):
        self.write_config()
        path = self.exchange / "domain-intel" / "2026-05" / "item.md"
        write_md(path, {"id": "1", "source": "domain-intel", "title": "Agent Platform UX", "date": "2026-05-09", "significance": 4})

        with temp_env(PERSONAL_OS_CONFIG=self.config, PKOS_CONFIG=self.pkos_config):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "plan",
                    "--date",
                    "2026-05-09",
                    "--type",
                    "daily",
                    "--source-file",
                    str(path),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
        cleaned_plan = json.loads(proc.stdout)
        cleaned_run_dir = Path(cleaned_plan["excerpt_bundle_path"]).parent
        self.assertFalse(cleaned_run_dir.exists())

        with temp_env(PERSONAL_OS_CONFIG=self.config, PKOS_CONFIG=self.pkos_config):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "plan",
                    "--date",
                    "2026-05-09",
                    "--type",
                    "daily",
                    "--source-file",
                    str(path),
                    "--keep-scratch",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
        kept_plan = json.loads(proc.stdout)
        kept_run_dir = Path(kept_plan["excerpt_bundle_path"]).parent
        self.assertTrue(kept_run_dir.exists())
        self.assertTrue((kept_run_dir / "topic-plan.json").exists())
        self.assertTrue((kept_run_dir / "topic-excerpts.json").exists())


if __name__ == "__main__":
    unittest.main()
