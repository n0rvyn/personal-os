---
name: podcast-transcript
description: This skill should be used when the user invokes "/podcast-transcript", asks to "create a daily podcast", "generate a podcast transcript", "make a TTS-ready podcast", or "produce a spoken daily briefing". Produces a deduped, TTS-ready daily podcast transcript from Personal-OS artifacts with deterministic source selection, dedup state, and history writes.
model: sonnet
allowed-tools:
  - Read
  - Write
  - Bash
  - Skill
---

## Overview

`podcast-transcript` is a user-invocable skill for producing a spoken daily
podcast transcript. The deterministic source planner owns artifact discovery,
metadata normalization, source/topic dedup state, and history lookup. The
writer agent only writes from the preselected topic plan.

## Arguments

- `--date YYYY-MM-DD`: target date; default today.
- `--type daily`: only `daily` is supported. Reject `weekly` until it is added.
- `--max-topics N`: default 4; valid range 1 to 8.
- `--source-file PATH`: optional debug path for a single markdown artifact.
- `--source-window-days N`: default 30; valid range 1 to 365.
- `--topic-window-days N`: default 14; valid range 1 to 365.
- `--dry-run`: print the topic plan and do not dispatch the writer.
- `--keep-scratch`: preserve scratch files for debugging.

## Output Paths

Resolve `vault`, `exchange_dir`, and `scratch_dir` through the local
Personal-OS config used by `podcast_sources.py`.

- Transcript: `{vault}/60-Digests/Podcast/{YYYY-MM}/{date}-daily-podcast.md`
- Manifest: `{vault}/.state/podcast-transcript/manifests/{YYYY-MM}/{date}-daily.json`
- Scratch: `{scratch_dir}/pkos/podcast-transcript/{date}-{run_id}/`

## Process

### Step 1: Validate Arguments

Reject invalid dates, unsupported `--type`, and range violations before reading
source files.

### Step 2: Build Topic Plan

Run the deterministic planner before writer dispatch:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/podcast-transcript/scripts/podcast_sources.py \
  plan --date {date} --type daily --max-topics {max_topics} \
  --source-window-days {source_window_days} \
  --topic-window-days {topic_window_days} \
  {--source-file source_file} \
  {--keep-scratch} \
  --output {scratch_dir}/topic-plan.json
```

If `--dry-run` is present, print `{scratch_dir}/topic-plan.json` and stop here.

### Step 3: Stop On Zero Topics

If the topic plan has zero eligible topics, stop before writer dispatch and
report:

```text
[podcast-transcript] No new topics for {date}; all candidates were duplicate, repeated, or below threshold.
```

### Step 4: Dispatch Writer

Dispatch `pkos:podcast-writer` with the topic plan JSON and the
`excerpt_bundle_path` written by `podcast_sources.py`. The writer receives only
those explicit paths and must not add topics.

### Step 5: Write Transcript And Manifest

Write transcript markdown atomically at:

```text
{vault}/60-Digests/Podcast/{YYYY-MM}/{date}-daily-podcast.md
```

Frontmatter must include:

```yaml
type: podcast-transcript
date: 2026-05-09
episode_id: daily-2026-05-09
topic_keys: []
source_identities: []
transcript_hash: ""
input_artifacts: []
```

Write manifest JSON at:

```text
{vault}/.state/podcast-transcript/manifests/{YYYY-MM}/{date}-daily.json
```

The manifest includes:
- `transcript_path`
- `topic_plan`
- `diagnostics`
- `history_matches`
- selected topic count
- skipped duplicate count

### Step 6: Commit History

After transcript write, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/podcast-transcript/scripts/podcast_sources.py \
  commit --manifest {manifest_path}
```

### Step 7: Report

Report:
- transcript path
- manifest path
- selected topic count
- skipped duplicate count
- nearest historical episodes

## Boundaries

- Do not run audio creation (TTS, encoding, or playback).
- Do not send results to messaging targets (email, Slack, push).
- Do not alter daily digest behavior or output paths.
- Ensure downstream polish and audio steps consume the final transcript only;
  do not make source selection or dedup decisions in downstream steps.
