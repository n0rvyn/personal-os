---
name: digest
description: "Internal skill — generates daily/weekly digest. Triggered by Adam cron (daily 7:30am, weekly Sunday 9am). This is the canonical entry point — orchestrates digest-collect → digest-render → digest-publish as a single continuous sequence."
model: sonnet
allowed-tools:
  - Skill
  - Bash
---

## Overview

Thin orchestrator for the 3-step digest pipeline. Runs all three sub-skills sequentially in one invocation. Each sub-skill writes its output to `~/.adam/workflow-output/` for the next step to consume; a single failure mid-sequence leaves intermediate artifacts on disk for debugging.

## Arguments

- `--type TYPE`: daily (default) or weekly — passed through to each sub-skill
- `--date DATE`: target date (default: today) — passed through

## Process

You MUST execute all three sub-skills in sequence. Do NOT stop after step 1 or step 2 — the digest is incomplete until digest-publish has run.

### Step 1: Collect

Invoke `Skill(skill="pkos:digest-collect", args="--type {type} --date {date}")`.
Wait for it to complete. If it fails, abort with the error.

### Step 2: Render

Invoke `Skill(skill="pkos:digest-render", args="--type {type} --date {date}")`.
Wait for it to complete. If it fails, abort with the error (the collected artifact at `~/.adam/workflow-output/digest-collect-{date}.json` is preserved for retry).

### Step 3: Publish

Invoke `Skill(skill="pkos:digest-publish", args="--type {type} --date {date}")`.
Wait for it to complete. If it fails, log the error but report partial success (the digest markdown at `~/Obsidian/PKOS/60-Digests/{date}.md` is already saved).

### Step 4: Report

```
[digest] PKOS {daily|weekly} Digest — {date}
  Stage 1 (collect): done
  Stage 2 (render):  done
  Stage 3 (publish): done | partial — {reason}
  Written to: ~/Obsidian/PKOS/60-Digests/{date}.md
```

## Sub-skill autonomy

Each sub-skill (digest-collect, digest-render, digest-publish) can be invoked directly for debugging or partial re-runs. The orchestrator's only job is to run all three in order and consolidate their reports.
