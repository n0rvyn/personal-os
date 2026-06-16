"""Tests for lib/runner.py — pipeline sequencer (the conveyor belt).

Written before lib/runner.py exists; collection must fail at this point
(`No module named 'lib.runner'`).

Pins (per phase1-code-runner-plan Task 3-tests):
- run_pipeline(show, *, date, no_tts=False, dispatch=..., gates=..., config=...)
  exists and is importable.
- Runner executes steps in pipeline order (load_pipeline(show) sequence).
- A station whose gate returns ok=False (or whose artifact is missing) →
  runner halts and returns {status:"halted", failed_step:<name>, reason:<...>};
  subsequent stations are NOT invoked.
- no_tts=True → step 14 (TTS) + step 15 mp3 move (the TTS-only sub-step)
  are skipped; the .md / broadcast-script / stance-card paths still run.
- Parallel groups (7/8/9) fan out across A/B/C (3 dispatches per group).
- 12a/16a retry: a single failure re-dispatches the corresponding station;
  exceeding the retry cap halts.
- 5b degraded-but-present (artifact exists, "degraded" marker) → runner
  continues; 5b artifact missing → runner halts.
- Composite gate: step 7 with a sub-floor draft triggers check_min_chars
  failure even if check_artifact passes.
- Anti-homogenization bridge: the assemble-briefs step (5b→7) reads
  magnitude-verdict.json + material-summary.md, computes per-candidate
  airtime via magnitude_to_airtime, and renders the covered-ground
  avoidance memo. The writing-brief-A.json it produces is consumed by
  the step-7 davinci dispatch (the routing+guard channel into the
  draft) — a missing avoid_memo handoff makes the anti-repeat guard
  a no-op. The legacy `recent_anchors` field has been retired (DP-001=A)
  in favor of the cross-episode covered-ground store.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.runner import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins (mirrors of pipeline / dispatch whitelists)
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
    contract — Task 3-impl will resolve this."""
    from lib import runner  # noqa: F401
    assert hasattr(runner, "run_pipeline")


# ---------------------------------------------------------------------------
# Fakes: dispatch + gates + helpers
#
# These fakes do NOT touch the real filesystem or spawn any subprocess.
# They record every interaction so tests can assert ordering, halt
# behavior, no-TTS skipping, parallel fan-out, retry loops, and the
# anti-homogenization bridge handoff.
# ---------------------------------------------------------------------------

class _FakeDispatch:
    """Stand-in for `lib.dispatch.dispatch_persona`.

    The fake doesn't run a subprocess; it just records the call and
    optionally writes a stub artifact file so the gate (check_artifact
    / check_min_chars) sees a passing artifact.

    Knobs (settable on the instance BEFORE invoking run_pipeline):
      - write_artifact (bool, default True): whether to materialize the
        artifact file under scratch_dir.
      - return_value (dict|None, default None): if set, return this dict
        verbatim (overrides the default success / failure shape).
      - fail_steps (set[str]): names of stations whose dispatch should
        return ok:False (without writing the artifact). Used to inject
        a halt at a specific step.
      - inspect_call (callable|None): invoked as inspect_call(agent_name,
        user_prompt, scratch_dir, expected_artifact, **kwargs) at every
        call. Used by the anti-homogenization-bridge test to capture
        the user_prompt the runner hands to davinci.
    """
    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.write_artifact: bool = True
        self.return_value: dict[str, Any] | None = None
        self.fail_steps: set[str] = set()
        self.inspect_call = None
        self.call_count_by_step: dict[str, int] = {}
        self._lock = threading.Lock()  # concurrent fan-out dispatch is thread-safe

    def __call__(
        self,
        agent_name: str,
        user_prompt: str,
        scratch_dir: Any,
        expected_artifact: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # The runner is expected to pass the station name (or an
        # identifying key) so we can route per-step behavior. Most
        # runner designs thread it through kwargs (`step_name=`) or
        # a parallel tag in the artifact name. We try to recover it
        # from the kwargs first, then fall back to deriving from the
        # artifact filename.
        step_name = kwargs.get("step_name")
        if step_name is None:
            # Best-effort recovery — tests assert on `calls[i]["step"]`,
            # so we tag the call with the artifact filename as a fallback
            # identifier. The impl may set this more precisely.
            step_name = expected_artifact
        with self._lock:
            self.calls.append({
                "agent": agent_name,
                "user_prompt": user_prompt,
                "scratch_dir": str(scratch_dir),
                "expected_artifact": expected_artifact,
                "step": step_name,
                "kwargs": dict(kwargs),
            })
            self.call_count_by_step[step_name] = (
                self.call_count_by_step.get(step_name, 0) + 1
            )

        if self.inspect_call is not None:
            self.inspect_call(
                agent_name, user_prompt, scratch_dir, expected_artifact,
                **kwargs,
            )

        # Per-step forced failure (used for halt tests)
        if step_name in self.fail_steps:
            return {
                "ok": False,
                "reason": f"fake-forced failure for step {step_name!r}",
                "artifact_path": str(Path(str(scratch_dir)) / expected_artifact),
            }

        # Explicit return_value override
        if self.return_value is not None:
            rv = dict(self.return_value)
            rv.setdefault("artifact_path", str(Path(str(scratch_dir)) / expected_artifact))
            return rv

        # Default success: write a stub artifact so check_artifact passes.
        artifact_path = Path(str(scratch_dir)) / expected_artifact
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if self.write_artifact:
            # Plain text body — long enough that check_min_chars('floor')
            # for 'morning' (6500) would fail, short enough to pass for
            # ordinary presence checks. For min_chars tests we override
            # write_artifact on a per-test basis by writing extra bytes
            # via `body_writer`.
            if not artifact_path.exists() or artifact_path.stat().st_size == 0:
                artifact_path.write_text("stub body", encoding="utf-8")
        return {
            "ok": True,
            "reason": f"fake wrote {artifact_path}",
            "artifact_path": str(artifact_path),
        }


def _make_gate_map() -> dict[str, Any]:
    """Build a gate-function map mirroring what run_pipeline is expected to
    construct internally. Tests can pass this as the `gates=` injection
    point (or fall back to the runner's default gate_map). Each gate
    returns `{"ok": bool, "reason": str}`."""
    from lib.episode import check_artifact, check_min_chars  # existing
    return {
        "check_artifact": check_artifact,
        "check_min_chars": check_min_chars,
        "check_stance_card_absent": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
        "check_factcheck": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
        "check_resonance_present": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
        "check_topic_log_appended": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
        "check_write_card_returned": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
        "check_stance_card": lambda *a, **kw: {"ok": True, "reason": "ok (default fake)"},
    }


def _set_out(cfg, out) -> None:
    """Phase 4: set output_dir + the derived episodes/state/reports subdirs on
    a (MagicMock) config stub and create them on disk.

    The runner now reads cfg.vault.episodes_dir/state_dir/reports_dir and wraps
    them in Path(); a bare MagicMock auto-creates those as child MagicMocks,
    and Path(MagicMock()) raises TypeError at runner entry. Setting real string
    values + mkdir mirrors lib.config._validate_vault_paths so every
    output_dir-assigning test exercises the real subdir layout.
    """
    out = Path(out)
    cfg.vault.output_dir = str(out)
    cfg.vault.episodes_dir = str(out / "episodes")
    cfg.vault.state_dir = str(out / "state")
    cfg.vault.reports_dir = str(out / "reports")
    for d in (out, out / "episodes", out / "state", out / "reports"):
        d.mkdir(parents=True, exist_ok=True)


def _make_config_stub() -> MagicMock:
    """A minimal PodcastTeamConfig-shaped object the runner can read."""
    cfg = MagicMock()
    cfg.vault = MagicMock()
    _set_out(cfg, "/tmp/ief-podcast-studio-runner-test-output")
    cfg.tts = MagicMock()
    return cfg


# ---------------------------------------------------------------------------
# Helper: bootstrap a minimal on-disk state for run_pipeline to consume
# ---------------------------------------------------------------------------

def _bootstrap_scratch(tmp_path: Path) -> Path:
    """Create a scratch dir under tmp_path and stage the artifacts the
    runner's code bridges (continuity-read, assemble-briefs) need to find.

    Returns the scratch Path.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


# ---------------------------------------------------------------------------
# 1) Runner executes steps in pipeline order
# ---------------------------------------------------------------------------

def test_runner_executes_steps_in_pipeline_order(monkeypatch, tmp_path):
    """A normal run (no failures) must invoke stations in the order returned
    by load_pipeline(show). The dispatch fake records every call; we assert
    the step name sequence matches the pipeline order."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    # A clean run must NOT halt
    assert isinstance(result, dict), f"run_pipeline must return dict, got {type(result)}"
    assert result.get("status") != "halted", (
        f"clean run must not halt, got {result!r}"
    )

    # The agent steps (5, 5b, 7×3, 8×3, 9×3, 10, 12, 12a, 13, 14, 16a, 16)
    # must all have been dispatched, in pipeline order. The runner's design
    # separates "agent" stations (dispatch) from "code" stations (no dispatch),
    # so the dispatch call sequence = the agent-station sequence.
    pipeline_steps = load_pipeline("morning")
    agent_step_names = [
        s["name"] for s in pipeline_steps if s.get("kind") == "agent"
    ]
    assert len(agent_step_names) > 0, "test precondition: pipeline must have agent stations"

    # Build the actual sequence from the fake's recorded calls. The fake
    # tags each call with `step=` (the station name, or a parallel-tagged
    # artifact if the impl doesn't thread step_name through). Either way
    # the per-call ORDER must match the pipeline's agent-station ORDER.
    # Parallel groups dispatch 3 times (A/B/C) — collapse consecutive
    # duplicates so the sequence check is station-by-station, not
    # dispatch-by-dispatch.
    actual_step_names: list[str] = []
    for call in fake.calls:
        sn = call["step"]
        # Parallel groups (7/8/9) may tag with "A"/"B"/"C" — collapse to
        # the station name for the sequence check.
        for name in agent_step_names:
            if sn == name or sn.startswith(name + ":") or sn.startswith(name + "."):
                actual_step_names.append(name)
                break
        else:
            # If we couldn't map it to a pipeline step, keep it as-is
            # (the test will fail if the order is wrong).
            actual_step_names.append(sn)

    # Collapse consecutive duplicates (parallel A/B/C → 1 station entry)
    collapsed: list[str] = []
    for name in actual_step_names:
        if not collapsed or collapsed[-1] != name:
            collapsed.append(name)
    actual_step_names = collapsed

    # Every agent step appears in actual order; the relative order matches
    # the pipeline order.
    assert actual_step_names == agent_step_names, (
        f"runner dispatched steps out of order.\n"
        f"expected: {agent_step_names}\n"
        f"actual:   {actual_step_names}"
    )


# ---------------------------------------------------------------------------
# 2) Halt: a station with gate ok=False halts the pipeline
# ---------------------------------------------------------------------------

def test_runner_halts_on_gate_failure(monkeypatch, tmp_path):
    """When a station's gate returns ok=False (or its artifact is missing),
    the runner must halt and return {status:'halted', failed_step:<name>,
    reason:<str>}. Stations AFTER the failed one must NOT be invoked."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    # Pick an agent station in the MIDDLE of the pipeline to fail. The
    # runner must halt there and skip everything downstream.
    pipeline_steps = load_pipeline("morning")
    target_step = "magnitude"  # step 5b — liangchen magnitude verdict
    assert any(s["name"] == target_step for s in pipeline_steps), (
        f"test precondition: pipeline must contain step {target_step!r}"
    )

    # Force the magnitude dispatch to return ok=False (no artifact written)
    fake.fail_steps = {target_step}
    # Also ensure no artifact is written for that step (already implied by
    # fail_steps branch above — the fake skips write_artifact when failing).

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert isinstance(result, dict)
    assert result.get("status") == "halted", (
        f"halt expected, got {result!r}"
    )
    assert result.get("failed_step") == target_step, (
        f"halted step must be {target_step!r}, got {result.get('failed_step')!r}"
    )
    assert "reason" in result and result["reason"], (
        f"halted result must include a non-empty `reason`, got {result!r}"
    )

    # Stations AFTER the failed one must not have been dispatched.
    target_idx = next(
        i for i, s in enumerate(pipeline_steps) if s["name"] == target_step
    )
    downstream_agent_steps = [
        s["name"] for s in pipeline_steps[target_idx + 1:]
        if s.get("kind") == "agent"
    ]
    dispatched_steps = {c["step"] for c in fake.calls}
    for ds in downstream_agent_steps:
        # The fake may tag with parallel suffixes; check membership with
        # startswith to be tolerant of the impl's tagging.
        leaked = [
            c["step"] for c in fake.calls
            if c["step"] == ds or c["step"].startswith(ds + ":")
        ]
        assert not leaked, (
            f"downstream step {ds!r} was invoked despite halt at {target_step!r}: "
            f"{leaked!r}"
        )


def test_runner_halts_on_missing_artifact(monkeypatch, tmp_path):
    """The dispatch returns ok=True but the artifact file isn't actually on
    disk (check_artifact gate fails). The runner must halt."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    # Disable artifact writing — dispatch will "succeed" but the file
    # doesn't exist, so check_artifact should reject it.
    fake.write_artifact = False
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert isinstance(result, dict)
    assert result.get("status") == "halted", (
        f"missing artifact must halt the pipeline, got {result!r}"
    )
    # The first station that requires a written artifact (after 3a) is
    # the failure point. The exact name is whatever the runner surfaces
    # — the test asserts halt+reason, not a specific name.
    assert "failed_step" in result, f"halt result must include failed_step, got {result!r}"
    assert result["failed_step"], "failed_step must be non-empty"


# ---------------------------------------------------------------------------
# 3) no_tts: skip TTS-only stations; rest of the pipeline still runs
# ---------------------------------------------------------------------------

def test_no_tts_skips_tts_and_mp3_move(tmp_path):
    """With no_tts=True, the TTS step (step 14) and the mp3-move sub-step
    (part of step 15) must be skipped. Other artifact paths (.md,
    broadcast-script, stance card) must still be touched."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", (
        f"no_tts run must not halt on a clean path, got {result!r}"
    )

    pipeline_steps = load_pipeline("morning")
    tts_step_names = {
        s["name"] for s in pipeline_steps if s.get("skip_when") == "no_tts"
    }
    assert "tts" in tts_step_names, (
        f"pipeline must tag a step with skip_when='no_tts', got {tts_step_names!r}"
    )

    # No dispatch for any step whose station is tagged skip_when=='no_tts'
    dispatched_steps = {c["step"] for c in fake.calls}
    for ts in tts_step_names:
        leaked = [
            c["step"] for c in fake.calls
            if c["step"] == ts or c["step"].startswith(ts + ":")
        ]
        assert not leaked, (
            f"no_tts=True must skip step {ts!r}; got dispatched: {leaked!r}"
        )

    # Conversely, the broadcast-rewrite (step 13) and stance-write
    # (step 16) stations must STILL have been dispatched.
    for required in ("broadcast-rewrite",):
        present = any(
            c["step"] == required or c["step"].startswith(required + ":")
            for c in fake.calls
        )
        assert present, (
            f"no_tts must still dispatch {required!r}; got steps: "
            f"{[c['step'] for c in fake.calls]!r}"
        )


# ---------------------------------------------------------------------------
# 4) Parallel fan-out: 7/8/9 dispatch A/B/C
# ---------------------------------------------------------------------------

def test_parallel_groups_fan_out_abc(tmp_path):
    """The 3 parallel groups (drafts=7, critiques=8, polishes=9) must each
    dispatch A, B, C — that's 3 calls per group, 9 calls total."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )
    assert result.get("status") != "halted", f"got {result!r}"

    # Each parallel group's station must appear exactly 3 times in the
    # dispatch log.
    for group in ("drafts", "critiques", "polishes"):
        # Tolerant of how the impl tags parallel calls: the fake records
        # `step=station_name[:tag]` if the impl threads a tag through, or
        # bare `step=station_name` if not. We accept both shapes via
        # call_count_by_step OR a startswith scan.
        count = fake.call_count_by_step.get(group, 0)
        if count == 0:
            count = sum(
                1 for c in fake.calls
                if c["step"] == group or c["step"].startswith(group + ":")
                or c["step"].startswith(group + ".")
            )
        assert count == 3, (
            f"parallel group {group!r} must dispatch 3 times (A/B/C), got {count}; "
            f"calls: {[(c['step'], c['expected_artifact']) for c in fake.calls]!r}"
        )


# ---------------------------------------------------------------------------
# 5) Composite gate: check_min_chars triggers on short draft
# ---------------------------------------------------------------------------

def test_composite_gate_min_chars_triggers_failure(tmp_path):
    """Step 7 has a composite gate [check_artifact, check_min_chars(floor)].
    A sub-floor draft must trigger check_min_chars failure (even though
    check_artifact passes) and halt the pipeline at that step."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline
    from lib.episode import floor_chars_for_show

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    # write_artifact=True, but we override the body to be much shorter
    # than the floor (6500 for morning). We do this via a side-effect
    # callback that runs AFTER the fake's default write.
    floor = floor_chars_for_show("morning")
    short_body = "x" * 100  # way below 6500

    original_call = fake.__class__.__call__

    def writing_short_drafts(*args, **kwargs):
        # Default behavior: write stub body, return ok=True
        # NOTE: *args already includes the bound `self` (Python binds it
        # when the runner calls `dispatch_fn(args)`), so we pass *args
        # directly to original_call — NOT `original_call(fake, *args)`.
        result = original_call(*args, **kwargs)
        # If this is a step-7 dispatch (drafts-A/B/C), overwrite with
        # a sub-floor body so check_min_chars will fail.
        ea = kwargs.get("expected_artifact", "")
        sn = kwargs.get("step_name", "")
        is_drafts = (
            ea.startswith("draft-") or sn == "drafts"
            or (isinstance(sn, str) and sn.startswith("drafts"))
        )
        if is_drafts and result.get("ok"):
            artifact_path = Path(result["artifact_path"])
            artifact_path.write_text(short_body, encoding="utf-8")
        return result

    fake.__class__.__call__ = writing_short_drafts  # type: ignore[assignment]
    try:
        gates = _make_gate_map()
        config = _make_config_stub()

        result = run_pipeline(
            "morning",
            date="2026-06-14",
            dispatch=fake,
            gates=gates,
            config=config,
            scratch_dir=scratch,
        )

        assert isinstance(result, dict)
        assert result.get("status") == "halted", (
            f"sub-floor draft must halt, got {result!r}"
        )
        # The halt should name the drafts step (or a parallel slice of it).
        failed = result.get("failed_step", "")
        assert "draft" in failed.lower(), (
            f"halt expected at drafts step, got failed_step={failed!r}"
        )
    finally:
        fake.__class__.__call__ = original_call  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 6) Retry: 12a/16a re-dispatch on failure; cap → halt
# ---------------------------------------------------------------------------

def test_retry_dispatches_twice_then_halts_on_cap(tmp_path):
    """A retry station (12a=factcheck) with retry=1 must be re-dispatched
    exactly once on failure; a SECOND failure halts the pipeline."""
    from lib.runner import run_pipeline
    from lib.pipeline import load_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    # Force the factcheck step (12a) to fail every time → retry exhausted → halt.
    fake.fail_steps = {"factcheck"}
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"retry-exhausted must halt, got {result!r}"
    )
    # The factcheck station was called 1 (initial) + 1 (retry) = 2 times
    # before the runner gave up.
    fc_calls = fake.call_count_by_step.get("factcheck", 0)
    if fc_calls == 0:
        fc_calls = sum(
            1 for c in fake.calls
            if c["step"] == "factcheck" or c["step"].startswith("factcheck")
        )
    assert fc_calls == 2, (
        f"factcheck should be called initial+retry=2 times, got {fc_calls}; "
        f"calls: {[c['step'] for c in fake.calls]!r}"
    )
    # G1 (test-fidelity fix): the retry must RE-DISPATCH the PARENT generator
    # (finalize), not just re-run the failing gate station against unchanged
    # input. finalize is dispatched once in the normal flow + once during the
    # single factcheck retry = 2. The old test asserted only factcheck==2, so
    # it passed even when the retry was inert — this assertion pins the fix.
    fin_calls = fake.call_count_by_step.get("finalize", 0)
    if fin_calls == 0:
        fin_calls = sum(
            1 for c in fake.calls
            if c["step"] == "finalize" or c["step"].startswith("finalize")
        )
    assert fin_calls == 2, (
        f"G1: factcheck retry must re-dispatch parent finalize "
        f"(expected 2 calls: normal+retry), got {fin_calls}; "
        f"calls: {[c['step'] for c in fake.calls]!r}"
    )
    assert "factcheck" in str(result.get("failed_step", "")).lower(), (
        f"halt should name factcheck, got {result!r}"
    )


def test_retry_succeeds_on_second_attempt(tmp_path):
    """A retry station whose first dispatch fails and second succeeds must
    NOT halt — the pipeline continues past the retry."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    # Fail factcheck the first time only; succeed on retry.
    call_counter = {"n": 0}
    original_call = fake.__class__.__call__

    def fail_first_factcheck(*args, **kwargs):
        result = original_call(*args, **kwargs)
        sn = kwargs.get("step_name", "")
        ea = kwargs.get("expected_artifact", "")
        is_factcheck = (
            sn == "factcheck" or ea.startswith("factcheck")
            or (isinstance(sn, str) and sn.startswith("factcheck"))
        )
        if is_factcheck:
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                # First attempt: report failure AND make sure the artifact
                # is missing so the gate fails.
                return {
                    "ok": False,
                    "reason": "fake first-attempt failure",
                    "artifact_path": str(Path(str(kwargs.get("scratch_dir", scratch))) / ea),
                }
        return result

    fake.__class__.__call__ = fail_first_factcheck  # type: ignore[assignment]
    try:
        gates = _make_gate_map()
        config = _make_config_stub()

        result = run_pipeline(
            "morning",
            date="2026-06-14",
            dispatch=fake,
            gates=gates,
            config=config,
            scratch_dir=scratch,
        )

        # Must NOT halt — the second attempt succeeded.
        assert result.get("status") != "halted", (
            f"second-attempt success must not halt, got {result!r}"
        )
        assert call_counter["n"] == 2, (
            f"factcheck should have been called twice, got {call_counter['n']}"
        )
    finally:
        fake.__class__.__call__ = original_call  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 7) 5b degradation vs missing — runner distinguishes
# ---------------------------------------------------------------------------

def test_5b_artifact_present_degraded_passes_through(tmp_path):
    """If the magnitude-verdict.json is present and parseable, the runner
    must NOT halt at 5b — even if the parsed verdict carries a `degraded`
    marker (the safe_parse_verdict fail-soft path)."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    # Pre-stage a valid magnitude verdict on disk in the scratch dir so
    # check_artifact passes. The dispatch fake will also try to write
    # one, but a pre-existing one is fine (the fake overwrites).
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({
            "verdicts": [
                {
                    "candidate": "A",
                    "magnitude": "light",
                    "matches_prior": None,
                    "what_moved": "",
                    "recap_hook": None,
                    "degraded": True,
                }
            ]
        }),
        encoding="utf-8",
    )

    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    # 5b passed (artifact present), so the pipeline is at least past 5b.
    # It MAY still halt later (the test is only asserting the runner did
    # not refuse to leave step 5b because of the `degraded` flag).
    if result.get("status") == "halted":
        failed = str(result.get("failed_step", ""))
        assert failed != "magnitude", (
            f"5b with degraded-but-present verdict must not halt at "
            f"'magnitude' step, got {result!r}"
        )


def test_5b_artifact_missing_halts(tmp_path):
    """If the magnitude-verdict.json is NOT written to disk (e.g. the
    liangchen dispatch claimed ok=True but produced no file), the runner
    must halt at 5b. This is the deny-default distinction: degraded-but-
    present ≠ missing."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    # Disable artifact writing → dispatch returns ok=True but no file
    # is on disk. The runner's check_artifact gate at 5b will catch it.
    fake.write_artifact = False

    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert result.get("status") == "halted", (
        f"5b artifact missing must halt, got {result!r}"
    )
    # The first station that requires a written artifact is the halt
    # point — the runner should name it. We accept any "first" station
    # that requires artifact presence (magnitude or earlier).
    failed = str(result.get("failed_step", ""))
    assert failed, f"halt result must name the failed step, got {result!r}"


# ---------------------------------------------------------------------------
# 8) Anti-homogenization bridge (must-revise core)
# ---------------------------------------------------------------------------

def test_assemble_briefs_hands_avoid_memo_to_step7_davinci(tmp_path):
    """The compose→drafts bridge must read the covered-ground store
    (DP-001=A) and inject the rendered `avoid_memo` into
    writing-brief-A.json. The step-7 davinci dispatch then receives
    that memo as part of its user_prompt — the anti-repeat guard
    channel. The legacy `recent_anchors` / `recent_anchors_union` field
    on the brief is RETIRED: a missing avoid_memo handoff makes the
    guard a no-op; a residual recent_anchors field means the runner
    is still wired to the old magnitude-judge signal."""
    from lib.runner import run_pipeline
    from lib.episode import floor_chars_for_show
    from lib.magnitude import magnitude_to_airtime
    from lib.coveredground import write_store

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)

    # Pre-stage a covered-ground store under `output_dir` with a single
    # hot anchor. The runner's `_assemble_briefs` will load this and
    # render an `avoid_memo` referencing the anchor. The hot entry uses
    # 3 in-window episode dates (count predicate) so `is_stale` fires
    # deterministically and the memo is non-empty.
    today_str = "2026-06-14"
    hot_anchor = "1956苏伊士运河危机"
    store = {
        "anchors": {
            hot_anchor: {
                "first_used": "2026-06-05",
                "last_used":  "2026-06-13",
                "count":      3,
                "episodes": [
                    {"date": "2026-06-05", "show": "morning"},
                    {"date": "2026-06-09", "show": "morning"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            }
        }
    }
    (output_dir / "state").mkdir(parents=True, exist_ok=True)
    write_store(output_dir / "state", store)

    # Pre-stage a magnitude verdict WITHOUT a `recent_anchors` field —
    # the legacy channel is gone. magnitude=medium → airtime=segment,
    # which we assert later in the brief.
    expected_magnitude = "medium"
    expected_airtime = magnitude_to_airtime(expected_magnitude)
    assert expected_airtime == "segment", (
        f"test precondition: medium → segment, got {expected_airtime!r}"
    )

    verdict_doc = {
        "verdicts": [
            {
                "candidate": "A",
                "magnitude": expected_magnitude,
                "matches_prior": None,
                "what_moved": "a new development",
                "recap_hook": None,
            }
        ]
    }
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps(verdict_doc), encoding="utf-8"
    )
    # Pre-stage material-summary.md with brief-A/B/C stubs (the davinci
    # collector writes these; the runner should also tolerate an empty
    # brief block by carrying empty fallbacks).
    (scratch / "material-summary.md").write_text(
        "stub\n\n## brief-A\nstub\n\n## brief-B\nstub\n\n## brief-C\nstub\n",
        encoding="utf-8",
    )

    # Capture the user_prompt the runner hands to the step-7 davinci
    # dispatch. The runner must include the avoid_memo in that prompt —
    # that's the bridge.
    captured_prompts: list[str] = []

    def inspect(agent_name, user_prompt, scratch_dir, expected_artifact, **kwargs):
        ea = expected_artifact
        if ea.startswith("draft-") and "md" in ea:
            captured_prompts.append(user_prompt)

    fake = _FakeDispatch()
    fake.inspect_call = inspect
    gates = _make_gate_map()
    config = _make_config_stub()
    _set_out(config, output_dir)

    result = run_pipeline(
        "morning",
        date=today_str,
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    # Locate writing-brief-A.json (the runner may have tagged the slice).
    writing_brief = scratch / "writing-brief-A.json"
    if not writing_brief.exists():
        candidates = list(scratch.glob("writing-brief*"))
        assert candidates, (
            f"assemble-briefs must write a writing-brief artifact to scratch; "
            f"scratch contents: {list(scratch.iterdir())!r}"
        )
        writing_brief = candidates[0]

    brief = json.loads(writing_brief.read_text(encoding="utf-8"))
    # G7: the brief MUST carry the avoid_memo (the new避让 channel). A
    # missing or empty avoid_memo is a no-op anti-repeat guard.
    assert "avoid_memo" in brief, (
        f"writing-brief must carry an `avoid_memo` field (DP-001=A); "
        f"got brief={brief!r}"
    )
    assert isinstance(brief["avoid_memo"], str), (
        f"`avoid_memo` must be a string, got "
        f"{type(brief['avoid_memo']).__name__}: {brief['avoid_memo']!r}"
    )
    assert brief["avoid_memo"], (
        f"`avoid_memo` must be non-empty when the store has a hot anchor; "
        f"got brief={brief!r}"
    )
    assert hot_anchor in brief["avoid_memo"], (
        f"`avoid_memo` must mention the hot anchor {hot_anchor!r} from the "
        f"covered-ground store; got memo={brief['avoid_memo']!r}"
    )
    # DP-001=A: the legacy recent_anchors / recent_anchors_union keys
    # MUST be gone. Their presence means the runner is still wired to
    # the old magnitude-judge signal.
    assert "recent_anchors" not in brief, (
        f"writing-brief must NOT carry `recent_anchors` (DP-001=A — that "
        f"signal is retired); got brief={brief!r}"
    )
    assert "recent_anchors_union" not in brief, (
        f"writing-brief must NOT carry `recent_anchors_union` (DP-001=A — "
        f"that signal is retired); got brief={brief!r}"
    )
    # Airtime routing still flows from magnitude_to_airtime.
    assert brief.get("airtime") in ("segment", "lead"), (
        f"writing-brief must carry airtime from "
        f"magnitude_to_airtime(medium)='segment', got brief={brief!r}"
    )

    # The davinci dispatch (step 7) must have been called with a
    # user_prompt that mentions BOTH the airtime (segment) AND the
    # avoid_memo's hot anchor — the anti-homogenization guard.
    assert captured_prompts, (
        f"step-7 davinci dispatch was never invoked; calls: "
        f"{[(c['agent'], c['expected_artifact']) for c in fake.calls]!r}"
    )
    joined = "\n".join(captured_prompts)
    assert hot_anchor in joined, (
        f"step-7 davinci prompt must include the avoid_memo anchor "
        f"{hot_anchor!r} (the anti-repeat guard handoff). "
        f"Prompt head: {joined[:500]!r}"
    )
    assert expected_airtime in joined, (
        f"step-7 davinci prompt must include the airtime routing "
        f"{expected_airtime!r} (magnitude_to_airtime handoff). "
        f"Prompt head: {joined[:500]!r}"
    )


def test_assemble_briefs_empty_memo_when_no_store(tmp_path):
    """When no covered-ground store exists (clean vault, first run),
    the brief's `avoid_memo` is the empty string — the runner still
    produces a valid brief, the step-7 davinci dispatch still runs
    (with a structured "(无 covered-ground 避让约束)" placeholder),
    and there is no halt. The old `recent_anchors` channel is absent
    by construction."""
    from lib.runner import run_pipeline
    from lib.coveredground import store_path as cg_store_path

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    # Sanity: no covered-ground.yaml exists on disk.
    assert not cg_store_path(output_dir / "state").exists()

    scratch = _bootstrap_scratch(tmp_path)
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "material-summary.md").write_text(
        "stub\n\n## brief-A\nstub\n\n## brief-B\nstub\n\n## brief-C\nstub\n",
        encoding="utf-8",
    )

    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()
    _set_out(config, output_dir)

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )

    assert result.get("status") != "halted", (
        f"clean run with no covered-ground store must not halt, "
        f"got {result!r}"
    )

    writing_brief = scratch / "writing-brief-A.json"
    assert writing_brief.exists(), (
        f"writing-brief-A.json must still be written even without a "
        f"store; scratch={list(scratch.iterdir())!r}"
    )
    brief = json.loads(writing_brief.read_text(encoding="utf-8"))
    assert "avoid_memo" in brief, (
        f"writing-brief must carry `avoid_memo` key even when no store; "
        f"got brief={brief!r}"
    )
    assert brief["avoid_memo"] == "", (
        f"`avoid_memo` must be the empty string when no store exists; "
        f"got {brief['avoid_memo']!r}"
    )
    # DP-001=A: the legacy channel stays absent.
    assert "recent_anchors" not in brief
    assert "recent_anchors_union" not in brief


def test_avoid_memo_does_not_suppress_opinions(tmp_path):
    """Temperature shield: `render_memo` lists apparatus anchors with
    "avoid / 换说法" framing — it NEVER advises against opinions, takes,
    or bets. The store only stores apparatus-shaped inputs (the
    distiller is the sole input source), so the memo's vocabulary
    must not contain suppression cues like "别下注" / "别表态". The
    runner hands the memo verbatim into the brief; the test guards
    that the temperature principle survives the entire chain."""
    from lib.runner import run_pipeline
    from lib.coveredground import write_store

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)

    # Pre-stage a covered-ground store with a hot anchor. (Content of
    # the anchor doesn't matter — what we test is the vocabulary of
    # the rendered memo.)
    hot_anchor = "苏伊士运河"
    today_str = "2026-06-14"
    store = {
        "anchors": {
            hot_anchor: {
                "first_used": "2026-06-05",
                "last_used":  "2026-06-13",
                "count":      3,
                "episodes": [
                    {"date": "2026-06-05", "show": "morning"},
                    {"date": "2026-06-09", "show": "morning"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            }
        }
    }
    (output_dir / "state").mkdir(parents=True, exist_ok=True)
    write_store(output_dir / "state", store)

    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "material-summary.md").write_text(
        "stub\n\n## brief-A\nstub\n", encoding="utf-8",
    )

    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()
    _set_out(config, output_dir)

    result = run_pipeline(
        "morning",
        date=today_str,
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )
    assert result.get("status") != "halted", f"got {result!r}"

    writing_brief = scratch / "writing-brief-A.json"
    assert writing_brief.exists()
    brief = json.loads(writing_brief.read_text(encoding="utf-8"))
    memo = brief.get("avoid_memo", "")
    # The memo is non-empty (we pre-staged a hot anchor) and contains
    # the apparatus anchor itself.
    assert memo and hot_anchor in memo, (
        f"memo must reference the hot apparatus anchor; got memo={memo!r}"
    )
    # Temperature shield: the memo's vocabulary must NOT suppress
    # opinions / takes / bets. (The store only stores apparatus-shaped
    # inputs, and render_memo only emits avoidance cues for those.)
    forbidden_phrases = ["别下注", "别表态", "不要下注", "不要表态", "禁止下注", "禁止表态"]
    for phrase in forbidden_phrases:
        assert phrase not in memo, (
            f"avoid_memo must not suppress opinions / takes; found "
            f"forbidden phrase {phrase!r} in memo={memo!r}"
        )


# ---------------------------------------------------------------------------
# 9) show argument routing: morning / evening both execute
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("show", ["morning", "evening"])
def test_runner_handles_both_shows(tmp_path, show):
    """The runner must accept both 'morning' and 'evening' shows. (The
    topology is the same; the editorial branch is what differs.)"""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        show,
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )
    # A clean run must not halt (and must produce a dict)
    assert isinstance(result, dict)
    assert result.get("status") != "halted", (
        f"clean {show} run must not halt, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 10) Runner result envelope
# ---------------------------------------------------------------------------

def test_runner_returns_status_dict(monkeypatch, tmp_path):
    """A successful run returns a dict with `status` ∈ {'ok', 'halted',
    'blocked'}. The exact 'ok' string may vary ('done' / 'completed' /
    'ok' / no-status) — we accept any non-halt result and assert the
    shape."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )
    assert isinstance(result, dict)
    assert "status" in result or set(result.keys()) >= {"failed_step", "reason"}, (
        f"runner result must be a status envelope, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 11) Agents are not invoked with non-whitelisted names
# ---------------------------------------------------------------------------

def test_dispatch_calls_only_whitelisted_agents(tmp_path):
    """The runner must never dispatch to an agent name outside the
    whitelist (the dispatch guard is the security boundary, but the
    runner's own station table also constrains the names)."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    gates = _make_gate_map()
    config = _make_config_stub()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        dispatch=fake,
        gates=gates,
        config=config,
        scratch_dir=scratch,
    )
    # Every dispatch call used a whitelisted agent name.
    for c in fake.calls:
        assert c["agent"] in AGENT_WHITELIST, (
            f"runner dispatched non-whitelisted agent {c['agent']!r} "
            f"at step {c['step']!r}"
        )


# ---------------------------------------------------------------------------
# Post-review fixes (implementation-reviewer 2026-06-14): G2 per-slice floor,
# G6 orchestrator load. The full production-gate handshake (G7
# material_summary_path etc.) is exercised end-to-end by the no-TTS e2e
# phase-acceptance run, not stubbed here.
# ---------------------------------------------------------------------------

def test_composite_gate_floors_each_parallel_slice_not_just_A(tmp_path):
    """G2: the floor gate must check EACH parallel slice, not only A.
    Shorten ONLY the B draft (A and C stay full stubs) → the run must halt
    at drafts:B. The old code gated only the A path, so a short B/C draft
    slipped into scoring (the 06-14 short-episode defect could survive)."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    fake = _FakeDispatch()
    short_body = "x" * 100  # above the 50-byte stub bypass, below the 6500 floor
    original_call = fake.__class__.__call__

    def short_b_only(*args, **kwargs):
        result = original_call(*args, **kwargs)
        if kwargs.get("step_name") == "drafts:B" and result.get("ok"):
            Path(result["artifact_path"]).write_text(short_body, encoding="utf-8")
        return result

    fake.__class__.__call__ = short_b_only  # type: ignore[assignment]
    try:
        result = run_pipeline(
            "morning",
            date="2026-06-14",
            dispatch=fake,
            gates=_make_gate_map(),
            config=_make_config_stub(),
            scratch_dir=scratch,
        )
        assert result.get("status") == "halted", (
            f"a sub-floor B draft must halt (G2), got {result!r}"
        )
        assert result.get("failed_step") == "drafts:B", (
            f"halt must name the B slice specifically, got "
            f"{result.get('failed_step')!r}"
        )
    finally:
        fake.__class__.__call__ = original_call  # type: ignore[assignment]


def test_topic_log_step_loads_orchestrator_via_file_path(tmp_path):
    """G6: the prep dir is hyphenated (`podcast-studio-prep`), NOT importable
    via a dotted path. _topic_log_step must load orchestrator.py via importlib
    from its file path and call run_finalize (which has NO `show` param). The
    old dotted-underscore import raised ModuleNotFoundError → swallowed →
    topic_log_path never set → halt on EVERY production run. This test calls
    the REAL orchestrator (the handshake the stub gates never exercised)."""
    from lib.runner import _topic_log_step

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "t", "body": "正文内容" * 50}, ensure_ascii=False),
        encoding="utf-8",
    )
    plugin_root = Path(__file__).resolve().parent.parent.parent  # podcast-studio/
    ctx = {
        "output_dir": str(output_dir),
        "date": "2026-06-14",
        "show": "morning",
        "scratch_dir": scratch,
        "plugin_root": str(plugin_root),
    }

    result = _topic_log_step(ctx)

    assert result is not None, (
        "G6: _topic_log_step returned None — orchestrator import or "
        "run_finalize call failed (the exact bug this test pins)"
    )
    assert ctx.get("topic_log_path") is not None, "ctx['topic_log_path'] must be set"
    assert Path(result).exists() and Path(result).stat().st_size > 0


def test_production_gate_map_no_tts_reaches_publish(tmp_path):
    """META-FINDING prevention: run with the REAL gate map (gates=None) on a
    fully-staged scratch — the production gate→ctx handshakes that the stub
    tests never exercised. This is the run that, pre-fix, halted at step 12a
    factcheck (G7 None provenance) and would then halt at 15b topic-log (G6
    broken import). Post-fix it must reach the end without halting.

    Dispatch is still faked (no claude calls); the VALUE is exercising the
    real gates: check_factcheck (needs ctx['material_summary_path']),
    check_topic_log_appended (needs the real orchestrator load), real
    check_stance_card + write_card."""
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    # Pre-stage the artifacts whose CONTENT the real gates / code steps read.
    # The fake dispatch only writes a stub when the file is absent, so these
    # valid pre-staged files survive (it does not overwrite them).
    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **测试**: 一条主观观察 (source: vault, 2026-06-14)\n",
        encoding="utf-8",
    )
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "score-verdict.json").write_text(
        json.dumps({"candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": 15}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "测试标题", "body": "正文" * 4000}, ensure_ascii=False),
        encoding="utf-8",
    )
    # All-subjective-skip → check_factcheck flags nothing → ok.
    (scratch / "factcheck-verdict.json").write_text(
        json.dumps({"claims": [
            {"claim": "我赌某事", "cited_fact_id": None, "verdict": "subjective-skip"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )

    fake = _FakeDispatch()  # writes stubs only for not-yet-present artifacts
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,                 # ← REAL production gate map
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    assert result.get("status") != "halted", (
        f"production gate run must not halt post-fix (G6+G7), got "
        f"{result!r} — failed_step={result.get('failed_step')!r}"
    )
    # The reader .md must have been published from the finalize body.
    published = list((output_dir / "episodes").glob("2026-06-14-*.md"))
    assert published, f"no .md published to {output_dir}; result={result!r}"


def test_resume_skips_steps_with_existing_artifacts(tmp_path):
    """resume=True: a step whose artifact already exists + passes its gate is
    NOT re-dispatched. This is the speedup — without it every e2e iteration
    re-ran collect (~15 min). Stage collect's artifact and assert collect is
    skipped while a step without a staged artifact (drafts) still dispatches."""
    from lib.runner import run_pipeline

    scratch = _bootstrap_scratch(tmp_path)
    # Pre-stage collect's artifact (valid, non-empty) → resume skips collect.
    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **x**: y (source: vault, 2026-06-14)\n", encoding="utf-8"
    )
    fake = _FakeDispatch()
    run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=_make_gate_map(),
        config=_make_config_stub(),
        scratch_dir=scratch,
        resume=True,
    )
    collect_calls = sum(1 for c in fake.calls if c["step"] == "collect")
    draft_calls = sum(1 for c in fake.calls if str(c["step"]).startswith("drafts"))
    assert collect_calls == 0, (
        f"resume must SKIP collect (artifact pre-staged), got {collect_calls} "
        f"dispatch(es); calls={[c['step'] for c in fake.calls]!r}"
    )
    assert draft_calls >= 1, (
        "drafts (no pre-staged artifact) must still dispatch under resume"
    )


# ---------------------------------------------------------------------------
# Phase 2 — Task 7-tests: post-publish fail-soft, store-update, apparatus_used
#
# Pins (per task-7-tests):
# - `_execute_step` translates halt (dispatch/gate) into a `skipped` result
#   for steps with `fail_soft=True` (no propagation, run stays `status:ok`).
# - `fail_soft` only exempts MARKED stations — a missing pre-publish
#   artifact still halts the run.
# - `_run_code_step` has a `coveredground-update` branch: it reads the
#   distiller's `coveredground-apparatus.json` from scratch, computes
#   `update_store(...)`, and writes `covered-ground.yaml` atomically to
#   `output_dir`. fail-soft on any exception.
# - `_stance_write_step` injects `apparatus_used` into the card. The
#   deterministic extraction is: store-known anchors ∩ finalize body ∪
#   card `named_concept`. fail-soft on any exception (empty list fallback).
# ---------------------------------------------------------------------------


def test_distiller_failure_does_not_halt(tmp_path):
    """A coveredground-distiller dispatch returning {ok:False} must NOT
    halt the pipeline. The distiller runs POST-PUBLISH (steps 18/19
    follow cleanup at 17) — the episode is already on disk. The runner
    must translate the failure into a `skipped` result for the
    fail_soft-marked station, and the run returns `status:ok` with the
    published .md + stance_card_path in ctx."""
    from lib.runner import run_pipeline
    from lib.coveredground import store_path as cg_store_path

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)

    # Pre-stage artifacts needed for the pre-publish steps to pass. We
    # stage magnitude + material-summary + finalize + score + factcheck,
    # and the production gate map will not halt at any pre-publish gate.
    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **测试**: 一条主观观察 (source: vault, 2026-06-14)\n",
        encoding="utf-8",
    )
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "score-verdict.json").write_text(
        json.dumps({"candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": 15}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "测试标题", "body": "正文" * 4000}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "factcheck-verdict.json").write_text(
        json.dumps({"claims": [
            {"claim": "我赌某事", "cited_fact_id": None, "verdict": "subjective-skip"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # Inject a dispatch fake that FAILS only the coveredground-distiller
    # step (the post-publish agent). All other stations succeed.
    fake = _FakeDispatch()
    fake.fail_steps = {"coveredground-distill"}

    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,                # production gate map
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    # The distiller failure must NOT halt — the run returns a non-halt
    # status envelope. (We accept any non-'halted' status string the
    # runner produces for clean runs; 'ok' is the canonical name.)
    assert result.get("status") != "halted", (
        f"distiller failure must NOT halt (fail_soft), got {result!r}"
    )

    # The published .md must exist on disk — the episode was preserved.
    published = list((output_dir / "episodes").glob("2026-06-14-*.md"))
    assert published, (
        f"published .md must exist even when the distiller fails; "
        f"result={result!r}; output_dir contents={list(output_dir.iterdir())!r}"
    )

    # The stance card must exist on disk — the post-publish distiller
    # failure does not affect the card write.
    stance_cards = list((output_dir / "episodes").glob("2026-06-14-*.stance.yaml"))
    assert stance_cards, (
        f"stance card must exist even when the distiller fails; "
        f"output_dir contents={list(output_dir.iterdir())!r}"
    )


def test_failsoft_only_exempts_marked_station(tmp_path):
    """The fail_soft exemption is scoped to MARKED stations. A pre-publish
    station whose artifact is missing must STILL halt the pipeline.
    Specifically: a coveredground-distill station is fail_soft=True, but
    its MISSING artifact does not exempt OTHER pre-publish stations —
    conversely, a missing pre-publish artifact (no fail_soft) must halt
    even when a downstream fail_soft station is marked.

    This test pins the boundary: fail_soft does NOT leak to unmarked
    stations, and the run halts at the first pre-publish gate miss."""
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    # NO material-summary.md → the FIRST pre-publish agent station that
    # requires a written artifact will halt. The coveredground stations
    # are fail_soft but they run AFTER the publish step — the publish
    # step's prerequisite is the materialize output from the finalize
    # station (step 12), so the halt must come from there.
    # We use a write-disabled fake so no station writes its artifact.
    fake = _FakeDispatch()
    fake.write_artifact = False

    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    # The run MUST halt — fail_soft does not exempt pre-publish stations.
    assert result.get("status") == "halted", (
        f"missing pre-publish artifact must halt (fail_soft only exempts "
        f"marked stations), got {result!r}"
    )
    # The halt must NOT name a fail_soft station — that would mean
    # fail_soft leaked to unmarked stations (a regression).
    failed = str(result.get("failed_step", ""))
    assert failed not in ("coveredground-distill", "coveredground-update"), (
        f"halt must NOT name a fail_soft station — fail_soft leaked: "
        f"failed_step={failed!r}"
    )


def test_coveredground_update_writes_store(tmp_path):
    """The `coveredground-update` code station must:
      1. Read `coveredground-apparatus.json` from scratch.
      2. Extract `anchors` and run `update_store`.
      3. Write `covered-ground.yaml` to `output_dir` atomically.

    The injection: the dispatch fake (for the prior
    `coveredground-distill` agent station) writes a controlled apparatus
    json to scratch. The runner's `coveredground-update` branch must
    consume it and the resulting store must contain the anchors."""
    from lib.runner import run_pipeline
    from lib.coveredground import store_path as cg_store_path, load_store

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # The distiller's apparatus json (what the agent station writes).
    # These are the signature anchors/analogies/frameworks the persona
    # distills from the published body.
    apparatus = {"anchors": ["1956苏伊士运河危机", "印刷术类比"]}

    # Pre-stage all pre-publish artifacts so the production gate map
    # does not halt on any earlier station.
    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **测试**: 一条主观观察 (source: vault, 2026-06-14)\n",
        encoding="utf-8",
    )
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "score-verdict.json").write_text(
        json.dumps({"candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": 15}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "测试标题", "body": "正文" * 4000}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "factcheck-verdict.json").write_text(
        json.dumps({"claims": [
            {"claim": "我赌某事", "cited_fact_id": None, "verdict": "subjective-skip"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )

    # The dispatch fake: when the coveredground-distill station is
    # called, write the apparatus json to scratch (so the gate passes
    # and the subsequent code station has data to read).
    fake = _FakeDispatch()

    def _inspect_distill(agent_name, user_prompt, scratch_dir, expected_artifact, **kwargs):
        if agent_name == "coveredground-distiller":
            (Path(str(scratch_dir)) / "coveredground-apparatus.json").write_text(
                json.dumps(apparatus, ensure_ascii=False),
                encoding="utf-8",
            )

    fake.inspect_call = _inspect_distill

    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    # Sanity precondition: no store file exists yet.
    assert not cg_store_path(output_dir / "state").exists()

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    assert result.get("status") != "halted", (
        f"clean run with distiller-written apparatus must not halt, "
        f"got {result!r}"
    )

    # The store must have been written to state/ by the
    # coveredground-update station.
    store_file = cg_store_path(output_dir / "state")
    assert store_file.exists(), (
        f"coveredground-update must write covered-ground.yaml to "
        f"{output_dir}/state, but the file does not exist. "
        f"output_dir contents={list(output_dir.iterdir())!r}"
    )

    # Phase 4 boundary (Task 2-tests step 3): topic_log.yaml stays at output_dir
    # ROOT — the vendored podcast-studio-prep shares it, so it must NOT move into
    # a subdir. Assert it stayed put AND did not leak into state/.
    assert (output_dir / "topic_log.yaml").exists(), (
        "topic_log.yaml must stay at output_dir root (vendored-prep boundary)"
    )
    assert not (output_dir / "state" / "topic_log.yaml").exists(), (
        "topic_log.yaml must NOT move into state/ (it is not continuity-state the runner owns)"
    )

    # The store must contain the apparatus anchors.
    store = load_store(output_dir / "state")
    stored_anchors = set(store.get("anchors", {}).keys())
    for anchor in apparatus["anchors"]:
        assert anchor in stored_anchors, (
            f"coveredground-update must store apparatus anchor {anchor!r}; "
            f"got store={store!r}"
        )


def test_stance_write_includes_apparatus_used(tmp_path):
    """Step 16 (_stance_write_step) must inject `apparatus_used` into the
    stance card. The deterministic extraction is: store-known anchors
    intersected with the finalize body ∪ the card's `named_concept`
    field. Fail-soft: any exception falls back to an empty list, and
    the card still writes successfully."""
    from lib.runner import run_pipeline
    from lib.coveredground import write_store
    from lib.stance import load_cards

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # Pre-stage a covered-ground store with one anchor — this is the
    # signature apparatus the distiller picked up in a previous run.
    hot_anchor = "印刷术类比"
    today_str = "2026-06-14"
    store = {
        "anchors": {
            hot_anchor: {
                "first_used": "2026-06-05",
                "last_used":  "2026-06-13",
                "count":      3,
                "episodes": [
                    {"date": "2026-06-05", "show": "morning"},
                    {"date": "2026-06-09", "show": "morning"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            }
        }
    }
    (output_dir / "state").mkdir(parents=True, exist_ok=True)
    write_store(output_dir / "state", store)

    # The finalize body MUST contain the hot_anchor verbatim so the
    # deterministic extraction finds it. (The store-known anchors ∩
    # body computation looks for substring presence in the body text.)
    # The body must be long enough to pass the finalize floor (6500
    # non-whitespace chars for morning).
    finalize_body = (
        f"正文开头段。本期延续 {hot_anchor} 探讨信息如何流通。"
        + ("主体内容段落。" * 1200)
    )
    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **测试**: 一条主观观察 (source: vault, 2026-06-14)\n",
        encoding="utf-8",
    )
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "score-verdict.json").write_text(
        json.dumps({"candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": 15}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "测试标题", "body": finalize_body}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "factcheck-verdict.json").write_text(
        json.dumps({"claims": [
            {"claim": "我赌某事", "cited_fact_id": None, "verdict": "subjective-skip"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )

    fake = _FakeDispatch()
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date=today_str,
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    assert result.get("status") != "halted", (
        f"clean run with apparatus-bearing body must not halt, got {result!r}"
    )

    # The stance card must carry `apparatus_used` containing the
    # hot anchor that was both in the store AND in the finalize body.
    cards = load_cards(output_dir / "episodes")
    matching = [c for c in cards if c.get("episode", {}).get("date") == today_str]
    assert matching, (
        f"stance card for {today_str} must exist; cards={cards!r}"
    )
    card = matching[-1]
    assert "apparatus_used" in card, (
        f"stance card must carry `apparatus_used` field (DP-001=A — the "
        f"deterministic best-effort audit field); got card={card!r}"
    )
    apparatus_used = card["apparatus_used"]
    assert isinstance(apparatus_used, list), (
        f"apparatus_used must be a list, got {type(apparatus_used).__name__}: "
        f"{apparatus_used!r}"
    )
    assert hot_anchor in apparatus_used, (
        f"apparatus_used must include the hot anchor {hot_anchor!r} "
        f"(present in both the store and the finalize body); "
        f"got apparatus_used={apparatus_used!r}"
    )


# ---------------------------------------------------------------------------
# Phase 3 — Task 5-tests: runner 13a scorecard execution (advisory +
# enforce flag). Pins (per task 5-tests):
#   - scorecard station writes verdict (scratch) + {date}-{show}.scorecard.md
#     (output_dir)
#   - advisory: hard-gate red → status:ok (no halt), scorecard marks
#     not-passed, published artifact still lands
#   - enforce_scorecard=True + hard-gate red → halt (failed_step=scorecard)
#   - cross-period store read uses pre-update (step 19) state — the
#     is_stale anchor check fires from the published-time store
#   - judge dispatch failure → judge dims `unscored`, hard gates still
#     judge, advisory does not crash
#   - new `enforce_scorecard` kwarg threaded through run_pipeline
# Written before Task 5-impl lands the scorecard execution branch +
# enforce_scorecard flag. All five new tests must FAIL until then.
# ---------------------------------------------------------------------------


def _stage_scorecard_pre_publish_artifacts(
    scratch: Path,
    *,
    finalize_body: str = "",
    script_text: str = "",
    factcheck_ok: bool = True,
    score_total: int = 15,
) -> None:
    """Stage the scratch artifacts the 13a scorecard station needs to read.

    Defaults: clean inputs (all hard gates pass). Individual tests override
    to inject specific red conditions (short script, bad score total, etc.).
    The finalize body must be long enough to clear the morning floor (6500
    non-whitespace chars) so production gates don't halt at finalize.
    """
    # The materialize body is what gets published as the reader .md — it
    # doesn't need to be the *final* body, but it must clear the floor.
    if not finalize_body:
        finalize_body = (
            "## ① 开头段。" * 500
            + "## ② 主体段一。" * 500
            + "## ③ 主体段二(判断织入)。" * 500
            + "## ④ 结尾段。" * 500
        )

    (scratch / "material-summary.md").write_text(
        "## 当日新闻背景\n- **测试**: 一条主观观察 (source: vault, 2026-06-14)\n",
        encoding="utf-8",
    )
    (scratch / "magnitude-verdict.json").write_text(
        json.dumps({"verdicts": [
            {"candidate": "A", "matches_prior": None, "magnitude": "light",
             "what_moved": "", "recap_hook": None},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "score-verdict.json").write_text(
        json.dumps({"candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": score_total}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (scratch / "finalize-result.json").write_text(
        json.dumps({"title": "测试标题", "body": finalize_body}, ensure_ascii=False),
        encoding="utf-8",
    )
    # factcheck: all subjective-skip → ok=True (the production gate checks
    # there are no failed factual claims)
    if factcheck_ok:
        (scratch / "factcheck-verdict.json").write_text(
            json.dumps({"claims": [
                {"claim": "我赌某事", "cited_fact_id": None,
                 "verdict": "subjective-skip"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        (scratch / "factcheck-verdict.json").write_text(
            json.dumps({"claims": [
                {"claim": "某条事实", "cited_fact_id": "src1",
                 "verdict": "fail"},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )

    # The broadcast-script-{date}.txt (念稿). The structlint duration
    # gate counts non-whitespace chars; default to 7000 (clears the 6570
    # floor). Individual tests override with a short string to force red.
    if not script_text:
        script_text = "正" * 7000  # 7000 non-whitespace chars (~19 min)
    (scratch / "broadcast-script-2026-06-14.txt").write_text(
        script_text, encoding="utf-8",
    )


def test_scorecard_station_writes_verdict_and_md(tmp_path):
    """Phase 3 / Task 5 — normal-run baseline:
    - scorecard station runs and writes `scorecard-verdict.json` to scratch
    - the human-readable scorecard is copied to output_dir as
      `{date}-{show}.scorecard.md`
    - the run returns status:ok (advisory mode, all hard gates pass on
      clean input)

    Pins the contract that every e2e run produces a scorecard artifact,
    not just a verdict — the human-readable markdown is the part reviewers
    actually read. """
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")
    _stage_scorecard_pre_publish_artifacts(scratch)

    fake = _FakeDispatch()
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    assert result.get("status") != "halted", (
        f"clean run with scorecard must not halt, got {result!r}"
    )

    # The scorecard verdict is the gate artifact for step 13a.
    verdict_path = scratch / "scorecard-verdict.json"
    assert verdict_path.exists() and verdict_path.stat().st_size > 0, (
        f"scorecard station must write scorecard-verdict.json to scratch; "
        f"scratch contents: {list(scratch.iterdir())!r}"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert "passed" in verdict, (
        f"scorecard-verdict must carry `passed`; got keys={list(verdict.keys())!r}"
    )

    # Human-readable scorecard lands in reports/ (cleanup preserves
    # output_dir — cleanup only nukes scratch).
    md_path = output_dir / "reports" / "2026-06-14-morning.scorecard.md"
    assert md_path.exists() and md_path.stat().st_size > 0, (
        f"scorecard.md must be written to output_dir as "
        f"{{date}}-{{show}}.scorecard.md; "
        f"output_dir contents: {list(output_dir.iterdir())!r}"
    )

    # The scorecard step must have been dispatched (the scorecard persona
    # is the agent for step 13a).
    scorecard_calls = [
        c for c in fake.calls if c.get("step") == "scorecard"
    ]
    assert scorecard_calls, (
        f"scorecard station must be dispatched; calls: "
        f"{[(c['step'], c['agent']) for c in fake.calls]!r}"
    )
    assert scorecard_calls[0]["agent"] == "scorecard", (
        f"scorecard station must dispatch to 'scorecard' persona, got "
        f"{scorecard_calls[0]['agent']!r}"
    )


def test_scorecard_advisory_does_not_halt_on_red(tmp_path):
    """Phase 3 / Task 5 — advisory mode:
    Hard gates RED (structlint duration fails because the script is
    <6570 chars; intra-dup fails because the body has repeated text)
    but `enforce_scorecard` is False (default).

    Expected:
      - run returns status:ok (advisory: do NOT halt on hard-gate red)
      - the published .md still lands in output_dir
      - the scorecard verdict marks `passed=False`
      - the scorecard.md in output_dir reflects the failure

    Pins the contract that advisory mode is a record-only behavior — the
    runner records the failure and continues so iteration can see the
    scorecard. `enforce_scorecard` is the production-period switch."""
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # Short 念稿 → structlint duration gate red (<6570 non-whitespace chars)
    short_script = "正" * 100
    # Body with verbatim repeat → dedup intra gate red
    repeat_body = (
        # *200 (not *50): the body must clear the finalize floor (6500 非空白字)
        # so the run REACHES the 13a scorecard station under test — a shorter
        # body halts at finalize (step 12) first and never exercises 13a.
        # NOTE: this inline body has NO newlines, so structlint sees it as a
        # single section → the hard-gate-RED that drives passed=False here is
        # the SECTIONS gate (≠4) + the DURATION gate (short script below),
        # which is all the station test needs. (intra-dup correctness is
        # covered by test_dedup.py + test_scorecard_integration.py, not here.)
        "## ① 开头段。重复的内容。" * 200
        + "## ② 主体段一。重复的内容。" * 200
        + "## ③ 主体段二。重复的内容。" * 200
        + "## ④ 结尾段。" * 200
    )
    _stage_scorecard_pre_publish_artifacts(
        scratch,
        finalize_body=repeat_body,
        script_text=short_script,
    )

    fake = _FakeDispatch()
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
        # enforce_scorecard defaults to False / absent
    )

    # Advisory: status MUST NOT be 'halted' (the hard-gate red is recorded
    # but doesn't abort the run).
    assert result.get("status") != "halted", (
        f"advisory mode must NOT halt on hard-gate red; got {result!r}"
    )

    # Published .md still lands in output_dir (advisory preserves the run).
    published = list((output_dir / "episodes").glob("2026-06-14-*.md"))
    assert published, (
        f"advisory mode must preserve the published .md even on hard-gate "
        f"red; output_dir={list(output_dir.iterdir())!r}"
    )

    # The scorecard verdict MUST record the failure (passed=False).
    verdict_path = scratch / "scorecard-verdict.json"
    assert verdict_path.exists(), (
        f"scorecard-verdict.json must be written even when hard gates are "
        f"red (advisory record), got scratch={list(scratch.iterdir())!r}"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert verdict.get("passed") is False, (
        f"advisory scorecard must mark passed=False when hard gates are "
        f"red; got verdict={verdict!r}"
    )


def test_scorecard_enforce_halts_on_red(tmp_path):
    """Phase 3 / Task 5 — production mode (enforce_scorecard=True):
    Hard gates RED AND `enforce_scorecard=True`.

    Expected:
      - run returns status:halted, failed_step='scorecard'
      - the published .md does NOT land in output_dir (production gates
        are not bypassed; the enforce flag turns scorecard from record-only
        to abort-on-red)

    Pins the contract that --enforce-scorecard switches scorecard from
    advisory to halt-on-red. Without this flag the run continues."""
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # Same red condition as the advisory test.
    short_script = "正" * 100
    repeat_body = (
        # *200 (not *50): the body must clear the finalize floor (6500 非空白字)
        # so the run REACHES the 13a scorecard station under test — a shorter
        # body halts at finalize (step 12) first and never exercises 13a.
        # NOTE: this inline body has NO newlines, so structlint sees it as a
        # single section → the hard-gate-RED that drives passed=False here is
        # the SECTIONS gate (≠4) + the DURATION gate (short script below),
        # which is all the station test needs. (intra-dup correctness is
        # covered by test_dedup.py + test_scorecard_integration.py, not here.)
        "## ① 开头段。重复的内容。" * 200
        + "## ② 主体段一。重复的内容。" * 200
        + "## ③ 主体段二。重复的内容。" * 200
        + "## ④ 结尾段。" * 200
    )
    _stage_scorecard_pre_publish_artifacts(
        scratch,
        finalize_body=repeat_body,
        script_text=short_script,
    )

    fake = _FakeDispatch()
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
        enforce_scorecard=True,
    )

    # Production mode: halt on hard-gate red.
    assert result.get("status") == "halted", (
        f"enforce_scorecard=True must halt on hard-gate red, got {result!r}"
    )
    failed = result.get("failed_step", "")
    assert failed == "scorecard", (
        f"halt must name 'scorecard' as failed_step, got {failed!r}"
    )


def test_scorecard_reads_preupdate_store(tmp_path):
    """Phase 3 / Task 5 — cross-period read timing:
    The scorecard station at 13a runs BEFORE coveredground-update (19).
    It must read the PRE-update store (the same store davinci saw via
    avoid_memo when writing), so the cross-period is_stale check fires
    against the avoid-list davinci was supposed to honor.

    Setup:
      - output_dir/covered-ground.yaml contains a hot anchor (is_stale=True)
      - the broadcast script mentions that hot anchor

    Expected:
      - scorecard verdict's hard_gates includes a cross_dup hit naming
        the hot anchor
      - passed=False
      - the verdict records the cross-period repeat (the davinci used an
        avoid_memo-marked anchor → scorecard flags it)"""
    from lib.runner import run_pipeline
    from lib.coveredground import write_store

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    # Pre-stage a covered-ground store with a HOT anchor (3 in-window
    # episodes → is_stale fires). Use an anchor that the broadcast script
    # will contain verbatim so the cross-period check has something to
    # flag.
    today_str = "2026-06-14"
    hot_anchor = "1956苏伊士运河危机"
    store = {
        "anchors": {
            hot_anchor: {
                "first_used": "2026-06-05",
                "last_used":  "2026-06-13",
                "count":      3,
                "episodes": [
                    {"date": "2026-06-05", "show": "morning"},
                    {"date": "2026-06-09", "show": "morning"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            }
        }
    }
    (output_dir / "state").mkdir(parents=True, exist_ok=True)
    write_store(output_dir / "state", store)

    # Build a clean finalize body (no verbatim repeat), but bake the
    # hot_anchor into the broadcast script so cross-period fires.
    clean_body = (
        "## ① 开头段。" * 500
        + "## ② 主体段一。" * 500
        + "## ③ 主体段二(判断织入)。" * 500
        + "## ④ 结尾段。" * 500
    )
    script_text = "正" * 7000 + hot_anchor + "正" * 100
    _stage_scorecard_pre_publish_artifacts(
        scratch,
        finalize_body=clean_body,
        script_text=script_text,
    )

    fake = _FakeDispatch()
    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date=today_str,
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    # The verdict must record the cross-period repeat.
    verdict_path = scratch / "scorecard-verdict.json"
    assert verdict_path.exists(), (
        f"scorecard-verdict.json must be written; scratch="
        f"{list(scratch.iterdir())!r}"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    # Search all hard_gates entries for a cross-period hit naming the
    # anchor. The shape is {hard_gates:[{name, ok, detail, hits?}, ...]}
    # — accept either nested in hits or as a substring in detail.
    found = False
    raw = json.dumps(verdict, ensure_ascii=False)
    if hot_anchor in raw:
        found = True
    assert found, (
        f"scorecard verdict must flag the cross-period repeat of "
        f"hot anchor {hot_anchor!r}; verdict={verdict!r}"
    )

    # passed=False because at least one hard gate is red.
    assert verdict.get("passed") is False, (
        f"cross-period repeat must mark scorecard passed=False; "
        f"verdict={verdict!r}"
    )


def test_scorecard_judge_failure_advisory(tmp_path):
    """Phase 3 / Task 5 — judge-dispatch fail-soft:
    The scorecard persona dispatch returns {ok:False} (simulating a
    LLM timeout / parse failure / persona crash). The hard-gate
    evaluation still runs (deterministic, not LLM-dependent), but the
    judge dims are marked `unscored` in the verdict.

    Expected:
      - run returns status:ok (advisory does not crash on judge failure)
      - verdict's judge_dims are `unscored` (the fail-soft path)
      - verdict still records the hard gates (deterministic, ran fine)"""
    from lib.runner import run_pipeline

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    scratch = _bootstrap_scratch(tmp_path)
    (output_dir / "topic_log.yaml").write_text("episodes: []\n", encoding="utf-8")

    _stage_scorecard_pre_publish_artifacts(scratch)

    # Force the scorecard dispatch to return {ok:False} AND NOT write the
    # verdict artifact. The runner's safe_parse_scorecard + build_scorecard
    # must produce the verdict locally (the deterministic hard gates
    # don't need the persona to have run) and write it to scratch.
    fake = _FakeDispatch()
    fake.fail_steps = {"scorecard"}

    config = _make_config_stub()
    _set_out(config, output_dir)
    plugin_root = Path(__file__).resolve().parent.parent.parent

    result = run_pipeline(
        "morning",
        date="2026-06-14",
        no_tts=True,
        dispatch=fake,
        gates=None,
        config=config,
        scratch_dir=scratch,
        plugin_root=str(plugin_root),
    )

    # Advisory: judge dispatch failure must NOT crash the run.
    assert result.get("status") != "halted", (
        f"judge dispatch failure must not halt (advisory fail-soft), "
        f"got {result!r}"
    )

    # The verdict still lands — it's deterministic + produced locally,
    # even if the persona dispatch failed.
    verdict_path = scratch / "scorecard-verdict.json"
    assert verdict_path.exists(), (
        f"verdict must be written even when judge dispatch fails; "
        f"scratch={list(scratch.iterdir())!r}"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))

    # Judge dims must be unscored (the dispatch-failure path).
    raw = json.dumps(verdict, ensure_ascii=False)
    assert "unscored" in raw, (
        f"judge dims must be marked unscored when dispatch fails; "
        f"verdict={verdict!r}"
    )

    # Hard gates MUST still be evaluated (deterministic, persona-
    # independent). With clean inputs they should be ok=True; verdict's
    # passed field reflects hard-gate state.
    assert "hard_gates" in verdict, (
        f"verdict must carry hard_gates even when judge dispatch fails "
        f"(hard gates are deterministic); verdict={verdict!r}"
    )
