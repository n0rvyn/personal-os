#!/usr/bin/env python3
"""Score a YouTube episode on 6 quality dimensions.

Ports youtube-scout 6-dimension scoring (DP-002, DP-A7):
  - transcript_density: words per minute (weight 25)
  - freshness: days since publish with decay (weight 20)
  - originality: informational score (weight 20) — informational only; LLM defers
  - depth: transcript word count + structure (weight 15)
  - signal_to_noise: filler-word ratio (weight 10)
  - credibility: channel sub threshold + view count (weight 10)

Aggregate: weighted_total (0-100) → bucketed significance 1-5.

Usage:
    python3 score_episode.py --input /tmp/candidate.json --transcript "Hello world..."
    python3 score_episode.py --video-id dQw4w9WgXcQ --title "Title" --published "2026-04-01" --transcript "..."

Output: JSON with sub-scores and significance (1-5).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weights (must sum to 100)
WEIGHT_TRANSCRIPT_DENSITY = 25
WEIGHT_FRESHNESS = 20
WEIGHT_ORIGINALITY = 20
WEIGHT_DEPTH = 15
WEIGHT_SIGNAL_TO_NOISE = 10
WEIGHT_CREDIBILITY = 10

assert WEIGHT_TRANSCRIPT_DENSITY + WEIGHT_FRESHNESS + WEIGHT_ORIGINALITY + WEIGHT_DEPTH + WEIGHT_SIGNAL_TO_NOISE + WEIGHT_CREDIBILITY == 100

# Filler words for signal-to-noise detection
FILLER_PATTERNS = [
    r"\blike\b", r"\bso\b", r"\bjust\b", r"\bbasically\b",
    r"\bactually\b", r"\bliterally\b", r"\bhonestly\b",
    r"\banyway\b", r"\bwell\b", r"\bokay\b", r"\bright\b",
    r"\bumm+\b", r"\bahm+\b", r"\buhm+\b",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EpisodeMetadata:
    video_id: str
    title: str
    published: str  # ISO date string
    channel_name: str = ""
    subscriber_count: Optional[int] = None  # not always available
    view_count: Optional[int] = None
    transcript: str = ""


@dataclass
class ScoringResult:
    video_id: str
    transcript_density: float          # 0-1
    freshness: float                   # 0-1
    originality: float                 # 0-1 (informational only; returns 0.5 default)
    depth: float                      # 0-1
    signal_to_noise: float            # 0-1 (1 = low filler)
    credibility: float                 # 0-1
    weighted_total: float             # 0-100
    significance: int                  # 1-5
    notes: list[str]


# ---------------------------------------------------------------------------
# Dimension 1: Transcript Density (words per minute)
# ---------------------------------------------------------------------------

def score_transcript_density(transcript: str, video_duration_minutes: float = 30) -> float:
    """Score based on words per minute in transcript.

    Ideal: ~150-180 wpm for a well-spoken talk.
    > 200 wpm: possible ASR artifacts or very fast speech → penalize.
    < 80 wpm: very sparse captions / poor transcription.
    """
    if not transcript:
        return 0.0

    words = len(transcript.split())

    # If we don't know duration, estimate from word count
    # (this is approximate — video_duration_minutes is passed from discovery)
    if video_duration_minutes <= 0:
        # Estimate: assume average 150 wpm
        video_duration_minutes = max(1, words / 150)

    wpm = words / video_duration_minutes

    # Score: peak at 150 wpm, penalize below and above
    if wpm <= 0:
        return 0.0
    elif wpm < 80:
        return 0.3 + (wpm / 80) * 0.2  # 0.3 to 0.5
    elif wpm < 150:
        return 0.5 + ((wpm - 80) / 70) * 0.3  # 0.5 to 0.8
    elif wpm <= 200:
        return 0.8 + ((wpm - 150) / 50) * 0.2  # 0.8 to 1.0
    else:
        # Too fast — possible ASR artifact
        return max(0.0, 1.0 - (wpm - 200) / 100)


# ---------------------------------------------------------------------------
# Dimension 2: Freshness (days since publish, decay)
# ---------------------------------------------------------------------------

def score_freshness(published_str: str, now: Optional[datetime] = None) -> float:
    """Score freshness with exponential decay.

    0 days → 1.0
    7 days → 0.9
    30 days → 0.7
    90 days → 0.5
    > 180 days → 0.1
    """
    now = now or datetime.now(timezone.utc)

    try:
        # Try parsing common formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                pub = datetime.strptime(published_str[:19], fmt.replace("%z", ""))
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            # Fallback: try fromisoformat
            pub = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
    except Exception:
        # Unknown format — neutral score
        return 0.5

    days = max(0, (now - pub).days)
    # Exponential decay: score = exp(-days / 90)
    decay = math.exp(-days / 90)
    return min(1.0, max(0.0, decay))


# ---------------------------------------------------------------------------
# Dimension 3: Originality (informational score)
# ---------------------------------------------------------------------------

def score_originality(transcript: str) -> float:
    """Score informational/originality based on transcript content.

    This is informational-only (not LLM-based). Returns a default 0.5
    because LLM-based originality scoring is deferred to the insight-analyzer stage.

    Future extension: could detect known-formats (tutorial structure, listicles)
    and penalize, but that requires LLM-level analysis.
    """
    if not transcript:
        return 0.0

    # Informational signals: technical terms, citations, specific numbers, etc.
    tech_terms = len(re.findall(
        r"\b(paper|research|study|algorithm|framework|protocol|"
        r"benchmark|architecture|implementation|"
        r"\d+[\.,]\d+%?|0x[0-9a-fA-F]+)\b",
        transcript.lower()
    ))
    word_count = len(transcript.split())
    if word_count == 0:
        return 0.0

    # Normalize: ~10+ technical references per 1000 words = high originality
    density = tech_terms / (word_count / 1000)
    return min(1.0, density / 20)


# ---------------------------------------------------------------------------
# Dimension 4: Depth (word count + structure)
# ---------------------------------------------------------------------------

def score_depth(transcript: str) -> float:
    """Score depth based on word count and structural signals.

    - < 500 words: shallow (0.0-0.3)
    - 500-2000 words: medium (0.3-0.7)
    - 2000-5000 words: deep (0.7-0.9)
    - 5000+ words: very deep (0.9-1.0)
    Plus structural bonuses for numbered lists, "first/second/third", etc.
    """
    if not transcript:
        return 0.0

    words = len(transcript.split())
    lower = transcript.lower()

    # Word count score
    if words < 500:
        word_score = words / 500 * 0.3
    elif words < 2000:
        word_score = 0.3 + ((words - 500) / 1500) * 0.4
    elif words < 5000:
        word_score = 0.7 + ((words - 2000) / 3000) * 0.2
    else:
        word_score = 0.9 + min(0.1, (words - 5000) / 10000)

    # Structure bonus
    structure_signals = [
        r"\b(first|second|third|fourth|fifth)\b",
        r"\b\d+\.\s+\w",  # numbered lists "1. Something"
        r"\b(step|phase|chapter|section)\s+\d+",
        r"\bsummary|conclusion|introduction|overview\b",
    ]
    structure_count = sum(len(re.findall(p, lower)) for p in structure_signals)
    structure_bonus = min(0.1, structure_count * 0.02)

    return min(1.0, word_score + structure_bonus)


# ---------------------------------------------------------------------------
# Dimension 5: Signal-to-Noise (filler word ratio)
# ---------------------------------------------------------------------------

def score_signal_to_noise(transcript: str) -> float:
    """Score signal-to-noise: high filler word ratio → low score.

    Filler words: like, so, just, basically, actually, literally, honestly, etc.
    Returns: 1.0 = no filler (high signal), 0.0 = all filler.
    """
    if not transcript:
        return 0.0

    filler_count = 0
    for pattern in FILLER_PATTERNS:
        filler_count += len(re.findall(pattern, transcript.lower()))

    words = len(transcript.split())
    if words == 0:
        return 0.0

    filler_ratio = filler_count / words

    # Score: 0 filler ratio → 1.0; > 5% filler → 0.0
    score = 1.0 - (filler_ratio / 0.05)
    return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------
# Dimension 6: Credibility (channel + view signals)
# ---------------------------------------------------------------------------

def score_credibility(
    subscriber_count: Optional[int] = None,
    view_count: Optional[int] = None,
) -> float:
    """Score credibility based on channel size and engagement.

    Signals:
    - subscriber_count >= 100K → high credibility signal
    - view_count: relative to subscriber count (engagement ratio)
    """
    if subscriber_count is None and view_count is None:
        # No data — neutral
        return 0.5

    score = 0.0

    # Subscriber score (log scale)
    if subscriber_count is not None:
        if subscriber_count >= 1_000_000:
            score += 0.5
        elif subscriber_count >= 100_000:
            score += 0.4
        elif subscriber_count >= 10_000:
            score += 0.2
        elif subscriber_count >= 1_000:
            score += 0.1
        # < 1K subscribers: 0 extra score

    # Engagement score (views / subscribers ratio)
    if subscriber_count and subscriber_count > 0 and view_count is not None:
        ratio = view_count / subscriber_count
        if ratio > 10:
            score += 0.2
        elif ratio > 2:
            score += 0.1
        # Low ratio: no bonus

    # View-only heuristic: very high absolute views suggest credibility
    elif view_count is not None:
        if view_count >= 1_000_000:
            score += 0.3
        elif view_count >= 100_000:
            score += 0.2
        elif view_count >= 10_000:
            score += 0.1

    return min(1.0, score)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def compute_significance(weighted_total: float) -> int:
    """Bucket weighted_total (0-100) to significance 1-5.

    0-20   → 1
    20-40  → 2
    40-60  → 3
    60-80  → 4
    80-100 → 5
    """
    if weighted_total <= 20:
        return 1
    elif weighted_total <= 40:
        return 2
    elif weighted_total <= 60:
        return 3
    elif weighted_total <= 80:
        return 4
    else:
        return 5


def score_episode(
    metadata: EpisodeMetadata,
    video_duration_minutes: float = 30,
) -> ScoringResult:
    """Compute all 6 sub-scores and aggregate significance for an episode."""
    t_density = score_transcript_density(metadata.transcript, video_duration_minutes)
    freshness = score_freshness(metadata.published)
    originality = score_originality(metadata.transcript)
    depth = score_depth(metadata.transcript)
    s2n = score_signal_to_noise(metadata.transcript)
    cred = score_credibility(metadata.subscriber_count, metadata.view_count)

    weighted = (
        t_density * WEIGHT_TRANSCRIPT_DENSITY +
        freshness * WEIGHT_FRESHNESS +
        originality * WEIGHT_ORIGINALITY +
        depth * WEIGHT_DEPTH +
        s2n * WEIGHT_SIGNAL_TO_NOISE +
        cred * WEIGHT_CREDIBILITY
    )

    significance = compute_significance(weighted)

    notes = []
    if metadata.subscriber_count and metadata.subscriber_count >= 1_000_000:
        notes.append("high-subscriber channel")
    if video_duration_minutes >= 60:
        notes.append("long-form content")
    if len(metadata.transcript.split()) >= 5000:
        notes.append("high-depth transcript")

    return ScoringResult(
        video_id=metadata.video_id,
        transcript_density=round(t_density, 4),
        freshness=round(freshness, 4),
        originality=round(originality, 4),
        depth=round(depth, 4),
        signal_to_noise=round(s2n, 4),
        credibility=round(cred, 4),
        weighted_total=round(weighted, 2),
        significance=significance,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score YouTube episode on 6 quality dimensions")
    parser.add_argument("--video-id", help="Video ID (required if not using --input)")
    parser.add_argument("--title", default="")
    parser.add_argument("--published", help="Publish date: YYYY-MM-DD or ISO 8601 (required if not using --input)")
    parser.add_argument("--transcript", default="", help="Full transcript text")
    parser.add_argument("--channel-name", default="")
    parser.add_argument("--subscriber-count", type=int, default=None)
    parser.add_argument("--view-count", type=int, default=None)
    parser.add_argument("--duration-minutes", type=float, default=30)
    parser.add_argument("--input", help="JSON file with episode metadata")
    parser.add_argument("--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    if args.input:
        data = json.loads(Path(args.input).read_text())
        # Support both dict and list
        if isinstance(data, list):
            data = data[0] if data else {}
        metadata = EpisodeMetadata(
            video_id=data.get("video_id", ""),
            title=data.get("title", ""),
            published=data.get("published", ""),
            channel_name=data.get("channel_name", ""),
            subscriber_count=data.get("subscriber_count"),
            view_count=data.get("view_count"),
            transcript=data.get("transcript", ""),
        )
        dur = data.get("duration_minutes", 30)
    else:
        if not args.video_id or not args.published:
            parser.error("--video-id and --published are required when not using --input")
        metadata = EpisodeMetadata(
            video_id=args.video_id,
            title=args.title,
            published=args.published,
            channel_name=args.channel_name,
            subscriber_count=args.subscriber_count,
            view_count=args.view_count,
            transcript=args.transcript,
        )
        dur = args.duration_minutes

    result = score_episode(metadata, video_duration_minutes=dur)

    output = {
        "video_id": result.video_id,
        "youtube_scoring": {
            "transcript_density": result.transcript_density,
            "freshness": result.freshness,
            "originality": result.originality,
            "depth": result.depth,
            "signal_to_noise": result.signal_to_noise,
            "credibility": result.credibility,
            "weighted_total": result.weighted_total,
            "significance": result.significance,
            "notes": result.notes,
        },
    }

    s = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(s, encoding="utf-8")
    else:
        print(s)


if __name__ == "__main__":
    main()
