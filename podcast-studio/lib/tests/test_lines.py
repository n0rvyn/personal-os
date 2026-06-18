"""Tests for the line registry (lib/lines.py) — Phase 1 engine extraction.

Pins the LineBundle contract and the opinion-line (morning/evening) wiring.
The opinion bundle must be byte-identical to today's behavior: its topology
must equal the frozen pre-refactor golden (lib/tests/fixtures/topology_golden.json),
NOT a live load_pipeline() call (which would be tautological).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.lines import LineBundle, get_line

_GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "topology_golden.json").read_text(
        encoding="utf-8"
    )
)


def test_get_line_morning_evening_same_opinion():
    """morning and evening are two shows on ONE line (opinion)."""
    m = get_line("morning")
    e = get_line("evening")
    assert isinstance(m, LineBundle) and isinstance(e, LineBundle)
    assert m.line_id == "opinion"
    assert e.line_id == "opinion"
    # same line → same bundle object
    assert m is e


@pytest.mark.parametrize("show", ["papers", "xxx", ""])
def test_get_line_unknown_raises(show):
    """An unregistered show fails closed, naming the show."""
    with pytest.raises(ValueError) as ei:
        get_line(show)
    assert repr(show) in str(ei.value) or show in str(ei.value)


def test_bundle_topology_matches_frozen_golden():
    """Opinion-line topology must equal the FROZEN pre-refactor golden.

    This is the byte-identical pin's non-tautological half: the expected
    value is the committed fixture, never a live load_pipeline() call.
    """
    assert get_line("morning").topology("morning") == _GOLDEN["morning"]
    assert get_line("evening").topology("evening") == _GOLDEN["evening"]


def test_bundle_exposes_contract():
    """LineBundle exposes the D-004 shape + floor_fn."""
    b = get_line("morning")
    for attr in (
        "line_id",
        "topology",
        "gate_map",
        "executor_map",
        "editorial_loader",
        "agent_dir",
        "floor_fn",
    ):
        assert hasattr(b, attr), f"LineBundle missing {attr!r}"
    assert b.agent_dir == "agents"
    assert callable(b.topology)
    assert callable(b.gate_map)
    assert callable(b.editorial_loader)
    assert callable(b.floor_fn)
