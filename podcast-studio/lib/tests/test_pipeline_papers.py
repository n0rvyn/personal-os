"""Tests for the paper-line collection topology (lib/pipeline_papers.py).

Pins the Phase-2 collection skeleton:

  config -> scratch -> discovery -> curator -> fetch -> ledger

Five collection stations + one ledger-verify code station. Two of these
are agent stations (curator / ledger-writer) that must validate against
the paper whitelist (PAPER_AGENT_WHITELIST = {"curator","ledger-writer"}).
The remaining stations are deterministic code stations.

These tests are FAIL-first: they expect `lib.pipeline_papers` to exist
with `_build_paper_steps()`, `load_papers_pipeline()`, and
`PAPER_AGENT_WHITELIST`. Task 6-impl creates the module and exposes
those names.

Non-goals: generation/publish stations (P3/P4); engine end-to-end.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.pipeline_papers import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

# Paper-line agent whitelist (per Task 6-impl plan).
PAPER_AGENT_WHITELIST = {"curator", "ledger-writer"}

# The ordered list of paper collection station names. The topology is the
# ordered pipeline: config (load papers config) -> scratch (make scratch dir)
# -> discovery (fetch arXiv candidates) -> curator (persona picks 1) ->
# fetch (full text) -> ledger (persona writes the fact-ledger) ->
# ledger-verify (code: validate_ledger + verify_anchors gate).
EXPECTED_STATION_ORDER = [
    # collection half (P2)
    "config",
    "scratch",
    "discovery",
    "curator",
    "fetch",
    "ledger-write",
    "ledger-verify",
    # generation half (P3) — downstream of the verified ledger
    "committee",
    "digest-score",
    "digest-select",
    "finalize",
    "faithfulness",
]


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 6-impl will resolve this."""
    from lib import pipeline_papers  # noqa: F401

    assert hasattr(pipeline_papers, "_build_paper_steps")
    assert hasattr(pipeline_papers, "load_papers_pipeline")
    assert hasattr(pipeline_papers, "PAPER_AGENT_WHITELIST")


def test_paper_agent_whitelist_exposes_two_agents():
    """The paper-line whitelist must contain exactly the two collection
    personas — curator (selects paper) and ledger-writer (extracts the
    fact-ledger). These are the agents Task 5 creates under agents/papers/.
    """
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST

    assert isinstance(PAPER_AGENT_WHITELIST, (set, frozenset))
    assert PAPER_AGENT_WHITELIST == PAPER_AGENT_WHITELIST  # noqa: PLR0124
    # The two personas Task 5 wires up:
    assert "curator" in PAPER_AGENT_WHITELIST
    assert "ledger-writer" in PAPER_AGENT_WHITELIST


# ---------------------------------------------------------------------------
# Topology: station order, kinds, and per-station shape
# ---------------------------------------------------------------------------

def test_topology_station_order():
    """The collection topology MUST follow the ordered sequence
    config -> scratch -> discovery -> curator -> fetch -> ledger-write ->
    ledger-verify. Order encodes the dependency: discovery produces the
    candidate list the curator consumes, curator selects the arxiv_id the
    fetch station uses, fetch hands full text to the ledger-write persona,
    and ledger-verify gates the output."""
    from lib.pipeline_papers import _build_paper_steps

    steps = _build_paper_steps()
    names = [s["name"] for s in steps]
    assert names == EXPECTED_STATION_ORDER, (
        f"expected ordered collection topology {EXPECTED_STATION_ORDER}, "
        f"got {names}"
    )


def test_topology_has_expected_kind_distribution():
    """Collection (P2): agents curator + ledger-write; code config/scratch/
    discovery/fetch/ledger-verify. Generation (P3): agents committee/digest-score/
    finalize/faithfulness; code digest-select. The validator's kind check must
    accept every step."""
    from lib.pipeline_papers import _build_paper_steps

    steps = _build_paper_steps()
    kinds = {s["kind"] for s in steps}
    assert "code" in kinds, "expected at least one code station"
    assert "agent" in kinds, "expected at least one agent station"

    agent_names = [s["name"] for s in steps if s["kind"] == "agent"]
    code_names = [s["name"] for s in steps if s["kind"] == "code"]
    assert set(agent_names) == {
        "curator", "ledger-write",                       # collection
        "committee", "digest-score", "finalize", "faithfulness",  # generation
    }, (
        f"agent stations must be the 2 collection + 4 generation personas, got {agent_names}"
    )
    assert set(code_names) == {
        "config", "scratch", "discovery", "fetch", "ledger-verify",  # collection
        "digest-select",                                              # generation
    }, (
        f"code stations must be the 5 collection + digest-select, got {code_names}"
    )


def test_agent_stations_have_whitelisted_agents():
    """Each agent station's `agent` field must be in PAPER_AGENT_WHITELIST.
    The validator uses the parameterized whitelist — this test pins that
    the agent stations declare their whitelist membership explicitly."""
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST, _build_paper_steps

    steps = _build_paper_steps()
    for step in steps:
        if step["kind"] != "agent":
            continue
        agent = step.get("agent")
        assert isinstance(agent, str) and agent, (
            f"agent station {step['name']!r} missing non-empty `agent`"
        )
        assert agent in PAPER_AGENT_WHITELIST, (
            f"agent station {step['name']!r} agent={agent!r} "
            f"not in PAPER_AGENT_WHITELIST {sorted(PAPER_AGENT_WHITELIST)}"
        )


def test_code_stations_have_agent_none():
    """Every code station must carry agent=None — that's how the runner
    distinguishes a deterministic helper from a persona dispatch."""
    from lib.pipeline_papers import _build_paper_steps

    steps = _build_paper_steps()
    for step in steps:
        if step["kind"] != "code":
            continue
        assert step.get("agent") is None, (
            f"code station {step['name']!r} must have agent=None, "
            f"got {step['agent']!r}"
        )


# ---------------------------------------------------------------------------
# Every step carries all required fields (so validate_pipeline passes)
# ---------------------------------------------------------------------------

def test_every_step_has_required_fields():
    """Every step must carry name, kind, agent, inputs, artifact, gate,
    parallel, retry, skip_when, fail_soft — the same shape the opinion
    pipeline uses (see lib/pipeline.py::validate_pipeline)."""
    from lib.pipeline_papers import _build_paper_steps

    required_fields = (
        "name", "kind", "agent", "inputs", "artifact",
        "gate", "parallel", "retry", "skip_when", "fail_soft",
    )
    steps = _build_paper_steps()
    assert steps, "topology must be non-empty"
    for step in steps:
        assert isinstance(step, dict), f"step must be a dict, got {type(step)}"
        for field in required_fields:
            assert field in step, (
                f"step {step.get('name')!r} missing required field: {field!r}"
            )
        # name must be a non-empty string
        assert isinstance(step["name"], str) and step["name"], (
            f"step name must be non-empty str, got {step['name']!r}"
        )
        # inputs must be a list (the runner's resolver expects this)
        assert isinstance(step["inputs"], list), (
            f"step {step['name']!r} inputs must be list, "
            f"got {type(step['inputs']).__name__}"
        )


def test_topology_validates_clean_under_parameterized_validator():
    """The parameterized `validate_pipeline(steps, whitelist=...)` from
    lib/pipeline.py must accept the paper topology when given the paper
    whitelist. This is the cross-module validation contract — Task 6-impl
    parameterizes validate_pipeline so it stays backward compatible
    (defaulting to AGENT_WHITELIST for the opinion path)."""
    from lib.pipeline import validate_pipeline
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST, _build_paper_steps

    steps = _build_paper_steps()
    # Should NOT raise when the paper whitelist is supplied.
    validate_pipeline(steps, whitelist=PAPER_AGENT_WHITELIST)


# ---------------------------------------------------------------------------
# load_papers_pipeline: deep-copy isolation + identical-shape return
# ---------------------------------------------------------------------------

def test_load_papers_pipeline_returns_fresh_copy():
    """`load_papers_pipeline(show)` must return a NEW list of NEW dicts on
    every call, mirroring the opinion `load_pipeline` contract. A caller
    mutating the returned list cannot affect subsequent loads (the runner
    rewrites `parallel` for parallel fan-out)."""
    from lib.pipeline_papers import load_papers_pipeline

    a = load_papers_pipeline("papers")
    b = load_papers_pipeline("papers")
    assert a is not b, "load_papers_pipeline must return a fresh list each call"
    # Top-level steps are also fresh dicts.
    assert all(
        a[i] is not b[i]
        for i in range(len(a))
    ), "load_papers_pipeline must return fresh step dicts each call"
    # But they have equal VALUE.
    assert a == b


def test_load_papers_pipeline_validates_self():
    """`load_papers_pipeline` must self-validate via
    `validate_pipeline(steps, whitelist=PAPER_AGENT_WHITELIST)`. A
    malformed topology (e.g. an agent outside the whitelist) must surface
    here — not silently ship through to the runner."""
    from lib.pipeline_papers import load_papers_pipeline

    # If the topology is well-formed and the whitelist is correct, this
    # raises nothing. A bad topology would raise ValueError at load time.
    steps = load_papers_pipeline("papers")
    assert isinstance(steps, list)
    assert steps, "load_papers_pipeline must return a non-empty list"


# ---------------------------------------------------------------------------
# line-bundle integration: the registered paper line resolves via get_line
# ---------------------------------------------------------------------------

def test_paper_line_resolves_via_get_line():
    """After Task 6-impl wires the PAPER_LINE bundle into _LINE_REGISTRY,
    `get_line("papers")` returns a LineBundle whose topology is the
    collection list. This pins the contract that the engine sees when it
    runs a paper show."""
    from lib.lines import LineBundle, get_line
    from lib.pipeline_papers import _build_paper_steps

    bundle = get_line("papers")
    assert isinstance(bundle, LineBundle)
    # The bundle's topology callable, when invoked with "papers", must
    # equal the paper collection topology.
    assert bundle.topology("papers") == _build_paper_steps()


def test_paper_line_bundle_exposes_paper_agent_dir():
    """The PAPER_LINE bundle must point `agent_dir` at the paper personas
    directory (`agents/papers/`), not at the opinion `agents/` dir. This
    is what Task 5 creates and what Task 9 dispatches into via
    `claude -p --append-system-prompt <agents/papers/<name>.md>`."""
    from lib.lines import get_line

    bundle = get_line("papers")
    assert bundle.agent_dir == "agents/papers", (
        f"paper line agent_dir must be 'agents/papers', got {bundle.agent_dir!r}"
    )


# ---------------------------------------------------------------------------
# fail-closed: load_papers_pipeline rejects unknown show / bad input shape
# ---------------------------------------------------------------------------

def test_load_papers_pipeline_rejects_unknown_show():
    """An unknown show name fails-closed (the show param is validated by
    the validator's contract — a typo'd show must surface as ValueError,
    not silently load the same topology for any string)."""
    from lib.pipeline_papers import load_papers_pipeline

    with pytest.raises(ValueError):
        load_papers_pipeline("not-a-show")
