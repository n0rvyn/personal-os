"""Tests for lib/coveredground.py — covered-ground store (跨期记忆).

video-studio adaptation of the podcast-studio suite:
- episode signature: (date, show) → (date, seq int) — video has no
  morning/evening split, the global per-episode seq disambiguates.
- is_stale: calendar windows → SEQ-based recency (MR-1). An anchor is
  hot iff any episode has `seq > current_seq - recent_n` (default 20).

Pins:
- store_path: returns <output_dir>/covered-ground.yaml; realpath-asserted
- load_store / write_store: yaml roundtrip; empty/missing → empty store;
  atomic overwrite; fail-soft on parse error (corrupt YAML → {anchors:{}})
- update_store: new anchor creates entry; existing anchor increments count,
  updates last_used, appends episode; reskin detection (high similarity)
  merges into existing key; (date,seq) dedup
- is_stale: seq within last recent_n → True; outside → False; respects
  recent_n; the weekly-cadence no-op guard fires at ~7-day spacing
- render_memo: lists hot anchors with "避开/换说法" semantics; empty store →
  empty memo; temperature shield (no "别下注" wording)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.coveredground import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports ----------

def test_module_imports():
    from lib import coveredground  # noqa: F401
    assert hasattr(coveredground, "store_path")
    assert hasattr(coveredground, "load_store")
    assert hasattr(coveredground, "write_store")
    assert hasattr(coveredground, "update_store")
    assert hasattr(coveredground, "is_stale")
    assert hasattr(coveredground, "render_memo")


# ---------- store_path ----------

def test_store_path_under_output_dir(tmp_path):
    """store_path returns `<output_dir>/covered-ground.yaml` and stays
    inside output_dir (realpath check)."""
    from lib.coveredground import store_path
    p = store_path(str(tmp_path))
    assert p.name == "covered-ground.yaml"
    assert str(p.resolve()).startswith(str(tmp_path.resolve()))


def test_store_path_escapes_raises(tmp_path):
    """store_path realpath-asserts that the resolved path stays inside
    output_dir — a symlink whose canonical form is outside must raise."""
    from lib.coveredground import store_path
    real = tmp_path.resolve()
    sibling = real.parent / f"{real.name}_sibling"
    if not sibling.exists():
        sibling.mkdir()
    link = tmp_path / "link"
    link.symlink_to(sibling)
    with pytest.raises(ValueError):
        store_path(str(link))


# ---------- load_store / write_store: roundtrip + fail-soft ----------

def test_load_store_missing_file_returns_empty(tmp_path):
    from lib.coveredground import load_store
    store = load_store(str(tmp_path))
    assert store == {"anchors": {}}


def test_load_store_empty_file_returns_empty(tmp_path):
    from lib.coveredground import load_store, store_path
    p = store_path(str(tmp_path))
    p.write_text("", encoding="utf-8")
    store = load_store(str(tmp_path))
    assert store == {"anchors": {}}


def test_load_store_corrupt_yaml_fail_soft(tmp_path):
    """A corrupt covered-ground.yaml must NOT halt the run — load_store
    returns the empty store shape (fail-soft, whole chain)."""
    from lib.coveredground import load_store, store_path
    p = store_path(str(tmp_path))
    # Unparseable YAML (unbalanced brackets / bad indentation).
    p.write_text("anchors: {[[[ : : not valid yaml", encoding="utf-8")
    store = load_store(str(tmp_path))
    assert store == {"anchors": {}}


def test_store_roundtrip(tmp_path):
    """write_store + load_store preserve the dict exactly (anchors and
    their per-anchor sub-dicts), with (date, seq) episodes."""
    from lib.coveredground import load_store, write_store
    payload = {
        "anchors": {
            "1956苏伊士": {
                "first_used": "2026-06-01",
                "last_used": "2026-06-15",
                "count": 3,
                "episodes": [
                    {"date": "2026-06-01", "seq": 3},
                    {"date": "2026-06-08", "seq": 4},
                    {"date": "2026-06-15", "seq": 5},
                ],
            },
            "印刷术类比": {
                "first_used": "2026-06-05",
                "last_used": "2026-06-05",
                "count": 1,
                "episodes": [{"date": "2026-06-05", "seq": 2}],
            },
        }
    }
    write_store(str(tmp_path), payload)
    loaded = load_store(str(tmp_path))
    assert loaded == payload


def test_write_store_atomic_overwrite(tmp_path):
    from lib.coveredground import load_store, write_store
    write_store(str(tmp_path), {"anchors": {"A": {"count": 1}}})
    write_store(str(tmp_path), {"anchors": {"B": {"count": 2}}})
    loaded = load_store(str(tmp_path))
    assert "A" not in loaded["anchors"]
    assert loaded["anchors"]["B"]["count"] == 2


def test_write_store_no_orphan_temp_on_error(tmp_path):
    from lib.coveredground import write_store
    bogus = tmp_path / "does-not-exist"
    with pytest.raises(Exception):
        write_store(str(bogus), {"anchors": {}})
    for entry in tmp_path.iterdir():
        assert not entry.name.endswith(".tmp")
        assert ".partial" not in entry.name


# ---------- update_store: new anchor ----------

def test_update_new_anchor():
    from lib.coveredground import update_store
    store = {"anchors": {}}
    date = "2026-06-14"
    ep = {"date": date, "seq": 5}
    update_store(
        store,
        anchors=["1956苏伊士"],
        date=date,
        episode=ep,
        similarity_fn=lambda a, b: 0.0,  # never reskin on the first call
    )
    assert "1956苏伊士" in store["anchors"]
    entry = store["anchors"]["1956苏伊士"]
    assert entry["first_used"] == date
    assert entry["last_used"] == date
    assert entry["count"] == 1
    assert entry["episodes"] == [ep]


# ---------- update_store: existing anchor increments + (date,seq) dedup ----------

def test_update_existing_anchor_increments():
    from lib.coveredground import update_store
    store = {"anchors": {}}
    sim_zero = lambda a, b: 0.0
    update_store(store, anchors=["苏伊士"], date="2026-06-08",
                 episode={"date": "2026-06-08", "seq": 4},
                 similarity_fn=sim_zero)
    update_store(store, anchors=["苏伊士"], date="2026-06-15",
                 episode={"date": "2026-06-15", "seq": 5},
                 similarity_fn=sim_zero)
    entry = store["anchors"]["苏伊士"]
    assert entry["count"] == 2
    assert entry["last_used"] == "2026-06-15"
    assert entry["first_used"] == "2026-06-08"
    assert entry["episodes"] == [
        {"date": "2026-06-08", "seq": 4},
        {"date": "2026-06-15", "seq": 5},
    ]

    # Same episode (date,seq) passed twice → not double-counted
    update_store(store, anchors=["苏伊士"], date="2026-06-15",
                 episode={"date": "2026-06-15", "seq": 5},
                 similarity_fn=sim_zero)
    assert entry["count"] == 2
    assert entry["episodes"] == [
        {"date": "2026-06-08", "seq": 4},
        {"date": "2026-06-15", "seq": 5},
    ]


# ---------- is_stale: SEQ-based recency (MR-1) ----------

def test_is_stale_within_recent_window():
    """An episode used within the last recent_n (default 20) episodes is
    hot: seq 028, current_seq 030 → threshold 10, 28 > 10 → True."""
    from lib.coveredground import is_stale
    entry = {"count": 1, "episodes": [{"date": "2026-06-15", "seq": 28}]}
    assert is_stale(entry, 30) is True


def test_is_stale_outside_recent_window():
    """An episode older than recent_n episodes is cool: seq 005,
    current_seq 030 → threshold 10, 5 > 10 is False → not stale."""
    from lib.coveredground import is_stale
    entry = {"count": 1, "episodes": [{"date": "2026-01-01", "seq": 5}]}
    assert is_stale(entry, 30) is False


def test_is_stale_fires_at_weekly_cadence():
    """MR-1 no-op guard. seed anchor used at seq 025 (weekly spacing),
    current_seq=030 → is_stale True (within the last 20 — proves the memo
    FIRES at weekly cadence, not the silent no-op the old calendar windows
    caused). A seq 005 anchor (5 <= 030-20) is out of window → False."""
    from lib.coveredground import is_stale
    hot_entry = {"count": 1, "episodes": [{"date": "2026-06-15", "seq": 25}]}
    assert is_stale(hot_entry, 30) is True

    cold_entry = {"count": 1, "episodes": [{"date": "2026-01-01", "seq": 5}]}
    assert is_stale(cold_entry, 30) is False


def test_is_stale_respects_recent_n():
    """The recent_n kwarg narrows the window: seq 025 with current_seq 030
    is hot at recent_n=20 (threshold 10) but cool at recent_n=3
    (threshold 27, 25 > 27 is False)."""
    from lib.coveredground import is_stale
    entry = {"count": 1, "episodes": [{"date": "2026-06-15", "seq": 25}]}
    assert is_stale(entry, 30, recent_n=20) is True
    assert is_stale(entry, 30, recent_n=3) is False


def test_is_stale_multi_episode_any_recent():
    """Hot iff ANY episode is recent. Old seq 003 + recent seq 029 →
    the recent one makes it hot at current_seq 030."""
    from lib.coveredground import is_stale
    entry = {
        "count": 2,
        "episodes": [
            {"date": "2026-01-01", "seq": 3},
            {"date": "2026-06-15", "seq": 29},
        ],
    }
    assert is_stale(entry, 30) is True


def test_is_stale_bad_input_fail_soft():
    """Malformed inputs never raise — return False."""
    from lib.coveredground import is_stale
    assert is_stale("not a dict", 30) is False
    assert is_stale({"episodes": "not a list"}, 30) is False
    assert is_stale({"episodes": []}, 30) is False
    assert is_stale({"episodes": [{"date": "x", "seq": 25}]}, "not-int") is False


def test_is_stale_real_weekly_intervals():
    """Real-path shape: 3 weekly episodes (seq 003/004/005, dates 7 days
    apart) are ALL hot at current_seq=006 — the calendar windows would
    have judged them cool (no-op), the seq window judges them hot."""
    from lib.coveredground import is_stale
    entry = {
        "count": 3,
        "episodes": [
            {"date": "2026-06-01", "seq": 3},
            {"date": "2026-06-08", "seq": 4},
            {"date": "2026-06-15", "seq": 5},
        ],
    }
    assert is_stale(entry, 6) is True


# ---------- render_memo: hot anchors ----------

def test_render_memo_lists_hot_anchors():
    """A store with one hot anchor (recent seq) → memo names it AND
    carries avoid-style wording. Cool anchors (old seq) are NOT listed."""
    from lib.coveredground import render_memo
    store = {
        "anchors": {
            "1956苏伊士": {                  # hot: seq 028/029, within 20 of 030
                "first_used": "2026-06-08",
                "last_used": "2026-06-15",
                "count": 2,
                "episodes": [
                    {"date": "2026-06-08", "seq": 28},
                    {"date": "2026-06-15", "seq": 29},
                ],
            },
            "印刷术类比": {                  # cool: seq 005, out of window
                "first_used": "2026-01-01",
                "last_used": "2026-01-01",
                "count": 1,
                "episodes": [{"date": "2026-01-01", "seq": 5}],
            },
        }
    }
    memo = render_memo(store, 30)
    assert memo
    assert "1956苏伊士" in memo
    assert "印刷术类比" not in memo
    assert any(s in memo for s in ("避开", "避让", "换说法", "换新的", "avoid"))


def test_render_memo_empty_when_none_hot():
    """No hot anchors → empty memo (never mentions a cool anchor)."""
    from lib.coveredground import render_memo
    store = {
        "anchors": {
            "印刷术类比": {
                "first_used": "2026-01-01",
                "last_used": "2026-01-01",
                "count": 1,
                "episodes": [{"date": "2026-01-01", "seq": 5}],
            },
        }
    }
    memo = render_memo(store, 30)
    assert "印刷术类比" not in memo


def test_render_memo_empty_store():
    from lib.coveredground import render_memo
    assert render_memo({"anchors": {}}, 30) == ""


# ---------- render_memo: temperature shield ----------

def test_render_memo_targets_apparatus_not_opinions():
    """The memo must NEVER carry 'don't bet / don't opine' wording —
    covered-ground is purely an apparatus guardrail."""
    from lib.coveredground import render_memo
    store = {
        "anchors": {
            "1956苏伊士": {
                "first_used": "2026-06-08",
                "last_used": "2026-06-15",
                "count": 2,
                "episodes": [
                    {"date": "2026-06-08", "seq": 28},
                    {"date": "2026-06-15", "seq": 29},
                ],
            },
        }
    }
    memo = render_memo(store, 30)
    forbidden = ("别下注", "不要表态", "不要下注", "别表态", "don't bet", "don't opine")
    for phrase in forbidden:
        assert phrase not in memo, f"memo leaks opinion-suppression: {phrase!r}"


# ---------- update_store: reskin detection ----------

def test_reskin_detection_merges_when_similarity_high():
    from lib.coveredground import update_store
    store = {"anchors": {}}

    def high_sim(a, b):
        return 0.95

    update_store(
        store, anchors=["苏伊士运河 1956"], date="2026-06-08",
        episode={"date": "2026-06-08", "seq": 4},
        similarity_fn=high_sim,
    )
    assert len(store["anchors"]) == 1
    first_key = next(iter(store["anchors"].keys()))

    update_store(
        store, anchors=["1956年苏伊士运河"], date="2026-06-15",
        episode={"date": "2026-06-15", "seq": 5},
        similarity_fn=high_sim,
    )
    assert len(store["anchors"]) == 1
    assert first_key in store["anchors"]
    entry = store["anchors"][first_key]
    assert entry["count"] == 2
    assert entry["last_used"] == "2026-06-15"
    assert entry["episodes"] == [
        {"date": "2026-06-08", "seq": 4},
        {"date": "2026-06-15", "seq": 5},
    ]


def test_reskin_detection_creates_new_when_similarity_low():
    from lib.coveredground import update_store
    store = {"anchors": {}}

    def low_sim(a, b):
        return 0.0

    update_store(
        store, anchors=["苏伊士运河"], date="2026-06-08",
        episode={"date": "2026-06-08", "seq": 4},
        similarity_fn=low_sim,
    )
    update_store(
        store, anchors=["完全无关的招牌锚"], date="2026-06-15",
        episode={"date": "2026-06-15", "seq": 5},
        similarity_fn=low_sim,
    )
    assert len(store["anchors"]) == 2
    assert "苏伊士运河" in store["anchors"]
    assert "完全无关的招牌锚" in store["anchors"]
    for key, entry in store["anchors"].items():
        assert entry["count"] == 1
        assert len(entry["episodes"]) == 1


# ===========================================================================
# Anti-homogenization plan — Task B6-tests: covered-ground-update code 站.
# update_covered_ground(output_dir, covered_json_path, date, seq) reads the
# episode's covered.json (anchors list), folds it into the cross-episode store
# via load_store→update_store→write_store, fail-soft on every error.
#
# FAIL before Task B6-impl: coveredground has no update_covered_ground
# (AttributeError). Green after B6-impl.
# ===========================================================================

def test_covered_update_merges_anchors_disk_roundtrip(tmp_path):
    """update_covered_ground reads covered.json anchors and merges them into the
    store; a FRESH load_store (disk round-trip) sees the new anchors → proves
    the station wrote to <output_dir>/covered-ground.yaml, not just an in-memory
    dict."""
    import json
    from lib import coveredground

    out = tmp_path / "out"
    out.mkdir()
    covered = tmp_path / "covered.json"
    covered.write_text(
        json.dumps({"anchors": ["心率像转速表", "周末长距离撞墙"]}),
        encoding="utf-8",
    )

    coveredground.update_covered_ground(str(out), str(covered), "2026-07-04", 5)

    # Fresh instance load = disk round-trip.
    store = coveredground.load_store(str(out))
    assert "心率像转速表" in store["anchors"], store["anchors"]
    assert "周末长距离撞墙" in store["anchors"], store["anchors"]
    assert store["anchors"]["心率像转速表"]["count"] == 1
    # (date, seq) recorded on the episode list.
    eps = store["anchors"]["心率像转速表"]["episodes"]
    assert eps == [{"date": "2026-07-04", "seq": 5}], eps


def test_covered_update_empty_anchors_noop(tmp_path):
    """Empty anchors → no-op: the station returns before write_store, so no
    store file is written (fresh load stays empty)."""
    import json
    from lib import coveredground

    out = tmp_path / "out"
    out.mkdir()
    covered = tmp_path / "covered.json"
    covered.write_text(json.dumps({"anchors": []}), encoding="utf-8")

    coveredground.update_covered_ground(str(out), str(covered), "2026-07-04", 5)

    assert not coveredground.store_path(out).exists(), (
        "empty anchors must not write a store file"
    )
    assert coveredground.load_store(str(out))["anchors"] == {}


def test_covered_update_corrupt_json_no_raise(tmp_path):
    """A corrupt covered.json must be swallowed (fail-soft): the station never
    halts an already-rendered episode."""
    from lib import coveredground

    out = tmp_path / "out"
    out.mkdir()
    covered = tmp_path / "covered.json"
    covered.write_text("{ not valid json ", encoding="utf-8")

    # Must NOT raise.
    coveredground.update_covered_ground(str(out), str(covered), "2026-07-04", 5)
    # And must not have written a bogus store.
    assert coveredground.load_store(str(out))["anchors"] == {}


def test_covered_update_missing_covered_json_no_raise(tmp_path):
    """A missing covered.json path → swallowed, no store written (fail-soft)."""
    from lib import coveredground

    out = tmp_path / "out"
    out.mkdir()
    covered = tmp_path / "does-not-exist.json"

    coveredground.update_covered_ground(str(out), str(covered), "2026-07-04", 5)
    assert coveredground.load_store(str(out))["anchors"] == {}
