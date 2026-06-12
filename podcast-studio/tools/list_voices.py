"""Emit the voice-id list for a provider, parsed live from the voice catalog.

Used by config-studio's dynamicOptions to populate the `tts.host_voice` dropdown
based on the selected `tts.provider`. Parsing the catalog (rather than hardcoding
a list) keeps the catalog markdown the single source of truth.

Contract:
- volc: BARE ids (strip any `volc-` prefix, matching how config stores them),
  taken from the Volcengine section INCLUDING its "NOT verified" sub-table
  (so the BV001_streaming default is selectable).
- minimax: ids keep their `mm-` prefix.
- The "Cross-vendor equivalents" table (prefixed ids) is excluded: the section
  slice stops at the next `## ` header.
- Only the first column of table rows is read, so per-row Resource-Ids / model
  names in later columns are ignored.
"""
import argparse
import json
import re
from pathlib import Path

CATALOG = (
    Path(__file__).resolve().parent.parent
    / "skills" / "podcast-studio-tts" / "references" / "voice-catalog.md"
)

# Match the MAIN provider section (not the "How to add/verify ..." how-to sections).
SECTION_PREFIX = {"volc": "## Volcengine", "minimax": "## MiniMax"}


def _section_lines(text: str, header_prefix: str) -> list[str]:
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            if line.startswith(header_prefix):
                in_section = True
                continue
            if in_section:
                break  # next top-level header ends the section
        if in_section:
            out.append(line)
    return out


def _first_column_ids(section_lines: list[str]) -> list[str]:
    ids: list[str] = []
    for line in section_lines:
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        m = re.search(r"`([^`]+)`", cells[0])  # backtick-wrapped id in the first column
        if m:
            ids.append(m.group(1))
    return ids


def list_voices(provider: str) -> list[str]:
    prefix = SECTION_PREFIX.get(provider)
    if prefix is None:
        return []
    ids = _first_column_ids(_section_lines(CATALOG.read_text(encoding="utf-8"), prefix))
    if provider == "volc":
        ids = [i[len("volc-"):] if i.startswith("volc-") else i for i in ids]
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", required=True)
    args = p.parse_args()
    print(json.dumps(list_voices(args.provider)))


if __name__ == "__main__":
    main()
