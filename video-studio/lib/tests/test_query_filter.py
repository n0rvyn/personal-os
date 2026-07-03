"""Tests for lib/query_filter.py — sanitize_query (Task 1) + rerank_candidates (Task 2)."""

from __future__ import annotations

from lib.query_filter import _BANNED_VISUAL, rerank_candidates, sanitize_query


def test_sanitize_query_strips_banned_tokens():
    result = sanitize_query("runner coding on laptop")
    lowered = result.lower()
    for banned in ("office", "laptop", "code", "coding", "whiteboard", "screen"):
        assert banned not in lowered
    assert "runner" in lowered


def test_sanitize_query_passthrough_when_clean():
    q = "exhausted runner slowing late race"
    assert sanitize_query(q) == q


def test_sanitize_query_all_banned_falls_back_to_safe_default():
    result = sanitize_query("office desk keyboard")
    assert result
    lowered = result.lower()
    for banned in ("office", "desk", "keyboard"):
        assert banned not in lowered


def test_banned_visual_set_covers_expected_terms():
    expected = {
        "office",
        "laptop",
        "code",
        "coding",
        "whiteboard",
        "screen",
        "keyboard",
        "desk",
        "monitor",
        "abstract",
    }
    assert expected <= _BANNED_VISUAL


def test_rerank_candidates_running_slug_beats_coding_slug():
    query = "exhausted runner late race"
    candidates = [
        "tired-runner-marathon-track",
        "man-coding-laptop-office",
        "sunrise-road-jogger",
    ]
    result = rerank_candidates(query, candidates)
    assert result
    assert result[0] == "tired-runner-marathon-track"
    assert "man-coding-laptop-office" not in result


def test_rerank_candidates_drops_banned_visual_candidates():
    query = "runner on track"
    candidates = [
        "runner-on-track",
        "office-laptop-code",
        "whiteboard-diagram-formula",
    ]
    result = rerank_candidates(query, candidates)
    assert "office-laptop-code" not in result
    assert "whiteboard-diagram-formula" not in result
    assert "runner-on-track" in result


def test_rerank_candidates_scores_by_token_overlap():
    query = "exhausted runner late race"
    candidates = [
        "sunrise-road-jogger",
        "tired-runner-marathon-race",
    ]
    result = rerank_candidates(query, candidates)
    assert result[0] == "tired-runner-marathon-race"


def test_rerank_candidates_empty_candidates_returns_empty():
    assert rerank_candidates("runner on track", []) == []
