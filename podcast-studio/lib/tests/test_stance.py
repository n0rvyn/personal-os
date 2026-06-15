"""Tests for lib/stance.py — stance-card continuity mechanics.

Written before lib/stance.py exists; collection must fail at this
point (`No module named 'lib.stance'`).

Pins:
- Append-only write (refuse overwrite, old card byte-unchanged)
- Prior-card load + malformed→raise
- Empty `{}` placeholder is SKIPPED, not raised
- Due-bet filtering by `settle_by`
- Morning→evening same-day open-question carry
- Anti-fabrication invariant: settles.ref must match a prior bet id
- Same-card self-reference bypass closed
- No confidence-number FIELDS (numbers inside claim text are allowed)
- Future-dated write rejected
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.stance import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import stance  # noqa: F401
    assert hasattr(stance, "load_cards")
    assert hasattr(stance, "write_card")
    assert hasattr(stance, "due_bets")
    assert hasattr(stance, "carried_open_questions")
    assert hasattr(stance, "validate_settlement")
    assert hasattr(stance, "new_bet_id")
    assert hasattr(stance, "stance_path")


# ---------- stance_path (extracted from episode.py) ----------

def test_stance_path_naming(tmp_path):
    """stance_path returns `{output_dir}/{date}-{show}.stance.yaml`."""
    from lib.stance import stance_path
    p = stance_path(str(tmp_path), "2026-06-08", "morning")
    assert p.name == "2026-06-08-morning.stance.yaml"
    assert str(p).startswith(str(tmp_path.resolve()))


# ---------- helpers ----------

def _card(date: str, show: str, **kwargs) -> dict:
    """Build a minimal valid card. Tests override/extend fields."""
    card = {
        "episode": {"date": date, "show": show},
        "bets": [],
        "open_questions": [],
        "named_concept": [],
        "topics": [],
    }
    card.update(kwargs)
    return card


def _bet(bet_id: str, claim: str = "claim text", settle_by: str = "2026-06-10",
         status: str = "open", horizon: str = "3d") -> dict:
    return {
        "id": bet_id,
        "claim": claim,
        "horizon": horizon,
        "settle_by": settle_by,
        "status": status,
    }


def _write_card(output_dir, date, show, card) -> None:
    from lib.stance import write_card
    write_card(str(output_dir), date, show, card)


# ---------- write then load roundtrip ----------

def test_write_then_load_roundtrip(tmp_path):
    """A card written to the canonical stance path loads back identically
    (roundtrip preserves the card's data)."""
    from lib.stance import load_cards
    card = _card("2026-06-08", "morning", bets=[_bet("bet-20260608morning-1")])
    _write_card(tmp_path, "2026-06-08", "morning", card)

    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0]["episode"]["date"] == "2026-06-08"
    assert loaded[0]["episode"]["show"] == "morning"
    assert len(loaded[0]["bets"]) == 1
    assert loaded[0]["bets"][0]["id"] == "bet-20260608morning-1"


# ---------- append-only refuse overwrite ----------

def test_append_only_refuses_overwrite(tmp_path):
    """A second write for the same `{date}-{show}` raises; the existing
    file is byte-unchanged (append-only invariant)."""
    card1 = _card("2026-06-08", "morning", bets=[_bet("bet-20260608morning-1")])
    _write_card(tmp_path, "2026-06-08", "morning", card1)

    from lib.stance import stance_path
    path = stance_path(str(tmp_path), "2026-06-08", "morning")
    original = path.read_bytes()

    card2 = _card("2026-06-08", "morning", bets=[_bet("bet-20260608morning-2")])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", card2)

    # Existing file must be byte-unchanged
    assert path.read_bytes() == original


# ---------- due_bets filter ----------

def test_due_bets_filter(tmp_path):
    """due_bets returns bets with status=open and settle_by<=today; future
    and closed bets are excluded."""
    from lib.stance import load_cards, due_bets
    card = _card("2026-06-01", "morning", bets=[
        _bet("bet-1", settle_by="2026-06-05", status="open"),     # due
        _bet("bet-2", settle_by="2026-06-15", status="open"),     # future
        _bet("bet-3", settle_by="2026-06-05", status="closed"),   # closed
        _bet("bet-4", settle_by="2026-05-01", status="open"),     # past (still due)
    ])
    _write_card(tmp_path, "2026-06-01", "morning", card)
    cards = load_cards(str(tmp_path))

    due = due_bets(cards, "2026-06-08")
    due_ids = {b["id"] for b in due}
    assert "bet-1" in due_ids
    assert "bet-4" in due_ids
    assert "bet-2" not in due_ids   # future
    assert "bet-3" not in due_ids   # closed


# ---------- open_questions carry (morning → same-day evening) ----------

def test_open_questions_carry(tmp_path):
    """A morning card's open_questions surface for the same-day evening."""
    from lib.stance import load_cards, carried_open_questions
    morning = _card("2026-06-08", "morning", open_questions=[
        "Q1: does X generalize?",
        "Q2: what about Y?",
    ])
    _write_card(tmp_path, "2026-06-08", "morning", morning)
    cards = load_cards(str(tmp_path))

    carried = carried_open_questions(cards, "2026-06-08", "evening")
    assert "Q1: does X generalize?" in carried
    assert "Q2: what about Y?" in carried


def test_open_questions_do_not_carry_to_evening_different_day(tmp_path):
    """Morning questions persist for same-day evening; a different day
    does NOT pick up another day's questions (each day is its own arc)."""
    from lib.stance import load_cards, carried_open_questions
    morning = _card("2026-06-08", "morning", open_questions=["Q1: day1"])
    _write_card(tmp_path, "2026-06-08", "morning", morning)
    cards = load_cards(str(tmp_path))

    # Day later: no carry
    carried = carried_open_questions(cards, "2026-06-09", "evening")
    assert carried == []


# ---------- anti-fabrication invariant: valid ref accepted ----------

def test_settlement_valid_ref_accepted(tmp_path):
    """A new card whose `settles.ref` matches a prior bet id writes OK."""
    from lib.stance import load_cards
    prior = _card("2026-06-01", "morning", bets=[
        _bet("bet-prior-1", claim="X will happen", settle_by="2026-06-05"),
    ])
    _write_card(tmp_path, "2026-06-01", "morning", prior)

    new = _card("2026-06-08", "morning", settles=[
        {"ref": "bet-prior-1", "verdict": "hit", "evidence": "it happened"},
    ])
    # Should NOT raise
    _write_card(tmp_path, "2026-06-08", "morning", new)
    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[1]["settles"][0]["ref"] == "bet-prior-1"


# ---------- anti-fabrication invariant: fabricated ref rejected ----------

def test_settlement_fabricated_ref_rejected(tmp_path):
    """`settles.ref` to a non-existent bet id → write rejected
    (anti-fabrication invariant: a card can only settle a bet that
    actually exists in a prior card)."""
    prior = _card("2026-06-01", "morning", bets=[
        _bet("bet-prior-1", settle_by="2026-06-05"),
    ])
    _write_card(tmp_path, "2026-06-01", "morning", prior)

    new = _card("2026-06-08", "morning", settles=[
        {"ref": "bet-DOES-NOT-EXIST", "verdict": "hit", "evidence": "..."},
    ])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", new)


# ---------- same-card self-reference bypass closed ----------

def test_settlement_same_card_ref_rejected(tmp_path):
    """A card whose `settles.ref` points at a bet defined in the SAME
    card (not a prior card) → rejected. Closes the self-reference
    bypass: you cannot settle a bet introduced in the same card you
    are writing."""
    card = _card("2026-06-08", "morning",
        bets=[_bet("bet-self-1", settle_by="2026-06-10")],
        settles=[{"ref": "bet-self-1", "verdict": "hit", "evidence": "..."}],
    )
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", card)


# ---------- no confidence-number field rejected ----------

def test_no_confidence_number_rejected(tmp_path):
    """A card carrying a numeric confidence-style FIELD is rejected.
    Numbers INSIDE free-text `claim` are allowed (the rule targets
    confidence scores, not all numbers)."""
    # Numeric confidence FIELD at top level → rejected
    bad = _card("2026-06-08", "morning", confidence=0.8)
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad)

    # Numeric confidence FIELD inside a bet → rejected
    bad_bet = _card("2026-06-08", "morning", bets=[
        {"id": "bet-1", "claim": "x", "horizon": "3d", "settle_by": "2026-06-10",
         "status": "open", "confidence": 0.5},
    ])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_bet)


def test_numbers_in_claim_text_allowed(tmp_path):
    """Numbers inside the free-text `claim` are allowed (the no-confidence
    rule targets confidence FIELDS, not every digit)."""
    card = _card("2026-06-08", "morning", bets=[
        _bet("bet-1", claim="命中率会超过 60% 持续 12 个月", settle_by="2026-06-10"),
    ])
    # Should NOT raise
    _write_card(tmp_path, "2026-06-08", "morning", card)


# ---------- malformed card raises ----------

def test_malformed_card_raises(tmp_path):
    """A corrupt prior card → load raises naming the file. Fail-closed:
    do not silently treat as 'no bets' (that would hide a settlement
    and enable a fabricated fresh start)."""
    from lib.stance import stance_path
    path = stance_path(str(tmp_path), "2026-06-01", "morning")
    # Garbage that won't parse as a card at all
    path.write_text("this is: not: valid: yaml: [[[", encoding="utf-8")

    from lib.stance import load_cards
    with pytest.raises(Exception) as excinfo:
        load_cards(str(tmp_path))
    # The error message should name the offending file
    assert "2026-06-01-morning" in str(excinfo.value)


# ---------- empty placeholder card skipped ----------

def test_empty_placeholder_card_skipped(tmp_path):
    """An empty `{}`/contentless stance file (e.g. a leftover Phase-2
    placeholder) is SKIPPED on load (treated as no card), not raised
    as malformed — so a prior empty placeholder never blocks continuity."""
    from lib.stance import stance_path, load_cards
    path = stance_path(str(tmp_path), "2026-06-01", "morning")
    path.write_text("{}", encoding="utf-8")

    # Should NOT raise; should return empty list
    loaded = load_cards(str(tmp_path))
    assert loaded == []


def test_completely_empty_file_skipped(tmp_path):
    """A zero-byte stance file is also treated as a placeholder and skipped."""
    from lib.stance import stance_path, load_cards
    path = stance_path(str(tmp_path), "2026-06-01", "morning")
    path.write_text("", encoding="utf-8")

    loaded = load_cards(str(tmp_path))
    assert loaded == []


# ---------- future-dated card rejected ----------

def test_future_dated_card_rejected(tmp_path):
    """`write_card` with a `date` in the future → rejected (basic
    backdating guard; you cannot settle a not-yet episode)."""
    card = _card("2099-12-31", "morning", bets=[_bet("bet-future-1")])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2099-12-31", "morning", card)


# ---------- new_bet_id format ----------

def test_new_bet_id_format():
    """new_bet_id produces `bet-{YYYYMMDD}{show}-{n}`."""
    from lib.stance import new_bet_id
    bid = new_bet_id("2026-06-08", "morning", 1)
    assert bid == "bet-20260608morning-1"
    bid3 = new_bet_id("2026-06-08", "morning", 3)
    assert bid3 == "bet-20260608morning-3"


# ---------- atomic write: no orphan temp on error ----------

def test_atomic_write_no_orphan_on_error(tmp_path):
    """If write_card fails (e.g. overwrite), no temp file is left
    behind in output_dir."""
    card1 = _card("2026-06-08", "morning", bets=[_bet("bet-1")])
    _write_card(tmp_path, "2026-06-08", "morning", card1)

    card2 = _card("2026-06-08", "morning", bets=[_bet("bet-2")])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", card2)

    # No .tmp / .partial / temp files left behind
    for entry in tmp_path.iterdir():
        assert not entry.name.endswith(".tmp")
        assert ".partial" not in entry.name


# ---------- resonance field (Phase 5: optional, additive) ----------

def test_resonance_field_accepted(tmp_path):
    """`resonance` is an OPTIONAL field on a stance card (Phase 5).
    Accepts a plain string OR a list of strings. Numbers inside the
    free-text resonance (e.g. "ten-times more shareable") are fine —
    the rule targets confidence-style FIELDS, not all digits."""
    # String form
    card_str = _card(
        "2026-06-08", "morning",
        resonance="the throughline on creative tools is the kind of "
                  "thing listeners would forward to a friend",
    )
    _write_card(tmp_path, "2026-06-08", "morning", card_str)

    # List-of-strings form
    card_list = _card(
        "2026-06-08", "evening",
        resonance=[
            "memorable naming (knowledge work as a craft)",
            "a question the audience can sit with for a week",
        ],
    )
    _write_card(tmp_path, "2026-06-08", "evening", card_list)

    from lib.stance import load_cards
    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 2
    # Sort order: (date asc, show asc) → evening < morning alphabetically,
    # so on the same date the evening card sorts to index 0 and morning
    # to index 1. The list-form resonance is on the evening card; the
    # string-form on the morning card.
    assert isinstance(loaded[0]["resonance"], list)
    assert loaded[0]["resonance"][0].startswith("memorable naming")
    assert loaded[1]["resonance"].startswith("the throughline on creative")


def test_resonance_rejects_number(tmp_path):
    """A numeric `resonance` (e.g. a confidence-style score) is rejected:
    the temperature principle forbids confidence-style numeric FIELDS,
    even on this new Phase-5 surface. The field is qualitative only."""
    from lib.stance import load_cards

    # Plain number → rejected at write
    bad_float = _card("2026-06-08", "morning", resonance=0.8)
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_float)

    # Int → rejected
    bad_int = _card("2026-06-08", "morning", resonance=7)
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_int)

    # List containing a number → rejected
    bad_mixed = _card("2026-06-08", "morning", resonance=["good", 5])
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_mixed)

    # Boolean (also rejected — not textual)
    bad_bool = _card("2026-06-08", "morning", resonance=True)
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_bool)

    # Dict → rejected
    bad_dict = _card(
        "2026-06-08", "morning",
        resonance={"score": 0.9, "note": "x"},
    )
    with pytest.raises(Exception):
        _write_card(tmp_path, "2026-06-08", "morning", bad_dict)

    # No card was written
    assert load_cards(str(tmp_path)) == []


def test_stance_card_exists_preflight(tmp_path):
    """stance_card_exists is the same-day re-run tripwire: False before the
    slot is written, True after — lets the pipeline fail fast pre-publish."""
    from lib.stance import stance_card_exists, write_card
    assert stance_card_exists(tmp_path, "2026-06-13", "morning") is False
    card = {
        "episode": {"date": "2026-06-13", "show": "morning"},
        "bets": [], "open_questions": [], "settles": [],
        "named_concept": [], "topics": [],
    }
    write_card(tmp_path, "2026-06-13", "morning", card)
    assert stance_card_exists(tmp_path, "2026-06-13", "morning") is True
    # a different show in the same day is a different slot
    assert stance_card_exists(tmp_path, "2026-06-13", "evening") is False


# ---------- apparatus_used field (Phase 2: covered-ground audit) ----------

def test_write_card_accepts_apparatus_used(tmp_path):
    """A card with `apparatus_used: list[str]` is accepted by _validate_card_shape
    and round-trips through load_cards (Phase 2: covered-ground cross-episode
    memory audits which signature anchors/analogies/frames each episode used)."""
    from lib.stance import load_cards
    card = _card(
        "2026-06-08", "morning",
        apparatus_used=["1956苏伊士运河危机", "印刷术类比"],
    )
    _write_card(tmp_path, "2026-06-08", "morning", card)

    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0]["apparatus_used"] == ["1956苏伊士运河危机", "印刷术类比"]


def test_apparatus_used_must_be_list(tmp_path):
    """`apparatus_used` must be a list[str]. A non-list value (here a bare
    string) is rejected by _validate_card_shape — naming the field in the
    error so the writer can correct it."""
    bad_string = _card("2026-06-08", "morning", apparatus_used="苏伊士")
    with pytest.raises(Exception) as excinfo:
        _write_card(tmp_path, "2026-06-08", "morning", bad_string)
    assert "apparatus_used" in str(excinfo.value)


def test_apparatus_used_optional(tmp_path):
    """A card without `apparatus_used` is unaffected (backward-compat:
    Phase 2 adds the field as optional; pre-Phase-2 cards stay valid)."""
    from lib.stance import load_cards
    # No apparatus_used in the card at all
    card = _card("2026-06-08", "morning", bets=[_bet("bet-1")])
    _write_card(tmp_path, "2026-06-08", "morning", card)

    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 1
    # apparatus_used absent → not in the loaded dict
    assert "apparatus_used" not in loaded[0]


def test_card_with_only_apparatus_not_placeholder(tmp_path):
    """A card whose only non-`episode` content is `apparatus_used` is NOT
    treated as an empty placeholder by _is_empty_card_placeholder — so it
    loads and is visible to covered-ground. (A bare `{}` placeholder must
    NOT block continuity; conversely, a card that records apparatus used
    must not be silently dropped.)"""
    from lib.stance import load_cards
    # Minimal card: episode + apparatus_used only. The Phase-1 required
    # keys (bets/open_questions) are present-empty.
    card = {
        "episode": {"date": "2026-06-08", "show": "morning"},
        "bets": [],
        "open_questions": [],
        "apparatus_used": ["苏伊士类比"],
    }
    _write_card(tmp_path, "2026-06-08", "morning", card)

    loaded = load_cards(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0]["apparatus_used"] == ["苏伊士类比"]
