---
name: digest-render
description: "Internal sub-skill of pkos:digest. Renders digest markdown from collected JSON. Invoked by /digest after digest-collect."
model: sonnet
allowed-tools:
  - Read
  - Write
  - Glob
  - Bash
  - Skill
---

## Overview

Sub-skill 2 of 3 for the digest pipeline. Reads the collected JSON artifact from `digest-collect`, dispatches `pkos:digest-writer` to compose the digest markdown, and writes the result to `~/Obsidian/PKOS/60-Digests/{date}.md`.

## Arguments

- `--type TYPE`: daily (default) or weekly
- `--date DATE`: target date (default: today, YYYY-MM-DD)

## Process

### Step 1: Read collected artifact
Read `~/.adam/workflow-output/digest-collect-{date}.json`. If the file does not exist, output `[digest-render] ERROR: collect artifact not found. Run digest-collect first.` and stop.

### Step 2: Dispatch digest-writer agent
Dispatch `pkos:digest-writer` agent with the full JSON content. The agent composes a structured digest markdown.

### Step 3: Write to Obsidian daily note
Write or append to `~/Obsidian/PKOS/60-Digests/{date}.md`:

```markdown
---
type: daily
created: {date}
---

# {date}

## PKOS Daily Digest

{content from digest-writer}
```

If the file already exists (manual notes present), append the digest section after existing content.

### Step 4: Report
```
[digest-render] Wrote ~/Obsidian/PKOS/60-Digests/{date}.md
  Artifact: ~/.adam/workflow-output/digest-collect-{date}.json
```
