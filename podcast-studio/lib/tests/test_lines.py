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


@pytest.mark.parametrize("show", ["unknownshow", "xxx", ""])
def test_get_line_unknown_raises(show):
    """An unregistered show fails closed, naming the show.

    NOTE (must-fix #1, Phase 2): "papers" was originally in this parametrize
    list under the assumption it was unregistered. After Task 6-impl
    registers PAPER_LINE in `_LINE_REGISTRY`, `get_line("papers")` no
    longer raises. We swap it out for `"unknownshow"` — a still-unregistered
    sentinel — so the unknown-show fail-closed case still tests an
    unknown show (its pinning value: an UNREGISTERED name raises).
    """
    with pytest.raises(ValueError) as ei:
        get_line(show)
    assert repr(show) in str(ei.value) or show in str(ei.value)


# ---------------------------------------------------------------------------
# Phase 2 — paper-line bundle registration (Task 6-impl).
# ---------------------------------------------------------------------------

def test_get_line_papers_bundle():
    """After Task 6-impl registers PAPER_LINE, `get_line("papers")`
    returns a LineBundle whose line_id is "paper" and whose agent_dir
    points at the paper-personas directory (`agents/papers/`). This is the
    structural proof that the engine can resolve the paper show."""
    from lib.lines import LineBundle, get_line

    bundle = get_line("papers")
    assert isinstance(bundle, LineBundle)
    # line_id distinguishes paper from opinion (morning/evening both
    # resolve to "opinion").
    assert bundle.line_id == "paper", (
        f"paper bundle line_id must be 'paper', got {bundle.line_id!r}"
    )
    # agent_dir is what the dispatch persona reads from; for the paper
    # line it's agents/papers/ (Task 5).
    assert bundle.agent_dir == "agents/papers", (
        f"paper bundle agent_dir must be 'agents/papers', got "
        f"{bundle.agent_dir!r}"
    )
    # The bundle must expose the D-004 contract shape (same as the
    # opinion bundle — required by the line-agnostic engine).
    for attr in (
        "topology", "gate_map", "executor_map",
        "editorial_loader", "floor_fn",
    ):
        assert hasattr(bundle, attr), f"LineBundle missing {attr!r}"
    # The topology callable, invoked with the registered show name, must
    # equal the collection topology that lib/pipeline_papers builds.
    from lib.pipeline_papers import _build_paper_steps
    assert bundle.topology("papers") == _build_paper_steps()


def test_opinion_topology_unchanged_after_paper_registration():
    """The ZERO-CHANGE pin: registering the paper bundle MUST NOT alter
    the morning/evening opinion topology. The frozen golden (commited in
    `lib/tests/fixtures/topology_golden.json`) is the non-tautological
    reference — a live load_pipeline() call would be tautological.

    This test re-asserts the golden AFTER Task 6-impl runs. If the paper
    registration accidentally overwrites or aliases the opinion entries
    in `_LINE_REGISTRY`, the morning/evening golden would diverge.
    """
    # Re-load the golden from disk (do NOT cache _GOLDEN against a live
    # load_pipeline() — that would be tautological).
    golden = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "topology_golden.json")
        .read_text(encoding="utf-8")
    )
    # Morning and evening topologies are byte-identical to the golden.
    assert get_line("morning").topology("morning") == golden["morning"]
    assert get_line("evening").topology("evening") == golden["evening"]
    # And morning == evening (one line, two shows).
    assert (
        get_line("morning").topology("morning")
        == get_line("evening").topology("evening")
    )


def test_opinion_and_paper_lines_are_distinct_objects():
    """The paper bundle must NOT be the same LineBundle instance as the
    opinion bundle. The engine looks up bindings via get_line(show) and
    must see two distinct bundles so per-line state (gate_map, executor_map,
    agent_dir) does not bleed across lines."""
    opinion = get_line("morning")
    paper = get_line("papers")
    assert opinion is not paper, (
        "paper bundle must be a distinct LineBundle instance from opinion"
    )
    assert opinion.line_id != paper.line_id, (
        f"line_id must differ: opinion={opinion.line_id!r} "
        f"paper={paper.line_id!r}"
    )


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


# ---------------------------------------------------------------------------
# Phase 3 — LineBundle.whitelist field (Task 1-tests).
#
# The bundle gains a `whitelist` field carrying the per-line agent
# whitelist the runner threads into dispatch_persona. Opinion must
# carry AGENT_WHITELIST (byte-identical to today); paper must carry
# PAPER_AGENT_WHITELIST.
# ---------------------------------------------------------------------------

def test_bundle_has_whitelist_field():
    """LineBundle must expose a `whitelist` field.

    Opinion (morning/evening) → AGENT_WHITELIST (byte-identical to pre-P3).
    Paper (papers) → PAPER_AGENT_WHITELIST (curator/ledger-writer).

    The `whitelist` field is the per-line agent whitelist the runner
    threads into dispatch_persona via the `whitelist=` kwarg. Defaults
    must preserve opinion behavior byte-identically (per the
    "byte-identical despite the edit" obligation).
    """
    from lib.dispatch import AGENT_WHITELIST
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST

    # Morning + evening share ONE line → one whitelist.
    opinion_m = get_line("morning")
    opinion_e = get_line("evening")
    paper = get_line("papers")

    # 1. The field exists on all bundles.
    for b, label in ((opinion_m, "opinion(morning)"), (opinion_e, "opinion(evening)"), (paper, "paper")):
        assert hasattr(b, "whitelist"), f"{label} bundle missing `whitelist` field"
        assert b.whitelist is not None, (
            f"{label} bundle.whitelist must be set, got None"
        )

    # 2. Opinion bundles carry AGENT_WHITELIST (the existing opinion
    #    whitelist) — byte-identical to pre-P3 behavior.
    assert opinion_m.whitelist == AGENT_WHITELIST, (
        f"opinion morning whitelist must equal dispatch.AGENT_WHITELIST; "
        f"got {sorted(opinion_m.whitelist)}"
    )
    assert opinion_e.whitelist == AGENT_WHITELIST, (
        f"opinion evening whitelist must equal dispatch.AGENT_WHITELIST; "
        f"got {sorted(opinion_e.whitelist)}"
    )

    # 3. Paper bundle carries PAPER_AGENT_WHITELIST (curator/ledger-writer).
    assert paper.whitelist == PAPER_AGENT_WHITELIST, (
        f"paper whitelist must equal PAPER_AGENT_WHITELIST; "
        f"got {sorted(paper.whitelist)}"
    )

    # 4. Opinion and paper whitelists are distinct objects (no shared
    #    mutable state across lines — the firewall half).
    assert opinion_m.whitelist is not paper.whitelist, (
        "opinion and paper whitelists must be distinct objects "
        "(no shared mutable state)"
    )
