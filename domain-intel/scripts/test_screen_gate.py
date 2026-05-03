#!/usr/bin/env python3
"""Unit tests for screen_gate.py."""

import pytest

from screen_gate import (
    DEFAULT_THRESHOLD,
    RELEVANCE_THRESHOLD,
    ScreenDecision,
    category_to_confidence,
    screen_decision,
    should_proceed_to_stage_2,
)


# ---------------------------------------------------------------------------
# should_proceed_to_stage_2
# ---------------------------------------------------------------------------

def test_low_confidence_low_relevance_drops():
    """confidence=0.3, BM25=0.2, threshold=0.6 → drop."""
    assert should_proceed_to_stage_2(
        confidence=0.3,
        keyword_relevance=0.2,
        threshold=0.6,
    ) is False


def test_high_confidence_proceeds():
    """confidence=0.8, BM25=0 → proceed (confidence alone is enough)."""
    assert should_proceed_to_stage_2(
        confidence=0.8,
        keyword_relevance=0.0,
        threshold=0.6,
    ) is True


def test_high_relevance_overrides_low_confidence():
    """confidence=0.4, BM25=0.85 → proceed (complement rule)."""
    assert should_proceed_to_stage_2(
        confidence=0.4,
        keyword_relevance=0.85,
        threshold=0.6,
    ) is True


def test_threshold_from_config():
    """confidence=0.5, BM25=0, threshold=0.4 → proceed (boundary >=)."""
    assert should_proceed_to_stage_2(
        confidence=0.5,
        keyword_relevance=0.0,
        threshold=0.4,
    ) is True


def test_boundary_threshold_exact():
    """confidence=0.6, BM25=0, threshold=0.6 → proceed (>= boundary)."""
    assert should_proceed_to_stage_2(
        confidence=0.6,
        keyword_relevance=0.0,
        threshold=0.6,
    ) is True


def test_boundary_relevance_exact():
    """confidence=0.1, BM25=0.7, threshold=0.6 → proceed (>= 0.7)."""
    assert should_proceed_to_stage_2(
        confidence=0.1,
        keyword_relevance=0.7,
        threshold=0.6,
    ) is True


def test_both_just_below_threshold():
    """confidence=0.59, BM25=0.69 → drop (neither condition met)."""
    assert should_proceed_to_stage_2(
        confidence=0.59,
        keyword_relevance=0.69,
        threshold=0.6,
    ) is False


# ---------------------------------------------------------------------------
# screen_decision (structured output)
# ---------------------------------------------------------------------------

def test_screen_decision_proceed():
    d = screen_decision(confidence=0.8, keyword_relevance=0.1)
    assert d.action == "proceed"
    assert d.reason is None
    assert d.confidence == 0.8


def test_screen_decision_drop():
    d = screen_decision(confidence=0.3, keyword_relevance=0.2)
    assert d.action == "drop"
    assert d.reason == "low-confidence-screen"
    assert d.confidence == 0.3


def test_screen_decision_drop_fields():
    d = screen_decision(
        confidence=0.45,
        keyword_relevance=0.5,
        threshold=0.6,
    )
    assert d.action == "drop"
    assert d.reason == "low-confidence-screen"
    assert d.confidence == 0.45
    assert d.keyword_relevance == 0.5
    assert d.threshold == 0.6


# ---------------------------------------------------------------------------
# category_to_confidence
# ---------------------------------------------------------------------------

def test_categorical_compat_strong():
    """Legacy 'strong' → 0.85; with threshold 0.6 → action='proceed'."""
    conf = category_to_confidence("strong")
    assert conf == 0.85
    assert should_proceed_to_stage_2(conf, 0.0, DEFAULT_THRESHOLD) is True


def test_categorical_compat_weak():
    """Legacy 'weak' → 0.55; with threshold 0.6 and no keyword → drop."""
    conf = category_to_confidence("weak")
    assert conf == 0.55
    assert should_proceed_to_stage_2(conf, 0.0, DEFAULT_THRESHOLD) is False


def test_categorical_compat_noise():
    """Legacy 'noise' → 0.15; always drops."""
    conf = category_to_confidence("noise")
    assert conf == 0.15
    assert should_proceed_to_stage_2(conf, 0.0, DEFAULT_THRESHOLD) is False


def test_categorical_case_insensitive():
    assert category_to_confidence("STRONG") == 0.85
    assert category_to_confidence("Weak") == 0.55


def test_categorical_unknown():
    assert category_to_confidence("unknown") == 0.0
    assert category_to_confidence("") == 0.0
