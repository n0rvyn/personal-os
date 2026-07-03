"""video-studio pipeline runner — deterministic sequencer for the Phase-2 step table.

run_pipeline(config, *, pipeline, step_fns, dispatch, gates, scratch_dir)

Sequencing contract (per Task 4 tests):
  - Steps execute in the order of the injected pipeline list.
  - code steps: step_fns[step["name"]](step, ctx, scratch_dir).
  - agent steps: dispatch(agent_name, step, ctx, scratch_dir).
  - Composite gate: any gate item returning ok=False → halt immediately;
    subsequent steps are NOT invoked.
  - Missing artifact (step["artifact"] key absent from ctx, or pointing at
    an empty/absent file) → halt.
  - Retry: a step with retry>0 re-invokes the step fn on gate miss; exceeding
    the cap halts.
  - Clean run: lib.episode.cleanup_scratch(scratch_dir) is called after the
    last step.
  - Halted run: cleanup_scratch is NOT called.
  - run_pipeline returns {"status": "done", ...} on success and
    {"status": "halted", "failed_step": <name>, "reason": <...>} on halt.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import lib.episode


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def _run_gates(
    step: dict[str, Any],
    ctx: dict[str, Any],
    gates: dict[str, Any],
) -> tuple[bool, str]:
    """Evaluate all gate items for a step.

    Returns (ok, reason).  ok=True iff every gate item passes.

    Gate fns have the signature: fn(path: str) -> {"ok": bool, "reason": str}.
    The path is resolved from ctx[step["artifact"]]; if the artifact key is
    absent from ctx the gate fails immediately without calling any gate fn.
    """
    gate_items: list[dict[str, Any]] = step.get("gate") or []
    artifact_key: str | None = step.get("artifact")

    for gate_item in gate_items:
        fn_name: str = gate_item.get("fn", "")
        if not fn_name:
            return False, f"gate item missing 'fn': {gate_item!r}"

        gate_fn = gates.get(fn_name)
        if gate_fn is None:
            return False, f"unknown gate fn: {fn_name!r}"

        # Resolve the artifact path from ctx.
        if artifact_key:
            if artifact_key not in ctx:
                return (
                    False,
                    f"artifact {artifact_key!r} not produced by step "
                    f"{step['name']!r} (key absent from ctx)",
                )
            path: str = ctx[artifact_key]
        else:
            path = ""

        try:
            result = gate_fn(path)
        except Exception as exc:  # noqa: BLE001 — gate exceptions must not crash runner
            return False, f"gate {fn_name!r} raised: {exc}"

        if not isinstance(result, dict):
            return (
                False,
                f"gate {fn_name!r} returned non-dict: {type(result).__name__!r}",
            )
        if not result.get("ok"):
            return False, result.get("reason", f"gate {fn_name!r} failed")

    return True, "all gates passed"


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

def _execute_step(
    step: dict[str, Any],
    ctx: dict[str, Any],
    scratch_dir: Path,
    step_fns: dict[str, Any],
    dispatch: Any,
) -> None:
    """Execute a single step according to its kind.

    code: calls step_fns[step["name"]](step, ctx, scratch_dir).
    agent: calls dispatch(step["agent"], step, ctx, scratch_dir).
    """
    kind: str = step.get("kind", "code")
    name: str = step["name"]

    if kind == "agent":
        agent: str = step.get("agent") or ""
        dispatch(agent, step, ctx, scratch_dir)
    else:
        fn = step_fns.get(name)
        if fn is None:
            raise KeyError(
                f"no step_fn registered for code step {name!r}; "
                f"available: {list(step_fns)}"
            )
        fn(step, ctx, scratch_dir)


# ---------------------------------------------------------------------------
# Main pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(
    config: Any,
    *,
    pipeline: list[dict[str, Any]],
    step_fns: dict[str, Any],
    dispatch: Any,
    gates: dict[str, Any],
    scratch_dir: Path,
) -> dict[str, Any]:
    """Execute a video-studio pipeline.

    Parameters
    ----------
    config : Any
        Config object (passed to run_pipeline but NOT injected into ctx by the
        runner — code steps access it via the injected step_fns closure if needed).
    pipeline : list[dict]
        Ordered step list (lib.pipeline.load_pipeline() or test-injected).
    step_fns : dict[str, callable]
        Maps step name → fn(step, ctx, scratch_dir) for kind="code" steps.
    dispatch : callable
        fn(agent_name, step, ctx, scratch_dir) for kind="agent" steps.
    gates : dict[str, callable]
        Maps gate fn name → fn(path: str) → {"ok": bool, "reason": str}.
    scratch_dir : Path
        Per-invocation scratch directory; receives intermediate artifacts.

    Returns
    -------
    {"status": "done", ...} on a clean run (cleanup called).
    {"status": "halted", "failed_step": str, "reason": str} on halt (no cleanup).
    """
    ctx: dict[str, Any] = {}
    scratch_dir = Path(scratch_dir)

    for step in pipeline:
        name: str = step["name"]
        max_attempts: int = 1 + step.get("retry", 0)

        for attempt in range(max_attempts):
            _execute_step(step, ctx, scratch_dir, step_fns, dispatch)

            if not step.get("gate"):
                break  # no gate → step always passes

            gate_ok, gate_reason = _run_gates(step, ctx, gates)
            if gate_ok:
                break  # gate passed → move to next step

            # Gate failed.
            if attempt < max_attempts - 1:
                continue  # retry available → re-run the step

            # No retries remain → halt (no cleanup on halted runs).
            return {
                "status": "halted",
                "failed_step": name,
                "reason": gate_reason,
            }

    # All steps completed without halting → cleanup then return done.
    lib.episode.cleanup_scratch(scratch_dir)
    return {"status": "done"}


# ---------------------------------------------------------------------------
# CLI entry point — `python -m lib.runner --config <yaml> [--fixture] [--seq]`
# ---------------------------------------------------------------------------
#
# Thin shell: load config → build the real 13-step wiring (lib.wiring) →
# run_pipeline → print the episode product paths (or the halt step + reason).
# `prepare_fn` / `run_pipeline_fn` are injectable so the smoke test exercises
# the entry without real media/ffmpeg. Phase 7's skill wraps this command.


def _cli(argv: list[str] | None = None, *, prepare_fn=None, run_pipeline_fn=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m lib.runner",
        description="video-studio pipeline runner — fixture beats → episodes/{seq}-{slug}/{seq}-{slug}.mp4",
    )
    parser.add_argument("--config", required=False, default=None, help="Path to the video-studio config YAML (optional; omit to use config discovery)")
    parser.add_argument("--fixture", default="beats.long.fixture.json",
                        help="Fixture beats JSON (default: beats.long.fixture.json)")
    parser.add_argument("--seq", default="001", help="Episode sequence number (default: 001)")
    parser.add_argument("--slug", default="ep", help="Episode slug (default: ep)")
    parser.add_argument("--dry", action="store_true",
                        help="Dry render: copy cached .spike media seams ($0), real ffmpeg")
    args = parser.parse_args(argv)

    from lib.config import load_config, ConfigError, ConfigNotFound
    from lib.pipeline import load_pipeline, validate_pipeline

    try:
        config = load_config(args.config)
    except ConfigNotFound as e:
        print(
            f"error: {e}\n"
            "未找到配置。请选择：\n"
            "  • 在内容项目目录中运行（项目根含 CLAUDE.md，.video-studio/config.yaml）；\n"
            "  • 或用 /video-make 建内容项目；\n"
            "  • 或迁移旧 ~/.video-studio（迁移工具：lib.content_project.migrate_legacy_config）。",
            file=sys.stderr,
        )
        return 1
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Phase 7: preflight — check ffmpeg has libass BEFORE entering render (AD-3).
    # FFMPEG_BIN env takes priority over config.ffmpeg.ffmpeg_bin (AD-4, mirrors wiring.py:66).
    import os as _os
    from lib.preflight import check_ffmpeg_libass, PreflightError
    _ffmpeg_bin = _os.environ.get("FFMPEG_BIN", config.ffmpeg.ffmpeg_bin)
    try:
        check_ffmpeg_libass(_ffmpeg_bin)
    except PreflightError as e:
        print(str(e), file=sys.stderr)
        return 1

    # Phase 6: Stage-1 entry — select next pending episode from syllabus (if configured).
    # Must run BEFORE prepare_render so seq/slug are closure-bound with the right values.
    from lib.syllabus import select_next
    selection = select_next(config)
    syllabus_configured = bool(getattr(config, "syllabus_path", ""))

    if selection is None and syllabus_configured:
        print("无 pending 期/大纲跑完", file=sys.stderr)
        return 0

    # Determine seq/slug: from selection (syllabus mode) or CLI args (Phase-2 mode).
    ep_seq = selection.seq if selection is not None else args.seq
    ep_slug = selection.slug if selection is not None else args.slug

    fixture_path = Path(args.fixture)
    if not fixture_path.is_absolute():
        fixture_path = Path.cwd() / fixture_path
    if not fixture_path.exists():
        print(f"error: fixture not found: {fixture_path}", file=sys.stderr)
        return 1

    pipeline = load_pipeline()
    validate_pipeline(pipeline)

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    if prepare_fn is None:
        from lib.wiring import prepare_render as prepare_fn  # lazy: keeps media off import path

    if selection is not None:
        bundle = prepare_fn(config, fixture_path=fixture_path, seq=ep_seq,
                            slug=ep_slug, dry=args.dry, log=_log, selection=selection)
    else:
        bundle = prepare_fn(config, fixture_path=fixture_path, seq=ep_seq,
                            slug=ep_slug, dry=args.dry, log=_log)

    run = run_pipeline_fn or run_pipeline
    result = run(config, pipeline=pipeline, **bundle)

    if result.get("status") != "done":
        print(f"HALTED at step {result.get('failed_step')!r}: {result.get('reason')}",
              file=sys.stderr)
        return 2

    ep = lib.episode.episode_paths(config.output_dir, ep_seq, ep_slug)
    print(f"done — episode {ep_seq}-{ep_slug}")
    for key in ("final", "subtitles", "shotlist", "provenance"):
        p = ep[key]
        mark = "✓" if Path(p).exists() else "—"
        print(f"  {mark} {key}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
