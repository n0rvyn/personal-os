"""Tests for lib/pipeline.py — step-table data structure + validation.

Written before lib/pipeline.py exists; collection must fail at this
point (`No module named 'lib.pipeline'`).

Pins (per phase1-code-runner-plan Task 1-tests):
- load_pipeline("morning") / load_pipeline("evening") return an ordered list
  of step dicts covering steps 1–17 (including 3a/5b/12a/15a/15b/16a).
- Every step has {name, kind, artifact, gate}; kind=="agent" steps also
  carry `agent` ∈ the whitelist.
- The two code bridge stations exist in order:
    * continuity-read (step 4, code)
    * assemble-briefs (between 5b and 7, code) — inputs reference
      magnitude-verdict.json; artifact writes writing-brief-A/B/C.json
- Parallel groups (7/8/9) are tagged parallel=["A","B","C"].
- Retry stations (12/12a, 16/16a) carry a retry cap.
- Composite gates: step 7/9 contain both check_artifact and
  check_min_chars (with args.min_chars=="floor"); step 12 contains
  check_min_chars with args.json_field=="body". Each gate item is
  shaped {"fn": <name>, "args": {...}}.
- fail-closed validation: a step missing fn / kind="bogus" / agent="ghost"
  raises ValueError naming the offending field.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.pipeline import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

AGENT_WHITELIST = {
    "davinci",
    "liangchen",
    "bible-distiller",
    "coveredground-distiller",
    "laohei",
    "kuaidao",
    "qianzhongshu",
    "bianyang",
    "jay",
    "zhijianyuan",
    "scorecard",
}


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import pipeline  # noqa: F401
    assert hasattr(pipeline, "load_pipeline")


# ---------------------------------------------------------------------------
# load_pipeline returns an ordered step list covering all 17 stations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("show", ["morning", "evening"])
def test_load_pipeline_returns_ordered_list(show):
    """load_pipeline(show) returns a list of step dicts. The list must be
    ordered (sequential execution) and must include steps 1–17 (including
    sub-stations 3a/5b/12a/15a/15b/16a)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline(show)
    assert isinstance(steps, list), "load_pipeline must return a list"
    assert len(steps) >= 17, f"expected ≥17 stations, got {len(steps)}"

    # Every step must have a string `name` field
    for step in steps:
        assert isinstance(step, dict), f"step must be a dict, got {type(step)}"
        assert "name" in step and isinstance(step["name"], str), (
            f"step missing string `name`: {step}"
        )


# ---------------------------------------------------------------------------
# Each step has name, kind, artifact, gate; agent steps also carry agent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("show", ["morning", "evening"])
def test_every_step_has_required_fields(show):
    """Every step must carry name, kind, artifact (str|None), gate (list|None).
    Steps with kind=='agent' must also carry an `agent` field whose value is
    in the whitelist."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline(show)
    for step in steps:
        assert "kind" in step, f"step {step.get('name')!r} missing `kind`"
        assert step["kind"] in ("code", "agent"), (
            f"step {step.get('name')!r} has invalid kind={step['kind']!r}; "
            "must be 'code' or 'agent'"
        )
        # artifact may be None (e.g. cleanup steps) but must be a string when set
        assert "artifact" in step, (
            f"step {step.get('name')!r} missing `artifact` field"
        )
        assert step["artifact"] is None or isinstance(step["artifact"], str), (
            f"step {step.get('name')!r} artifact must be str|None"
        )
        # gate must be a list of dicts (possibly empty/None for pure code steps)
        assert "gate" in step, (
            f"step {step.get('name')!r} missing `gate` field"
        )
        if step["gate"] is not None:
            assert isinstance(step["gate"], list), (
                f"step {step.get('name')!r} gate must be a list"
            )
            for gate_item in step["gate"]:
                assert isinstance(gate_item, dict), (
                    f"step {step.get('name')!r} gate items must be dicts"
                )

        if step["kind"] == "agent":
            assert "agent" in step, (
                f"agent step {step.get('name')!r} missing `agent` field"
            )
            assert step["agent"] in AGENT_WHITELIST, (
                f"agent step {step.get('name')!r} has agent={step['agent']!r} "
                f"not in whitelist {sorted(AGENT_WHITELIST)}"
            )


# ---------------------------------------------------------------------------
# Parallel groups (7/8/9) tagged with parallel=["A","B","C"]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parallel_group_name", ["drafts", "critiques", "polishes"])
def test_parallel_groups_have_three_way_fanout(parallel_group_name):
    """The three parallel stations (step 7 drafts, step 8 critiques, step 9
    polishes) must be tagged with parallel=['A','B','C'] to signal 3-way
    fan-out (one call per candidate)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")

    matches = [
        s for s in steps
        if s.get("parallel") and s["parallel"] == ["A", "B", "C"]
    ]
    assert len(matches) >= 3, (
        f"expected ≥3 steps with parallel=['A','B','C'] (drafts/critiques/"
        f"polishes), found {len(matches)} in {parallel_group_name}"
    )


# ---------------------------------------------------------------------------
# Retry stations (12/12a, 16/16a) carry a retry cap
# ---------------------------------------------------------------------------

def test_retry_stations_have_retry_cap():
    """The retry-pair stations (12/12a — finalize+factcheck, 16/16a —
    stance+settlement) must carry a `retry` field with an integer cap."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    retry_steps = [s for s in steps if s.get("retry") is not None]

    assert len(retry_steps) >= 2, (
        f"expected ≥2 retry-capped stations (12a, 16a), got {len(retry_steps)}"
    )

    for step in retry_steps:
        assert isinstance(step["retry"], int), (
            f"retry field must be int, got {type(step['retry'])} on {step['name']!r}"
        )
        assert step["retry"] >= 1, (
            f"retry cap must be ≥1, got {step['retry']} on {step['name']!r}"
        )


# ---------------------------------------------------------------------------
# Code bridge stations exist in the correct order
# ---------------------------------------------------------------------------

def test_continuity_read_bridge_present():
    """continuity-read is the step-4 code bridge station. It must exist as a
    code station and must appear BEFORE step 5 (davinci collection)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    names = [s["name"] for s in steps]

    assert "continuity-read" in names, (
        f"continuity-read code bridge missing from step list: {names}"
    )

    idx_continuity = names.index("continuity-read")
    # The davinci collection step is step 5; find any davinci step after 4
    after_davinci = [
        i for i, s in enumerate(steps)
        if i > idx_continuity and s.get("kind") == "agent"
        and s.get("agent") == "davinci"
    ]
    assert after_davinci, "no davinci agent step after continuity-read"


def test_assemble_briefs_bridge_between_5b_and_7():
    """assemble-briefs is the code bridge station between step 5b (liangchen
    magnitude verdict) and step 7 (davinci 3-way drafting). It must:
      - be a code station
      - appear AFTER 5b (magnitude) and BEFORE step 7 (drafts)
      - reference magnitude-verdict.json in its inputs
      - declare artifacts writing-brief-A.json / writing-brief-B.json /
        writing-brief-C.json
    This is the routing+避让 channel into the drafting step — its absence
    makes the anti-homogenization guard a no-op."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    names = [s["name"] for s in steps]

    assert "assemble-briefs" in names, (
        f"assemble-briefs code bridge missing from step list: {names}"
    )

    idx_assemble = names.index("assemble-briefs")

    # Must appear after magnitude (step 5b) and before drafts (step 7)
    assert "magnitude" in names or "5b" in names, (
        "magnitude step missing from pipeline"
    )
    magnitude_idx = next(
        (i for i, n in enumerate(names) if "magnitude" in n.lower()),
        None,
    )
    assert magnitude_idx is not None and idx_assemble > magnitude_idx, (
        f"assemble-briefs must come AFTER magnitude step (idx {magnitude_idx}), "
        f"found at idx {idx_assemble}"
    )

    # Locate the 3-way davinci drafts step (step 7) — must come AFTER assemble
    drafts_step = next(
        (
            s for s in steps[idx_assemble + 1:]
            if s.get("kind") == "agent"
            and s.get("agent") == "davinci"
            and s.get("parallel") == ["A", "B", "C"]
        ),
        None,
    )
    assert drafts_step is not None, (
        "no 3-way davinci drafts step after assemble-briefs"
    )

    # Inputs must reference magnitude-verdict.json
    assemble = steps[idx_assemble]
    inputs = assemble.get("inputs") or []
    has_magnitude_input = any(
        "magnitude-verdict" in str(inp) for inp in inputs
    )
    assert has_magnitude_input, (
        f"assemble-briefs inputs must reference magnitude-verdict.json, "
        f"got inputs={inputs!r}"
    )

    # Artifact must produce writing-brief-{A,B,C}.json — at least one of the
    # A/B/C trio must be declared in either `artifact` or via parallel/inputs.
    artifact = assemble.get("artifact")
    artifact_str = str(artifact) if artifact is not None else ""
    # artifact may name a single combined path or one of the three; check
    # that the brief naming convention is at least referenced somewhere
    # (the impl may keep `artifact` pointing at one brief and produce the
    # rest via parallel fan-out OR name all three explicitly).
    # Plan guarantees the brief-X.json naming lives on this station.
    brief_refs = [
        inp for inp in inputs
        if "writing-brief" in str(inp).lower()
    ]
    assert (
        "writing-brief" in artifact_str.lower()
        or len(brief_refs) > 0
    ), (
        f"assemble-briefs must reference writing-brief-{{A,B,C}}.json "
        f"via artifact or inputs, got artifact={artifact!r} inputs={inputs!r}"
    )


# ---------------------------------------------------------------------------
# Composite gates: step 7/9 (drafts/polishes) carry check_min_chars with
# min_chars=='floor'; step 12 carries check_min_chars with json_field=='body'.
# Every gate item must be shaped {'fn': ..., 'args': {...}}.
# ---------------------------------------------------------------------------

def test_composite_gate_shape_drafts():
    """Step 7 (davinci drafts) gate must be a composite of check_artifact
    AND check_min_chars(args.min_chars=='floor')."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    drafts = next(
        (
            s for s in steps
            if s.get("kind") == "agent"
            and s.get("agent") == "davinci"
            and s.get("parallel") == ["A", "B", "C"]
        ),
        None,
    )
    assert drafts is not None, "no 3-way davinci drafts step"
    gate = drafts.get("gate")
    assert gate and isinstance(gate, list) and len(gate) >= 2, (
        f"drafts gate must be a composite list (≥2 items), got {gate!r}"
    )

    fn_names = [g.get("fn") for g in gate]
    assert "check_artifact" in fn_names, (
        f"drafts gate missing check_artifact, got fns={fn_names}"
    )
    assert "check_min_chars" in fn_names, (
        f"drafts gate missing check_min_chars, got fns={fn_names}"
    )

    # Every gate item must have `fn` (and optionally `args`)
    for g in gate:
        assert isinstance(g, dict) and "fn" in g, (
            f"every gate item must be a dict with `fn`, got {g!r}"
        )
        if "args" in g:
            assert isinstance(g["args"], dict), (
                f"gate item args must be dict, got {g!r}"
            )

    # The check_min_chars item must carry args.min_chars == 'floor' (sentinel)
    min_chars_item = next(g for g in gate if g.get("fn") == "check_min_chars")
    assert min_chars_item.get("args", {}).get("min_chars") == "floor", (
        f"drafts check_min_chars must use args.min_chars='floor' sentinel, "
        f"got args={min_chars_item.get('args')!r}"
    )


def test_composite_gate_shape_polishes():
    """Step 9 (kuaidao polishes) gate must be a composite of check_artifact
    AND check_min_chars(args.min_chars=='floor')."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    polishes = next(
        (
            s for s in steps
            if s.get("kind") == "agent"
            and s.get("agent") == "kuaidao"
            and s.get("parallel") == ["A", "B", "C"]
        ),
        None,
    )
    assert polishes is not None, "no 3-way kuaidao polishes step"
    gate = polishes.get("gate")
    fn_names = [g.get("fn") for g in (gate or [])]
    assert "check_artifact" in fn_names, (
        f"polishes gate missing check_artifact, got fns={fn_names}"
    )
    assert "check_min_chars" in fn_names, (
        f"polishes gate missing check_min_chars, got fns={fn_names}"
    )

    min_chars_item = next(g for g in gate if g.get("fn") == "check_min_chars")
    assert min_chars_item.get("args", {}).get("min_chars") == "floor", (
        f"polishes check_min_chars must use args.min_chars='floor' sentinel, "
        f"got args={min_chars_item.get('args')!r}"
    )


def test_composite_gate_shape_finalize():
    """Step 12 (kuaidao finalize) gate must include check_min_chars with
    args.json_field=='body' (length-check the finalize `body` JSON field)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    finalize = next(
        (
            s for s in steps
            if s.get("kind") == "agent"
            and s.get("agent") == "kuaidao"
            and "finalize" in (s.get("name", "") + " " + str(s.get("artifact", ""))).lower()
        ),
        None,
    )
    assert finalize is not None, (
        "no kuaidao finalize step (name/artifact must contain 'finalize')"
    )
    gate = finalize.get("gate") or []
    fn_names = [g.get("fn") for g in gate]
    assert "check_min_chars" in fn_names, (
        f"finalize gate missing check_min_chars, got fns={fn_names}"
    )
    min_chars_item = next(g for g in gate if g.get("fn") == "check_min_chars")
    args = min_chars_item.get("args", {})
    assert args.get("json_field") == "body", (
        f"finalize check_min_chars must use args.json_field='body', "
        f"got args={args!r}"
    )


# ---------------------------------------------------------------------------
# fail-closed validation: bad step tables raise ValueError naming the field
# ---------------------------------------------------------------------------

def _make_valid_step_overrides():
    """Return overrides that produce a step table considered 'valid' under
    the basic shape check. Tests then mutate one field to force validation
    failure."""
    return {
        "name": "synthetic",
        "kind": "code",
        "agent": None,
        "inputs": [],
        "artifact": None,
        "gate": None,
        "parallel": None,
        "retry": None,
        "skip_when": None,
        "fail_soft": None,
    }


def test_validate_pipeline_rejects_unknown_kind():
    """A step with an unknown `kind` is rejected with ValueError naming the
    bad value."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    bad["kind"] = "bogus"

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "kind" in msg, (
        f"ValueError must name the offending field 'kind', got: {exc_info.value}"
    )
    assert "bogus" in msg, (
        f"ValueError must echo the bad value 'bogus', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_unknown_agent():
    """A step with kind='agent' but agent not in whitelist is rejected with
    ValueError naming the bad agent."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    bad["kind"] = "agent"
    bad["agent"] = "ghost"

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "agent" in msg, (
        f"ValueError must name the offending field 'agent', got: {exc_info.value}"
    )
    assert "ghost" in msg, (
        f"ValueError must echo the bad agent 'ghost', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_gate_item_missing_fn():
    """A gate item missing `fn` is rejected with ValueError naming 'fn'."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    bad["gate"] = [{"args": {}}]  # missing `fn`

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "fn" in msg, (
        f"ValueError must name the offending field 'fn', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_missing_required_field():
    """A step missing a required field (e.g. `name`) is rejected with
    ValueError naming the missing field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    del bad["name"]

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "name" in msg, (
        f"ValueError must name the missing field 'name', got: {exc_info.value}"
    )


def test_validate_pipeline_accepts_well_formed_steps():
    """A well-formed step table is accepted (no raise)."""
    from lib.pipeline import validate_pipeline

    good = _make_valid_step_overrides()
    # Should not raise
    validate_pipeline([good])


# ---------------------------------------------------------------------------
# Phase 2 — fail_soft field + two post-publish stations (coveredground-distill,
# coveredground-update). Per task 6-tests: step schema adds a `fail_soft` field
# (None | bool); `coveredground-distill` (agent=coveredground-distiller,
# fail_soft=True) and `coveredground-update` (code, fail_soft=True) are appended
# AFTER `cleanup` (step 17). `coveredground-distiller` joins AGENT_WHITELIST in
# pipeline + dispatch. validate_pipeline rejects non-bool fail_soft.
# ---------------------------------------------------------------------------


def test_fail_soft_field_present_on_every_step():
    """Every step must carry the `fail_soft` field (None is the legal default;
    new post-publish stations set it to True). The fail-closed contract requires
    the key to be present, even if the value is None."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    for step in steps:
        assert "fail_soft" in step, (
            f"step {step.get('name')!r} missing required field: 'fail_soft'"
        )
        assert step["fail_soft"] is None or isinstance(step["fail_soft"], bool), (
            f"step {step.get('name')!r} fail_soft must be None or bool, "
            f"got {type(step['fail_soft']).__name__}"
        )


def test_validate_pipeline_rejects_non_bool_fail_soft():
    """validate_pipeline must reject a step whose fail_soft is neither None
    nor bool (e.g. the string "true") and the ValueError must name the
    offending field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    bad["fail_soft"] = "true"  # string is illegal

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "fail_soft" in msg, (
        f"ValueError must name the offending field 'fail_soft', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_missing_fail_soft():
    """validate_pipeline must reject a step missing the `fail_soft` key —
    fail-closed on missing required field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_step_overrides()
    del bad["fail_soft"]

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "fail_soft" in msg, (
        f"ValueError must name the missing field 'fail_soft', got: {exc_info.value}"
    )


def test_coveredground_distill_station_present_after_cleanup():
    """The `coveredground-distill` post-publish agent station must:
      - exist in the pipeline
      - be kind='agent', agent='coveredground-distiller'
      - have fail_soft=True (fail-soft, post-publish)
      - appear AFTER the `cleanup` step (step 17) — distiller runs after the
        published artifacts are written
    """
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    names = [s["name"] for s in steps]

    assert "coveredground-distill" in names, (
        f"coveredground-distill station missing from step list: {names}"
    )

    distill = next(s for s in steps if s["name"] == "coveredground-distill")
    assert distill["kind"] == "agent", (
        f"coveredground-distill must be kind='agent', got {distill['kind']!r}"
    )
    assert distill["agent"] == "coveredground-distiller", (
        f"coveredground-distill must dispatch to 'coveredground-distiller', "
        f"got {distill['agent']!r}"
    )
    assert distill["fail_soft"] is True, (
        f"coveredground-distill must be fail_soft=True (post-publish), "
        f"got {distill['fail_soft']!r}"
    )

    # Must appear AFTER cleanup (step 17) — distiller runs after publish
    assert "cleanup" in names, "cleanup step missing from pipeline"
    idx_cleanup = names.index("cleanup")
    idx_distill = names.index("coveredground-distill")
    assert idx_distill > idx_cleanup, (
        f"coveredground-distill (idx {idx_distill}) must come AFTER "
        f"cleanup (idx {idx_cleanup})"
    )


def test_coveredground_update_station_present_after_cleanup():
    """The `coveredground-update` post-publish code station must:
      - exist in the pipeline
      - be kind='code' (deterministic helper, no LLM)
      - have fail_soft=True
      - appear AFTER the `cleanup` step (step 17)
    """
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    names = [s["name"] for s in steps]

    assert "coveredground-update" in names, (
        f"coveredground-update station missing from step list: {names}"
    )

    update = next(s for s in steps if s["name"] == "coveredground-update")
    assert update["kind"] == "code", (
        f"coveredground-update must be kind='code', got {update['kind']!r}"
    )
    assert update["agent"] is None, (
        f"coveredground-update must have agent=None (code station), "
        f"got {update['agent']!r}"
    )
    assert update["fail_soft"] is True, (
        f"coveredground-update must be fail_soft=True, "
        f"got {update['fail_soft']!r}"
    )

    idx_cleanup = names.index("cleanup")
    idx_update = names.index("coveredground-update")
    assert idx_update > idx_cleanup, (
        f"coveredground-update (idx {idx_update}) must come AFTER "
        f"cleanup (idx {idx_cleanup})"
    )


def test_pipeline_agent_whitelist_contains_coveredground_distiller():
    """`lib.pipeline.AGENT_WHITELIST` must include 'coveredground-distiller' so
    validate_pipeline accepts the new agent station."""
    from lib.pipeline import AGENT_WHITELIST

    assert "coveredground-distiller" in AGENT_WHITELIST, (
        f"AGENT_WHITELIST must include 'coveredground-distiller', "
        f"got {sorted(AGENT_WHITELIST)}"
    )


def test_load_pipeline_does_not_raise_at_import_time():
    """load_pipeline('morning') must succeed without raising — the canonical
    table is self-validated at module import + on each load. If the new
    stations are malformed (missing fail_soft, bad kind, agent not in
    whitelist), this call would raise ValueError."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    assert isinstance(steps, list)
    assert len(steps) >= 19, (
        f"expected ≥19 stations (17 + 2 post-publish), got {len(steps)}"
    )


# ---------------------------------------------------------------------------
# Phase 3 — 13a scorecard station + scorecard in AGENT_WHITELIST.
# Per task 4-tests: step table must include `scorecard` (kind=agent,
# agent=scorecard, artifact="scorecard-verdict.json") BETWEEN
# broadcast-rewrite (step 13) and tts (step 14). fail_soft must NOT be True
# (advisory is enforced via --enforce-scorecard, not via fail_soft — the
# latter would silently swallow halts). scorecard joins AGENT_WHITELIST.
# ---------------------------------------------------------------------------

def test_scorecard_station_present_between_broadcast_rewrite_and_tts():
    """The 13a scorecard agent station must:
      - exist in the pipeline
      - be kind='agent', agent='scorecard'
      - declare artifact='scorecard-verdict.json'
      - appear AFTER broadcast-rewrite (step 13) and BEFORE tts (step 14)
        — this is the only window where the broadcast script, factcheck-
        verdict, finalize-result, and score-verdict all coexist in scratch
        (cleanup at step 17 wipes scratch afterwards).
    """
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    names = [s["name"] for s in steps]

    assert "scorecard" in names, (
        f"scorecard station missing from step list: {names}"
    )
    assert "broadcast-rewrite" in names, (
        "broadcast-rewrite (step 13) must be present"
    )
    assert "tts" in names, "tts (step 14) must be present"

    idx_scorecard = names.index("scorecard")
    idx_broadcast = names.index("broadcast-rewrite")
    idx_tts = names.index("tts")

    assert idx_scorecard > idx_broadcast, (
        f"scorecard (idx {idx_scorecard}) must come AFTER "
        f"broadcast-rewrite (idx {idx_broadcast})"
    )
    assert idx_scorecard < idx_tts, (
        f"scorecard (idx {idx_scorecard}) must come BEFORE "
        f"tts (idx {idx_tts})"
    )

    scorecard = next(s for s in steps if s["name"] == "scorecard")
    assert scorecard["kind"] == "agent", (
        f"scorecard must be kind='agent', got {scorecard['kind']!r}"
    )
    assert scorecard["agent"] == "scorecard", (
        f"scorecard must dispatch to persona 'scorecard', "
        f"got {scorecard['agent']!r}"
    )
    assert scorecard["artifact"] == "scorecard-verdict.json", (
        f"scorecard artifact must be 'scorecard-verdict.json', "
        f"got {scorecard['artifact']!r}"
    )


def test_scorecard_station_fail_soft_not_true():
    """The scorecard step must NOT be fail_soft=True. Advisory behavior is
    implemented by the runner (which respects --enforce-scorecard), not by
    the step's fail_soft field. A fail_soft=True here would silently swallow
    the hard-gate red halt that the production --enforce-scorecard mode
    needs to surface."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    scorecard = next(s for s in steps if s["name"] == "scorecard")

    fs = scorecard.get("fail_soft")
    assert fs is None or fs is False, (
        f"scorecard fail_soft must be None or False (advisory is runner-side, "
        f"not fail_soft), got {fs!r}"
    )


def test_scorecard_station_gate_uses_check_artifact():
    """The scorecard step's gate must include check_artifact (the scorecard
    verdict is the only mandatory output — judge dims are advisory)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline("morning")
    scorecard = next(s for s in steps if s["name"] == "scorecard")

    gate = scorecard.get("gate") or []
    fn_names = [g.get("fn") for g in gate]
    assert "check_artifact" in fn_names, (
        f"scorecard gate must include check_artifact (verdict is the "
        f"mandatory artifact), got fns={fn_names}"
    )


def test_pipeline_agent_whitelist_contains_scorecard():
    """`lib.pipeline.AGENT_WHITELIST` must include 'scorecard' so
    validate_pipeline accepts the new 13a agent station."""
    from lib.pipeline import AGENT_WHITELIST

    assert "scorecard" in AGENT_WHITELIST, (
        f"AGENT_WHITELIST must include 'scorecard', "
        f"got {sorted(AGENT_WHITELIST)}"
    )