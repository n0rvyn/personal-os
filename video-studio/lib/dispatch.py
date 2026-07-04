"""Persona dispatch primitive — drive a headless `claude -p` for one video persona.

This module is the SOLE place in video-studio that shells out to `claude -p`.
It encapsulates:

  1. Whitelist guard for `agent_name` (no arbitrary file reads)
  2. Path-traversal guard for `expected_artifact` (no writes outside scratch)
  3. Construction of the `claude -p` command as a LIST (no `shell=True`,
     neutralizing shell injection through the user prompt — threat model)
  4. Injection of the persona system prompt from `agents/<name>.md`
  5. A direct "write your output to <abs path>" directive in the user
     prompt so the persona knows where to land its artifact
  6. Translation of subprocess outcomes into the gate contract
     `{"ok": bool, "reason": str, ...}`

This module does NOT parse stdout JSON — the `_extract_json_array` pattern
in `lib/storyboard.py` stays there. Phase 4 will wire a shotlist.json gate.

Threat model:
  - LIST argv + no `shell=True` is non-negotiable.
  - `agent_name` is whitelisted before any fs read.
  - `expected_artifact` is resolved and verified to be inside `scratch_dir`
    before any fs write is requested.
  - The dispatch is fail-closed: non-zero exit, subprocess timeout, or
    missing/empty artifact all return `{ok: False, reason: <str>}`.
"""
from __future__ import annotations

import os
import subprocess  # noqa: F401  (referenced via type/exception below)
from pathlib import Path
from typing import Any, Callable, Optional, Union


# ---------------------------------------------------------------------------
# Agent whitelist — only these agent names may be dispatched.
# ---------------------------------------------------------------------------
VIDEO_AGENT_WHITELIST: frozenset[str] = frozenset({
    "digest-writer",
    "director",
    "planner",
    "query-writer",
    "covered-ground-distiller",
})


# Default allowlist of tools the headless persona may call.
# Video personas need Read/Write/Bash for artifact production, Grep/Glob for
# source scanning, and PDF reading. No web access (content-faithful discipline).
DEFAULT_ALLOWED_TOOLS = "Read,Write,Bash,Grep,Glob"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DispatchError(ValueError):
    """Raised at the entry boundary for invalid inputs (whitelist / path
    traversal / empty agent_name). The runner halts before invoking the
    subprocess."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_artifact(
    scratch_dir: Union[str, os.PathLike],
    expected_artifact: str,
) -> Path:
    """Resolve `expected_artifact` against `scratch_dir` and verify the
    result stays inside `scratch_dir`. Raises DispatchError on traversal.

    Returns the absolute resolved Path (not necessarily existing — this
    helper only enforces WHERE the path lands, not THAT it exists yet).
    """
    scratch_resolved = Path(scratch_dir).resolve()
    if not expected_artifact:
        raise DispatchError("expected_artifact is empty")
    candidate = (scratch_resolved / expected_artifact).resolve()
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
    agent_dir: str = "agents",
    whitelist: "frozenset[str]" = VIDEO_AGENT_WHITELIST,
) -> dict[str, Any]:
    """Run a single persona step via `claude -p` and return the gate result.

    Parameters
    ----------
    agent_name
        Persona name; must be in VIDEO_AGENT_WHITELIST (or the supplied
        `whitelist`).
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
        The `claude` binary name. Defaults to `"claude"`.
    plugin_root
        Path to the video-studio plugin root (the directory containing
        `agents/` and `lib/`). Used to read `agents/<name>.md` for the
        system prompt and set `cwd=` on the subprocess. Required.
    timeout
        Per-call subprocess timeout in seconds. Default 600s.
    model
        Optional explicit model for the `claude -p` invocation. If None,
        the claude CLI default model is used.
    allowed_tools
        Comma-separated tool list for `--allowedTools`. Defaults to
        `DEFAULT_ALLOWED_TOOLS`.
    agent_dir
        Directory under `plugin_root` holding the persona `.md` system
        prompt. Defaults to `"agents"`.
    whitelist
        Frozenset of allowed agent names. Defaults to
        `VIDEO_AGENT_WHITELIST`.

    Returns
    -------
    dict with keys:
        ok              : bool
        reason          : str (always present)
        artifact_path   : str (absolute resolved path; present on success
                          and on "subprocess exited ok but no artifact")
        returncode      : int (subprocess returncode; present if the
                          subprocess was actually invoked)
        stderr_excerpt  : str (truncated stderr on non-zero exit)

    Failure modes (all `ok: False`):
        - Invalid `agent_name` (not in whitelist)           → DispatchError
        - Path traversal in `expected_artifact`             → DispatchError
        - Missing `plugin_root` / unreadable agent.md       → DispatchError
        - `claude -p` not on PATH / OSError                 → {ok:False, ...}
        - Subprocess non-zero exit                          → {ok:False, ...}
        - Subprocess timeout                                → {ok:False, ...}
        - Subprocess OK but artifact missing / empty        → {ok:False, ...}
    """
    # ------------------------------------------------------------------ Guard 1: whitelist
    if not agent_name:
        raise DispatchError("agent_name is empty")
    if agent_name not in whitelist:
        raise DispatchError(
            f"agent_name not in whitelist: {agent_name!r} "
            f"(allowed: {sorted(whitelist)})"
        )

    # ------------------------------------------------------------------ Guard 2: path traversal
    artifact_path = _resolve_artifact(scratch_dir, expected_artifact)

    # ------------------------------------------------------------------ Guard 3: plugin_root + agent.md
    if plugin_root is None:
        raise DispatchError("plugin_root is required (needed to read agents/<name>.md)")
    plugin_root_resolved = Path(plugin_root).resolve()
    agent_md_path = plugin_root_resolved / agent_dir / f"{agent_name}.md"
    if not agent_md_path.is_file():
        raise DispatchError(
            f"agent.md not found: {agent_md_path} "
            f"(agent_name={agent_name!r}, agent_dir={agent_dir!r}, "
            f"plugin_root={plugin_root_resolved})"
        )
    agent_md_text = agent_md_path.read_text(encoding="utf-8")
    system_prompt = f"# persona source: {agent_md_path}\n\n{agent_md_text}"

    # ------------------------------------------------------------------ Build the user prompt
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
        system_prompt,
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
            # shell=False is the default of subprocess.run; we never set it True.
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": (
                f"claude -p timed out after {timeout}s for persona "
                f"{agent_name!r} (artifact expected at {artifact_path})"
            ),
            "artifact_path": str(artifact_path),
            "returncode": None,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "reason": (
                f"claude binary not found (argv[0]={argv[0]!r}): {exc}. "
                f"Check that the claude CLI is on PATH."
            ),
            "artifact_path": str(artifact_path),
            "returncode": None,
        }
    except OSError as exc:
        return {
            "ok": False,
            "reason": f"subprocess OSError for persona {agent_name!r}: {exc}",
            "artifact_path": str(artifact_path),
            "returncode": None,
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
    # Inline check — video-studio has no episode.py (unlike podcast-studio).
    ap = Path(artifact_path)
    artifact_exists = ap.exists() and ap.stat().st_size > 0
    if not artifact_exists:
        return {
            "ok": False,
            "reason": (
                f"claude -p for persona {agent_name!r} exited 0 but "
                f"artifact is missing or empty. Expected at {artifact_path}."
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
