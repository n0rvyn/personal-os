#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_notion_obsidian_links import inspect_link, obsidian_open_uri
from audit_notion_obsidian_links import normalized_property_name


def page_with_props(page_id: str, title: str, extra_props: dict) -> dict:
    props = {
        "Name": {
            "type": "title",
            "title": [{"plain_text": title}],
        }
    }
    props.update(extra_props)
    return {"id": page_id, "properties": props}


def rich_text(value: str) -> dict:
    return {
        "type": "rich_text",
        "rich_text": [{"plain_text": value, "text": {"content": value}}],
    }


def url(value: str) -> dict:
    return {"type": "url", "url": value}


class NotionObsidianLinkAuditTests(unittest.TestCase):
    def test_property_name_normalization_matches_human_labels(self) -> None:
        self.assertEqual(normalized_property_name("Obsidian Link"), normalized_property_name("obsidian_link"))
        self.assertEqual(normalized_property_name("Source-Note Path"), normalized_property_name("source_note_path"))

    def test_obsidian_uri_encodes_slashes_and_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            note_path = vault_root / "10-Knowledge" / "foo bar.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("# Foo Bar\n", encoding="utf-8")

            self.assertEqual(
                obsidian_open_uri(note_path, vault_root, "PKOS"),
                "obsidian://open?vault=PKOS&file=10-Knowledge%2Ffoo%20bar.md",
            )

    def test_absolute_path_is_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            note_path = vault_root / "10-Knowledge" / "foo bar.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("# Foo Bar\n", encoding="utf-8")
            page = page_with_props("page-1", "Foo Bar", {"obsidian_link": url(str(note_path))})

            finding = inspect_link("db-1", page, "obsidian_link", vault_root, "PKOS")

        self.assertEqual(finding.status, "repairable")
        self.assertEqual(
            finding.repaired_value,
            "obsidian://open?vault=PKOS&file=10-Knowledge%2Ffoo%20bar.md",
        )

    def test_unencoded_obsidian_uri_is_repairable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            note_path = vault_root / "10-Knowledge" / "foo bar.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("# Foo Bar\n", encoding="utf-8")
            page = page_with_props(
                "page-1",
                "Foo Bar",
                {"obsidian_link": url("obsidian://open?vault=PKOS&file=10-Knowledge/foo bar.md")},
            )

            finding = inspect_link("db-1", page, "obsidian_link", vault_root, "PKOS")

        self.assertEqual(finding.status, "repairable")
        self.assertEqual(
            finding.repaired_value,
            "obsidian://open?vault=PKOS&file=10-Knowledge%2Ffoo%20bar.md",
        )

    def test_empty_link_uses_unique_source_note_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            note_path = vault_root / "30-Projects" / "AppA" / "Verdicts" / "2026-04-12-AppA-verdict.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("# AppA Verdict\n", encoding="utf-8")
            page = page_with_props(
                "page-1",
                "AppA Verdict",
                {
                    "source_note_path": url(""),
                    "source_note_id": rich_text("2026-04-12-AppA-verdict"),
                },
            )

            finding = inspect_link("db-1", page, "source_note_path", vault_root, "PKOS")

        self.assertEqual(finding.status, "repairable")
        self.assertEqual(finding.reason, "from_source_note_id")

    def test_missing_current_path_falls_back_to_source_note_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            note_path = vault_root / "30-Projects" / "AppA" / "Verdicts" / "2026-04-12-AppA-verdict.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text("# AppA Verdict\n", encoding="utf-8")
            page = page_with_props(
                "page-1",
                "AppA Verdict",
                {
                    "source_note_path": url("/private/tmp/old-vault/2026-04-12-AppA-verdict.md"),
                    "source_note_id": rich_text("2026-04-12-AppA-verdict"),
                },
            )

            finding = inspect_link("db-1", page, "source_note_path", vault_root, "PKOS")

        self.assertEqual(finding.status, "repairable")
        self.assertEqual(finding.reason, "from_source_note_id_after_missing_current")

    def test_ambiguous_source_note_id_is_not_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp).resolve() / "PKOS"
            for project in ("AppA", "AppB"):
                note_path = vault_root / "30-Projects" / project / "same-note.md"
                note_path.parent.mkdir(parents=True)
                note_path.write_text(f"# {project}\n", encoding="utf-8")
            page = page_with_props(
                "page-1",
                "Same Note",
                {"source_note_path": url(""), "source_note_id": rich_text("same-note")},
            )

            finding = inspect_link("db-1", page, "source_note_path", vault_root, "PKOS")

        self.assertEqual(finding.status, "unresolved")
        self.assertEqual(finding.reason, "ambiguous_source_note_id")


if __name__ == "__main__":
    unittest.main()
