"""Tests for lib/runner.py — pipeline sequencer for video-studio.

Written before lib/runner.py exists; collection must succeed here (imports
are deferred into each test body), but EVERY test fails at runtime with
`ModuleNotFoundError: No module named 'lib.runner'`.  Task 4-impl resolves
this.

Pins (per video-studio Phase 2 Task 4-tests):
- run_pipeline(config, *, pipeline=..., step_fns=..., dispatch=..., gates=...,
  scratch_dir=...) executes steps in the order of the injected pipeline list.
- code steps: runner calls step_fns[step["name"]](step, ctx, scratch_dir).
- agent steps: runner calls dispatch(agent_name, step, ctx, scratch_dir).
- Composite gate: any gate item returning ok=False → halt immediately;
  subsequent steps are NOT invoked.
- Missing artifact (step["artifact"] key absent from ctx, or pointing at an
  empty/absent file) → halt.
- Retry: a step with retry>0 re-invokes the step fn on gate miss; exceeding
  the cap halts.
- Clean run: cleanup_scratch(scratch_dir) is called after the last step.
- Halted run: cleanup_scratch is NOT called.
- run_pipeline returns {"status": "done", ...} on success and
  {"status": "halted", "failed_step": <name>, "reason": <...>} on halt.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.runner import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Helpers: build synthetic step dicts and fake seams
# ---------------------------------------------------------------------------

def _code_step(
    name: str,
    artifact: str | None = None,
    gate: list[dict] | None = None,
    retry: int = 0,
) -> dict[str, Any]:
    """Return a minimal synthetic code step."""
    s: dict[str, Any] = {
        "name": name,
        "kind": "code",
        "agent": None,
        "inputs": [],
        "artifact": artifact,
        "gate": gate,
    }
    if retry:
        s["retry"] = retry
    return s


def _agent_step(
    name: str,
    agent: str,
    artifact: str | None = None,
    gate: list[dict] | None = None,
    retry: int = 0,
) -> dict[str, Any]:
    """Return a minimal synthetic agent step."""
    s: dict[str, Any] = {
        "name": name,
        "kind": "agent",
        "agent": agent,
        "inputs": [],
        "artifact": artifact,
        "gate": gate,
    }
    if retry:
        s["retry"] = retry
    return s


def _make_config_stub() -> MagicMock:
    """Minimal config-shaped object the runner can consume."""
    cfg = MagicMock()
    cfg.vault = MagicMock()
    cfg.vault.output_dir = "/tmp/video-runner-test-output"
    return cfg


def _make_gates(extra: dict | None = None) -> dict[str, Any]:
    """Gate map with real check_artifact + pass-through fakes for others."""
    from lib.episode import check_artifact
    gates: dict[str, Any] = {
        "check_artifact": check_artifact,
    }
    if extra:
        gates.update(extra)
    return gates


def _writing_step_fn(
    artifact_content: str = "ok",
    fail: bool = False,
) -> Any:
    """Return a step fn that writes the artifact into ctx + scratch.

    If fail=True, the fn does NOT write the artifact key to ctx (simulating
    a step that fails to produce its output).
    """
    def _fn(step: dict, ctx: dict, scratch_dir: Path) -> None:
        if fail:
            return
        artifact_key = step.get("artifact")
        if artifact_key:
            p = scratch_dir / f"{artifact_key}.txt"
            p.write_text(artifact_content, encoding="utf-8")
            ctx[artifact_key] = str(p)
    return _fn


def _noop_step_fn() -> Any:
    """Return a step fn that does nothing (for steps with artifact=None)."""
    def _fn(step: dict, ctx: dict, scratch_dir: Path) -> None:
        pass
    return _fn


def _tracking_step_fn(calls: list) -> Any:
    """Return a step fn that appends the step name to `calls` each time it runs."""
    def _fn(step: dict, ctx: dict, scratch_dir: Path) -> None:
        calls.append(step["name"])
        artifact_key = step.get("artifact")
        if artifact_key:
            p = scratch_dir / f"{artifact_key}.txt"
            p.write_text("ok", encoding="utf-8")
            ctx[artifact_key] = str(p)
    return _fn


class _FakeDispatch:
    """Stand-in for an agent dispatch callable.

    Records every call; optionally writes the artifact stub so gates pass.
    Can be configured to fail specific steps.
    """
    def __init__(self, write_artifact: bool = True, fail_steps: set | None = None):
        self.calls: list[dict[str, Any]] = []
        self.write_artifact = write_artifact
        self.fail_steps: set = fail_steps or set()

    def __call__(
        self,
        agent_name: str,
        step: dict,
        ctx: dict,
        scratch_dir: Path,
    ) -> dict[str, Any]:
        step_name = step.get("name", agent_name)
        self.calls.append({
            "agent": agent_name,
            "step": step_name,
        })

        if step_name in self.fail_steps:
            return {
                "ok": False,
                "reason": f"fake-forced failure for step {step_name!r}",
            }

        artifact_key = step.get("artifact")
        if artifact_key and self.write_artifact:
            p = scratch_dir / f"{artifact_key}.txt"
            p.write_text("stub artifact", encoding="utf-8")
            ctx[artifact_key] = str(p)

        return {"ok": True, "reason": f"fake dispatch ok for {step_name!r}"}


# ---------------------------------------------------------------------------
# 0) Module import
# ---------------------------------------------------------------------------

def test_module_imports():
    """lib.runner must import cleanly and export run_pipeline.

    Failing here is the test-FAIL-first contract.
    """
    from lib import runner  # noqa: F401
    assert hasattr(runner, "run_pipeline"), "lib.runner must export run_pipeline"


# ---------------------------------------------------------------------------
# 1) Steps execute in pipeline order
# ---------------------------------------------------------------------------

def test_runner_executes_steps_in_pipeline_order(tmp_path):
    """Runner must call step_fns in the same order as the injected pipeline."""
    from lib.runner import run_pipeline

    pipeline = [
        _code_step("step-a"),
        _code_step("step-b"),
        _code_step("step-c"),
    ]
    execution_order: list[str] = []
    step_fns = {name: _tracking_step_fn(execution_order) for name in ["step-a", "step-b", "step-c"]}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", f"clean run must not halt: {result!r}"
    assert execution_order == ["step-a", "step-b", "step-c"], (
        f"steps must execute in pipeline order, got: {execution_order}"
    )


def test_runner_step_order_matches_real_pipeline(tmp_path):
    """Runner must execute the real load_pipeline() steps in their declared order."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    real_steps = load_pipeline()
    executed: list[str] = []

    # Build a step_fn for each step that records its name and writes artifact if needed.
    step_fns: dict[str, Any] = {}
    for step in real_steps:
        step_fns[step["name"]] = _tracking_step_fn(executed)

    # Agent steps (e.g. `script`) route through dispatch, not step_fns — record
    # them to `executed` too so the order assertion covers the full pipeline.
    def tracking_dispatch(agent_name, step, ctx, scratch_dir):
        executed.append(step["name"])
        art = step.get("artifact")
        if art:
            p = scratch_dir / f"{art}.txt"
            p.write_text("ok", encoding="utf-8")
            ctx[art] = str(p)
        return {"ok": True}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=real_steps,
        step_fns=step_fns,
        dispatch=tracking_dispatch,
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", f"clean run on real pipeline must not halt: {result!r}"

    expected_order = [s["name"] for s in real_steps]
    assert executed == expected_order, (
        f"execution order mismatch.\nExpected: {expected_order}\nGot: {executed}"
    )


# ---------------------------------------------------------------------------
# 2) code step vs agent step dispatch
# ---------------------------------------------------------------------------

def test_code_step_calls_step_fn(tmp_path):
    """A code step must invoke the matching step_fns entry (not dispatch)."""
    from lib.runner import run_pipeline

    called: list[str] = []

    def my_step_fn(step, ctx, scratch_dir):
        called.append("my-step")

    pipeline = [_code_step("my-step")]
    fake_dispatch = _FakeDispatch()

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns={"my-step": my_step_fn},
        dispatch=fake_dispatch,
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert "my-step" in called, "code step must call step_fn"
    assert len(fake_dispatch.calls) == 0, (
        "code step must NOT call dispatch, but dispatch was called: "
        f"{fake_dispatch.calls}"
    )


def test_agent_step_calls_dispatch(tmp_path):
    """An agent step must invoke dispatch with the agent name (not step_fn)."""
    from lib.runner import run_pipeline

    step_fn_called: list[str] = []

    def my_step_fn(step, ctx, scratch_dir):
        step_fn_called.append("wrongly-called")

    pipeline = [_agent_step("my-agent-step", agent="storyboard")]
    fake_dispatch = _FakeDispatch()

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns={"my-agent-step": my_step_fn},
        dispatch=fake_dispatch,
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert len(fake_dispatch.calls) == 1, (
        f"agent step must call dispatch once, got {len(fake_dispatch.calls)} calls"
    )
    assert fake_dispatch.calls[0]["agent"] == "storyboard", (
        f"dispatch must receive agent name 'storyboard', "
        f"got {fake_dispatch.calls[0]['agent']!r}"
    )
    assert step_fn_called == [], (
        "agent step must NOT call step_fn, but it was called"
    )


# ---------------------------------------------------------------------------
# 3) Gate: fail → halt
# ---------------------------------------------------------------------------

def test_gate_fail_halts_runner(tmp_path):
    """A gate that returns ok=False must halt the runner at that step."""
    from lib.runner import run_pipeline

    def always_fail(path: str) -> dict:
        return {"ok": False, "reason": "forced gate failure"}

    pipeline = [
        _code_step("step-ok", artifact="thing_ok", gate=[{"fn": "check_artifact"}]),
        _code_step("step-fail", artifact="thing_fail", gate=[{"fn": "always_fail"}]),
        _code_step("step-never"),
    ]

    calls: list[str] = []
    step_fns = {
        "step-ok": _tracking_step_fn(calls),
        "step-fail": _tracking_step_fn(calls),
        "step-never": _tracking_step_fn(calls),
    }

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates={**_make_gates(), "always_fail": always_fail},
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"runner must halt on gate failure, got status={result.get('status')!r}"
    )
    assert result.get("failed_step") == "step-fail", (
        f"failed_step must be 'step-fail', got {result.get('failed_step')!r}"
    )
    assert "step-never" not in calls, (
        "steps after the halt must NOT be executed"
    )


def test_composite_gate_any_fail_halts(tmp_path):
    """If a step has multiple gate items and any one fails, runner must halt."""
    from lib.runner import run_pipeline

    def gate_pass(path: str) -> dict:
        return {"ok": True, "reason": "gate-pass"}

    def gate_fail(path: str) -> dict:
        return {"ok": False, "reason": "gate-fail forced"}

    pipeline = [
        _code_step(
            "composite-step",
            artifact="thing",
            gate=[{"fn": "gate_pass"}, {"fn": "gate_fail"}],
        ),
    ]

    executed: list[str] = []
    step_fns = {"composite-step": _tracking_step_fn(executed)}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates={**_make_gates(), "gate_pass": gate_pass, "gate_fail": gate_fail},
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"runner must halt when any composite gate item fails, "
        f"got status={result.get('status')!r}"
    )
    assert result.get("failed_step") == "composite-step", (
        f"failed_step must be 'composite-step', got {result.get('failed_step')!r}"
    )


# ---------------------------------------------------------------------------
# 4) Missing artifact → halt
# ---------------------------------------------------------------------------

def test_missing_artifact_halts_runner(tmp_path):
    """A step whose artifact ctx key is absent (or points at an absent file)
    must halt the runner."""
    from lib.runner import run_pipeline

    # The step fn does NOT write the artifact key to ctx.
    def failing_step_fn(step, ctx, scratch_dir):
        # Deliberately do not write ctx[step["artifact"]].
        pass

    pipeline = [
        _code_step("bad-step", artifact="missing_thing", gate=[{"fn": "check_artifact"}]),
        _code_step("next-step"),
    ]
    calls: list[str] = []
    step_fns = {
        "bad-step": failing_step_fn,
        "next-step": _tracking_step_fn(calls),
    }

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"runner must halt on missing artifact, got status={result.get('status')!r}"
    )
    assert result.get("failed_step") == "bad-step", (
        f"failed_step must be 'bad-step', got {result.get('failed_step')!r}"
    )
    assert "next-step" not in calls, (
        "steps after the halt must NOT be executed"
    )


def test_empty_artifact_file_halts_runner(tmp_path):
    """A step whose artifact file is zero bytes must also halt (check_artifact
    treats empty files as missing)."""
    from lib.runner import run_pipeline

    def write_empty(step, ctx, scratch_dir):
        artifact_key = step.get("artifact")
        p = scratch_dir / f"{artifact_key}.txt"
        p.write_bytes(b"")  # zero-byte file
        ctx[artifact_key] = str(p)

    pipeline = [
        _code_step("empty-step", artifact="empty_thing", gate=[{"fn": "check_artifact"}]),
    ]
    step_fns = {"empty-step": write_empty}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"empty artifact must halt runner, got status={result.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# 5) Retry: gate miss → re-run step; cap exceeded → halt
# ---------------------------------------------------------------------------

def test_retry_on_gate_miss_reruns_step(tmp_path):
    """A step with retry>0 must be re-run on a gate miss; if retry succeeds,
    the runner must continue (not halt)."""
    from lib.runner import run_pipeline

    attempt_count: list[int] = [0]

    def flaky_step_fn(step, ctx, scratch_dir):
        attempt_count[0] += 1
        # Fail on the first attempt, succeed on the second.
        artifact_key = step.get("artifact")
        if artifact_key and attempt_count[0] >= 2:
            p = scratch_dir / f"{artifact_key}.txt"
            p.write_text("ok", encoding="utf-8")
            ctx[artifact_key] = str(p)
        # First attempt: don't write — gate will fail, triggering retry.

    pipeline = [
        _code_step("flaky", artifact="flaky_thing", gate=[{"fn": "check_artifact"}], retry=1),
        _code_step("next"),
    ]
    calls: list[str] = []
    step_fns = {
        "flaky": flaky_step_fn,
        "next": _tracking_step_fn(calls),
    }

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", (
        f"flaky step succeeds on retry — runner must not halt: {result!r}"
    )
    assert attempt_count[0] == 2, (
        f"flaky step must be tried twice (1 attempt + 1 retry), got {attempt_count[0]}"
    )
    assert "next" in calls, "step after the retried step must still execute"


def test_retry_exceeds_cap_halts(tmp_path):
    """A step that keeps failing despite all retries must halt the runner."""
    from lib.runner import run_pipeline

    attempt_count: list[int] = [0]

    def always_failing_step(step, ctx, scratch_dir):
        attempt_count[0] += 1
        # Never writes artifact → gate always fails.

    pipeline = [
        _code_step("always-fail", artifact="never_written", gate=[{"fn": "check_artifact"}], retry=2),
        _code_step("never-reached"),
    ]
    calls: list[str] = []
    step_fns = {
        "always-fail": always_failing_step,
        "never-reached": _tracking_step_fn(calls),
    }

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"exhausted retries must halt runner, got status={result.get('status')!r}"
    )
    assert result.get("failed_step") == "always-fail", (
        f"failed_step must be 'always-fail', got {result.get('failed_step')!r}"
    )
    # 1 initial attempt + 2 retries = 3 total
    assert attempt_count[0] == 3, (
        f"with retry=2, step must be attempted 3 times, got {attempt_count[0]}"
    )
    assert "never-reached" not in calls, (
        "steps after the halt must NOT be executed"
    )


# ---------------------------------------------------------------------------
# 6) Artifacts land in scratch
# ---------------------------------------------------------------------------

def test_artifact_written_to_scratch(tmp_path, monkeypatch):
    """The step fn receives scratch_dir and ctx; runner must verify artifact
    is present in ctx after the step runs.

    cleanup_scratch is no-op'd here so this test inspects the step's written
    artifact in isolation from the (separately-tested) cleanup-on-clean-run
    behavior — otherwise a clean run wipes scratch before the assertion.
    """
    from lib.runner import run_pipeline

    monkeypatch.setattr("lib.episode.cleanup_scratch", lambda _s: None)

    def step_writes_artifact(step, ctx, scratch_dir):
        p = scratch_dir / "my_artifact.txt"
        p.write_text("content", encoding="utf-8")
        ctx[step["artifact"]] = str(p)

    pipeline = [
        _code_step("write-artifact", artifact="my_artifact", gate=[{"fn": "check_artifact"}]),
    ]
    step_fns = {"write-artifact": step_writes_artifact}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", (
        f"run must succeed when artifact is written, got: {result!r}"
    )
    artifact_path = scratch / "my_artifact.txt"
    assert artifact_path.exists(), "artifact file must exist in scratch_dir"
    assert artifact_path.stat().st_size > 0, "artifact file must be non-empty"


# ---------------------------------------------------------------------------
# 7) Cleanup: called on clean run, NOT on halt
# ---------------------------------------------------------------------------

def test_cleanup_called_on_clean_run(tmp_path, monkeypatch):
    """cleanup_scratch must be called after a successful run."""
    from lib.runner import run_pipeline

    cleanup_calls: list[Any] = []

    def fake_cleanup(scratch):
        cleanup_calls.append(str(scratch))

    monkeypatch.setattr("lib.episode.cleanup_scratch", fake_cleanup)

    pipeline = [_code_step("only-step")]
    step_fns = {"only-step": _noop_step_fn()}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", f"run must succeed: {result!r}"
    assert len(cleanup_calls) == 1, (
        f"cleanup_scratch must be called exactly once on clean run, "
        f"got {len(cleanup_calls)} calls"
    )


def test_cleanup_not_called_on_halt(tmp_path, monkeypatch):
    """cleanup_scratch must NOT be called when the runner halts mid-pipeline."""
    from lib.runner import run_pipeline

    cleanup_calls: list[Any] = []

    def fake_cleanup(scratch):
        cleanup_calls.append(str(scratch))

    monkeypatch.setattr("lib.episode.cleanup_scratch", fake_cleanup)

    def failing_step(step, ctx, scratch_dir):
        pass  # does not write artifact → gate fails → halt

    pipeline = [
        _code_step("halt-me", artifact="nothing", gate=[{"fn": "check_artifact"}]),
    ]
    step_fns = {"halt-me": failing_step}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", f"run must halt: {result!r}"
    assert len(cleanup_calls) == 0, (
        f"cleanup_scratch must NOT be called on a halted run, "
        f"got {len(cleanup_calls)} calls: {cleanup_calls}"
    )


# ---------------------------------------------------------------------------
# 8) run_pipeline return shape
# ---------------------------------------------------------------------------

def test_run_pipeline_returns_dict(tmp_path):
    """run_pipeline must always return a dict."""
    from lib.runner import run_pipeline

    pipeline = [_code_step("step")]
    step_fns = {"step": _noop_step_fn()}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert isinstance(result, dict), (
        f"run_pipeline must return a dict, got {type(result).__name__}"
    )


def test_run_pipeline_done_has_status(tmp_path):
    """A successful run must return {"status": "done", ...}."""
    from lib.runner import run_pipeline

    pipeline = [_code_step("step")]
    step_fns = {"step": _noop_step_fn()}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "done", (
        f"successful run must return status='done', got {result!r}"
    )


def test_run_pipeline_halted_has_failed_step_and_reason(tmp_path):
    """A halted run must return {"status": "halted", "failed_step": ...,
    "reason": ...}."""
    from lib.runner import run_pipeline

    def no_artifact_step(step, ctx, scratch_dir):
        pass

    pipeline = [
        _code_step("the-step", artifact="x", gate=[{"fn": "check_artifact"}]),
    ]
    step_fns = {"the-step": no_artifact_step}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"must be halted, got {result!r}"
    )
    assert "failed_step" in result, (
        f"halted result must include 'failed_step', got {list(result)}"
    )
    assert "reason" in result, (
        f"halted result must include 'reason', got {list(result)}"
    )
    assert isinstance(result["failed_step"], str), "failed_step must be a string"
    assert isinstance(result["reason"], str), "reason must be a string"


# ---------------------------------------------------------------------------
# 9) ctx bundle is passed through steps (producer → consumer)
# ---------------------------------------------------------------------------

def test_ctx_propagates_across_steps(tmp_path):
    """Each step receives the ctx dict accumulated by prior steps; the runner
    must pass the SAME ctx object from step to step."""
    from lib.runner import run_pipeline

    observed_ctx: list[dict] = []

    def step_a(step, ctx, scratch_dir):
        ctx["key_from_a"] = "value_from_a"

    def step_b(step, ctx, scratch_dir):
        observed_ctx.append(dict(ctx))

    pipeline = [
        _code_step("step-a"),
        _code_step("step-b"),
    ]
    step_fns = {"step-a": step_a, "step-b": step_b}

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    run_pipeline(
        _make_config_stub(),
        pipeline=pipeline,
        step_fns=step_fns,
        dispatch=_FakeDispatch(),
        gates=_make_gates(),
        scratch_dir=scratch,
    )

    assert len(observed_ctx) == 1, "step-b must have been called"
    assert observed_ctx[0].get("key_from_a") == "value_from_a", (
        "ctx must carry values set by prior steps; "
        f"got ctx keys: {list(observed_ctx[0])}"
    )


# ---------------------------------------------------------------------------
# 10) CLI argparse: --config optional (Phase 3 config-discovery)
# ---------------------------------------------------------------------------

def test_cli_argparse_no_config_does_not_raise_systemexit():
    """runner._cli with no --config must NOT raise SystemExit (argparse must
    not treat --config as required).

    This test FAILS before Task 2-impl changes --config to required=False.
    """
    from lib.runner import _cli
    from unittest.mock import patch, MagicMock

    # Intercept load_config so we don't need a real config file.
    fake_config = MagicMock()
    fake_config.ffmpeg = MagicMock()
    fake_config.ffmpeg.ffmpeg_bin = "/usr/bin/ffmpeg"

    with patch("lib.config.load_config", return_value=fake_config), \
         patch("lib.preflight.check_ffmpeg_libass"), \
         patch("lib.syllabus.select_next", return_value=None), \
         patch("lib.runner.run_pipeline", return_value={"status": "done"}):
        # This must not raise SystemExit due to missing required --config.
        try:
            _cli([])  # no --config arg
        except SystemExit as e:
            if e.code == 2:
                raise AssertionError(
                    "--config must be optional (required=False); "
                    "argparse exited with code 2 (missing required argument)"
                ) from e
            # Any other exit code (0, 1) is acceptable here —
            # the argparse error is the only forbidden failure.


def test_cli_argparse_no_config_calls_load_config_with_none():
    """When --config is not passed, _cli must call load_config(None) so that
    the discovery logic in load_config is triggered.

    This test FAILS before Task 2-impl changes runner to pass args.config (None)
    instead of requiring a value.
    """
    from lib.runner import _cli
    from unittest.mock import patch, MagicMock, call

    fake_config = MagicMock()
    fake_config.ffmpeg = MagicMock()
    fake_config.ffmpeg.ffmpeg_bin = "/usr/bin/ffmpeg"

    captured_calls: list = []

    def capturing_load_config(path=None):
        captured_calls.append(path)
        return fake_config

    with patch("lib.config.load_config", side_effect=capturing_load_config), \
         patch("lib.preflight.check_ffmpeg_libass"), \
         patch("lib.syllabus.select_next", return_value=None), \
         patch("lib.runner.run_pipeline", return_value={"status": "done"}):
        try:
            _cli([])
        except SystemExit as e:
            if e.code == 2:
                raise AssertionError(
                    "--config must be optional; argparse SystemExit(2) means "
                    "required=True is still in place"
                ) from e

    assert len(captured_calls) >= 1, "load_config must have been called"
    assert captured_calls[0] is None, (
        f"load_config must be called with None when --config is absent, "
        f"got {captured_calls[0]!r}"
    )


def test_cli_argparse_explicit_config_passes_path():
    """When --config /some/path.yaml is passed, _cli must call
    load_config('/some/path.yaml') (explicit path takes priority over discovery).
    """
    from lib.runner import _cli
    from unittest.mock import patch, MagicMock

    fake_config = MagicMock()
    fake_config.ffmpeg = MagicMock()
    fake_config.ffmpeg.ffmpeg_bin = "/usr/bin/ffmpeg"

    captured_calls: list = []

    def capturing_load_config(path=None):
        captured_calls.append(path)
        return fake_config

    with patch("lib.config.load_config", side_effect=capturing_load_config), \
         patch("lib.preflight.check_ffmpeg_libass"), \
         patch("lib.syllabus.select_next", return_value=None), \
         patch("lib.runner.run_pipeline", return_value={"status": "done"}):
        try:
            _cli(["--config", "/fake/config.yaml"])
        except SystemExit:
            pass  # exit 0 is acceptable

    assert len(captured_calls) >= 1, "load_config must have been called"
    assert captured_calls[0] == "/fake/config.yaml", (
        f"load_config must be called with the explicit path, "
        f"got {captured_calls[0]!r}"
    )
