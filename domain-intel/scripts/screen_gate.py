#!/usr/bin/env python3
"""Stage 1 screening gate for insight-analyzer.

Implements the two-stage confidence gate for domain-intel content screening:
  Proceed to Stage 2 (deep analysis) if:
    confidence >= threshold  OR  keyword_relevance >= 0.7

Otherwise, emit a dropped[] entry with reason="low-confidence-screen".

Migration helpers:
  - category_to_confidence(): maps legacy categorical labels to numeric defaults
    (strong → 0.85, weak → 0.55, noise → 0.15)

Threshold source (DP-A3):
  - Read from ~/.claude/personal-os.yaml domain_intel.screen_threshold
  - Fallback: DEFAULT_THRESHOLD = 0.6
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 0.6  # DP-A3 default
RELEVANCE_THRESHOLD = 0.7  # DP-A5 complement threshold

# Category → numeric mapping (legacy categorical → numeric, for migration)
CATEGORY_MAP = {
    "strong": 0.85,
    "weak": 0.55,
    "noise": 0.15,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ScreenDecision:
    """Structured result of the Stage 1 screening gate."""

    action: str          # "proceed" or "drop"
    reason: str | None  # None if proceeding; "low-confidence-screen" if dropped
    confidence: float
    keyword_relevance: float
    threshold: float


# ---------------------------------------------------------------------------
# Core gate logic
# ---------------------------------------------------------------------------

def should_proceed_to_stage_2(
    confidence: float,
    keyword_relevance: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    """Return True if the item should proceed to Stage 2 deep analysis.

    Decision rule (DP-A5 — complement gate):
      Proceed if:
        - confidence >= threshold   OR
        - keyword_relevance >= 0.7 (RELEVANCE_THRESHOLD)

      Drop if both conditions fail.
    """
    return confidence >= threshold or keyword_relevance >= RELEVANCE_THRESHOLD


def screen_decision(
    confidence: float,
    keyword_relevance: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> ScreenDecision:
    """Return a structured ScreenDecision for the Stage 1 gate."""
    if should_proceed_to_stage_2(confidence, keyword_relevance, threshold):
        return ScreenDecision(
            action="proceed",
            reason=None,
            confidence=confidence,
            keyword_relevance=keyword_relevance,
            threshold=threshold,
        )
    return ScreenDecision(
        action="drop",
        reason="low-confidence-screen",
        confidence=confidence,
        keyword_relevance=keyword_relevance,
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def category_to_confidence(category: str) -> float:
    """Map a legacy categorical signal_strength to numeric confidence.

    For use in migration/testing when an existing categorical score needs
    conversion to the new numeric confidence scale.
    """
    return CATEGORY_MAP.get(category.lower(), 0.0)


# ---------------------------------------------------------------------------
# Threshold loader
# ---------------------------------------------------------------------------

def load_threshold(config_path: str | None = None) -> float:
    """Load screen threshold from personal-os.yaml if present.

    Falls back to DEFAULT_THRESHOLD (0.6) if:
      - config file does not exist
      - domain_intel.screen_threshold is not set
      - file is invalid YAML
    """
    import yaml
    from pathlib import Path

    if config_path:
        cfg_file = Path(config_path)
    else:
        cfg_file = Path.home() / ".claude" / "personal-os.yaml"

    if not cfg_file.exists():
        return DEFAULT_THRESHOLD

    try:
        data = yaml.safe_load(cfg_file.read_text()) or {}
        domain_intel = data.get("domain_intel", {})
        threshold = domain_intel.get("screen_threshold", DEFAULT_THRESHOLD)
        return float(threshold)
    except (yaml.YAMLError, ValueError, OSError):
        return DEFAULT_THRESHOLD
