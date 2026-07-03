"""Tests for lib/pipeline.py — step-table data structure + validation.

Written before lib/pipeline.py exists; collection must fail at this
point (`No module named 'lib.pipeline'`). Task 2-impl resolves this.

Pins (per video-studio Phase 2 Task 2-tests):
- load_pipeline() returns an ordered list of step dicts covering all
  Phase-2 steps (config / cursor-read / scratch / extract / tts /
  gen-still / gen-video / chart / subtitle / assemble / provenance /
  publish / cleanup).
- Every step has {name, kind, agent, inputs, artifact, gate} fields;
  kind=='agent' steps also carry `agent` (non-empty string).
- `validate_pipeline` rejects:
    * kind='agent' without agent
    * gate item missing `fn`
    * gate item with empty `fn`
    * missing required field
    * unknown kind
- load_pipeline() is pure data: calling it has no visible side effects
  and the returned list carries no callable objects under `kind` or `gate`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.pipeline import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
    from lib import pipeline  # noqa: F401
    assert hasattr(pipeline, "load_pipeline")
    assert hasattr(pipeline, "validate_pipeline")


# ---------------------------------------------------------------------------
# load_pipeline returns an ordered step list covering all Phase-2 stations
# ---------------------------------------------------------------------------

def test_load_pipeline_returns_list():
    """load_pipeline() returns a non-empty list."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert isinstance(steps, list), "load_pipeline() must return a list"
    assert len(steps) > 0, "load_pipeline() must return a non-empty list"


def test_load_pipeline_each_step_is_dict():
    """Every element returned by load_pipeline() must be a dict."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    for i, step in enumerate(steps):
        assert isinstance(step, dict), (
            f"steps[{i}] must be a dict, got {type(step).__name__}"
        )


def test_load_pipeline_step_count():
    """Phase-2 pipeline must have at least 13 stations covering the full
    video production flow (config / cursor-read / scratch / extract / tts /
    gen-still / gen-video / chart / subtitle / assemble / provenance /
    publish / cleanup)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert len(steps) >= 13, (
        f"expected ≥13 Phase-2 stations, got {len(steps)}"
    )


def test_load_pipeline_step_count_16():
    """Phase-1 (shot-timeline): after inserting the 镜头计划 code step (post-tts,
    pre-gen-still), the pipeline must have exactly 16 stations (15 Phase-2/3/4
    steps + 镜头计划 = 16).

    Pre-impl (Task 3-impl pending): returns 15 → FAILS (red).
    Green after Task 3-impl inserts the 镜头计划 step.
    """
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert len(steps) == 16, (
        f"expected 16 stations (Phase-1 镜头计划 inserted), got {len(steps)}"
    )


# ---------------------------------------------------------------------------
# Every step has required fields
# ---------------------------------------------------------------------------

def test_every_step_has_required_fields():
    """Every step must carry name, kind, agent, inputs, artifact, gate.
    Steps with kind=='agent' must also carry a non-empty `agent` string."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    required = {"name", "kind", "agent", "inputs", "artifact", "gate"}
    for step in steps:
        for field in required:
            assert field in step, (
                f"step {step.get('name')!r} missing required field: {field!r}"
            )

        # name: non-empty string
        assert isinstance(step["name"], str) and step["name"], (
            f"step name must be a non-empty string, got {step['name']!r}"
        )

        # kind: 'code' or 'agent'
        assert step["kind"] in ("code", "agent"), (
            f"step {step['name']!r} kind must be 'code' or 'agent', "
            f"got {step['kind']!r}"
        )

        # agent: required and non-empty when kind=='agent'
        if step["kind"] == "agent":
            assert isinstance(step["agent"], str) and step["agent"], (
                f"step {step['name']!r} kind='agent' requires a non-empty "
                f"string `agent` field, got {step['agent']!r}"
            )
        else:
            # code steps must have agent=None
            assert step["agent"] is None, (
                f"step {step['name']!r} kind='code' must have agent=None, "
                f"got {step['agent']!r}"
            )

        # inputs: list
        assert isinstance(step["inputs"], list), (
            f"step {step['name']!r} inputs must be a list, "
            f"got {type(step['inputs']).__name__}"
        )

        # artifact: str or None
        assert step["artifact"] is None or isinstance(step["artifact"], str), (
            f"step {step['name']!r} artifact must be str|None, "
            f"got {type(step['artifact']).__name__}"
        )

        # gate: None or list of dicts each with non-empty 'fn'
        if step["gate"] is not None:
            assert isinstance(step["gate"], list), (
                f"step {step['name']!r} gate must be a list or None, "
                f"got {type(step['gate']).__name__}"
            )
            for j, gate_item in enumerate(step["gate"]):
                assert isinstance(gate_item, dict), (
                    f"step {step['name']!r} gate[{j}] must be a dict"
                )
                assert "fn" in gate_item, (
                    f"step {step['name']!r} gate[{j}] missing required field 'fn'"
                )
                assert isinstance(gate_item["fn"], str) and gate_item["fn"], (
                    f"step {step['name']!r} gate[{j}].fn must be a non-empty string"
                )


# ---------------------------------------------------------------------------
# Step sequence: required steps present and in correct relative order
# ---------------------------------------------------------------------------

REQUIRED_STEP_NAMES = [
    "config",
    "cursor-read",
    "scratch",
    "extract",
    "tts",
    "gen-still",
    "gen-video",
    "chart",
    "subtitle",
    "assemble",
    "provenance",
    "publish",
    "cleanup",
]


def test_all_required_steps_present():
    """The pipeline must contain all Phase-2 required steps by name."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    names = [s["name"] for s in steps]

    for required_name in REQUIRED_STEP_NAMES:
        assert required_name in names, (
            f"Required step {required_name!r} missing from pipeline. "
            f"Present steps: {names}"
        )


def test_config_is_first_step():
    """'config' must be the first step (index 0)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert steps[0]["name"] == "config", (
        f"First step must be 'config', got {steps[0]['name']!r}"
    )


def test_cleanup_is_last_step():
    """'cleanup' must be the last step."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert steps[-1]["name"] == "cleanup", (
        f"Last step must be 'cleanup', got {steps[-1]['name']!r}"
    )


def _step_index(steps, name):
    names = [s["name"] for s in steps]
    return names.index(name)


def test_step_order_scratch_before_tts():
    """'scratch' must precede 'tts'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "scratch") < _step_index(steps, "tts"), (
        "scratch must come before tts"
    )


def test_step_order_tts_before_gen_still():
    """'tts' must precede 'gen-still' (TTS drives clip timing)."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "tts") < _step_index(steps, "gen-still"), (
        "tts must come before gen-still"
    )


def test_shotplan_between_tts_and_gen_still():
    """'镜头计划' (Phase-1 shot-timeline step) must sit strictly between 'tts'
    and 'gen-still': it needs the real per-beat durations tts just measured,
    and gen-still must consume the windows it writes.

    Pre-impl: '镜头计划' is absent from the step table → ValueError from
    _step_index (module absent) → red. Green after Task 3-impl.
    """
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    idx_tts = _step_index(steps, "tts")
    idx_shotplan = _step_index(steps, "镜头计划")
    idx_gen_still = _step_index(steps, "gen-still")

    assert idx_tts < idx_shotplan < idx_gen_still, (
        f"镜头计划 (idx {idx_shotplan}) must be between tts (idx {idx_tts}) "
        f"and gen-still (idx {idx_gen_still})"
    )


def test_shotplan_step_shape_is_code_kind_no_gate():
    """镜头计划 is a pure-code step (no agent dispatch, no gate) — MR check
    per plan Task 3-impl step 1: gate=[] (falsy) so runner.py's
    `if not step.get("gate"): break` skips gate-running for this step."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    idx_shotplan = _step_index(steps, "镜头计划")
    step = steps[idx_shotplan]
    assert step["kind"] == "code"
    assert step["agent"] is None
    assert step["gate"] == []


def test_step_order_gen_still_before_subtitle():
    """'gen-still' must precede 'subtitle'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "gen-still") < _step_index(steps, "subtitle"), (
        "gen-still must come before subtitle"
    )


def test_step_order_subtitle_before_assemble():
    """'subtitle' must precede 'assemble'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "subtitle") < _step_index(steps, "assemble"), (
        "subtitle must come before assemble"
    )


def test_step_order_assemble_before_provenance():
    """'assemble' must precede 'provenance'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "assemble") < _step_index(steps, "provenance"), (
        "assemble must come before provenance"
    )


def test_step_order_provenance_before_publish():
    """'provenance' must precede 'publish'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "provenance") < _step_index(steps, "publish"), (
        "provenance must come before publish"
    )


def test_step_order_publish_before_cleanup():
    """'publish' must precede 'cleanup'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    assert _step_index(steps, "publish") < _step_index(steps, "cleanup"), (
        "publish must come before cleanup"
    )


def test_gen_video_between_gen_still_and_subtitle():
    """'gen-video' must appear between 'gen-still' and 'subtitle'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    idx_still = _step_index(steps, "gen-still")
    idx_video = _step_index(steps, "gen-video")
    idx_subtitle = _step_index(steps, "subtitle")

    assert idx_still < idx_video < idx_subtitle, (
        f"gen-video (idx {idx_video}) must be between gen-still "
        f"(idx {idx_still}) and subtitle (idx {idx_subtitle})"
    )


def test_chart_between_tts_and_subtitle():
    """'chart' must appear between 'tts' and 'subtitle'."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    idx_tts = _step_index(steps, "tts")
    idx_chart = _step_index(steps, "chart")
    idx_subtitle = _step_index(steps, "subtitle")

    assert idx_tts < idx_chart < idx_subtitle, (
        f"chart (idx {idx_chart}) must be between tts "
        f"(idx {idx_tts}) and subtitle (idx {idx_subtitle})"
    )


# ---------------------------------------------------------------------------
# load_pipeline returns pure data: no callables in kind or gate
# ---------------------------------------------------------------------------

def test_load_pipeline_is_pure_data():
    """load_pipeline() must return purely serialisable data — no callable
    objects (functions, classes) embedded in any step field. The execution
    layer (runner) resolves fn names at dispatch time; this module stores
    only names."""
    from lib.pipeline import load_pipeline

    steps = load_pipeline()
    for step in steps:
        for field, value in step.items():
            assert not callable(value), (
                f"step {step.get('name')!r}.{field} must not be callable; "
                f"got {value!r}"
            )
            # gate items must also be free of callables
            if field == "gate" and isinstance(value, list):
                for gate_item in value:
                    if isinstance(gate_item, dict):
                        for gkey, gval in gate_item.items():
                            assert not callable(gval), (
                                f"step {step.get('name')!r}.gate item "
                                f"{gkey!r} must not be callable; got {gval!r}"
                            )


def test_load_pipeline_returns_fresh_list():
    """Two successive calls to load_pipeline() must return independent lists —
    mutating one must not affect the other."""
    from lib.pipeline import load_pipeline

    a = load_pipeline()
    b = load_pipeline()

    assert a is not b, "load_pipeline() must return a new list on each call"
    # Mutate `a` and verify `b` is unaffected
    original_name = b[0]["name"]
    a[0]["name"] = "__mutated__"
    assert b[0]["name"] == original_name, (
        "Mutating the result of load_pipeline() must not affect other calls"
    )


# ---------------------------------------------------------------------------
# validate_pipeline: fail-closed validation
# ---------------------------------------------------------------------------

def _make_valid_code_step():
    """Return a minimal valid code step dict for use in validation tests."""
    return {
        "name": "synthetic",
        "kind": "code",
        "agent": None,
        "inputs": [],
        "artifact": None,
        "gate": None,
    }


def test_validate_pipeline_accepts_valid_code_step():
    """A well-formed code step must pass validate_pipeline without raising."""
    from lib.pipeline import validate_pipeline

    step = _make_valid_code_step()
    validate_pipeline([step])  # must not raise


def test_validate_pipeline_rejects_unknown_kind():
    """A step with an unrecognised kind must raise ValueError naming the field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
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


def test_validate_pipeline_rejects_agent_step_without_agent():
    """A step with kind='agent' but agent=None must raise ValueError naming
    the offending field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    bad["kind"] = "agent"
    bad["agent"] = None  # illegal: agent required when kind=='agent'

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "agent" in msg, (
        f"ValueError must name the offending field 'agent', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_agent_step_with_empty_agent():
    """A step with kind='agent' and agent='' (empty string) must raise
    ValueError."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    bad["kind"] = "agent"
    bad["agent"] = ""

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "agent" in msg, (
        f"ValueError must name the offending field 'agent', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_gate_item_missing_fn():
    """A gate item missing the `fn` field must raise ValueError naming 'fn'."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    bad["gate"] = [{"args": {}}]  # missing `fn`

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "fn" in msg, (
        f"ValueError must name the offending field 'fn', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_gate_item_empty_fn():
    """A gate item with fn='' (empty string) must raise ValueError."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    bad["gate"] = [{"fn": ""}]

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "fn" in msg, (
        f"ValueError must name the offending field 'fn', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_gate_item_non_dict():
    """A gate item that is not a dict must raise ValueError."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    bad["gate"] = ["check_artifact"]  # string, not dict

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    # The error may name 'gate' or the item type — just ensure it raises
    assert "ValueError" in type(exc_info.value).__name__ or True


def test_validate_pipeline_rejects_missing_required_field():
    """A step missing the `name` field must raise ValueError naming the field."""
    from lib.pipeline import validate_pipeline

    bad = _make_valid_code_step()
    del bad["name"]

    with pytest.raises(ValueError) as exc_info:
        validate_pipeline([bad])
    msg = str(exc_info.value).lower()
    assert "name" in msg, (
        f"ValueError must name the missing field 'name', got: {exc_info.value}"
    )


def test_validate_pipeline_rejects_non_list_steps():
    """validate_pipeline must reject a non-list argument."""
    from lib.pipeline import validate_pipeline

    with pytest.raises((ValueError, TypeError)):
        validate_pipeline({"name": "oops"})  # dict instead of list


def test_validate_pipeline_accepts_agent_step_with_agent():
    """A well-formed agent step (non-empty agent) must pass validate_pipeline."""
    from lib.pipeline import validate_pipeline

    step = _make_valid_code_step()
    step["kind"] = "agent"
    step["agent"] = "storyboard"  # any non-empty agent name

    validate_pipeline([step])  # must not raise


def test_validate_pipeline_accepts_gate_with_valid_fn():
    """A gate item with a non-empty fn string must pass validate_pipeline."""
    from lib.pipeline import validate_pipeline

    step = _make_valid_code_step()
    step["gate"] = [{"fn": "check_artifact"}]

    validate_pipeline([step])  # must not raise


def test_validate_pipeline_accepts_full_pipeline():
    """The canonical load_pipeline() output must pass validate_pipeline."""
    from lib.pipeline import load_pipeline, validate_pipeline

    steps = load_pipeline()
    validate_pipeline(steps)  # must not raise
