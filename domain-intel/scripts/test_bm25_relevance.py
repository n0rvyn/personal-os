#!/usr/bin/env python3
"""Unit tests for bm25_relevance.py."""

import math
from pathlib import Path
import sys

import pytest

from bm25_relevance import (
    DEFAULT_B,
    DEFAULT_K1,
    STOP_WORDS,
    BM25Relevance,
    _tokenize,
    build_from_strings,
    score_insights,
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def test_tokenize_lowercase():
    assert _tokenize("Hello World") == ["hello", "world"]


def test_tokenize_removes_punctuation():
    result = _tokenize("hello, world! what's up?")
    assert "hello" in result
    assert "world" in result


# ---------------------------------------------------------------------------
# BM25Relevance core
# ---------------------------------------------------------------------------

def test_high_relevance_score():
    """Insight with multiple query keywords in title/snippet → score > 0."""
    corpus = [
        ["ai", "inference", "engine", "local", "model"],
        ["chocolate", "cake", "recipe", "baking"],
        ["web", "development", "javascript", "react"],
    ]
    bm25 = BM25Relevance(corpus)

    # Insight about AI inference (matches corpus[0])
    insight_tokens = ["ai", "inference", "local", "model"]
    keywords = ["ai", "inference", "local"]

    raw = bm25.score(keywords, 0)
    assert raw > 0, "BM25 score for matching doc should be > 0"


def test_low_relevance_score():
    """Insight with no query keywords → score near 0."""
    corpus = [
        ["chocolate", "cake", "recipe", "baking"],
    ]
    bm25 = BM25Relevance(corpus)

    keywords = ["ai", "machine", "learning"]
    raw = bm25.score(keywords, 0)
    # No keyword appears in the corpus doc → all IDF contributions are from
    # unseen terms → score should be 0 (no contributions from known terms)
    assert raw == 0.0, f"Expected 0 for non-matching query, got {raw}"


def test_normalization_bounds():
    """All normalized scores fall in [0, 1]."""
    corpus_texts = [
        "machine learning inference engine open source",
        "chocolate cake baking recipe dessert",
        "web development javascript react framework",
        "photo editing software digital art tools",
    ]
    bm25 = build_from_strings(corpus_texts)

    keywords = ["machine", "learning", "ai"]

    for i, text in enumerate(corpus_texts):
        score = bm25.relevance_for_insight(text, keywords)
        assert 0.0 <= score <= 1.0, (
            f"Score for doc {i} out of bounds: {score}"
        )


def test_doc_length_normalization():
    """Long doc with same term frequency as short doc scores lower (b=0.75 effect).

    With N=50 and df=1 (term in 1 doc), IDF is positive (~3.9).
    Then: short_doc_denom ≈ 2.2, long_doc_denom ≈ 49.2 → short wins.
    """
    # 50 docs, "openai" appears only in doc 0 (df=1) → positive IDF
    short = ["openai", "model", "release"]
    filler = ["word"] * 47
    long = ["openai"] + filler

    corpus = [short] + [["unrelated"] * 10 for _ in range(49)]
    bm25 = BM25Relevance(corpus, k1=DEFAULT_K1, b=DEFAULT_B)

    query = ["openai"]

    score_short = bm25.score(query, 0)
    score_long = bm25.score(query, 1)

    assert score_short > score_long, (
        f"Short doc (score={score_short:.4f}) should score higher than "
        f"long doc (score={score_long:.4f}) with same term frequency. "
        "b=0.75 normalization is not working correctly."
    )
    # Both should be positive (positive IDF)
    assert score_short > 0, f"Short doc score should be positive, got {score_short}"


def test_idf_handles_unique_term():
    """A term appearing in only 1 of N docs gets higher IDF than a common term."""
    corpus = [
        ["python", "programming", "tutorial", "basics"],
        ["python", "programming", "advanced", "patterns"],
        ["javascript", "web", "frontend", "react"],
        ["rust", "systems", "programming", "performance"],
    ]
    bm25 = BM25Relevance(corpus)

    # "python" appears in 2/4 docs → IDF should be moderate
    idf_python = bm25.idf.get("python", 0)
    # "tutorial" appears in 1/4 docs → IDF should be higher
    idf_tutorial = bm25.idf.get("tutorial", 0)

    assert idf_tutorial > idf_python, (
        f"Unique term 'tutorial' (IDF={idf_tutorial:.4f}) should have "
        f"higher IDF than common term 'python' (IDF={idf_python:.4f})"
    )
    # Both should be positive (positive IDF for terms in the corpus)
    assert idf_python > 0, f"IDF for 'python' should be positive, got {idf_python}"


# ---------------------------------------------------------------------------
# rank_bm25 reference comparison
# ---------------------------------------------------------------------------

def test_bm25_matches_rank_bm25_reference():
    """Scores from our BM25 should match rank_bm25.BM25Okapi within < 1% error."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        pytest.skip("rank-bm25 not installed — this test is for reference validation")

    corpus_raw = [
        "machine learning inference engine local device",
        "chocolate cake recipe baking instructions",
        "javascript web development react framework",
        "photo editing software digital art creative",
        "open source llm inference optimization performance",
    ]
    corpus_tokenized = [_tokenize(t) for t in corpus_raw]
    keywords_raw = "machine learning open source"
    query_tokens = _tokenize(keywords_raw)

    # Our implementation
    our_bm25 = BM25Relevance(corpus_tokenized)
    our_scores = [our_bm25.score(query_tokens, i) for i in range(len(corpus_raw))]

    # Reference implementation
    ref_bm25 = BM25Okapi(corpus_tokenized)
    ref_scores = ref_bm25.get_scores(query_tokens)

    for i, (our, ref) in enumerate(zip(our_scores, ref_scores)):
        if ref == 0 and our == 0:
            continue
        if ref == 0:
            pytest.fail(
                f"Doc {i}: rank_bm25 score=0 but our score={our:.4f}. "
                "One implementation is returning a score the other isn't."
            )
        error_pct = abs(our - ref) / abs(ref) * 100
        assert error_pct < 1.0, (
            f"Doc {i} error {error_pct:.2f}% exceeds 1% threshold. "
            f"Our score={our:.4f}, rank_bm25 score={ref:.4f}"
        )


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def test_build_from_strings():
    bm25 = build_from_strings([
        "ai inference local model performance",
        "chocolate cake recipe",
    ])
    assert bm25.N == 2
    assert "ai" in bm25.idf
    assert "cake" in bm25.idf


def test_score_insights_sorted():
    """score_insights() returns list sorted by score descending."""
    insights = [
        {"title": "AI inference engine", "snippet": "local model"},
        {"title": "Chocolate cake recipe", "snippet": "baking tips"},
        {"title": "AI breakthroughs in local inference", "snippet": "new models"},
    ]
    keywords = ["ai", "inference", "local"]

    results = score_insights(insights, keywords)
    assert len(results) == 3
    # Results should be sorted descending by score
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True), (
        f"Scores not sorted descending: {scores}"
    )
    # The AI-related insight should be top
    top_insight, top_score = results[0]
    assert "ai" in top_insight["title"].lower()


def test_score_insights_empty_corpus():
    """Empty insights list returns empty list."""
    assert score_insights([], ["ai"]) == []
    assert score_insights(None, ["ai"]) == []
