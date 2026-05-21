---
name: vault-reorg
description: "Reorganize the PKOS vault after a bulk ingest — retag notes that don't classify by domain, dedup exact content-duplicates, and clear stale directories. Triggered via /pkos vault-reorg."
model: sonnet
allowed-tools:
  - Read
  - Bash
  - Glob
---

## Overview

A one-shot vault cleanup, run after the getnote backfill and the 99-Obsidian
migration have landed all their notes. Three mechanical passes, all reversible
(everything removed goes to `.trash/`, nothing is deleted):

- **retag** — a note whose `tags` + `topics` do not resolve to a domain falls to
  `general` in podcast cross-domain recall, i.e. it is invisible to recall. Each
  such note is classified by its content + parent directory, and the resulting
  domain is **appended** to its tags (existing tags are preserved).
- **dedup** — the getnote export and the 99-Obsidian migration each captured some
  of the same material. Exact body-content duplicates are collapsed to one copy;
  the rest move to `.trash/dedup-removed/`.
- **cleanup** — the stale `00-Inbox/getnote/` tree (a defunct getnote integration,
  superseded by the `getnote-import` skill) moves to `.trash/stale-getnote-inbox/`;
  empty directories are removed.

## Arguments

- `--dry-run` — report what would change without writing. Run first.
- `--only retag|dedup|cleanup` — run a single pass (default: all three).

## Process

### Step 1: Dry-run

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/vault-reorg/scripts/reorg.py" --dry-run
```

Present the report: retag counts (scanned / unclassified / tagged, with the
per-domain breakdown), dedup (duplicate groups / removed copies), cleanup (stale
notes relocated / empty dirs). The per-domain retag breakdown is what unstarves
cross-domain recall — confirm it looks right with the user.

### Step 2: Reorganize

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/vault-reorg/scripts/reorg.py"
```

### Step 3: Report

Present the final summary and the three `.trash/` locations (`dedup-removed/`,
`stale-getnote-inbox/`). Note that `90-Podcasts/` was renamed to
`90-Productions/Podcasts/` — if an empty `90-Podcasts/` directory remains, the user
may remove it.

## Notes

- retag is conservative: it only **appends** a domain tag, never removes or
  replaces existing tags, and only acts on notes that do not already classify.
- dedup matches **exact** body content (whitespace-normalized). Near-duplicates are
  left alone.
- domain classification logic (`scripts/domain_classify.py`) is bundled inside this
  skill — the retag pass has no cross-plugin dependency. The same keyword tables
  inform podcast-prep's cross-domain recall.
