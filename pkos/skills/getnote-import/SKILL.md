---
name: getnote-import
description: "Offline bulk import of a GetNote (Get笔记) HTML export archive into the PKOS vault. Use when the user has exported their full getnote history (the API is rate-limited) and wants it backfilled into Obsidian. Triggered via /pkos getnote-import."
model: sonnet
allowed-tools:
  - Read
  - Bash
  - Glob
---

## Overview

The getnote OpenAPI is rate-limited, so a full historical backfill is done by
exporting the whole archive from the getnote app and ingesting it offline. This
skill parses every exported HTML note and writes it into the PKOS vault, following
the vault directory contract.

It is a one-shot bulk backfill. Ongoing incremental sync stays with the `inbox`
skill's getnote source.

## Arguments

Parse from user input:
- `--export-dir DIR`: the unzipped getnote export archive. Default:
  `~/Obsidian/PKOS/Z-Get笔记导出-迁移后删除` (the user's current export drop).
- `--dry-run`: report routing without writing — always run this first.
- `--limit N`: process at most N notes (for a staged run).

## Process

### Step 1: Locate the export

The export archive contains a `notes/` directory of `<hash>.html` files. Confirm it
exists:

```bash
EXPORT_DIR="${EXPORT_DIR:-$HOME/Obsidian/PKOS/Z-Get笔记导出-迁移后删除}"
find "$EXPORT_DIR" -maxdepth 3 -type d -name notes
```

If no `notes/` directory is found, report the path checked and stop.

### Step 2: Dry-run

Always preview routing before writing:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/getnote-import/scripts/import_getnote_export.py" \
  --export-dir "$EXPORT_DIR" --dry-run
```

Present the report to the user: scanned / would-write / skipped counts and the
routing breakdown (`50-References` / `10-Knowledge` / `20-Ideas/观点心得`).

### Step 3: Import

On user confirmation, run the real import:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/getnote-import/scripts/import_getnote_export.py" \
  --export-dir "$EXPORT_DIR" --vault "$HOME/Obsidian/PKOS"
```

The run is idempotent and resumable — every written note carries a `getnote_id`
frontmatter field, and `<vault>/.state/getnote-import-state.yaml` records imported
ids. Re-running skips notes already imported or already present in the vault, so a
staged import (`--limit`) or an interrupted run can simply be re-invoked.

### Step 4: Report

Present the final summary: written count, skipped (dup / no-text), routing
breakdown, and the state file path.

## Routing

The HTML export drops the API `note_type` field (it lives only in the encrypted
`jsonData` block), so routing is inferred from visible HTML structure, faithful to
`99-System/10-Directory-Contract.md`:

| HTML signal | vault destination | `type` |
| --- | --- | --- |
| has a `<blockquote>` excerpt, or a 原文 source link | `50-References/` | `reference` |
| plain note with an `<h1>` title | `10-Knowledge/` | `knowledge` |
| plain note, no title | `20-Ideas/观点心得/` | `idea` |
| image-only note, no text | skipped | — |

A quoted or link-backed note is external-derived material, so it routes to
`50-References/` and never becomes a stance source for podcast recall. Audio (mp3)
and image blobs are ignored — text only.

## Error Handling

- Export directory or `notes/` missing → report the checked path, stop.
- A note file that fails to parse (encoding, malformed HTML) → counted as skipped,
  the run continues.
- Re-runs never duplicate: dedup is by `getnote_id`.

## Notes

- After the import is verified, the export drop
  `~/Obsidian/PKOS/Z-Get笔记导出-迁移后删除` can be deleted by the user (its name
  says so). This skill does not delete it.
- Export HTML format reference: `references/export-format.md`.
