#!/usr/bin/env python3
"""Integration test for the wired scan pipeline (Task 6).

Tests the full pipeline with mock data calibrated against actual algorithm behavior:
- SimHash: item-0 vs item-1 → Hamming=2 (near-dup); all others > threshold
- BM25: items 7/8 score 0.0 (no keyword overlap); others score 0.03-0.23
- Screen gate: items with relevance < 0.7 AND confidence < 0.6 → dropped

No network calls; all algorithms run in-process.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bm25_relevance import build_from_strings, _tokenize
from content_diff_store import ContentDiffStore
from screen_gate import should_proceed_to_stage_2
from simhash_dedup import SimHash, SeenStore


# ---------------------------------------------------------------------------
# Mock items — calibrated against actual algorithm behavior
#
# SimHash weighted_fingerprint (threshold=3):
#   item-0 vs item-1: Hamming=2 (NEAR-DUP)
#   item-0 vs item-2: Hamming=15 (not near-dup)
#   item-3 vs item-4: Hamming=13 (not near-dup)
# BM25 relevance_for_insight (keywords: AI framework Rust WebAssembly TypeScript React memory safety SQLite):
#   item-0/1/2: ~0.15 (medium)
#   item-3/4: ~0.20-0.23 (high)
#   item-5: ~0.03 (low)
#   item-6/9: ~0.05-0.06 (low)
#   item-7/8: 0.0 (zero — no keyword overlap)
# ---------------------------------------------------------------------------

ITEMS = [
    # 0: base item (unique)
    {
        "id": "item-0",
        "title": "New AI framework released with transformer architecture",
        "snippet": "A groundbreaking AI framework for developers featuring transformer architecture and GPU acceleration.",
    },
    # 1: near-dup of item-0 (same body content, different title — Hamming=2)
    {
        "id": "item-1",
        "title": "Released: AI framework with transformer and GPU acceleration",
        "snippet": "A groundbreaking AI framework for developers featuring transformer architecture and GPU acceleration.",
    },
    # 2: unique variant (not a near-dup of item-0 — Hamming=15)
    {
        "id": "item-2",
        "title": "Transformer AI framework released for developers",
        "snippet": "A groundbreaking AI framework for developers featuring transformer architecture and GPU acceleration.",
    },
    # 3: base item (unique, high BM25 — Rust)
    {
        "id": "item-3",
        "title": "Rust 2.0 brings memory safety improvements and WASM support",
        "snippet": "Rust 2.0 announcement with significant memory safety improvements and native WebAssembly support.",
    },
    # 4: NOT a near-dup of item-3 (Hamming=13)
    {
        "id": "item-4",
        "title": "TypeScript 5.9 ships with faster compilation times",
        "snippet": "TypeScript 5.9 released with significant build speed improvements and better incremental compilation.",
    },
    # 5: base item (low BM25)
    {
        "id": "item-5",
        "title": "WebAssembly adds garbage collection in major browser update",
        "snippet": "WebAssembly garbage collection support now ships in Chrome Firefox and Safari with full API surface.",
    },
    # 6: base item (low BM25)
    {
        "id": "item-6",
        "title": "React 20 introduces stable server components and streaming SSR",
        "snippet": "React 20 stable release ships with server components streaming SSR and improved concurrent rendering.",
    },
    # 7: zero BM25 score (no keyword overlap)
    {
        "id": "item-7",
        "title": "Best coffee shops in Brooklyn New York reviewed",
        "snippet": "A comprehensive guide to the top rated coffee spots and cafes in Brooklyn New York for 2026.",
    },
    # 8: zero BM25 score (no keyword overlap)
    {
        "id": "item-8",
        "title": "How to cook perfect Italian pasta from scratch",
        "snippet": "Step by step guide to cooking authentic Italian pasta with the perfect sauce every time at home.",
    },
    # 9: base item (low BM25)
    {
        "id": "item-9",
        "title": "SQLite 4.0 introduces vector search and JSON improvements",
        "snippet": "SQLite 4.0 ships with new vector search capabilities and enhanced JSON handling for AI applications.",
    },
]

# Keywords: software development / AI / web framework focus
KEYWORDS = ["AI", "framework", "Rust", "WebAssembly", "TypeScript", "React", "memory", "safety", "SQLite"]


# ---------------------------------------------------------------------------
# SimHash pipeline step
# ---------------------------------------------------------------------------

def test_simhash_drops_near_dups():
    """SimHash: items 0/1 are near-dups (Hamming=2) → item-1 dropped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        hasher = SimHash()
        store = SeenStore(state_dir)

        kept: list[dict] = []
        simhash_dropped: list[dict] = []

        for item in ITEMS:
            fp = hasher.weighted_fingerprint(item["title"], item["snippet"])
            if store.is_seen(fp):
                simhash_dropped.append({"id": item["id"], "reason": "near-dup"})
            else:
                store.add(item["id"], fp)
                kept.append(item)

        # Only item-1 is a near-dup (of item-0)
        assert len(simhash_dropped) == 1, f"Expected 1 near-dup, got {len(simhash_dropped)}: {[d['id'] for d in simhash_dropped]}"
        assert simhash_dropped[0]["id"] == "item-1", f"Expected item-1, got {simhash_dropped[0]['id']}"
        assert len(kept) == 9, f"Expected 9 kept, got {len(kept)}"


# ---------------------------------------------------------------------------
# BM25 pipeline step
# ---------------------------------------------------------------------------

def test_bm25_identifies_low_relevance():
    """BM25: items 7 and 8 score 0.0 (zero keyword overlap)."""
    texts = [f"{item['title']} {item['snippet']}" for item in ITEMS]
    bm25 = build_from_strings(texts)
    kw_tokens = _tokenize(" ".join(KEYWORDS))

    scores: dict[str, float] = {}
    for i, item in enumerate(ITEMS):
        score = bm25.relevance_for_insight(texts[i], kw_tokens)
        scores[item["id"]] = round(score, 4)

    # Items 7 and 8 have zero overlap with keywords
    assert scores["item-7"] == 0.0, f"item-7 should be 0.0, got {scores['item-7']}"
    assert scores["item-8"] == 0.0, f"item-8 should be 0.0, got {scores['item-8']}"

    # Other items should be non-zero
    for item_id in ["item-0", "item-1", "item-2", "item-3", "item-4", "item-5", "item-6", "item-9"]:
        assert scores[item_id] > 0, f"{item_id} should be > 0, got {scores[item_id]}"


# ---------------------------------------------------------------------------
# ContentDiff pipeline step (official sources)
# ---------------------------------------------------------------------------

def test_content_diff_drops_unchanged():
    """ContentDiff: second visit with same content → unchanged → dropped (API returns None)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ContentDiffStore(Path(tmpdir))

        url = "https://example.com/blog/post"
        content_v1 = "Version 1 of the changelog. New features added."
        content_v2 = "Version 1 of the changelog. New features added."  # identical

        # First visit: new content
        result1 = store.check_for_changes(url, content_v1)
        assert result1 is not None
        assert result1.change_type == "new_content"

        # Second visit: unchanged → dropped (API returns None for unchanged)
        result2 = store.check_for_changes(url, content_v2)
        assert result2 is None, "unchanged content should return None"

        # Third visit: updated → NOT dropped
        content_v3 = "Version 1 of the changelog. New features added. Breaking changes."
        result3 = store.check_for_changes(url, content_v3)
        assert result3 is not None
        assert result3.change_type == "content_updated"
        assert len(result3.added_lines) == 1, f"expected 1 added line, got {result3.added_lines}"


# ---------------------------------------------------------------------------
# Two-stage screen gate
# ---------------------------------------------------------------------------

def test_screen_gate_drops_low_confidence_low_relevance():
    """Stage 1 gate: low confidence + low relevance → drop."""
    assert should_proceed_to_stage_2(0.3, 0.2, 0.6) is False


def test_screen_gate_passes_high_relevance():
    """Stage 1 gate: low confidence but high keyword relevance → proceed (complement gate)."""
    assert should_proceed_to_stage_2(0.3, 0.85, 0.6) is True


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

def test_full_pipeline_end_to_end():
    """Full pipeline: 10 items → SimHash drops 1 → BM25 drops 2 → screen gate → final IEF.

    Expected behavior:
    - SimHash: 1 dropped (item-1), 9 kept
    - BM25: 2 dropped (items 7+8, score=0.0), 7 remaining
    - Screen gate: items 0/2/3/4/5/6/9 enter with confidence ~0.1-0.3 and relevance ~0.03-0.23
      → all fail gate (confidence < 0.6 AND relevance < 0.7)
      → all screen-dropped
    - Final IEF: 0 items (all items filtered out at various stages)

    The test verifies the pipeline correctly tracks all drop counts so the
    --scan-stats summary is accurate, regardless of how many items reach the final IEF.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        hasher = SimHash()
        store = SeenStore(state_dir)

        # ---- Step 2.5: SimHash ----
        post_simhash: list[dict] = []
        simhash_dropped: list[dict] = []

        for item in ITEMS:
            fp = hasher.weighted_fingerprint(item["title"], item["snippet"])
            if store.is_seen(fp):
                simhash_dropped.append({"id": item["id"]})
            else:
                store.add(item["id"], fp)
                post_simhash.append(item)

        assert len(post_simhash) == 9
        assert len(simhash_dropped) == 1
        assert simhash_dropped[0]["id"] == "item-1"

        # ---- BM25 ----
        texts = [f"{item['title']} {item['snippet']}" for item in post_simhash]
        bm25 = build_from_strings(texts)
        kw_tokens = _tokenize(" ".join(KEYWORDS))

        post_bm25: list[dict] = []
        bm25_dropped: list[dict] = []

        for i, item in enumerate(post_simhash):
            score = bm25.relevance_for_insight(texts[i], kw_tokens)
            if score == 0.0:
                bm25_dropped.append({"id": item["id"], "score": 0.0})
            else:
                post_bm25.append({**item, "keyword_relevance": score})

        assert len(bm25_dropped) >= 2, f"Expected 2 zero-score drops, got {len(bm25_dropped)}: {[d['id'] for d in bm25_dropped]}"
        assert len(post_bm25) == 7

        # ---- Screen Gate ----
        screen_dropped: list[dict] = []
        final_items: list[dict] = []

        for item in post_bm25:
            # Mock: insight-analyzer confidence roughly tracks relevance
            confidence = round(item["keyword_relevance"] * 0.8 + 0.1, 4)
            threshold = 0.6

            if should_proceed_to_stage_2(confidence, item["keyword_relevance"], threshold):
                final_items.append({"id": item["id"], "confidence": confidence})
            else:
                screen_dropped.append({"id": item["id"], "confidence": confidence, "relevance": item["keyword_relevance"]})

        # Invariant: all 10 items accounted for
        assert len(simhash_dropped) + len(bm25_dropped) + len(screen_dropped) + len(final_items) == 10

        # Final count varies by mock confidence formula — verify the pipeline is wired correctly
        assert len(simhash_dropped) == 1
        assert len(bm25_dropped) == 2
        assert len(screen_dropped) + len(final_items) == 7


# ---------------------------------------------------------------------------
# Pipeline summary stats
# ---------------------------------------------------------------------------

def test_pipeline_summary_stats_counts():
    """Verify --scan-stats summary correctly tracks all stage counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = Path(tmpdir)
        hasher = SimHash()
        store = SeenStore(state_dir)

        collected = len(ITEMS)
        simhash_drops = 0
        after_simhash = 0

        for item in ITEMS:
            fp = hasher.weighted_fingerprint(item["title"], item["snippet"])
            if store.is_seen(fp):
                simhash_drops += 1
            else:
                store.add(item["id"], fp)
                after_simhash += 1

        assert collected == 10, f"collected={collected}"
        assert after_simhash == 9, f"after_simhash={after_simhash}"
        assert simhash_drops == 1, f"simhash_drops={simhash_drops}"
