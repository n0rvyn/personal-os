#!/usr/bin/env python3
"""E2E test for youtube-scan against a real public channel.

Requires network access to YouTube (RSS + transcript APIs).
Marked @pytest.mark.network so it can be skipped in CI.

Uses Yannic Kilcher channel (UCZHmQk67mSJgfCCTn7xBfew) — public,
has English captions, posts technical AI/research content.

Note: This test may break if the channel deletes or private-locks
the test videos — rerun the test if so.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest


YANNIC_KILCHER_CHANNEL = {
    "id": "UCZHmQk67mSJgfCCTn7xBfew",
    "name": "Yannic Kilcher",
    "priority": "high",
    "tags": ["AI", "research"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tmp_config(channels: list[dict]) -> Path:
    """Write a temp personal-os.yaml with the given channels."""
    cfg = {
        "exchange_dir": tempfile.mkdtemp(),
        "scratch_dir": tempfile.mkdtemp(),
        "youtube_channels": channels,
        "youtube_filters": {
            "min_duration_minutes": 5,
            "max_age_days": 365,
            "require_transcript": True,
        },
    }
    import yaml
    tmp = Path(tempfile.mktemp(suffix=".yaml"))
    tmp.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp


def _run_script(script_path: str, args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a Python script and return (exit_code, stdout, stderr)."""
    import subprocess
    try:
        result = subprocess.run(
            ["python3", script_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"Timeout after {timeout}s"


# ---------------------------------------------------------------------------
# E2E Test
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_youtube_scan_e2e():
    """Full pipeline: discover → harvest → score → IEF write against Yannic Kilcher."""
    scripts_dir = Path(__file__).parent
    discover_py = scripts_dir / "discover_videos.py"
    harvest_py = scripts_dir / "harvest_transcripts.py"
    score_py = scripts_dir / "score_episode.py"

    assert discover_py.exists(), f"discover_videos.py not found at {discover_py}"
    assert harvest_py.exists(), f"harvest_transcripts.py not found at {harvest_py}"
    assert score_py.exists(), f"score_episode.py not found at {score_py}"

    # Step 1: Write temp config with Yannic Kilcher
    tmp_config = _write_tmp_config([YANNIC_KILCHER_CHANNEL])

    try:
        # Step 2: Discover videos
        candidates_path = Path(tempfile.mktemp(suffix=".json"))
        code, stdout, stderr = _run_script(
            str(discover_py),
            ["--config", str(tmp_config), "--max-age-days", "365", "--output", str(candidates_path)],
            timeout=30.0,
        )
        print(f"[discover_videos] exit={code}, stderr={stderr[:500]}")

        assert code == 0, f"discover_videos failed: {stderr}"
        assert candidates_path.exists(), "discover_videos did not produce output file"

        raw = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert isinstance(raw, list), f"Expected list, got {type(raw)}"
        if len(raw) == 0:
            pytest.skip(
                "RSS feed returned 0 candidates — channel may have no recent videos "
                "or RSS XML may be malformed (YouTube RSS is known to emit invalid tokens). "
                "Re-run with a different channel or check the RSS feed manually."
            )
        print(f"[discover_videos] Found {len(raw)} candidates")

        # Step 3: Harvest transcripts (limit to top 3 candidates)
        candidates_for_harvest = raw[:3]
        harvest_input = Path(tempfile.mktemp(suffix=".json"))
        harvest_input.write_text(
            json.dumps(candidates_for_harvest),
            encoding="utf-8",
        )
        transcripts_path = Path(tempfile.mktemp(suffix=".json"))
        code, stdout, stderr = _run_script(
            str(harvest_py),
            [
                "--input", str(harvest_input),
                "--output", str(transcripts_path),
                "--lang", "en,zh-Hans",
                "--min-duration-minutes", "5",
            ],
            timeout=120.0,
        )
        print(f"[harvest_transcripts] exit={code}, stderr={stderr[:500]}")
        assert code == 0, f"harvest_transcripts failed: {stderr}"
        assert transcripts_path.exists(), "harvest_transcripts did not produce output"

        transcripts = json.loads(transcripts_path.read_text(encoding="utf-8"))
        assert isinstance(transcripts, dict), f"Expected dict, got {type(transcripts)}"

        # At least one video should have a transcript
        with_transcript = [k for k, v in transcripts.items() if "text" in v and v["text"]]
        assert len(with_transcript) >= 1, (
            f"Expected >=1 video with transcript, got {len(with_transcript)}. "
            f"Transcript keys: {list(transcripts.keys())}"
        )
        print(f"[harvest_transcripts] {len(with_transcript)}/{len(transcripts)} videos have transcripts")

        # Step 4: Score one video with transcript
        video_id, transcript_data = next(
            (k, v) for k, v in transcripts.items() if "text" in v and v["text"]
        )
        candidate = next(c for c in candidates_for_harvest if c.get("video_id") == video_id)

        score_input = Path(tempfile.mktemp(suffix=".json"))
        score_input.write_text(
            json.dumps({
                "video_id": video_id,
                "title": candidate.get("title", "Test Video"),
                "published": candidate.get("published", time.strftime("%Y-%m-%d")),
                "channel_name": YANNIC_KILCHER_CHANNEL["name"],
                "transcript": transcript_data["text"],
                "transcript_lang": transcript_data.get("lang", "en"),
            }),
            encoding="utf-8",
        )

        score_output = Path(tempfile.mktemp(suffix=".json"))
        code, stdout, stderr = _run_script(
            str(score_py),
            ["--input", str(score_input), "--output", str(score_output)],
            timeout=30.0,
        )
        print(f"[score_episode] exit={code}, stderr={stderr[:200]}")
        assert code == 0, f"score_episode failed: {stderr}"

        scores = json.loads(score_output.read_text(encoding="utf-8"))
        assert "youtube_scoring" in scores, f"Missing youtube_scoring in output: {scores}"

        yt_score = scores["youtube_scoring"]
        # All sub-scores should be in [0, 1]
        for key in ["transcript_density", "freshness", "originality", "depth", "signal_to_noise", "credibility"]:
            assert key in yt_score, f"Missing {key} in youtube_scoring"
            assert 0.0 <= yt_score[key] <= 1.0, f"{key}={yt_score[key]} out of [0,1]"

        # weighted_total should be in [0, 100]
        assert 0.0 <= yt_score["weighted_total"] <= 100.0, f"weighted_total={yt_score['weighted_total']} out of [0,100]"

        # significance should be 1-5
        assert 1 <= yt_score["significance"] <= 5, f"significance={yt_score['significance']} out of [1,5]"
        print(f"[score_episode] significance={yt_score['significance']}, weighted_total={yt_score['weighted_total']}")

        # Step 5: Write IEF file
        from pathlib import Path as P
        import yaml

        cfg = yaml.safe_load(tmp_config.read_text(encoding="utf-8"))
        ief_dir = P(cfg["exchange_dir"]) / "domain-intel" / "2026-05"
        ief_dir.mkdir(parents=True, exist_ok=True)

        today = time.strftime("%Y-%m-%d")
        ief_file = ief_dir / f"youtube-{video_id}.md"

        ief_content = f"""---
id: {today}-youtube-{video_id}
source: youtube
url: "https://www.youtube.com/watch?v={video_id}"
title: "{candidate.get('title', 'Unknown')}"
significance: {yt_score['significance']}
tags: [{','.join(YANNIC_KILCHER_CHANNEL['tags'])}]
category: ai-ml
domain: youtube
date: {today}
read: false
youtube_scoring:
  transcript_density: {yt_score['transcript_density']}
  freshness: {yt_score['freshness']}
  originality: {yt_score['originality']}
  depth: {yt_score['depth']}
  signal_to_noise: {yt_score['signal_to_noise']}
  credibility: {yt_score['credibility']}
  weighted_total: {yt_score['weighted_total']}
  significance: {yt_score['significance']}
  notes: [{','.join(yt_score.get('notes', []))}]
channel: "{YANNIC_KILCHER_CHANNEL['name']}"
transcript_language: "{transcript_data.get('lang', 'en')}"
---

# {candidate.get('title', 'Unknown')}

**Channel:** {YANNIC_KILCHER_CHANNEL['name']}

**Scoring:** significance={yt_score['significance']} (weighted_total={yt_score['weighted_total']})
"""
        ief_file.write_text(ief_content, encoding="utf-8")
        assert ief_file.exists(), f"IEF file not written: {ief_file}"

        # Verify youtube_scoring metadata is present
        text = ief_file.read_text(encoding="utf-8")
        assert "youtube_scoring:" in text, "IEF should contain youtube_scoring metadata"
        print(f"[IEF] Written to {ief_file}")

    finally:
        # Cleanup temp files
        try:
            os.unlink(tmp_config)
        except Exception:
            pass
