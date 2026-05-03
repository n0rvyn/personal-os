---
name: youtube-scan
description: "Scan curated YouTube channels for new transcript-bearing episodes; analyze and write to IEF."
model: sonnet
allowed-tools:
  - Read
  - Bash(python3:*)
  - WebFetch
  - Skill(domain-intel:insight-analyzer)
---

## Overview

Scans curated YouTube channels (via RSS) for recent videos, extracts transcripts using Innertube API + HTML fallback (DP-001 Option A), scores episodes on 6 quality dimensions, analyzes with insight-analyzer, and writes IEF files.

**Channel configuration** lives in `~/.claude/personal-os.yaml` under `youtube_channels:`. See the Required Configuration section below.

## Process

### Step 1: Resolve Paths

```
WD=$(pwd)
SKILL_DIR=$(echo $WD)
SCRIPTS_DIR={path to domain-intel/skills/youtube-scan/scripts/}
```

### Step 2: Discover Videos

Invoke `discover_videos.py`:
```
Bash(command="python3 {SCRIPTS_DIR}/discover_videos.py --config ~/.claude/personal-os.yaml --max-age-days {max_age_days} --output /tmp/yt-candidates.json")
```

Where `max_age_days` is read from `youtube_filters.max_age_days` in config (default: 30).

If `youtube_channels` is empty or not configured:
```
Output: [youtube-scan] No channels configured. Add youtube_channels to ~/.claude/personal-os.yaml.
```
→ stop.

Output: JSON list of candidate videos with `{video_id, channel_id, channel_name, title, published, url, priority, tags}`.

### Step 3: Harvest Transcripts

For each candidate video, invoke `harvest_transcripts.py`:
```
Bash(command="python3 {SCRIPTS_DIR}/harvest_transcripts.py --input /tmp/yt-candidates.json --lang en,zh-Hans --min-duration-minutes {min_duration_minutes} --output /tmp/yt-transcripts.json")
```

Where `min_duration_minutes` is read from `youtube_filters.min_duration_minutes` (default: 15).

Filter candidates with transcripts:
- Load `/tmp/yt-transcripts.json`
- Keep only candidates where the transcript result has no `error` field
- Attach `transcript`, `transcript_lang`, `transcript_segments` to each candidate
- Track: `candidates = N`, `with_transcripts = N`

If `with_transcripts == 0`:
```
Output: [youtube-scan] No transcripts available for {candidates} discovered videos.
```
→ stop.

### Step 4: Score Episodes

For each candidate with transcript, invoke `score_episode.py`:
```
Bash(command="python3 {SCRIPTS_DIR}/score_episode.py --video-id {video_id} --title \"{title}\" --published \"{published_iso}\" --channel-name \"{channel_name}\" --transcript \"{transcript}\" --duration-minutes {duration_minutes}")
```

The script returns JSON with sub-scores and `significance` (1-5). Attach `youtube_scoring` to each candidate.

### Step 5: Invoke insight-analyzer

Dispatch the `domain-intel:insight-analyzer` agent with:
- **items**: each candidate (with `youtube_scoring` in metadata), as source_type `youtube`
- **source_type**: `youtube`
- **domains**: `[{name: "youtube"}]` (or configured domains)
- **significance_threshold**: from config or default 2
- **date**: today's date
- **lens_context**: (if LENS.md exists)

**Two-stage screening:** The insight-analyzer applies Stage 1 (confidence + keyword_relevance gate). Items below threshold are emitted as `dropped[]` with `reason: "low-confidence-screen"`. Only items passing Stage 1 proceed to Stage 2 deep analysis.

### Step 6: Write IEF Files

For each insight from the analyzer with `significance >= significance_threshold`:

1. Resolve output directory: `{exchange_dir}/domain-intel/{YYYY-MM}/`
   ```
   Bash(command="python3 -c \"from pathlib import Path; import yaml; cfg=yaml.safe_load(open('~/.claude/personal-os.yaml')) or {}; ex=cfg.get('exchange_dir','~/Obsidian/PKOS/.exchange'); print(Path(ex).expanduser())\"")
   ```
2. Write IEF file at `{exchange_dir}/domain-intel/{YYYY-MM}/youtube-{video_id}.md`:
```markdown
---
id: {YYYY-MM-DD}-youtube-{seq}
source: youtube
url: "{url}"
title: "{title}"
significance: {N}
tags: [{tags joined by comma}]
category: {category}
domain: {domain}
date: {YYYY-MM-DD}
read: false
youtube_scoring:
  transcript_density: {transcript_density}
  freshness: {freshness}
  originality: {originality}
  depth: {depth}
  signal_to_noise: {signal_to_noise}
  credibility: {credibility}
  weighted_total: {weighted_total}
  significance: {significance}
  notes: [{notes joined by comma}]
channel: "{channel_name}"
transcript_language: "{transcript_lang}"
---

# {title}

{insight.analyzer.output}
```

Track: `stored = N`

### Step 7: Report

```
[domain-intel/youtube-scan] Done — {YYYY-MM-DD}
  Discovered: {candidates} videos from {N} channels
  With transcripts: {with_transcripts}
  Analyzed: {analyzed}
  Stored IEF: {stored}
```

## Required Configuration

Add to `~/.claude/personal-os.yaml`:

```yaml
youtube_channels:
  - id: "UCZHmQk67mSJgfCCTn7xBfew"
    name: "Yannic Kilcher"
    priority: high
    tags: [ai, research]
youtube_filters:
  min_duration_minutes: 15
  max_age_days: 30
  require_transcript: true
```

**Schema per channel** (DP-A7):
- `id`: YouTube channel ID (UC... prefix)
- `name`: Display name
- `priority`: `high` | `medium` | `low` (future: affects scan frequency)
- `tags`: List of topic tags for categorization

## Error Handling

- If no channels configured: warn and stop (don't fail the parent scan)
- If discovery fails: log error, continue with empty list
- If a single video's transcript fails: skip that video, continue with others
- If insight-analyzer fails for a video: skip that video, continue with others
- IEF write failures: log warning, continue with remaining videos

## E2E Test Notes

The E2E test (`test_e2e.py`) runs against real YouTube. Mark it `@pytest.mark.network` so it can be skipped in CI. The test may break if the chosen channel deletes or private-locks the test videos — rerun the test if so.
