#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_product_lens_notion import build_payload, obsidian_open_uri


class ObsidianLinkTests(unittest.TestCase):
    def test_obsidian_open_uri_encodes_vault_relative_path(self) -> None:
        vault_root = Path("/tmp/PKOS")
        note_path = vault_root / "30-Projects" / "Link Test" / "Verdicts" / "2026-04-29-Link Test-verdict.md"

        self.assertEqual(
            obsidian_open_uri(note_path, vault_root),
            "obsidian://open?vault=PKOS&file="
            "30-Projects%2FLink%20Test%2FVerdicts%2F2026-04-29-Link%20Test-verdict.md",
        )

    def test_build_payload_uses_obsidian_uri_for_source_note_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_root = Path(tmp) / "PKOS"
            note_path = vault_root / "30-Projects" / "Link Test" / "Verdicts" / "2026-04-29-Link Test-verdict.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(
                """---
type: verdict
source: product-lens
created: 2026-04-29
tags: [product-lens, verdict, link-test]
quality: 2
citations: 0
related: []
status: active
producer_intent: repo_reprioritize
decision: focus
confidence: medium
project: Link Test
---

# Link Test Verdict

## Recommendation
- focus

## Biggest Risk
- Link encoding broke.

## Next Actions
- Keep Obsidian links clickable.
""",
                encoding="utf-8",
            )

            _, props, state_record = build_payload(note_path, vault_root)

        self.assertEqual(
            props["source_note_path"],
            "obsidian://open?vault=PKOS&file="
            "30-Projects%2FLink%20Test%2FVerdicts%2F2026-04-29-Link%20Test-verdict.md",
        )
        self.assertEqual(
            state_record["note_path"],
            "30-Projects/Link Test/Verdicts/2026-04-29-Link Test-verdict.md",
        )


if __name__ == "__main__":
    unittest.main()
