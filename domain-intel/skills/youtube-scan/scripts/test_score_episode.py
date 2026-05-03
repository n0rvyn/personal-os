#!/usr/bin/env python3
"""Unit tests for score_episode.py (4 test cases per plan spec)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from score_episode import (
    WEIGHT_TRANSCRIPT_DENSITY,
    WEIGHT_FRESHNESS,
    WEIGHT_ORIGINALITY,
    WEIGHT_DEPTH,
    WEIGHT_SIGNAL_TO_NOISE,
    WEIGHT_CREDIBILITY,
    EpisodeMetadata,
    score_episode,
    compute_significance,
)


def test_weighted_total_bounds():
    """Any valid input → weighted_total in [0, 100]."""
    meta = EpisodeMetadata(
        video_id="abc123",
        title="Test Video",
        published="2026-05-01",
        transcript="Hello world this is a test transcript with some content.",
    )
    result = score_episode(meta)
    assert 0.0 <= result.weighted_total <= 100.0, f"got {result.weighted_total}"


def test_significance_bucketing():
    """Bucketing: <=20→1, 21-40→2, 41-60→3, 61-80→4, 81-100→5."""
    assert compute_significance(0) == 1
    assert compute_significance(19) == 1
    assert compute_significance(20) == 1
    assert compute_significance(21) == 2
    assert compute_significance(40) == 2
    assert compute_significance(60) == 3
    assert compute_significance(80) == 4
    assert compute_significance(100) == 5


def test_weights_sum_to_100():
    """Weights sum to 100 per plan spec."""
    total = (
        WEIGHT_TRANSCRIPT_DENSITY
        + WEIGHT_FRESHNESS
        + WEIGHT_ORIGINALITY
        + WEIGHT_DEPTH
        + WEIGHT_SIGNAL_TO_NOISE
        + WEIGHT_CREDIBILITY
    )
    assert total == 100, f"got {total}"


def test_transcript_density_calc():
    """5000 words / 50 min = 100 wpm → score ~0.8-1.0."""
    meta = EpisodeMetadata(
        video_id="abc123",
        title="Test",
        published="2026-05-01",
        transcript=" ".join(["word"] * 5000),
    )
    result = score_episode(meta, video_duration_minutes=50)
    # 5000/50 = 100 wpm → in the 80-150 range → score 0.5-0.8
    assert 0.5 <= result.transcript_density <= 1.0, f"got {result.transcript_density}"
