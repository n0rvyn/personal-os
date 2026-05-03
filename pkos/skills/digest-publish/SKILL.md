---
name: digest-publish
description: "Internal sub-skill of pkos:digest. Publishes digest to Notion Weekly Review and pushes reminder notifications. Invoked by /digest after digest-render."
model: sonnet
allowed-tools:
  - Read
  - Write
  - Bash
---

## Overview

Sub-skill 3 of 3 for the digest pipeline. Reads the rendered digest markdown, optionally creates a Notion Weekly Review entry (for weekly type), and pushes high-priority reminders via mactools.

## Arguments

- `--type TYPE`: daily (default) or weekly
- `--date DATE`: target date (default: today, YYYY-MM-DD)

## Process

### Step 1: Read rendered digest
Read `~/Obsidian/PKOS/60-Digests/{date}.md`. If the file does not exist, output `[digest-publish] ERROR: digest file not found. Run digest-render first.` and stop.

### Step 2: Optional Notion Weekly Review entry
If `--type weekly` and a Weekly Review database exists in Notion, create an entry with the digest summary.

### Step 3: Push notification via mactools
For high-priority items (urgency: high), create a Reminder using the rename-resilient mactools autodetect:
```bash
MACTOOLS_VER=$(ls -1 ~/.claude/plugins/cache/indie-toolkit/mactools/ 2>/dev/null | sort -V | tail -1)
MACTOOLS_BASE=~/.claude/plugins/cache/indie-toolkit/mactools/${MACTOOLS_VER}
[[ -z "$MACTOOLS_VER" ]] && echo "[digest-publish] mactools not installed — skipping reminder push" || \
  "${MACTOOLS_BASE}/skills/reminders/scripts/reminders.sh" create \
    "PKOS: {count} high-priority items need attention" --list "PKOS Inbox"
```

### Step 4: Final report
```
[digest-publish] Done.
  Digest: ~/Obsidian/PKOS/60-Digests/{date}.md
```
