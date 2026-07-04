"""Test-first contract for lib.offline_pool.PoolIndex (Phase 6, D-012/D-006).

PoolIndex is the persistent, style-scoped, CROSS-EPISODE offline media pool:
a directory of generated still assets + a JSON index (pool_dir/index.json)
tagging each asset by the query it was generated for. Retrieval is by content
overlap (lib.query_filter.token_overlap), not exact match.

This is INCLUSION (reuse across episodes), NOT a dedup ledger — do not
conflate with lib.visual_ledger.
"""
from __future__ import annotations

import json

from lib.offline_pool import PoolIndex


def _write_dummy_asset(tmp_path, name="src.png"):
    src = tmp_path / name
    src.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    return src


# ---------------------------------------------------------------------------
# Empty pool
# ---------------------------------------------------------------------------


def test_empty_pool_lookup_returns_empty_list(tmp_path):
    pool = PoolIndex(tmp_path / "pool")
    assert pool.lookup("anything") == []


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------


def test_add_copies_asset_and_persists_index(tmp_path):
    pool_dir = tmp_path / "pool"
    src = _write_dummy_asset(tmp_path)

    pool = PoolIndex(pool_dir)
    result_path = pool.add(str(src), "runner on track")

    assert result_path
    from pathlib import Path

    assert Path(result_path).exists()
    assert (pool_dir / "index.json").exists()


# ---------------------------------------------------------------------------
# MF-1: disk round-trip — cross-instance / cross-episode load
# ---------------------------------------------------------------------------


def test_lookup_survives_fresh_instance_round_trip(tmp_path):
    pool_dir = tmp_path / "pool"
    src = _write_dummy_asset(tmp_path)

    pool = PoolIndex(pool_dir)
    added_path = pool.add(str(src), "runner on track")

    # Brand-new PoolIndex instance pointed at the same pool_dir — simulates a
    # new episode / new pipeline run loading the persisted index from disk.
    reloaded = PoolIndex(pool_dir)
    results = reloaded.lookup("runner track")

    assert results, "expected at least one hit after disk round-trip"
    paths = [p for p, _overlap in results]
    assert added_path in paths
    hit_overlap = dict(results)[added_path]
    assert hit_overlap >= 1


# ---------------------------------------------------------------------------
# lookup() ordering
# ---------------------------------------------------------------------------


def test_lookup_orders_by_overlap_descending(tmp_path):
    pool_dir = tmp_path / "pool"
    src1 = _write_dummy_asset(tmp_path, "src1.png")
    src2 = _write_dummy_asset(tmp_path, "src2.png")

    pool = PoolIndex(pool_dir)
    weak_path = pool.add(str(src1), "city skyline sunset")
    strong_path = pool.add(str(src2), "runner track sprint race")

    results = pool.lookup("runner track morning")
    assert results[0][0] == strong_path
    assert results[0][1] > dict(results)[weak_path]


# ---------------------------------------------------------------------------
# Filenames don't collide
# ---------------------------------------------------------------------------


def test_add_three_assets_yields_distinct_filenames(tmp_path):
    pool_dir = tmp_path / "pool"
    pool = PoolIndex(pool_dir)

    for i in range(3):
        src = _write_dummy_asset(tmp_path, f"src{i}.png")
        pool.add(str(src), f"query {i}")

    data = json.loads((pool_dir / "index.json").read_text(encoding="utf-8"))
    filenames = sorted(data.keys())
    assert filenames == ["pool_0000.png", "pool_0001.png", "pool_0002.png"]


# ---------------------------------------------------------------------------
# Path normalization: add() and lookup() must agree exactly
# ---------------------------------------------------------------------------


def test_add_and_lookup_return_identical_path_string(tmp_path):
    pool_dir = tmp_path / "pool"
    src = _write_dummy_asset(tmp_path)

    pool = PoolIndex(pool_dir)
    added_path = pool.add(str(src), "runner on track")

    results = pool.lookup("runner track")
    lookup_path = results[0][0]

    assert lookup_path == added_path
