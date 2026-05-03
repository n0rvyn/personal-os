---
name: digest-collect
description: "Internal sub-skill of pkos:digest. Gathers raw data (Notion captures, Obsidian notes, domain-intel highlights, signal feedback) for a given date. Writes structured JSON to a temp artifact path. Usually invoked by /digest, but can be invoked directly for debugging."
model: sonnet
allowed-tools:
  - Read
  - Bash
  - Glob
---

## Overview

Sub-skill 1 of 3 for the digest pipeline. Collects raw inputs into a single JSON artifact at `~/.adam/workflow-output/digest-collect-{date}.json` for digest-render to consume.

## Arguments

- `--type TYPE`: daily (default) or weekly
- `--date DATE`: target date (default: today, YYYY-MM-DD)

## Process

### Step 1: Resolve date window
- daily → start_date = date, end_date = date
- weekly → start_date = date - 6, end_date = date

### Step 2: Notion Pipeline DB query
```bash
NO_PROXY="*" python3 ~/.claude/skills/notion-with-api/scripts/notion_api.py query-db \
  32a1bde4-ddac-81ff-8f82-f2d8d7a361d7 \
  --filter '{"property": "Created", "date": {"on_or_after": "{start_date}"}}'
```
Save raw response.

### Step 3: Recent Obsidian notes
```bash
find ~/Obsidian/PKOS/10-Knowledge ~/Obsidian/PKOS/20-Ideas ~/Obsidian/PKOS/50-References \
  -name "*.md" -newer ~/Obsidian/PKOS/.signals/{yesterday}-feedback.yaml 2>/dev/null
```

### Step 4: Domain-intel highlights
```bash
ls ~/Obsidian/PKOS/50-References/domain-intel/{date}* 2>/dev/null
```

### Step 5: Signal feedback
```bash
cat ~/Obsidian/PKOS/.signals/{date}-feedback.yaml 2>/dev/null
```

### Step 6: Write artifact
Write a single JSON file to `~/.adam/workflow-output/digest-collect-{date}.json`:
```json
{
  "date": "{date}",
  "type": "daily|weekly",
  "captures": [...],
  "obsidian_notes": [...],
  "intel_highlights": [...],
  "signals": {...},
  "stats": {"new_captures": N, "intel_count": N, "pending": N}
}
```

### Step 7: Report
```
[digest-collect] Wrote ~/.adam/workflow-output/digest-collect-{date}.json
  Captures: {N}, Obsidian: {N}, Intel: {N}, Signals: {present|absent}
```
