#!/usr/bin/env python3
"""Unit tests for simhash_dedup.py."""

import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from simhash_dedup import (
    HAMMING_THRESHOLD,
    RETENTION_DAYS,
    _fnv1a_64,
    _ngrams_from_tokens,
    _tokenize,
    SeenStore,
    SimHash,
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def test_tokenize_lowercase():
    assert _tokenize("Hello World") == ["hello", "world"]


def test_tokenize_strips_punctuation():
    assert _tokenize("foo, bar! baz.") == ["foo", "bar", "baz"]


def test_tokenize_drops_non_alnum():
    # _tokenize splits on non-alphanumeric; punctuation is discarded (not kept).
    # "hello@world.com" → ["hello", "world", "com"] (at-sign splits the token).
    # underscore is alphanumeric → foo_bar is kept as one token.
    assert _tokenize("hello@world.com foo_bar") == ["hello", "world", "com", "foo_bar"]


def test_tokenize_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


# ---------------------------------------------------------------------------
# N-grams
# ---------------------------------------------------------------------------

def test_ngrams_small_text_returns_whole():
    # _ngrams_from_tokens joins tokens with space, then slices character n-grams.
    # "hello" (5 chars) with n=3 → ["hel", "ell", "llo"].
    assert _ngrams_from_tokens(["hello"]) == ["hel", "ell", "llo"]


def test_ngrams_trigram():
    tokens = _tokenize("abcd")
    # "abcd" with n=3 → char n-grams: "abc", "bcd"
    ngrams = _ngrams_from_tokens(tokens, n=3)
    assert ngrams == ["abc", "bcd"]


# ---------------------------------------------------------------------------
# FNV-1a (reference check)
# ---------------------------------------------------------------------------

def test_fnv1a_deterministic():
    h1 = _fnv1a_64("hello")
    h2 = _fnv1a_64("hello")
    assert h1 == h2
    assert h1 != _fnv1a_64("world")


# ---------------------------------------------------------------------------
# SimHash core
# ---------------------------------------------------------------------------

def test_identical_text_same_fingerprint():
    s = SimHash()
    fp1 = s.fingerprint("hello world")
    fp2 = s.fingerprint("hello world")
    assert fp1 == fp2
    assert fp1 != 0


def test_hamming_distance_zero():
    s = SimHash()
    fp = s.fingerprint("hello world")
    assert s.hamming(fp, fp) == 0


def test_hamming_distance_max():
    """Opposite fingerprints differ on every bit."""
    fp1 = 0
    fp2 = (1 << 64) - 1
    assert SimHash.hamming(fp1, fp2) == 64


def test_distinct_high_hamming():
    """Completely unrelated texts should have Hamming distance > threshold."""
    s = SimHash(threshold=HAMMING_THRESHOLD)
    fp1 = s.fingerprint(
        "machine learning artificial intelligence neural networks"
    )
    fp2 = s.fingerprint("recipe chocolate cake baking oven flour sugar")
    dist = s.hamming(fp1, fp2)
    assert dist > HAMMING_THRESHOLD, (
        f"Expected Hamming > {HAMMING_THRESHOLD}, got {dist}. "
        "Either these texts are near-dups (unlikely) or the algorithm is broken."
    )


def test_near_dup_low_hamming():
    """Title reordering of the same concept should be near-dup."""
    s = SimHash(threshold=HAMMING_THRESHOLD)
    fp1 = s.fingerprint("AI breakthrough changes everything in technology sector")
    fp2 = s.fingerprint("AI breakthrough technology sector changes everything in")
    dist = s.hamming(fp1, fp2)
    assert dist <= HAMMING_THRESHOLD, (
        f"Expected Hamming <= {HAMMING_THRESHOLD}, got {dist}. "
        "Reordered titles of same content should be near-dups."
    )


def test_title_weight_dominates():
    """Same content, different title — should NOT be near-dup due to title weight."""
    s = SimHash(threshold=HAMMING_THRESHOLD)
    fp1 = s.weighted_fingerprint(
        "Claude AI announces new model",
        "The model achieves state of the art results on multiple benchmarks "
        "including reasoning and code generation. It also demonstrates improved "
        "safety properties compared to previous versions.",
        title_weight=3.0,
    )
    fp2 = s.weighted_fingerprint(
        "OpenAI releases competitor model",
        "The model achieves state of the art results on multiple benchmarks "
        "including reasoning and code generation. It also demonstrates improved "
        "safety properties compared to previous versions.",
        title_weight=3.0,
    )
    dist = s.hamming(fp1, fp2)
    assert dist > HAMMING_THRESHOLD, (
        f"Expected Hamming > {HAMMING_THRESHOLD}, got {dist}. "
        "Same body but different title should NOT be near-dups (title weight 3x)."
    )


def test_is_near_dup_true():
    s = SimHash(threshold=3)
    fp1 = s.fingerprint("open source llm inference engine")
    fp2 = s.fingerprint("open source inference llm engine")
    assert s.is_near_dup(fp1, fp2)


def test_is_near_dup_false():
    s = SimHash(threshold=3)
    fp1 = s.fingerprint("machine learning")
    fp2 = s.fingerprint("baking a cake recipe")
    assert not s.is_near_dup(fp1, fp2)


def test_combine_fingerprints_equals_weighted():
    s = SimHash()
    fp = s.combine_fingerprints(
        "Test Title", "Test content for the document body"
    )
    fp2 = s.weighted_fingerprint(
        "Test Title", "Test content for the document body", title_weight=3.0
    )
    assert fp == fp2


# ---------------------------------------------------------------------------
# SeenStore persistence
# ---------------------------------------------------------------------------

def _tmp_dir():
    return Path(tempfile.mkdtemp(prefix="simhash_test_"))


def test_seen_store_persistence():
    """Write 3 fingerprints, reopen, confirm all readable."""
    tmp = _tmp_dir()
    try:
        store = SeenStore(tmp)
        s = SimHash()

        fp1 = s.fingerprint("article one about AI")
        fp2 = s.fingerprint("article two about ML")
        fp3 = s.fingerprint("article three about RL")

        store.add("id1", fp1)
        store.add("id2", fp2)
        store.add("id3", fp3)

        # Re-open store at same path (simulates new scan run)
        store2 = SeenStore(tmp)
        assert store2.count() == 3
        assert store2.is_seen(fp1)
        assert store2.is_seen(fp2)
        assert store2.is_seen(fp3)
        # Unseen fingerprint
        assert not store2.is_seen(s.fingerprint("unseen article"))
    finally:
        shutil.rmtree(tmp)


def test_seen_store_near_dup_cross_run():
    """New scan sees near-dups from previous scan via combined fingerprint."""
    tmp = _tmp_dir()
    try:
        store = SeenStore(tmp)
        s = SimHash()

        # Same title + body (identical) → combined fingerprint is identical
        fp_old = s.combine_fingerprints(
            "AI breakthrough announcement changes everything",
            "A major AI breakthrough changes the entire technology landscape. "
            "Researchers announce new capabilities that reshape the field."
        )
        store.add("old-id", fp_old)

        store2 = SeenStore(tmp)
        # Identical title + body → exact match → always seen
        fp_new = s.combine_fingerprints(
            "AI breakthrough announcement changes everything",
            "A major AI breakthrough changes the entire technology landscape. "
            "Researchers announce new capabilities that reshape the field."
        )
        assert store2.is_seen(fp_new), (
            "Exact title+body match from a previous scan should be detected as already-seen."
        )

        # Same title, slightly different body word (near-dup via title weight)
        fp_near = s.combine_fingerprints(
            "AI breakthrough announcement changes everything",
            "A major AI breakthrough changes the entire technology arena. "
            "Researchers announce new capabilities that reshape the field."
        )
        assert store2.is_seen(fp_near), (
            "Near-dup (one body word changed) from previous scan should be detected "
            "as already-seen via combined title-weight fingerprint."
        )
    finally:
        shutil.rmtree(tmp)


def test_seen_store_age_pruning():
    """With mocked timestamps spanning 100 days, prune leaves only <= 90 days."""
    tmp = _tmp_dir()
    try:
        store = SeenStore(tmp, retention_days=RETENTION_DAYS)
        s = SimHash()

        now = time.time()
        DAY = 86400

        # Entry 100 days old (well past retention) → pruned
        fp_old = s.fingerprint("old article one")
        store.add("old1", fp_old, ts=now - 100 * DAY)

        # Entry 30 days old (within retention) → kept
        fp_recent = s.fingerprint("recent article")
        store.add("recent", fp_recent, ts=now - 30 * DAY)

        # Entry 1 day old (within retention) → kept
        fp_new = s.fingerprint("new article")
        store.add("new", fp_new, ts=now - 1 * DAY)

        assert store.count() == 3

        removed = store.prune_expired()
        assert removed == 1, f"Expected 1 entry pruned (100-day-old), got {removed}"

        # Re-open and verify
        store2 = SeenStore(tmp, retention_days=RETENTION_DAYS)
        assert store2.count() == 2
        assert store2.is_seen(fp_old) is False, "100-day-old entry should have been pruned"
        assert store2.is_seen(fp_recent) is True, "30-day-old entry should be kept"
        assert store2.is_seen(fp_new) is True, "1-day-old entry should be kept"
    finally:
        shutil.rmtree(tmp)


def test_seen_store_is_seen_threshold():
    """is_seen() uses the threshold parameter to control Hamming distance sensitivity."""
    tmp = _tmp_dir()
    try:
        store = SeenStore(tmp)
        s = SimHash()

        fp = s.fingerprint(
            "machine learning inference engine open source high performance"
        )
        store.add("id1", fp)

        # Exact match (same fingerprint) — seen regardless of threshold
        assert store.is_seen(fp, threshold=0)
        assert store.is_seen(fp, threshold=3)
        assert store.is_seen(fp, threshold=10)

        # Unseen fingerprint — threshold=0 should reject (Hamming > 0)
        fp_other = s.fingerprint(
            "an entirely different article about baking chocolate cake recipes"
        )
        assert not store.is_seen(fp_other, threshold=0)
        assert not store.is_seen(fp_other, threshold=3)
    finally:
        shutil.rmtree(tmp)
