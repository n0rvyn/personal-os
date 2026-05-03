#!/usr/bin/env python3
"""Discover recent videos from curated YouTube channels via RSS.

Usage:
    python3 discover_videos.py --config ~/.claude/personal-os.yaml --max-age-days 30

Output:
    stdout: JSON list of candidate videos with metadata
    stderr: diagnostic messages
    Exit codes: 0 = success, 1 = error
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import feedparser


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_youtube_config(config_path: Optional[str] = None) -> dict:
    """Load youtube_channels and youtube_filters from personal-os.yaml."""
    import yaml

    if config_path is None:
        config_path = Path.home() / ".claude" / "personal-os.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        return {}

    data = yaml.safe_load(config_path.read_text()) or {}
    return {
        "channels": data.get("youtube_channels", []),
        "filters": data.get("youtube_filters", {}),
    }


# ---------------------------------------------------------------------------
# RSS discovery
# ---------------------------------------------------------------------------

def discover_channel_videos(
    channel_id: str,
    channel_name: str,
    priority: str = "medium",
    tags: Optional[list[str]] = None,
    max_age_days: int = 30,
) -> list[dict]:
    """Pull RSS feed for a channel and return recent videos within max_age_days."""
    tags = tags or []
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    try:
        feed = feedparser.parse(rss_url)
        if feed.bozo and feed.bozo_exception:
            print(f"[{channel_name}] RSS parse warning: {feed.bozo_exception}", file=sys.stderr)
    except Exception as e:
        print(f"[{channel_name}] RSS fetch failed: {e}", file=sys.stderr)
        return []

    cutoff = datetime.now() - timedelta(days=max_age_days)
    cutoff_ts = cutoff.timestamp()
    cutoff_iso = cutoff.strftime("%Y-%m-%d")

    videos = []
    for entry in feed.entries:
        try:
            published_ts = time.mktime(entry.published_parsed)
        except Exception:
            continue

        if published_ts < cutoff_ts:
            continue

        # Extract video_id from yt_video_id namespace or from the link
        video_id = ""
        if hasattr(entry, "yt_videoid"):
            video_id = entry.yt_videoid
        else:
            import re
            m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", entry.get("link", ""))
            if m:
                video_id = m.group(1)

        if not video_id:
            continue

        # Skip shorts
        if hasattr(entry, "title") and "shorts" in entry.get("link", "").lower():
            continue

        videos.append({
            "video_id": video_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "title": entry.get("title", "Unknown"),
            "published": entry.get("published", ""),
            "published_parsed": published_ts,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "priority": priority,
            "tags": tags,
        })

    return videos


def discover_all_channels(
    config_path: Optional[str] = None,
    max_age_days: Optional[int] = None,
) -> list[dict]:
    """Discover videos from all configured channels."""
    cfg = load_youtube_config(config_path)
    channels = cfg.get("channels", [])
    filters = cfg.get("filters", {})

    if not channels:
        print("No youtube_channels configured in personal-os.yaml", file=sys.stderr)
        return []

    if max_age_days is None:
        max_age_days = filters.get("max_age_days", 30)

    all_videos = []
    for ch in channels:
        channel_id = ch.get("id", "")
        channel_name = ch.get("name", channel_id)
        priority = ch.get("priority", "medium")
        tags = ch.get("tags", [])

        if not channel_id:
            continue

        print(f"[{channel_name}] Discovering recent videos...", file=sys.stderr)
        videos = discover_channel_videos(
            channel_id=channel_id,
            channel_name=channel_name,
            priority=priority,
            tags=tags,
            max_age_days=max_age_days,
        )
        print(f"[{channel_name}] Found {len(videos)} videos in last {max_age_days} days", file=sys.stderr)
        all_videos.extend(videos)

    return all_videos


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Discover recent YouTube videos from curated channels")
    parser.add_argument(
        "--config",
        help="Path to personal-os.yaml (default: ~/.claude/personal-os.yaml)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Max age in days for videos (default: from config or 30)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    videos = discover_all_channels(config_path=args.config, max_age_days=args.max_age_days)

    output = json.dumps(videos, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote {len(videos)} videos to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
