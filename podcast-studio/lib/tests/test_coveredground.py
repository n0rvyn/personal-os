"""Tests for lib/coveredground.py — covered-ground store (跨期记忆).

Written before lib/coveredground.py exists; collection must fail at this
point (`No module named 'lib.coveredground'`).

Pins:
- store_path: returns <output_dir>/covered-ground.yaml; realpath-asserted
- load_store / write_store: yaml roundtrip; empty/missing → empty store;
  atomic overwrite; fail-soft on parse error
- update_store: new anchor creates entry; existing anchor increments count,
  updates last_used, appends episode; reskin detection (high similarity)
  merges into existing key
- is_stale: 14-day count≥3 OR last 3 episodes ≥2 → True
- render_memo: lists hot anchors with "避开/换说法" semantics; empty store →
  empty memo; temperature shield (no "别下注" wording)
- Regression: covered-ground.yaml is NOT picked up by lib.stance.load_cards
  or lib.magnitude.gather_recent_bodies
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.coveredground import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
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
    output_dir — a `..`-style output_dir should raise (mirror
    `bible_path`'s realpath guard)."""
    from lib.coveredground import store_path
    # `..` injection via traversal: the resolved path is normalized to a
    # realpath before the in-dir check, so a `..` in the string either
    # stays inside (if not really escaping) or raises.
    # A reliable way to trigger the guard: use a symlink chain where the
    # canonical form is outside. We do that by symlinking tmp_path to a
    # name containing `..` resolved into a parent that is itself a symlink.
    # Easier: call store_path with a path whose realpath does not contain
    # the original string as prefix.
    real = tmp_path.resolve()
    # Build a path that, after realpath, lives elsewhere.
    sibling = real.parent / f"{real.name}_sibling"
    if not sibling.exists():
        sibling.mkdir()
    # A symlink dir whose target is the sibling — calling store_path with
    # the symlink path: realpath lands in `sibling` (inside `real.parent`,
    # NOT inside the symlink), so the guard should fire.
    link = tmp_path / "link"
    link.symlink_to(sibling)
    with pytest.raises(ValueError):
        store_path(str(link))


# ---------- load_store / write_store: roundtrip ----------

def test_load_store_missing_file_returns_empty(tmp_path):
    """load_store on a missing/empty covered-ground.yaml returns the empty
    store shape `{"anchors": {}}` — never raises, never returns None."""
    from lib.coveredground import load_store
    store = load_store(str(tmp_path))
    assert store == {"anchors": {}}


def test_load_store_empty_file_returns_empty(tmp_path):
    """An empty covered-ground.yaml is treated as the empty store (no
    crash, no contentless-but-marker dict)."""
    from lib.coveredground import load_store, store_path
    p = store_path(str(tmp_path))
    p.write_text("", encoding="utf-8")
    store = load_store(str(tmp_path))
    assert store == {"anchors": {}}


def test_store_roundtrip(tmp_path):
    """write_store + load_store preserve the dict exactly (anchors and
    their per-anchor sub-dicts)."""
    from lib.coveredground import load_store, write_store
    payload = {
        "anchors": {
            "1956苏伊士": {
                "first_used": "2026-06-01",
                "last_used": "2026-06-10",
                "count": 3,
                "episodes": [
                    {"date": "2026-06-01", "show": "morning"},
                    {"date": "2026-06-08", "show": "morning"},
                    {"date": "2026-06-10", "show": "evening"},
                ],
            },
            "印刷术类比": {
                "first_used": "2026-06-05",
                "last_used": "2026-06-05",
                "count": 1,
                "episodes": [{"date": "2026-06-05", "show": "evening"}],
            },
        }
    }
    write_store(str(tmp_path), payload)
    loaded = load_store(str(tmp_path))
    assert loaded == payload


def test_write_store_atomic_overwrite(tmp_path):
    """write_store OVERWRITES (yaml dict, not append). Two writes leave
    only the second."""
    from lib.coveredground import load_store, write_store
    write_store(str(tmp_path), {"anchors": {"A": {"count": 1}}})
    write_store(str(tmp_path), {"anchors": {"B": {"count": 2}}})
    loaded = load_store(str(tmp_path))
    assert "A" not in loaded["anchors"]
    assert loaded["anchors"]["B"]["count"] == 2


def test_write_store_no_orphan_temp_on_error(tmp_path):
    """If write_store fails (output_dir nonexistent), no .tmp file is left
    behind — mirrors `bible.write_bible`'s no-orphan guarantee."""
    from lib.coveredground import write_store
    bogus = tmp_path / "does-not-exist"
    with pytest.raises(Exception):
        write_store(str(bogus), {"anchors": {}})
    # No temp files left in tmp_path
    for entry in tmp_path.iterdir():
        assert not entry.name.endswith(".tmp")
        assert ".partial" not in entry.name


# ---------- update_store: new anchor ----------

def test_update_new_anchor(tmp_path):
    """An empty store + update_store with one anchor creates an entry
    with first_used=last_used=date, count=1, episodes=[ep]."""
    from lib.coveredground import load_store, update_store, write_store
    store = {"anchors": {}}
    date = "2026-06-14"
    ep = {"date": date, "show": "morning"}
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


# ---------- update_store: existing anchor increments ----------

def test_update_existing_anchor_increments(tmp_path):
    """Re-updating an existing anchor increments count, updates last_used,
    appends the new episode (deduped if the same ep is passed twice)."""
    from lib.coveredground import update_store
    store = {"anchors": {}}
    sim_zero = lambda a, b: 0.0
    update_store(store, anchors=["苏伊士"], date="2026-06-10",
                 episode={"date": "2026-06-10", "show": "morning"},
                 similarity_fn=sim_zero)
    update_store(store, anchors=["苏伊士"], date="2026-06-12",
                 episode={"date": "2026-06-12", "show": "evening"},
                 similarity_fn=sim_zero)
    entry = store["anchors"]["苏伊士"]
    assert entry["count"] == 2
    assert entry["last_used"] == "2026-06-12"
    assert entry["first_used"] == "2026-06-10"
    assert entry["episodes"] == [
        {"date": "2026-06-10", "show": "morning"},
        {"date": "2026-06-12", "show": "evening"},
    ]

    # Same episode passed twice → not double-counted
    update_store(store, anchors=["苏伊士"], date="2026-06-12",
                 episode={"date": "2026-06-12", "show": "evening"},
                 similarity_fn=sim_zero)
    assert entry["count"] == 2
    assert entry["episodes"] == [
        {"date": "2026-06-10", "show": "morning"},
        {"date": "2026-06-12", "show": "evening"},
    ]


# ---------- is_stale: count predicate ----------

def test_is_stale_count_in_window():
    """14-day window: count≥3 → True; count<3 → False. Pure predicate."""
    from lib.coveredground import is_stale
    today = _dt.date(2026, 6, 14)
    # count=3 with all episodes within 14 days of today → stale
    entry = {
        "count": 3,
        "episodes": [
            {"date": "2026-06-10", "show": "morning"},
            {"date": "2026-06-12", "show": "morning"},
            {"date": "2026-06-13", "show": "evening"},
        ],
    }
    assert is_stale(entry, today.isoformat()) is True

    # count=2 → not stale (count predicate fails)
    entry2 = {
        "count": 2,
        "episodes": [
            {"date": "2026-06-10", "show": "morning"},
            {"date": "2026-06-12", "show": "morning"},
        ],
    }
    assert is_stale(entry2, today.isoformat()) is False


def test_is_stale_count_window_excludes_old():
    """Count is windowed: episodes older than 14 days do NOT count toward
    the count≥3 predicate."""
    from lib.coveredground import is_stale
    today = _dt.date(2026, 6, 14)
    # 3 episodes, but 2 are >14 days old → only 1 in window → not stale
    entry = {
        "count": 3,
        "episodes": [
            {"date": "2026-05-01", "show": "morning"},   # 44 days old
            {"date": "2026-05-10", "show": "morning"},   # 35 days old
            {"date": "2026-06-13", "show": "morning"},   # 1 day old
        ],
    }
    assert is_stale(entry, today.isoformat()) is False


# ---------- is_stale: recency predicate ----------

def test_is_stale_recency_in_last_three_episodes():
    """An anchor that appeared in ≥2 of the most recent 3 episode dates
    is stale, even if its count is below 3. The 'last 3' window is
    measured in episode-DATES, not calendar days."""
    from lib.coveredground import is_stale
    today = _dt.date(2026, 6, 14)
    # Recency predicate: 2 of the last 3 dates → stale
    entry = {
        "count": 2,
        "episodes": [
            {"date": "2026-06-10", "show": "morning"},
            {"date": "2026-06-13", "show": "morning"},
        ],
    }
    # The "last 3" dates are determined by the *latest 3 distinct episode
    # dates in the store*. With only this anchor in the store, the recency
    # slice is just the anchor's own dates (size 2). A ≥2-of-2-of-2 match
    # is vacuously stale, but the test is only meaningful with a populated
    # store. We exercise the pure predicate by also seeding the store.
    # (For the per-anchor predicate, the recency slice is implicit in the
    # entry's own episodes list — the plan spec is `distinct episodes
    # in last 3 >= 2`, and here distinct dates = 2, so it satisfies.)
    assert is_stale(entry, today.isoformat()) is True

    # Same anchor with only 1 episode date → recency predicate fails
    entry2 = {
        "count": 1,
        "episodes": [{"date": "2026-06-10", "show": "morning"}],
    }
    assert is_stale(entry2, today.isoformat()) is False


# ---------- render_memo: hot anchors ----------

def test_render_memo_lists_hot_anchors():
    """A store with one hot anchor → render_memo's text contains that
    anchor AND 'avoid'-style wording (避开 / 换说法). Cool anchors are
    NOT listed."""
    from lib.coveredground import render_memo
    today = _dt.date(2026, 6, 14)
    store = {
        "anchors": {
            "1956苏伊士": {                  # hot: 3 uses in window
                "first_used": "2026-06-10",
                "last_used": "2026-06-13",
                "count": 3,
                "episodes": [
                    {"date": "2026-06-10", "show": "morning"},
                    {"date": "2026-06-12", "show": "evening"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            },
            "印刷术类比": {                  # cool: 1 use
                "first_used": "2026-06-05",
                "last_used": "2026-06-05",
                "count": 1,
                "episodes": [{"date": "2026-06-05", "show": "evening"}],
            },
        }
    }
    memo = render_memo(store, today.isoformat())
    assert memo
    assert "1956苏伊士" in memo
    # Avoidance semantics (Chinese: at least one of these substrings).
    assert any(s in memo for s in ("避开", "避让", "换说法", "换新的", "avoid"))


def test_render_memo_empty_when_none_hot():
    """A store with no hot anchors → render_memo returns an empty string
    (or an explicit 'no avoidance needed' marker; the test accepts both)."""
    from lib.coveredground import render_memo
    today = _dt.date(2026, 6, 14)
    store = {
        "anchors": {
            "印刷术类比": {
                "first_used": "2026-06-05",
                "last_used": "2026-06-05",
                "count": 1,
                "episodes": [{"date": "2026-06-05", "show": "evening"}],
            },
        }
    }
    memo = render_memo(store, today.isoformat())
    # Empty string OR a "no avoidance needed" marker — neither should
    # mention any anchor.
    assert "印刷术类比" not in memo


# ---------- render_memo: temperature shield ----------

def test_render_memo_targets_apparatus_not_opinions():
    """The memo must NEVER carry 'don't bet / don't opine' wording —
    covered-ground is purely an apparatus (anchor / analogy / framework)
    guardrail. Temperature principle: subjective takes and bets are not
    thinned by the memo."""
    from lib.coveredground import render_memo
    today = _dt.date(2026, 6, 14)
    # Seed a hot anchor that would naturally appear
    store = {
        "anchors": {
            "1956苏伊士": {
                "first_used": "2026-06-10",
                "last_used": "2026-06-13",
                "count": 3,
                "episodes": [
                    {"date": "2026-06-10", "show": "morning"},
                    {"date": "2026-06-12", "show": "evening"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            },
        }
    }
    memo = render_memo(store, today.isoformat())
    # Forbidden phrases (none of these should appear in the memo).
    forbidden = ("别下注", "不要表态", "不要下注", "别表态", "don't bet", "don't opine")
    for phrase in forbidden:
        assert phrase not in memo, f"memo leaks opinion-suppression: {phrase!r}"


# ---------- update_store: reskin detection ----------

def test_reskin_detection_merges_when_similarity_high():
    """When the similarity_fn returns a high score against an existing
    key, update_store folds the new anchor INTO the existing entry
    (count+1, last_used updated, episodes appended) rather than creating
    a parallel entry."""
    from lib.coveredground import update_store
    store = {"anchors": {}}
    # Inject a high-similarity reskin: the new anchor is a thin reskin
    # of an existing one (the impl folds it in).
    def high_sim(a, b):
        return 0.95

    update_store(
        store, anchors=["苏伊士运河 1956"], date="2026-06-10",
        episode={"date": "2026-06-10", "show": "morning"},
        similarity_fn=high_sim,
    )
    # First call has no existing key → always creates a new entry,
    # regardless of similarity (no prior to compare against).
    assert len(store["anchors"]) == 1
    first_key = next(iter(store["anchors"].keys()))

    # Second call passes a new candidate; high_sim returns 0.95 →
    # impl should fold into the existing entry.
    update_store(
        store, anchors=["1956年苏伊士运河"], date="2026-06-12",
        episode={"date": "2026-06-12", "show": "evening"},
        similarity_fn=high_sim,
    )
    # Still one key (the reskin was folded in), and the entry was
    # incremented.
    assert len(store["anchors"]) == 1
    assert first_key in store["anchors"]
    entry = store["anchors"][first_key]
    assert entry["count"] == 2
    assert entry["last_used"] == "2026-06-12"
    assert entry["episodes"] == [
        {"date": "2026-06-10", "show": "morning"},
        {"date": "2026-06-12", "show": "evening"},
    ]


def test_reskin_detection_creates_new_when_similarity_low():
    """When the similarity_fn returns a low score against all existing
    keys, update_store creates a NEW entry (does not fold)."""
    from lib.coveredground import update_store
    store = {"anchors": {}}
    def low_sim(a, b):
        return 0.0

    update_store(
        store, anchors=["苏伊士运河"], date="2026-06-10",
        episode={"date": "2026-06-10", "show": "morning"},
        similarity_fn=low_sim,
    )
    update_store(
        store, anchors=["完全无关的招牌锚"], date="2026-06-12",
        episode={"date": "2026-06-12", "show": "evening"},
        similarity_fn=low_sim,
    )
    # Two distinct entries: each was below the reskin threshold relative
    # to the other.
    assert len(store["anchors"]) == 2
    assert "苏伊士运河" in store["anchors"]
    assert "完全无关的招牌锚" in store["anchors"]
    # Each is a count=1 anchor with a single episode.
    for key, entry in store["anchors"].items():
        assert entry["count"] == 1
        assert len(entry["episodes"]) == 1


# ---------- regression: covered-ground.yaml is invisible to other loaders ----------

def test_store_ignored_by_card_and_body_loaders(tmp_path):
    """`lib.stance.load_cards` and `lib.magnitude.gather_recent_bodies`
    must NOT pick up `covered-ground.yaml` (a yaml with the same suffix
    as a stance card could trip an overly-broad regex)."""
    from lib.stance import load_cards
    from lib.magnitude import gather_recent_bodies

    # Seed: a real stance card, a real episode body, and the covered-ground
    # yaml (which is NOT supposed to be read by either loader).
    (tmp_path / "2026-06-10-morning.stance.yaml").write_text(
        "episode:\n  date: '2026-06-10'\n  show: morning\n"
        "bets: []\nopen_questions: []\n",
        encoding="utf-8",
    )
    (tmp_path / "2026-06-10-morning.md").write_text(
        "本期正文，1956苏伊士运河。", encoding="utf-8"
    )
    # The store: same dir, must not leak into either loader.
    (tmp_path / "covered-ground.yaml").write_text(
        "anchors:\n  苏伊士: {count: 3, episodes: []}\n",
        encoding="utf-8",
    )

    cards = load_cards(str(tmp_path))
    # Exactly the one real stance card; the store did NOT leak in.
    assert len(cards) == 1
    assert "苏伊士" not in str(cards[0])

    bodies = gather_recent_bodies(str(tmp_path), today="2026-06-14")
    # Only the .md file is a body; the store is not a body.
    assert len(bodies) == 1
    assert "苏伊士运河" in bodies[0]["excerpt"]
