"""Tests for lib/thesis_ledger.py — 论点账本 (Phase 3).

Pins:
- store_path/load_store/write_store: roundtrip; missing→empty; atomic; no orphan
- update_store: new thesis; reskin fold (different wording, same argument →
  one entry); per-episode advanced flag + bet_ids recorded; same-episode dedup
- is_recurring_stale: recurring + recent + last-use-not-advanced → True;
  a recurring thesis whose last use ADVANCED → False (persistence protected)
- render_thesis_memo: lists hot theses w/ "换题/新推进" semantics, not
  opinion-suppression
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_SIM0 = lambda a, b: 0.0
_SIM_HIGH = lambda a, b: 0.95


def test_module_surface():
    from lib import thesis_ledger as tl
    for name in ("store_path", "load_store", "write_store", "update_store",
                 "is_recurring_stale", "render_thesis_memo"):
        assert hasattr(tl, name)


def test_store_roundtrip_and_missing(tmp_path):
    from lib.thesis_ledger import load_store, write_store
    assert load_store(str(tmp_path)) == {"theses": {}}
    payload = {"theses": {"判断力是最后护城河": {
        "first_used": "2026-06-24", "last_used": "2026-06-30", "count": 2,
        "statements": ["判断力是最后护城河"],
        "episodes": [
            {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": []},
            {"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []},
        ],
    }}}
    write_store(str(tmp_path), payload)
    assert load_store(str(tmp_path)) == payload


def test_write_store_no_orphan_on_error(tmp_path):
    from lib.thesis_ledger import write_store
    bogus = tmp_path / "nope"
    with pytest.raises(Exception):
        write_store(str(bogus), {"theses": {}})
    for e in tmp_path.iterdir():
        assert not e.name.endswith(".tmp")


def test_update_new_thesis_records_advanced_and_bets():
    from lib.thesis_ledger import update_store
    store = {"theses": {}}
    update_store(
        store,
        theses=[{"statement": "判断力是最后护城河", "advanced": False, "bet_ids": ["bet-x"]}],
        date="2026-06-24",
        episode={"date": "2026-06-24", "show": "evening"},
        similarity_fn=_SIM0,
    )
    entry = store["theses"]["判断力是最后护城河"]
    assert entry["count"] == 1
    ep = entry["episodes"][0]
    assert ep == {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": ["bet-x"]}


def test_update_reskin_folds_reworded_thesis():
    """Same argument, different wording → folded into ONE entry (the whole
    point covered-ground's anchor-only view misses: 54≈57)."""
    from lib.thesis_ledger import update_store
    store = {"theses": {}}
    update_store(store, theses=[{"statement": "真正剩下的是认清自己", "advanced": False, "bet_ids": []}],
                 date="2026-06-24", episode={"date": "2026-06-24", "show": "evening"},
                 similarity_fn=_SIM_HIGH)
    assert len(store["theses"]) == 1
    key = next(iter(store["theses"]))
    update_store(store, theses=[{"statement": "判断力才是最后的护城河", "advanced": False, "bet_ids": []}],
                 date="2026-06-30", episode={"date": "2026-06-30", "show": "morning"},
                 similarity_fn=_SIM_HIGH)
    # folded → still one entry, count 2, both episodes present
    assert len(store["theses"]) == 1
    entry = store["theses"][key]
    assert entry["count"] == 2
    assert [e["date"] for e in entry["episodes"]] == ["2026-06-24", "2026-06-30"]


def test_is_recurring_stale_true_when_reused_without_advance():
    from lib.thesis_ledger import is_recurring_stale
    entry = {
        "count": 2,
        "episodes": [
            {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": []},
            {"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []},
        ],
    }
    assert is_recurring_stale(entry, "2026-06-30") is True


def test_is_recurring_stale_false_when_last_use_advanced():
    """Persistence protected (DP-001=A): a recurring thesis whose latest use
    ADVANCED (settled/pushed a bet) is good serialization, NOT stale."""
    from lib.thesis_ledger import is_recurring_stale
    entry = {
        "count": 2,
        "episodes": [
            {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": []},
            {"date": "2026-06-30", "show": "morning", "advanced": True, "bet_ids": ["bet-y"]},
        ],
    }
    assert is_recurring_stale(entry, "2026-06-30") is False


def test_is_recurring_stale_false_when_single_use():
    from lib.thesis_ledger import is_recurring_stale
    entry = {"count": 1, "episodes": [
        {"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []}]}
    assert is_recurring_stale(entry, "2026-06-30") is False


def test_is_recurring_stale_false_when_stale_out_of_window():
    from lib.thesis_ledger import is_recurring_stale
    entry = {"count": 2, "episodes": [
        {"date": "2026-05-01", "show": "evening", "advanced": False, "bet_ids": []},
        {"date": "2026-05-10", "show": "morning", "advanced": False, "bet_ids": []}]}
    assert is_recurring_stale(entry, "2026-06-30") is False  # last use 51 days ago


def test_render_thesis_memo_lists_hot_and_targets_rederivation():
    from lib.thesis_ledger import render_thesis_memo
    store = {"theses": {
        "判断力是最后护城河": {
            "first_used": "2026-06-24", "last_used": "2026-06-30", "count": 2,
            "statements": ["判断力是最后护城河"],
            "episodes": [
                {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": []},
                {"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []},
            ],
        },
    }}
    memo = render_thesis_memo(store, "2026-06-30")
    assert "判断力是最后护城河" in memo
    assert any(s in memo for s in ("换题", "新推进", "推进"))
    # temperature: never tells the host to drop an opinion
    for forbidden in ("别下注", "不要表态", "别表态", "不要下注"):
        assert forbidden not in memo


def test_render_thesis_memo_empty_when_none_hot():
    from lib.thesis_ledger import render_thesis_memo
    store = {"theses": {"一次性论点": {
        "first_used": "2026-06-30", "last_used": "2026-06-30", "count": 1,
        "statements": ["一次性论点"],
        "episodes": [{"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []}],
    }}}
    assert render_thesis_memo(store, "2026-06-30") == ""
