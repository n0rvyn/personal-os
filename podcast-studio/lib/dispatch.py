"""Persona dispatch primitive — drive a headless `claude -p` for one station.

This module is the SOLE place in the codebase that shells out to
`claude -p`. It encapsulates:

  1. Whitelist guard for `agent_name` (no arbitrary file reads)
  2. Path-traversal guard for `expected_artifact` (no writes outside scratch)
  3. Construction of the `claude -p` command as a LIST (no `shell=True`,
     neutralizing shell injection through the user prompt — threat model)
  4. Injection of the persona system prompt from `agents/<name>.md`
  5. A direct "write your output to <abs path>" directive in the user
     prompt so the persona knows where to land its artifact
  6. Translation of subprocess outcomes into the gate contract
     `{"ok": bool, "reason": str, ...}`

This module is DATA-FREE — it doesn't import or know about the step table
in `lib/pipeline.py`. The runner composes the user prompt; this module just
runs the dispatch.

Threat model (per phase1-code-runner-plan §Threat Model):
  - LIST argv + no `shell=True` is non-negotiable.
  - `agent_name` is whitelisted before any fs read.
  - `expected_artifact` is resolved and verified to be inside `scratch_dir`
    before any fs write is requested.
  - The dispatch is fail-closed: non-zero exit, subprocess timeout, or
    missing artifact all return `{ok: False, reason: <str>}` — never raise
    to the caller.
"""
from __future__ import annotations

import os
import subprocess  # noqa: F401  (referenced via type/exception below)
from pathlib import Path
from typing import Any, Callable, Optional, Union


# ---------------------------------------------------------------------------
# Agent whitelist — mirrors AGENT_WHITELIST in lib/pipeline.py and the test
# pins. Kept duplicated (not imported) so dispatch.py stays a leaf module
# with no dependency on the pipeline module's validation surface.
# ---------------------------------------------------------------------------
AGENT_WHITELIST = frozenset({
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
})


# Default allowlist of tools the headless persona may call. Mirrors the
# inventory in agents/*.md prompts. The persona subagent is intentionally
# given read+write+bash+web+search so it can fetch context (vault / news /
# web) and write its single artifact to the requested path.
DEFAULT_ALLOWED_TOOLS = (
    "Read,Write,Bash,WebSearch,WebFetch,Grep,Glob,"
    "Skill,TodoWrite,NotebookEdit,NotebookRead"
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DispatchError(ValueError):
    """Raised at the entry boundary for invalid inputs (whitelist / path
    traversal / empty agent_name). Callers MAY treat this as a hard fail
    (the runner halts the pipeline before invoking the subprocess)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_artifact(
    scratch_dir: Union[str, os.PathLike],
    expected_artifact: str,
) -> Path:
    """Resolve `expected_artifact` against `scratch_dir` and verify the
    result stays inside `scratch_dir`. Raises DispatchError on traversal.

    The check is symmetric with `episode.episode_paths`'s traversal guard
    (`lib/episode.py:127-132`): realpath-based, allows nested paths inside
    scratch, rejects `..`-style escape.

    Returns the absolute resolved Path (not necessarily existing — this
    helper only enforces WHERE the path lands, not THAT it exists yet).
    """
    scratch_resolved = Path(scratch_dir).resolve()
    if not expected_artifact:
        raise DispatchError("expected_artifact is empty")
    candidate = (scratch_resolved / expected_artifact).resolve()
    # On some platforms (macOS) /tmp is a symlink to /private/tmp; Path.resolve
    # already collapses that, so scratch_resolved and candidate share a prefix.
    try:
        candidate.relative_to(scratch_resolved)
    except ValueError:
        raise DispatchError(
            f"expected_artifact escapes scratch_dir: "
            f"{expected_artifact!r} -> {candidate} (scratch={scratch_resolved})"
        )
    return candidate


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch_persona(
    agent_name: str,
    user_prompt: str,
    scratch_dir: Union[str, os.PathLike],
    expected_artifact: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    claude_bin: str = "claude",
    plugin_root: Optional[Union[str, os.PathLike]] = None,
    timeout: int = 600,
    model: Optional[str] = None,
    allowed_tools: Optional[str] = None,
) -> dict[str, Any]:
    """Run a single persona step via `claude -p` and return the gate result.

    Parameters
    ----------
    agent_name
        Persona name; must be in AGENT_WHITELIST.
    user_prompt
        The body of work to hand the persona. Combined with a final-line
        "write your output to <abs>" directive before being passed to
        `claude -p` as the user prompt.
    scratch_dir
        The scratch directory for this run. `expected_artifact` is
        resolved against this; the path-traversal guard verifies the
        resolved path stays inside this directory.
    expected_artifact
        Filename (possibly with subdirs) the persona should write to,
        relative to `scratch_dir`. Absolute paths and `..`-segments are
        rejected.
    runner
        Subprocess-compatible callable. Defaults to `subprocess.run`.
        Tests inject a fake.
    claude_bin
        The `claude` binary name. Defaults to `"claude"`. Tests can
        override to assert argv structure.
    plugin_root
        Path to the podcast-studio plugin root (the directory containing
        `agents/` and `lib/`). Used to (a) read `agents/<name>.md` for the
        system prompt, and (b) set `cwd=` on the subprocess so the
        persona's relative tool paths resolve correctly. Required.
    timeout
        Per-call subprocess timeout in seconds. Default 600s.
    model
        Optional explicit model for the `claude -p` invocation. If None,
        the claude CLI default model is used. The Phase 1 plan notes the
        default model may move (Sonnet → MiniMax M3) — wiring is left as
        a single kwarg so callers (the runner) can route by step kind.
    allowed_tools
        Comma-separated tool list for `--allowedTools`. Defaults to
        `DEFAULT_ALLOWED_TOOLS`.

    Returns
    -------
    dict with keys:
        ok              : bool
        reason          : str (always present; explains the outcome)
        artifact_path   : str (absolute resolved path; present on success
                          AND on the "subprocess exited ok but no
                          artifact" branch so the caller can see WHERE
                          was expected)
        returncode      : int (subprocess returncode; present if the
                          subprocess was actually invoked)
        stderr_excerpt  : str (truncated stderr on non-zero exit)

    Failure modes (all `ok: False`):
        - Invalid `agent_name` (not in whitelist)         → DispatchError
        - Path traversal in `expected_artifact`            → DispatchError
        - Missing `plugin_root` / unreadable agent.md     → DispatchError
        - `claude -p` not on PATH / OSError                → `{ok:False,
                                                               reason: ...}`
        - Subprocess non-zero exit                        → `{ok:False,
                                                               reason: ...}`
        - Subprocess timeout                              → `{ok:False,
                                                               reason: ...}`
        - Subprocess OK but artifact missing / empty      → `{ok:False,
                                                               reason: ...}`
    """
    # ------------------------------------------------------------------ Guard 1: whitelist
    if not agent_name:
        raise DispatchError("agent_name is empty")
    if agent_name not in AGENT_WHITELIST:
        raise DispatchError(
            f"agent_name not in whitelist: {agent_name!r} "
            f"(allowed: {sorted(AGENT_WHITELIST)})"
        )

    # ------------------------------------------------------------------ Guard 2: path traversal
    artifact_path = _resolve_artifact(scratch_dir, expected_artifact)
    scratch_dir_resolved = Path(scratch_dir).resolve()

    # ------------------------------------------------------------------ Guard 3: plugin_root + agent.md
    if plugin_root is None:
        raise DispatchError("plugin_root is required (needed to read agents/<name>.md)")
    plugin_root_resolved = Path(plugin_root).resolve()
    agent_md_path = plugin_root_resolved / "agents" / f"{agent_name}.md"
    if not agent_md_path.is_file():
        raise DispatchError(
            f"agent.md not found: {agent_md_path} "
            f"(agent_name={agent_name!r}, plugin_root={plugin_root_resolved})"
        )
    agent_md_text = agent_md_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ Build the user prompt
    # The persona is told the EXACT absolute path of its single output
    # artifact. "Only write that file" is intentional — keeps each step
    # self-contained and makes the gate (`check_artifact`) tractable.
    final_prompt = (
        f"{user_prompt.rstrip()}\n\n"
        f"---\n"
        f"把你的产物写到这一个文件(绝对路径):\n"
        f"  {artifact_path}\n"
        f"只写这一个文件。完成后用 Read 工具再读一遍自检,确认非空。"
    )

    # ------------------------------------------------------------------ Build the argv LIST
    # Threat-model critical: list args + no shell=True.
    tools = allowed_tools if allowed_tools is not None else DEFAULT_ALLOWED_TOOLS
    argv: list[str] = [
        claude_bin,
        "-p",
        final_prompt,
        "--append-system-prompt",
        agent_md_text,
        "--allowedTools",
        tools,
    ]
    if model:
        argv.extend(["--model", model])

    # ------------------------------------------------------------------ Run the subprocess
    try:
        completed = runner(
            argv,
            cwd=str(plugin_root_resolved),
            capture_output=True,
            text=True,
            timeout=timeout,
            # shell=False is the default of subprocess.run; we pass nothing
            # here so callers (and the test pin) can verify shell was
            # never set to True.
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": (
                f"claude -p timed out after {timeout}s for persona "
                f"{agent_name!r} (artifact expected at {artifact_path})"
            ),
            "artifact_path": str(artifact_path),
        }
    except FileNotFoundError as exc:
        # The `claude` binary itself isn't on PATH.
        return {
            "ok": False,
            "reason": (
                f"claude binary not found (argv[0]={argv[0]!r}): {exc}. "
                f"Check that the claude CLI is on PATH for the dispatching user."
            ),
            "artifact_path": str(artifact_path),
        }
    except OSError as exc:
        return {
            "ok": False,
            "reason": f"subprocess OSError for persona {agent_name!r}: {exc}",
            "artifact_path": str(artifact_path),
        }

    # ------------------------------------------------------------------ Subprocess returned
    returncode = getattr(completed, "returncode", None)
    stderr = getattr(completed, "stderr", "") or ""
    if returncode != 0:
        stderr_excerpt = (stderr.strip() or "<no stderr>")[-500:]
        return {
            "ok": False,
            "reason": (
                f"claude -p exited {returncode} for persona {agent_name!r}; "
                f"stderr (tail): {stderr_excerpt}"
            ),
            "artifact_path": str(artifact_path),
            "returncode": returncode,
            "stderr_excerpt": stderr_excerpt,
        }

    # ------------------------------------------------------------------ Gate on the artifact
    # Lazy import: episode.py pulls PyYAML etc; we keep dispatch.py as a leaf
    # so the runner / pipeline modules can import it without dragging the
    # whole `lib` package into a path it doesn't otherwise need.
    from lib.episode import check_artifact  # local import for leaf-ness

    gate = check_artifact(artifact_path)
    if not gate.get("ok"):
        return {
            "ok": False,
            "reason": (
                f"claude -p for persona {agent_name!r} exited 0 but "
                f"artifact check failed: {gate.get('reason', '<no reason>')}. "
                f"Expected at {artifact_path}."
            ),
            "artifact_path": str(artifact_path),
            "returncode": returncode,
        }

    # ------------------------------------------------------------------ Success
    return {
        "ok": True,
        "reason": f"persona {agent_name!r} wrote {artifact_path} (rc={returncode})",
        "artifact_path": str(artifact_path),
        "returncode": returncode,
    }
