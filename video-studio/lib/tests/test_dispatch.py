"""Tests for lib/dispatch.py — persona dispatch primitive (claude -p).

Written before lib/dispatch.py exists; collection must fail at this point
(`No module named 'lib.dispatch'`).

Pins (per Phase 1 plan Task 3-tests):
- dispatch_persona(agent_name, user_prompt, scratch_dir, expected_artifact, *,
  runner=...) constructs the claude -p command as a LIST (not shell=True).
- The user prompt embedded into the command contains the absolute artifact
  path; the system-prompt injection contains the agent.md text.
- Success path (fake writes artifact + exit 0) → {ok: True, artifact_path}.
- Failure path (non-zero exit / artifact missing / simulated timeout) →
  {ok: False, reason}.
- agent_name not in VIDEO_AGENT_WHITELIST raises DispatchError (before
  invoking the runner).
- expected_artifact path-traversal attempt ("../escape.json") is rejected.
- VIDEO_AGENT_WHITELIST == frozenset({"digest-writer","director","planner","query-writer"}).
  (query-writer added Phase 3 — content-matched scene query per window.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.dispatch import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

EXPECTED_WHITELIST = frozenset({"digest-writer", "director", "planner", "query-writer"})


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 3-impl will resolve this."""
    from lib import dispatch  # noqa: F401
    assert hasattr(dispatch, "dispatch_persona")


# ---------------------------------------------------------------------------
# Whitelist constant check
# ---------------------------------------------------------------------------

def test_video_agent_whitelist_value():
    """VIDEO_AGENT_WHITELIST must be exactly the four expected agents."""
    from lib.dispatch import VIDEO_AGENT_WHITELIST

    assert VIDEO_AGENT_WHITELIST == EXPECTED_WHITELIST, (
        f"VIDEO_AGENT_WHITELIST must be {EXPECTED_WHITELIST!r}, "
        f"got {VIDEO_AGENT_WHITELIST!r}"
    )


# ---------------------------------------------------------------------------
# Fakes for the subprocess runner
# ---------------------------------------------------------------------------

class _FakeCompletedProcess:
    """Minimal CompletedProcess stand-in for the fake runner."""
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    """Captures the kwargs/argv the real dispatch passes to subprocess.run.

    Records:
      - call_count: number of times invoked
      - last_argv: the argv list passed positionally
      - last_kwargs: the kwargs dict (must include shell=False or omit it)
      - last_cwd / last_timeout: kwargs the impl may pass

    Optional behavior knobs:
      - returncode: exit code to report
      - write_artifact: bool — whether to create the expected_artifact file
        to simulate a successful persona run
      - timeout_simulate: bool — raise TimeoutExpired instead of returning
    """
    def __init__(
        self,
        returncode: int = 0,
        write_artifact: bool = True,
        timeout_simulate: bool = False,
    ):
        self.call_count = 0
        self.last_argv = None
        self.last_kwargs = None
        self.returncode = returncode
        self.write_artifact = write_artifact
        self.timeout_simulate = timeout_simulate
        self.last_cwd = None
        self.last_timeout = None

    def __call__(self, argv, **kwargs):
        import subprocess as _sp  # only used to reference TimeoutExpired
        self.call_count += 1
        self.last_argv = argv
        self.last_kwargs = kwargs
        self.last_cwd = kwargs.get("cwd")
        self.last_timeout = kwargs.get("timeout")

        if self.timeout_simulate:
            raise _sp.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

        if self.write_artifact:
            # Recover the expected artifact path stashed by the test.
            ep = getattr(self, "_expected_artifact", None)
            if ep is not None:
                p = Path(ep)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("artifact body", encoding="utf-8")

        return _FakeCompletedProcess(returncode=self.returncode)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agents_dir(tmp_path: Path, agent_name: str = "director") -> Path:
    """Create a minimal agents/ dir with one .md file the dispatch primitive
    can read as a system prompt."""
    agents = tmp_path / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{agent_name}.md").write_text(
        f"You are {agent_name}, the video storyboard director.", encoding="utf-8"
    )
    return agents


# ---------------------------------------------------------------------------
# Success path: fake writes artifact + exits 0 → {ok:True, artifact_path}
# ---------------------------------------------------------------------------

def test_dispatch_success_returns_ok_true_and_artifact_path(tmp_path):
    """Happy path: fake writes the artifact at expected_artifact and exits 0.
    dispatch_persona must return {ok:True, artifact_path:<abs>, ...}."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "storyboard.json"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    result = dispatch_persona(
        agent_name="director",
        user_prompt="Generate a storyboard for this topic.",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    assert isinstance(result, dict), f"result must be a dict, got {type(result)}"
    assert result.get("ok") is True, (
        f"success path must return ok=True, got result={result!r}"
    )
    assert "reason" in result, "result must carry a `reason` key (gate contract)"
    assert "artifact_path" in result, "result must include `artifact_path` on success"
    ap = Path(result["artifact_path"])
    assert ap.is_absolute(), f"artifact_path must be absolute, got {ap!r}"
    assert ap == (scratch / artifact_name).resolve(), (
        f"artifact_path={ap!r} must match expected={scratch / artifact_name!r}"
    )


# ---------------------------------------------------------------------------
# argv must be a list; shell=True must NOT be set
# ---------------------------------------------------------------------------

def test_argv_is_list_and_shell_not_true(tmp_path):
    """The command passed to subprocess.run must be a LIST (no shell=True).
    A list-arg invocation neutralizes shell-injection from the user prompt
    — the threat-model contract for this module."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="director",
        user_prompt="hello world",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv = fake.last_argv
    assert isinstance(argv, list), (
        f"argv passed to runner must be a list, got {type(argv).__name__}: {argv!r}"
    )
    assert len(argv) >= 1, f"argv must have at least the claude binary, got {argv!r}"
    shell_kw = fake.last_kwargs.get("shell", False) if fake.last_kwargs else False
    assert shell_kw is False, (
        f"shell= must be False (or absent), got shell={shell_kw!r} — "
        "list-arg invocations are the threat-model contract"
    )


def test_argv_first_element_is_claude_and_contains_p_flag(tmp_path):
    """The argv must start with the claude binary and include the -p flag."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv = fake.last_argv
    assert argv[0] == "claude", (
        f"argv[0] should be 'claude', got {argv[0]!r}"
    )
    assert "-p" in argv, (
        f"argv must contain '-p' for claude -p dispatch, got argv={argv!r}"
    )


def test_argv_contains_model_flag_when_model_given(tmp_path):
    """When model= is supplied, argv must include --model <value>."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
        model="sonnet",
    )

    argv = fake.last_argv
    argv_str = " ".join(str(a) for a in argv)
    assert "--model" in argv_str, (
        f"argv must contain '--model' when model= is supplied, got {argv!r}"
    )
    assert "sonnet" in argv_str, (
        f"argv must contain the model value 'sonnet', got {argv!r}"
    )


# ---------------------------------------------------------------------------
# Artifact path and system prompt injection
# ---------------------------------------------------------------------------

def test_user_prompt_contains_artifact_path(tmp_path):
    """The persona's instructions (argv entry) must include the absolute
    artifact path so the persona knows where to write its output."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "storyboard.json"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="director",
        user_prompt="Generate storyboard X",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv_str = " ".join(str(a) for a in fake.last_argv)
    expected_abs = str((scratch / artifact_name).resolve())
    assert expected_abs in argv_str or artifact_name in argv_str, (
        f"artifact path/name {expected_abs!r} not present in argv: {argv_str!r}"
    )


def test_system_prompt_contains_agent_md_text(tmp_path):
    """The agent's persona text (agents/<name>.md) must be injected as a
    system prompt so the persona loads with its full instructions."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path, "director")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv_str = " ".join(str(a) for a in fake.last_argv)
    agent_md_path = tmp_path / "agents" / "director.md"
    agent_md_text = agent_md_path.read_text(encoding="utf-8")
    contains_text = agent_md_text in argv_str
    contains_path = str(agent_md_path) in argv_str
    assert contains_text or contains_path, (
        "system-prompt injection must reference the agent.md — neither the "
        "literal text nor the .md path was found in argv: "
        f"{argv_str!r}"
    )


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_failure_on_nonzero_exit_returns_ok_false(tmp_path):
    """If the subprocess exits non-zero, dispatch_persona must return
    {ok:False, reason} — fail-closed."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=1, write_artifact=False)

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    assert isinstance(result, dict)
    assert result.get("ok") is False, (
        f"non-zero exit must return ok=False, got {result!r}"
    )
    assert "reason" in result and isinstance(result["reason"], str), (
        f"failure result must include a string `reason`, got {result!r}"
    )
    assert result["reason"], "reason must be non-empty"


def test_failure_on_missing_artifact_returns_ok_false(tmp_path):
    """If the subprocess exits 0 but did not write the artifact, dispatch
    must return {ok:False, reason} containing 'artifact'."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=False)  # exits 0 but no file

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    assert isinstance(result, dict)
    assert result.get("ok") is False, (
        f"missing artifact must fail-closed, got {result!r}"
    )
    assert "reason" in result and result["reason"], (
        f"missing-artifact result must include a non-empty `reason`, got {result!r}"
    )
    reason_lower = result["reason"].lower()
    assert "artifact" in reason_lower, (
        f"reason must mention 'artifact', got reason={result['reason']!r}"
    )


def test_failure_on_subprocess_timeout_returns_ok_false(tmp_path):
    """If the subprocess times out, dispatch must return {ok:False, reason}
    containing 'timed out' — TimeoutExpired must be translated to ok:False."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=False, timeout_simulate=True)

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
        timeout=10,
    )

    assert isinstance(result, dict)
    assert result.get("ok") is False, (
        f"timeout must return ok=False, got {result!r}"
    )
    assert "reason" in result and result["reason"], (
        f"timeout result must include a non-empty `reason`, got {result!r}"
    )
    reason_lower = result["reason"].lower()
    assert "timed out" in reason_lower or "timeout" in reason_lower, (
        f"timeout reason must mention 'timed out'/'timeout', got {result['reason']!r}"
    )


# ---------------------------------------------------------------------------
# Whitelist + path-traversal guards
# ---------------------------------------------------------------------------

def test_rejects_unknown_agent_name(tmp_path):
    """An agent name not in VIDEO_AGENT_WHITELIST must be rejected via
    DispatchError — the runner must NEVER be invoked (no arbitrary file read)."""
    from lib.dispatch import dispatch_persona, DispatchError

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_dispatch_error = False
    result = None
    try:
        result = dispatch_persona(
            agent_name="ghost",
            user_prompt="hello",
            scratch_dir=scratch,
            expected_artifact="out.txt",
            runner=fake,
            plugin_root=tmp_path,
        )
    except (DispatchError, ValueError) as e:
        raised_dispatch_error = True
        assert "ghost" in str(e), (
            f"DispatchError should name the bad agent, got: {e}"
        )

    if not raised_dispatch_error:
        assert isinstance(result, dict)
        assert result.get("ok") is False, (
            f"unknown agent must be rejected, got {result!r}"
        )
        assert "reason" in result and "ghost" in str(result["reason"]).lower(), (
            f"unknown-agent result must mention 'ghost', got {result!r}"
        )

    # The runner must not have been invoked for an unknown agent
    assert fake.call_count == 0, (
        f"runner must not be invoked for unknown agent, "
        f"call_count={fake.call_count}"
    )


def test_rejects_path_traversal_in_expected_artifact(tmp_path):
    """An expected_artifact that escapes scratch_dir (e.g. '../escape.json')
    must be rejected. The runner must NEVER be invoked."""
    from lib.dispatch import dispatch_persona, DispatchError

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_error = False
    result = None
    try:
        result = dispatch_persona(
            agent_name="director",
            user_prompt="hello",
            scratch_dir=scratch,
            expected_artifact="../escape.json",
            runner=fake,
            plugin_root=tmp_path,
        )
    except (DispatchError, ValueError) as e:
        raised_error = True
        msg = str(e).lower()
        assert any(tok in msg for tok in ("escape", "traversal", "outside", "invalid")), (
            f"Error should mention escape/traversal, got: {e}"
        )

    if not raised_error:
        assert isinstance(result, dict)
        assert result.get("ok") is False, (
            f"path traversal must be rejected, got {result!r}"
        )
        assert "reason" in result, (
            f"path-traversal result must include a `reason`, got {result!r}"
        )

    # The runner must not have been invoked for a path-traversal attempt
    assert fake.call_count == 0, (
        f"runner must not be invoked for path traversal, "
        f"call_count={fake.call_count}"
    )


def test_rejects_empty_agent_name(tmp_path):
    """An empty agent_name must be rejected."""
    from lib.dispatch import dispatch_persona, DispatchError

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_error = False
    result = None
    try:
        result = dispatch_persona(
            agent_name="",
            user_prompt="hello",
            scratch_dir=scratch,
            expected_artifact="out.txt",
            runner=fake,
            plugin_root=tmp_path,
        )
    except (DispatchError, ValueError):
        raised_error = True

    if not raised_error:
        assert isinstance(result, dict)
        assert result.get("ok") is False

    assert fake.call_count == 0, (
        f"runner must not be invoked for empty agent_name, "
        f"call_count={fake.call_count}"
    )


# ---------------------------------------------------------------------------
# Whitelist enumeration: every whitelisted agent must be accepted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_name", sorted(EXPECTED_WHITELIST))
def test_whitelisted_agent_passes_guard(tmp_path, agent_name):
    """Every whitelisted agent name must NOT be rejected at the whitelist guard.
    The dispatch may still fail downstream but the whitelist step must accept it."""
    from lib.dispatch import dispatch_persona

    # Create stub agents/<name>.md
    agents = tmp_path / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{agent_name}.md").write_text(
        f"stub persona for {agent_name}", encoding="utf-8"
    )

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / "out.txt"

    result = dispatch_persona(
        agent_name=agent_name,
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact="out.txt",
        runner=fake,
        plugin_root=tmp_path,
    )

    # The runner MUST have been invoked (whitelist passed)
    assert fake.call_count == 1, (
        f"whitelisted agent {agent_name!r} must pass the guard, "
        f"call_count={fake.call_count}"
    )
    assert isinstance(result, dict)
    assert result.get("ok") is True, (
        f"whitelisted agent {agent_name!r} success path must return ok=True, "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# Artifact resolution must stay inside scratch_dir
# ---------------------------------------------------------------------------

def test_artifact_path_resolved_inside_scratch(tmp_path):
    """The artifact_path returned on success must be inside scratch_dir."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch_resolved = scratch.resolve()

    artifact_name = "nested/inside/scratch.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    assert result.get("ok") is True, f"got {result!r}"
    ap = Path(result["artifact_path"]).resolve()
    assert str(ap).startswith(str(scratch_resolved) + os.sep), (
        f"artifact_path={ap!r} must be inside scratch_dir={scratch_resolved!r}"
    )


# ---------------------------------------------------------------------------
# Result schema completeness
# ---------------------------------------------------------------------------

def test_result_has_required_keys_on_success(tmp_path):
    """The result dict must include ok, reason, artifact_path, returncode."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    for key in ("ok", "reason", "artifact_path", "returncode"):
        assert key in result, (
            f"result must have key {key!r}, got keys={list(result.keys())}"
        )


def test_result_has_required_keys_on_failure(tmp_path):
    """The result dict must include ok, reason (and returncode) on failure."""
    from lib.dispatch import dispatch_persona

    _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=1, write_artifact=False)

    result = dispatch_persona(
        agent_name="director",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    for key in ("ok", "reason"):
        assert key in result, (
            f"failure result must have key {key!r}, got keys={list(result.keys())}"
        )
    assert result["ok"] is False
