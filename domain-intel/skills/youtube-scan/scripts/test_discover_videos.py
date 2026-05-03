#!/usr/bin/env python3
"""Unit tests for discover_videos.py (2 test cases per plan spec)."""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock feedparser module BEFORE importing discover_videos
# ---------------------------------------------------------------------------

_mock_feed = MagicMock(name="feedparser")

# Pre-populate sys.modules so discover_videos sees our mock
sys.modules["feedparser"] = _mock_feed

from discover_videos import discover_channel_videos


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _MockFeed:
    def __init__(self, entries):
        self.entries = entries
        self.bozo = False
        self.bozo_exception = None


def _make_entry(published_days_ago: int, video_id: str = "abc123XYZ000") -> MagicMock:
    """Create a mock feedparser entry with publish date `days_ago`."""
    ts = time.gmtime(time.time() - published_days_ago * 86400)
    entry = MagicMock()
    entry.published_parsed = ts
    entry.yt_videoid = video_id
    entry.title = f"Video {video_id}"
    entry.get = lambda k, default="": {"link": f"https://www.youtube.com/watch?v={video_id}"}.get(k, default)
    return entry


def _make_feed(entries: list) -> MagicMock:
    """Return a feedparser.parse mock that returns a feed with `entries`."""
    return MagicMock(return_value=_MockFeed(entries))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_max_age_filter_drops_old():
    """Published 60 days ago, max_age_days=30 → dropped."""
    old_entry = _make_entry(published_days_ago=60)
    _mock_feed.parse = _make_feed([old_entry])

    videos = discover_channel_videos(
        channel_id="UC123",
        channel_name="Test",
        max_age_days=30,
    )

    assert len(videos) == 0, f"Expected 0 (too old), got {len(videos)}"


def test_max_age_filter_keeps_fresh():
    """Published 5 days ago, max_age_days=30 → kept."""
    fresh_entry = _make_entry(published_days_ago=5, video_id="freshVid001")
    _mock_feed.parse = _make_feed([fresh_entry])

    videos = discover_channel_videos(
        channel_id="UC123",
        channel_name="Test",
        max_age_days=30,
    )

    assert len(videos) == 1, f"Expected 1, got {len(videos)}"
    assert videos[0]["video_id"] == "freshVid001"
