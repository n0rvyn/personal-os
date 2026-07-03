"""Tests for lib/skeleton.py — evening skeleton pool (Phase 6, DP-003=B).

Pins:
- pick_skeleton: deterministic per (date,show) (resume-stable); avoids recent
  ids; rotates through the whole pool before repeating
- record_skeleton: idempotent upsert (re-run same episode → no dup)
- load_history: missing/corrupt → []
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def test_pick_is_deterministic_for_same_episode():
    from lib.skeleton import pick_skeleton
    a = pick_skeleton("2026-06-30", "evening", recent=[])
    b = pick_skeleton("2026-06-30", "evening", recent=[])
    assert a["id"] == b["id"]  # resume-stable


def test_pick_avoids_recent():
    from lib.skeleton import pick_skeleton, POOL
    recent = [t["id"] for t in POOL[:3]]  # 3 of 4 recently used
    picked = pick_skeleton("2026-06-30", "evening", recent=recent)
    assert picked["id"] == POOL[3]["id"]  # only the 4th is eligible


def test_pick_rotates_full_pool_before_repeat():
    """Simulate a run of episodes recording each pick; across pool-size
    episodes the chosen ids should cover distinct templates (avoid-recent
    forces rotation)."""
    from lib.skeleton import pick_skeleton, POOL, recent_ids
    history: list[dict] = []
    chosen: list[str] = []
    for i in range(len(POOL)):
        rec = recent_ids(history, n=len(POOL) - 1)
        p = pick_skeleton(f"2026-07-{i+1:02d}", "evening", recent=rec)
        chosen.append(p["id"])
        history.append({"date": f"2026-07-{i+1:02d}", "show": "evening", "id": p["id"]})
    assert len(set(chosen)) == len(POOL)  # all distinct → full rotation


def test_record_is_idempotent_upsert(tmp_path):
    from lib.skeleton import record_skeleton, load_history
    record_skeleton(str(tmp_path), "2026-06-30", "evening", "spine-reversal")
    record_skeleton(str(tmp_path), "2026-06-30", "evening", "dual-track")  # re-run
    hist = load_history(str(tmp_path))
    same = [h for h in hist if h["date"] == "2026-06-30" and h["show"] == "evening"]
    assert len(same) == 1  # upsert, not append
    assert same[0]["id"] == "dual-track"


def test_record_appends_distinct_episodes(tmp_path):
    from lib.skeleton import record_skeleton, load_history
    record_skeleton(str(tmp_path), "2026-06-30", "evening", "spine-reversal")
    record_skeleton(str(tmp_path), "2026-07-01", "evening", "one-question")
    assert len(load_history(str(tmp_path))) == 2


def test_load_history_missing_returns_empty(tmp_path):
    from lib.skeleton import load_history
    assert load_history(str(tmp_path)) == []
