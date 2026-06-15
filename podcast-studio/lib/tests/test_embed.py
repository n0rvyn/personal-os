"""Tests for lib/embed.py — embedding interface (Swift helper + cosine + n-gram fallback).

Written before lib/embed.py exists; collection must fail at this
point (`No module named 'lib.embed'`).

Pins:
- cosine: identical vectors → 1.0; orthogonal → 0.0; zero vector → 0.0 (no divide-by-zero)
- ngram_similarity: 2-gram Jaccard; similar Chinese phrases score higher than unrelated;
  empty string safe (→ 0.0, no crash)
- similarity: uses vector cosine when helper available; falls back to n-gram on helper failure
- _helper_available: returns False when swift source/binary not on disk
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.embed import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import embed  # noqa: F401
    assert hasattr(embed, "cosine")
    assert hasattr(embed, "ngram_similarity")
    assert hasattr(embed, "embed_text")
    assert hasattr(embed, "similarity")
    assert hasattr(embed, "_helper_available")


# ---------- cosine ----------

def test_cosine_identical_is_one():
    """A vector compared with itself → cosine == 1.0 (modulo floating-point)."""
    from lib.embed import cosine
    a = [1.0, 0.0, 0.0]
    assert cosine(a, a) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    """Perpendicular unit vectors → cosine == 0.0."""
    from lib.embed import cosine
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_safe():
    """A zero vector must NOT crash with divide-by-zero; returns 0.0."""
    from lib.embed import cosine
    assert cosine([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0
    assert cosine([1.0, 2.0, 3.0], [0.0, 0.0, 0.0]) == 0.0
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


# ---------- ngram_similarity ----------

def test_ngram_jaccard_similar_chinese_higher_than_unrelated():
    """A phrase sharing most bigrams with the target scores higher than
    an unrelated sentence. N-gram Jaccard is the v1 fallback."""
    from lib.embed import ngram_similarity
    target = "1956苏伊士运河危机"
    similar = "苏伊士运河 1956"
    unrelated = "完全无关的句子"

    s_sim = ngram_similarity(target, similar)
    s_unrelated = ngram_similarity(target, unrelated)

    assert s_sim > s_unrelated
    # Both must be in [0, 1]
    assert 0.0 <= s_unrelated <= 1.0
    assert 0.0 <= s_sim <= 1.0


def test_ngram_jaccard_empty_string_safe():
    """Empty input must return 0.0 without crashing (no division by zero,
    no index errors on slicing)."""
    from lib.embed import ngram_similarity
    assert ngram_similarity("", "苏伊士运河") == 0.0
    assert ngram_similarity("苏伊士运河", "") == 0.0
    assert ngram_similarity("", "") == 0.0


# ---------- similarity (vector path) ----------

def test_similarity_uses_helper_when_available():
    """When a fake runner returns valid vectors, similarity must use cosine
    (not n-gram). Inject a runner that records the call and returns
    a known vector pair whose cosine is distinguishable from the n-gram
    score."""
    from lib import embed

    # Two parallel texts whose n-gram similarity is ~0.5
    # but whose injected vectors have cosine == 1.0
    a = "1956苏伊士运河危机"
    b = "完全不相关的中文句子"

    # Sanity check: n-gram path returns < 1.0 for this pair
    ngram = embed.ngram_similarity(a, b)
    assert ngram < 1.0

    # Fake runner: returns a known vector pair (parallel vectors → cosine 1.0)
    fake_vector = [1.0, 0.0, 0.0]
    call_log = []

    def fake_runner(cmd, **kwargs):
        call_log.append((cmd, kwargs))
        # Mimic Swift helper: print vector JSON to stdout
        result = type("R", (), {})()
        result.returncode = 0
        result.stdout = json.dumps({"vector": fake_vector})
        result.stderr = ""
        return result

    score = embed.similarity(a, b, runner=fake_runner)
    # Should have used the helper path → cosine == 1.0 (not n-gram < 1.0)
    assert score == pytest.approx(1.0)
    # And helper was actually invoked (twice — once per text)
    assert len(call_log) == 2


# ---------- similarity (fallback) ----------

def test_similarity_falls_back_on_helper_failure():
    """If the injected runner raises (or returns non-zero), similarity must
    silently fall back to n-gram — NOT raise."""
    from lib import embed

    a = "1956苏伊士运河危机"
    b = "苏伊士运河 1956"

    def failing_runner(cmd, **kwargs):
        # Simulate a non-macOS / missing-swift failure
        raise RuntimeError("swift helper not found")

    # Must not raise
    score = embed.similarity(a, b, runner=failing_runner)
    # Should equal the n-gram fallback score for this pair
    expected = embed.ngram_similarity(a, b)
    assert score == pytest.approx(expected)


def test_similarity_falls_back_on_nonzero_returncode():
    """If the injected runner returns non-zero exit, similarity must fall
    back to n-gram (no raise)."""
    from lib import embed

    a = "1956苏伊士运河危机"
    b = "苏伊士运河 1956"

    def nonzero_runner(cmd, **kwargs):
        result = type("R", (), {})()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "helper error"
        return result

    expected = embed.ngram_similarity(a, b)
    score = embed.similarity(a, b, runner=nonzero_runner)
    assert score == pytest.approx(expected)


# ---------- macOS detection ----------

def test_macos_detection_false_when_helper_missing(tmp_path):
    """_helper_available must return False when the swift source/binary
    does not exist on disk."""
    from lib import embed

    # tmp_path has no tools/embed.swift → helper is unavailable
    result = embed._helper_available(plugin_root=str(tmp_path))
    assert result is False
