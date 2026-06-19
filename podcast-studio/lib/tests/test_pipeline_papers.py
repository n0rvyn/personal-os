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
    # collection half (P2) + P4 front-段 continuity stations
    "config",
    "scratch",
    "same-day-guard",   # P4 (DP-404=A): one-episode-per-day fail-fast
    "discovery",
    "paper-log-read",   # P4 (DP-403=A): stage paper-log + arXiv-id dedup pre-filter
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
    # publish half (P4) — DP-601=B order: broadcast-script → tts →
    # paper-log-write (record dedup BEFORE airing) → publish → cleanup
    "broadcast-script",
    "tts",
    "paper-log-write",
    "publish",
    "cleanup",
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
        "broadcast-script", "tts",                       # P4 publish (broadcaster / jay)
    }, (
        f"agent stations must be the 2 collection + 4 generation + 2 publish personas, got {agent_names}"
    )
    assert set(code_names) == {
        "config", "scratch", "discovery", "fetch", "ledger-verify",  # collection
        "same-day-guard", "paper-log-read",                          # P4 front-段
        "digest-select",                                              # generation
        "paper-log-write", "publish", "cleanup",                     # P4 publish
    }, (
        f"code stations must be the collection + P4 + digest-select set, got {code_names}"
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


# ---------------------------------------------------------------------------
# Length floor placement (过长度门): on the finalize BODY, not the committee
# drafts. The committee writes 3 drafts but digest-select keeps 1, so a floor
# on the committee slices lets a discarded short draft halt an otherwise-fine
# episode (the live 2950<4500 B-draft false-halt). The floor moved to the
# finalize body (mirrors the opinion line's finalize body floor). These pins
# fail if the floor regresses back onto the committee step.
# ---------------------------------------------------------------------------

def _paper_step(name: str) -> dict:
    from lib.pipeline_papers import _build_paper_steps

    step = next((s for s in _build_paper_steps() if s["name"] == name), None)
    assert step is not None, f"step {name!r} must exist in the paper topology"
    return step


def test_committee_gate_is_existence_only_no_length_floor():
    """The committee station gates EXISTENCE only (check_artifact). It must
    NOT carry a length floor: flooring throwaway drafts that digest-select
    discards is the false-halt bug this fix removed."""
    committee = _paper_step("committee")
    gate_fns = [g.get("fn") for g in committee.get("gate", [])]
    assert gate_fns == ["check_artifact"], (
        f"committee gate must be exactly [check_artifact] (existence only); "
        f"a check_min_chars floor here re-introduces the discarded-draft "
        f"false-halt. got {gate_fns!r}"
    )
    # Still a per-slice parallel fan-out (each A/B/C must exist).
    assert committee.get("parallel") == ["A", "B", "C"], (
        f"committee must stay a 3-slice fan-out, got {committee.get('parallel')!r}"
    )


def test_finalize_gate_carries_body_length_floor_with_retry():
    """The single 过长度门 lives on the finalize body: check_min_chars with
    args.min_chars=='floor' (sentinel → _paper_floor=4500) AND
    json_field=='body' (the body inside finalize-result.json IS the published
    deliverable). retry==3: a too-short body re-derives the finalizer up to 3×
    then halts (D-009)."""
    finalize = _paper_step("finalize")
    gate = finalize.get("gate", [])
    fns = [g.get("fn") for g in gate]
    assert "check_min_chars" in fns, (
        f"finalize must carry the length floor (check_min_chars); got {fns!r}"
    )
    floor_item = next(g for g in gate if g.get("fn") == "check_min_chars")
    args = floor_item.get("args", {})
    assert args.get("min_chars") == "floor", (
        f"finalize floor must use the 'floor' sentinel (→ _paper_floor), "
        f"got {args.get('min_chars')!r}"
    )
    assert args.get("json_field") == "body", (
        f"finalize floor must count the 'body' JSON field (the deliverable), "
        f"got {args.get('json_field')!r}"
    )
    assert finalize.get("retry") == 3, (
        f"finalize retry must be 3 (re-derive a too-short body up to 3× then "
        f"halt), got {finalize.get('retry')!r}"
    )


# ---------------------------------------------------------------------------
# P4 Task 3: front-段 continuity stations (same-day-guard + paper-log-read)
# ---------------------------------------------------------------------------

def test_same_day_guard_between_scratch_and_discovery():
    """same-day-guard (DP-404=A) is a code station between scratch and discovery
    — fail-fast on a same-day re-run before any discovery/dispatch work."""
    from lib.pipeline_papers import _build_paper_steps
    names = [s["name"] for s in _build_paper_steps()]
    assert "same-day-guard" in names, "same-day-guard station must exist (DP-404=A)"
    assert names.index("scratch") < names.index("same-day-guard") < names.index("discovery"), (
        f"same-day-guard must sit between scratch and discovery, got {names}"
    )
    g = _paper_step("same-day-guard")
    assert g["kind"] == "code" and g["agent"] is None


def test_paper_log_read_between_discovery_and_curator():
    """paper-log-read (DP-403=A) is a code station between discovery and curator:
    it needs candidates.json (from discovery) and feeds the curator."""
    from lib.pipeline_papers import _build_paper_steps
    names = [s["name"] for s in _build_paper_steps()]
    assert "paper-log-read" in names, "paper-log-read station must exist (DP-403=A)"
    assert names.index("discovery") < names.index("paper-log-read") < names.index("curator"), (
        f"paper-log-read must sit between discovery and curator, got {names}"
    )
    r = _paper_step("paper-log-read")
    assert r["kind"] == "code" and r["artifact"] == "paper-log.json", (
        f"paper-log-read must produce paper-log.json, got {r.get('artifact')!r}"
    )


def test_curator_input_is_real_paperlog_file_not_stub():
    """DP-403=A: the curator reads the REAL paper-log.json (staged by
    paper-log-read), NOT the literal-string "paper-log" stub — the shared
    runner's `_CONCEPTUAL` set does not special-case "paper-log", so the old
    input was injected as raw text with no file behind it."""
    c = _paper_step("curator")
    assert "paper-log.json" in c["inputs"], (
        f"curator must read the real paper-log.json, got {c['inputs']}"
    )
    assert "paper-log" not in c["inputs"], (
        f"curator must NOT carry the literal-string 'paper-log' stub, got {c['inputs']}"
    )


# ---------------------------------------------------------------------------
# P4 Task 4: 口播稿 (broadcaster) + TTS (jay) publish-half stations
# ---------------------------------------------------------------------------

def test_broadcast_script_station_is_broadcaster_after_faithfulness():
    """broadcast-script is an agent station (agent=broadcaster, the paper-line
    口播改写 persona) downstream of faithfulness."""
    from lib.pipeline_papers import _build_paper_steps
    names = [s["name"] for s in _build_paper_steps()]
    assert "broadcast-script" in names
    assert names.index("faithfulness") < names.index("broadcast-script")
    s = _paper_step("broadcast-script")
    assert s["kind"] == "agent" and s["agent"] == "broadcaster", (
        f"broadcast-script must dispatch the broadcaster persona, got {s.get('agent')!r}"
    )
    assert s["artifact"] == "broadcast-script-{date}.txt"


def test_tts_station_is_jay_with_no_tts_skip():
    """tts is agent=jay, artifact audio-files.mp3, skip_when='no_tts'
    (mirrors opinion); runs after broadcast-script."""
    from lib.pipeline_papers import _build_paper_steps
    names = [s["name"] for s in _build_paper_steps()]
    assert names.index("broadcast-script") < names.index("tts")
    s = _paper_step("tts")
    assert s["kind"] == "agent" and s["agent"] == "jay"
    assert s["artifact"] == "audio-files.mp3"
    assert s["skip_when"] == "no_tts", (
        f"tts must carry skip_when='no_tts' (no-TTS mode), got {s.get('skip_when')!r}"
    )


def test_publish_half_agents_in_whitelist_load_time():
    """LOAD-time guard (verifier must-revise#2): broadcaster AND jay must be in
    PAPER_AGENT_WHITELIST, or load_papers_pipeline raises at validate_pipeline."""
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST, load_papers_pipeline
    assert "broadcaster" in PAPER_AGENT_WHITELIST
    assert "jay" in PAPER_AGENT_WHITELIST  # jay is opinion-only by default; paper line needs its own entry
    # Must not raise (every agent step's agent is whitelisted):
    load_papers_pipeline("papers")


# ---------------------------------------------------------------------------
# P4 Task 6: paper-log-write (DP-601=B — BEFORE publish) + cleanup
# ---------------------------------------------------------------------------

def test_paper_log_write_before_publish_dp601():
    """DP-601=B: paper-log-write records dedup BEFORE publish airs anything.
    Order must be …tts → paper-log-write → publish → cleanup."""
    from lib.pipeline_papers import _build_paper_steps
    names = [s["name"] for s in _build_paper_steps()]
    for s in ("paper-log-write", "publish", "cleanup"):
        assert s in names, f"{s} station must exist"
    assert names.index("tts") < names.index("paper-log-write") < names.index("publish") < names.index("cleanup"), (
        f"DP-601=B tail order violated, got {names}"
    )
    w = _paper_step("paper-log-write")
    assert w["kind"] == "code"
    # Blocking gate (NOT fail-soft — dedup命脉, D-013)
    assert w.get("fail_soft") in (None, False)
    assert w.get("gate"), "paper-log-write must carry a blocking gate (check_artifact)"
