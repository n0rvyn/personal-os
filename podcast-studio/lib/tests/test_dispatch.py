"""Tests for lib/dispatch.py — persona dispatch primitive (claude -p).

Written before lib/dispatch.py exists; collection must fail at this point
(`No module named 'lib.dispatch'`).

Pins (per phase1-code-runner-plan Task 2-tests):
- dispatch_persona(agent_name, user_prompt, scratch_dir, expected_artifact, *,
  runner=...) constructs the claude -p command as a LIST (not shell=True);
  records that for the test-side signature capture.
- The user prompt embedded into the command contains the absolute artifact
  path; the system-prompt injection contains the agent.md text.
- Success path (fake writes artifact + exit 0) → {ok: True, artifact_path}.
- Failure path (non-zero exit / artifact missing / simulated timeout) →
  {ok: False, reason}.
- agent_name not in the whitelist is rejected (ValueError OR {ok:False}).
- expected_artifact path-traversal attempt ("../escape.json") is rejected.
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
}


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
    from lib import dispatch  # noqa: F401
    assert hasattr(dispatch, "dispatch_persona")


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
      - last_kwargs: the kwargs dict (must include shell=False or omit it
        for the "list args, no shell=True" guarantee to hold)
      - last_cwd / last_timeout / last_capture_output / last_text: kwargs
        that the impl may pass
    Optional behavior knobs:
      - returncode: exit code to report
      - write_artifact: bool — whether to also create the expected_artifact
        file on disk to simulate a successful persona run
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

        if self.write_artifact and kwargs is not None:
            # The impl may stash the artifact path somewhere — recover from
            # argv by parsing the user prompt's "write to <abs>" directive.
            # For the fake we just respect the configured artifact path which
            # tests stash in `self._expected_artifact` before invoking.
            ep = getattr(self, "_expected_artifact", None)
            if ep is not None:
                p = Path(ep)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("artifact body", encoding="utf-8")

        return _FakeCompletedProcess(returncode=self.returncode)


# ---------------------------------------------------------------------------
# Success path: fake writes artifact + exits 0 → {ok:True, artifact_path}
# ---------------------------------------------------------------------------

def _make_agents_dir(tmp_path: Path) -> Path:
    """Create a minimal agents/ dir with one .md file the dispatch primitive
    can read as a system prompt."""
    agents = tmp_path / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "bianyang.md").write_text(
        "You are bianyang, the broadcast rewriter.", encoding="utf-8"
    )
    return agents


def test_dispatch_success_returns_ok_true_and_artifact_path(tmp_path):
    """Happy path: fake writes the artifact at expected_artifact and exits 0.
    dispatch_persona must return {ok:True, artifact_path:<abs>, ...}."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "broadcast-script.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    result = dispatch_persona(
        agent_name="bianyang",
        user_prompt="Rewrite the following into a broadcast script: ... ",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,  # contains agents/bianyang.md
    )

    assert isinstance(result, dict), f"result must be a dict, got {type(result)}"
    assert result.get("ok") is True, (
        f"success path must return ok=True, got result={result!r}"
    )
    assert "reason" in result, "result must carry a `reason` key (gate contract)"
    # artifact_path must be the absolute resolved path
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

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="bianyang",
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
    # shell=True must be absent or explicitly False
    shell_kw = fake.last_kwargs.get("shell", False) if fake.last_kwargs else False
    assert shell_kw is False, (
        f"shell= must be False (or absent), got shell={shell_kw!r} — "
        "list-arg invocations are the threat-model contract"
    )


def test_argv_first_element_is_claude_binary(tmp_path):
    """The first element of argv should be the claude executable (default
    'claude')."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="bianyang",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv = fake.last_argv
    assert argv[0] == "claude", (
        f"argv[0] should be the claude binary name, got {argv[0]!r}"
    )


# ---------------------------------------------------------------------------
# User prompt must contain the absolute artifact path; system-prompt
# injection must contain the agent.md text.
# ---------------------------------------------------------------------------

def test_user_prompt_contains_artifact_path(tmp_path):
    """The persona's instructions (argv entry) must include the absolute
    artifact path so the persona knows where to write its output."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "broadcast-script.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="bianyang",
        user_prompt="Rewrite body X",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    argv_str = " ".join(str(a) for a in fake.last_argv)
    expected_abs = str((scratch / artifact_name).resolve())
    # The artifact path (or a path containing the artifact name) must appear
    # somewhere in the argv as text — the impl can put it in the user prompt
    # arg, a temp file, or as a flag value.
    assert expected_abs in argv_str or artifact_name in argv_str, (
        f"artifact path/name {expected_abs!r} not present in argv: {argv_str!r}"
    )


def test_system_prompt_contains_agent_md_text(tmp_path):
    """The agent's persona text (agents/<name>.md) must be injected as a
    system prompt so the persona loads with its full instructions."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)  # bianyang.md present
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    dispatch_persona(
        agent_name="bianyang",
        user_prompt="hello",
        scratch_dir=scratch,
        expected_artifact=artifact_name,
        runner=fake,
        plugin_root=tmp_path,
    )

    # The argv elements (joined) must contain some marker of the agent.md
    # text — either the literal body, the path to the .md, or both.
    argv_str = " ".join(str(a) for a in fake.last_argv)
    agent_md_path = agents_dir / "bianyang.md"
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

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=1, write_artifact=False)

    result = dispatch_persona(
        agent_name="bianyang",
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
    must return {ok:False, reason} — check_artifact gates the success path."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=False)  # exits 0 but no file

    result = dispatch_persona(
        agent_name="bianyang",
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


def test_failure_on_subprocess_timeout_returns_ok_false(tmp_path):
    """If the subprocess times out, dispatch must return {ok:False, reason}
    — the runner raises TimeoutExpired, dispatch must translate to ok:False
    rather than propagating."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    artifact_name = "out.txt"
    fake = _FakeRunner(returncode=0, write_artifact=False, timeout_simulate=True)

    result = dispatch_persona(
        agent_name="bianyang",
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


# ---------------------------------------------------------------------------
# Whitelist + path-traversal guards
# ---------------------------------------------------------------------------

def test_rejects_unknown_agent_name(tmp_path):
    """An agent name not in the whitelist must be rejected — either as a
    ValueError raised at the entry boundary, or as a {ok:False} result with
    a reason that names the offending agent. The runner must NEVER be
    invoked in this case (no arbitrary file read)."""
    from lib.dispatch import dispatch_persona

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_value_error = False
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
    except ValueError as e:
        raised_value_error = True
        assert "ghost" in str(e), (
            f"ValueError should name the bad agent, got: {e}"
        )

    if not raised_value_error:
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
    must be rejected. The runner must NEVER be invoked in this case (no
    arbitrary file write outside the scratch dir)."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_value_error = False
    result = None
    try:
        result = dispatch_persona(
            agent_name="bianyang",
            user_prompt="hello",
            scratch_dir=scratch,
            expected_artifact="../escape.json",
            runner=fake,
            plugin_root=tmp_path,
        )
    except ValueError as e:
        raised_value_error = True
        # The error must mention the traversal/escape concept
        msg = str(e).lower()
        assert any(tok in msg for tok in ("escape", "traversal", "outside", "invalid")), (
            f"ValueError should mention escape/traversal, got: {e}"
        )

    if not raised_value_error:
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
    """An empty agent_name must be rejected (would map to agents/.md which
    doesn't exist / is not a persona)."""
    from lib.dispatch import dispatch_persona

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    fake = _FakeRunner(returncode=0, write_artifact=True)

    raised_value_error = False
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
    except ValueError:
        raised_value_error = True

    if not raised_value_error:
        assert isinstance(result, dict)
        assert result.get("ok") is False

    assert fake.call_count == 0, (
        f"runner must not be invoked for empty agent_name, "
        f"call_count={fake.call_count}"
    )


# ---------------------------------------------------------------------------
# Whitelist enumeration: every whitelisted agent name should be accepted
# (modulo the agent.md actually existing under plugin_root/agents/).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_name", sorted(AGENT_WHITELIST))
def test_whitelisted_agent_passes_guard(tmp_path, agent_name):
    """Every whitelisted agent name should NOT be rejected at the whitelist
    guard. The dispatch may still fail downstream (e.g. agent.md file
    missing) but the whitelist step itself must accept it."""
    from lib.dispatch import dispatch_persona

    # Create a stub agents/<name>.md so dispatch doesn't fail for that reason
    agents = tmp_path / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{agent_name}.md").write_text(
        f"stub for {agent_name}", encoding="utf-8"
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
    # And the result should reflect the success path
    assert isinstance(result, dict)
    assert result.get("ok") is True, (
        f"whitelisted agent {agent_name!r} success path must return ok=True, "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# Artifact resolution must stay inside scratch_dir
# ---------------------------------------------------------------------------

def test_artifact_path_resolved_inside_scratch(tmp_path):
    """The artifact_path returned on success (and the path the persona is
    instructed to write to) must be inside scratch_dir — the path-traversal
    guarantee at the resolution step, complementing the explicit
    '../escape.json' guard."""
    from lib.dispatch import dispatch_persona

    agents_dir = _make_agents_dir(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    scratch_resolved = scratch.resolve()

    artifact_name = "nested/inside/scratch.txt"
    fake = _FakeRunner(returncode=0, write_artifact=True)
    fake._expected_artifact = scratch / artifact_name

    result = dispatch_persona(
        agent_name="bianyang",
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
# Phase 3 — scorecard persona in dispatch AGENT_WHITELIST.
# Per task 4-tests: dispatch.AGENT_WHITELIST must include 'scorecard' so the
# 13a station's dispatch_persona('scorecard', ...) call does not get rejected
# at the whitelist guard. (Mirrors the pipeline-side pin.)
# ---------------------------------------------------------------------------

def test_dispatch_agent_whitelist_contains_scorecard():
    """`lib.dispatch.AGENT_WHITELIST` must include 'scorecard' — the 13a
    station's dispatch_persona('scorecard', ...) call would otherwise be
    rejected at the whitelist guard with a DispatchError. Kept in sync with
    the pipeline-side whitelist (both are leaves, no shared import)."""
    from lib.dispatch import AGENT_WHITELIST

    assert "scorecard" in AGENT_WHITELIST, (
        f"dispatch.AGENT_WHITELIST must include 'scorecard', "
        f"got {sorted(AGENT_WHITELIST)}"
    )
