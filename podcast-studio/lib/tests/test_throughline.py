"""Tests for lib/throughline.py — throughline portfolio mechanics.

Written before lib/throughline.py exists; collection must fail at this
point (`No module named 'lib.throughline'`).

Pins:
- `mine_candidates(cards, window)`: aggregates `topics` across prior
  stance cards within a recency window; returns ranked recurring-theme
  candidates.
- `load_obsessions(output_dir)` / `save_obsessions(output_dir, obsessions)`:
  persists user-confirmed obsessions to `{output_dir}/throughline.yaml`
  (atomic write); schema-validate on load (list of {id, theme, confirmed_at}).
- `pick_to_deepen(obsessions, cards)`: choose the confirmed obsession
  least-recently appearing in card topics; flag "new_angle".
- `empty / first-run path`: no prior topics or no store → empty candidates,
  no error, no crash.
- `malformed store`: corrupt obsession store → raise naming the file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.throughline import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import throughline  # noqa: F401
    assert hasattr(throughline, "mine_candidates")
    assert hasattr(throughline, "load_obsessions")
    assert hasattr(throughline, "save_obsessions")
    assert hasattr(throughline, "pick_to_deepen")


# ---------- helpers ----------

def _card(date: str, show: str, topics: list[str]) -> dict:
    """Build a minimal stance card with topics."""
    return {
        "episode": {"date": date, "show": show},
        "bets": [],
        "open_questions": [],
        "named_concept": [],
        "topics": topics,
    }


def _write_card(output_dir, date, show, card) -> None:
    from lib.stance import write_card
    write_card(str(output_dir), date, show, card)


# ---------- mine_candidates ----------

def test_mine_candidates_from_topics(tmp_path):
    """mine_candidates aggregates `topics` from prior cards, ranks
    recurring themes by frequency. Within a recency window, a topic
    appearing across multiple cards surfaces as a candidate."""
    from lib.stance import load_cards
    from lib.throughline import mine_candidates

    # Seed stance cards with overlapping topics
    c1 = _card("2026-06-01", "morning", topics=["AI agents", "knowledge work"])
    c2 = _card("2026-06-02", "morning", topics=["AI agents", "creative tools"])
    c3 = _card("2026-06-03", "morning", topics=["AI agents", "knowledge work"])
    _write_card(tmp_path, "2026-06-01", "morning", c1)
    _write_card(tmp_path, "2026-06-02", "morning", c2)
    _write_card(tmp_path, "2026-06-03", "morning", c3)

    cards = load_cards(str(tmp_path))
    # Window covers all 3 cards (anchor "2026-06-08", window 7d)
    candidates = mine_candidates(cards, window_days=7)
    # "AI agents" appears in 3 cards → top candidate
    # "knowledge work" appears in 2 cards
    # "creative tools" appears in 1 card
    assert isinstance(candidates, list)
    assert len(candidates) >= 1
    # Top candidate must be "AI agents" (count 3)
    top = candidates[0]
    assert top["topic"] == "AI agents"
    assert top["count"] == 3
    # At least 2 distinct candidates aggregated
    topics_seen = {c["topic"] for c in candidates}
    assert "AI agents" in topics_seen
    assert "knowledge work" in topics_seen


def test_mine_candidates_window_excludes_old(tmp_path):
    """Topics from cards outside the recency window are excluded."""
    from lib.stance import load_cards
    from lib.throughline import mine_candidates

    # Anchor "2026-06-08"; window 3 days → only 2026-06-05..06-08
    old = _card("2026-06-01", "morning", topics=["old theme"])
    recent = _card("2026-06-07", "morning", topics=["recent theme"])
    _write_card(tmp_path, "2026-06-01", "morning", old)
    _write_card(tmp_path, "2026-06-07", "morning", recent)

    cards = load_cards(str(tmp_path))
    candidates = mine_candidates(cards, window_days=3, today="2026-06-08")
    topics_seen = {c["topic"] for c in candidates}
    assert "recent theme" in topics_seen
    assert "old theme" not in topics_seen


# ---------- save_obsessions / load_obsessions roundtrip ----------

def test_persist_confirmed_obsessions_atomic(tmp_path):
    """save_obsessions writes to {output_dir}/throughline.yaml atomically;
    load_obsessions returns the persisted list unchanged. Atomic: temp
    file is replaced; cleanup on error (no orphan)."""
    from lib.throughline import save_obsessions, load_obsessions

    obsessions = [
        {"id": "obs-1", "theme": "AI agents and knowledge work", "confirmed_at": "2026-06-05"},
        {"id": "obs-2", "theme": "creative tools as thinking partners", "confirmed_at": "2026-06-05"},
    ]
    save_obsessions(str(tmp_path), obsessions)

    loaded = load_obsessions(str(tmp_path))
    assert loaded == obsessions

    # File lives at the canonical name
    path = Path(tmp_path) / "throughline.yaml"
    assert path.exists()

    # No orphan temp files
    for entry in tmp_path.iterdir():
        assert not entry.name.endswith(".tmp")
        assert ".partial" not in entry.name


def test_persist_overwrite_replaces(tmp_path):
    """A second save_obsessions call overwrites the file cleanly (this is
    NOT append-only like stance cards — it's user-curated)."""
    from lib.throughline import save_obsessions, load_obsessions

    first = [{"id": "obs-1", "theme": "old theme", "confirmed_at": "2026-06-01"}]
    second = [
        {"id": "obs-1", "theme": "new theme", "confirmed_at": "2026-06-05"},
        {"id": "obs-2", "theme": "added theme", "confirmed_at": "2026-06-05"},
    ]
    save_obsessions(str(tmp_path), first)
    save_obsessions(str(tmp_path), second)

    loaded = load_obsessions(str(tmp_path))
    assert loaded == second


def test_save_obsessions_validates_schema(tmp_path):
    """save_obsessions rejects malformed entries (e.g. missing required
    fields) so a corrupted store cannot be persisted."""
    from lib.throughline import save_obsessions

    # Missing `confirmed_at`
    bad = [{"id": "obs-1", "theme": "x"}]
    with pytest.raises(Exception):
        save_obsessions(str(tmp_path), bad)

    # Non-list input
    with pytest.raises(Exception):
        save_obsessions(str(tmp_path), {"id": "obs-1", "theme": "x", "confirmed_at": "2026-06-05"})

    # Missing `theme`
    with pytest.raises(Exception):
        save_obsessions(str(tmp_path), [{"id": "obs-1", "confirmed_at": "2026-06-05"}])

    # Missing `id`
    with pytest.raises(Exception):
        save_obsessions(str(tmp_path), [{"theme": "x", "confirmed_at": "2026-06-05"}])


# ---------- load_obsessions: empty / first-run ----------

def test_load_obsessions_empty_first_run(tmp_path):
    """load_obsessions on a directory with no throughline.yaml returns
    an empty list (no error, no crash)."""
    from lib.throughline import load_obsessions
    loaded = load_obsessions(str(tmp_path))
    assert loaded == []


def test_load_obsessions_missing_dir(tmp_path):
    """load_obsessions on a non-existent directory returns [] (no crash)."""
    from lib.throughline import load_obsessions
    missing = tmp_path / "does-not-exist"
    loaded = load_obsessions(str(missing))
    assert loaded == []


# ---------- pick_to_deepen ----------

def test_pick_to_deepen_least_recent(tmp_path):
    """pick_to_deepen returns the confirmed obsession least-recently
    appearing in card topics. With a fresh store and prior cards
    covering some obsessions, an un-covered obsession is due first."""
    from lib.stance import load_cards
    from lib.throughline import pick_to_deepen

    obsessions = [
        {"id": "obs-1", "theme": "AI agents", "confirmed_at": "2026-06-01"},
        {"id": "obs-2", "theme": "creative tools", "confirmed_at": "2026-06-01"},
    ]
    # Prior cards already covered "AI agents" recently
    cards = [
        _card("2026-06-07", "morning", topics=["AI agents", "other"]),
        _card("2026-06-08", "morning", topics=["AI agents"]),
    ]
    pick = pick_to_deepen(obsessions, cards)
    # "creative tools" (obs-2) is never covered in any card → last_seen=None →
    # it is the least-recently-deepened (most due). Pin the exact ordering so a
    # sort-key regression cannot pass (NTH-1).
    assert pick is not None
    assert pick["id"] == "obs-2"


def test_pick_to_deepen_all_covered_recent_returns_first(tmp_path):
    """If all confirmed obsessions were covered in the most recent card,
    pick_to_deepen still returns ONE of them (least-recently-deepened,
    falls back to first by confirmation order)."""
    from lib.stance import load_cards
    from lib.throughline import pick_to_deepen

    obsessions = [
        {"id": "obs-1", "theme": "theme A", "confirmed_at": "2026-06-01"},
        {"id": "obs-2", "theme": "theme B", "confirmed_at": "2026-06-01"},
        {"id": "obs-3", "theme": "theme C", "confirmed_at": "2026-06-01"},
    ]
    cards = [
        _card("2026-06-08", "morning", topics=["theme A", "theme B", "theme C"]),
    ]
    pick = pick_to_deepen(obsessions, cards)
    # All three covered equally recently → tiebreak falls back to first by
    # confirmation order (obs-1). Pin it exactly (NTH-1).
    assert pick is not None
    assert pick["id"] == "obs-1"


def test_pick_to_deepen_empty_obsessions(tmp_path):
    """pick_to_deepen with no confirmed obsessions returns None (no error)."""
    from lib.stance import load_cards
    from lib.throughline import pick_to_deepen

    pick = pick_to_deepen([], [])
    assert pick is None


def test_pick_to_deepen_marks_new_angle(tmp_path):
    """The returned pick has a `new_angle` flag (bool) — True if the
    obsession hasn't appeared in any prior card (i.e. fresh)."""
    from lib.throughline import pick_to_deepen
    obsessions = [
        {"id": "obs-1", "theme": "fresh theme", "confirmed_at": "2026-06-01"},
    ]
    pick = pick_to_deepen(obsessions, [])
    assert pick is not None
    # No prior cards → no card mentions the theme → new_angle=True
    assert "new_angle" in pick
    assert pick["new_angle"] is True


# ---------- empty / first-run path for mine_candidates ----------

def test_mine_candidates_empty_first_run(tmp_path):
    """mine_candidates with no prior cards returns [] (no error)."""
    from lib.throughline import mine_candidates
    candidates = mine_candidates([], window_days=7)
    assert candidates == []


def test_mine_candidates_no_topics(tmp_path):
    """mine_candidates with cards that have empty `topics` returns []."""
    from lib.stance import load_cards
    from lib.throughline import mine_candidates
    # Card with no topics
    c = _card("2026-06-08", "morning", topics=[])
    _write_card(tmp_path, "2026-06-08", "morning", c)
    cards = load_cards(str(tmp_path))
    candidates = mine_candidates(cards, window_days=7)
    assert candidates == []


# ---------- malformed store raises ----------

def test_malformed_store_raises(tmp_path):
    """A corrupt obsession store (e.g. not a list, or items missing
    required fields) → load_obsessions raises naming the file. Fail-closed:
    silently treating as 'empty' would let a corrupted store lose the
    user's confirmed obsessions."""
    from lib.throughline import load_obsessions

    store_path = Path(tmp_path) / "throughline.yaml"
    # Garbage that won't parse as YAML
    store_path.write_text("this is: not: valid: yaml: [[[", encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        load_obsessions(str(tmp_path))
    # Error must name the offending file
    assert "throughline.yaml" in str(excinfo.value)


def test_malformed_store_missing_fields_raises(tmp_path):
    """A store whose items are missing required fields raises (not a
    silent pass-through)."""
    from lib.throughline import load_obsessions

    store_path = Path(tmp_path) / "throughline.yaml"
    # Valid YAML list, but items missing `confirmed_at`
    store_path.write_text("- id: obs-1\n  theme: x\n", encoding="utf-8")

    with pytest.raises(Exception):
        load_obsessions(str(tmp_path))
