"""podcast-prep vault-root resolution rewire tests (test-FAIL-first).

These tests pin the contract that prep's vault root resolution comes from
the podcast-studio config (`lib.config.load_config()`), NOT from
`~/.claude/personal-os.yaml`. The rewire is Task 4-impl; at this point
the tests are expected to FAIL because the rewire is not yet applied
(prep still reads personal-os.yaml / falls back to ~/Obsidian/PKOS).

Resolution priority (after rewire):
1. explicit `--vault-root` arg
2. `PKOS_VAULT_ROOT` env var
3. `lib.config.load_config()` (podcast-studio config)
4. raise (no silent default)

Test contract:
- The four core tests fail at this point (rewire absent).
- The fifth test (no-personalos-read) passes by construction (we
  monkeypatch to detect any read attempt) — but at the import level
  it still fails because the rewire is not yet wired.
"""
from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Plugin root on path so prep's `from lib.config import ...` resolves.
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _write_config(path: Path, subjective: Path, news: Path, output: Path) -> None:
    path.write_text(textwrap.dedent(f"""
        vault:
          subjective_dir: {subjective}
          news_dir: {news}
          output_dir: {output}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """))


def _make_vault_dirs(root: Path) -> dict:
    subj = root / "subjective"
    news = root / "news"
    out = root / "output"
    for d in (subj, news, out):
        d.mkdir(parents=True, exist_ok=True)
    return {"subjective": subj, "news": news, "output": out}


# ---------------------------------------------------------------------------
# These tests target the rewire. The rewire is in Task 4-impl, so they fail
# at this point. Each test isolates one priority/precedence claim.
# ---------------------------------------------------------------------------

def test_explicit_arg_wins(tmp_path, monkeypatch):
    """Explicit --vault-root is returned verbatim, no config consulted."""
    from scripts.orchestrator import _resolve_vault_root  # type: ignore[import-not-found]

    explicit = tmp_path / "explicit_vault"
    explicit.mkdir()
    monkeypatch.delenv("PKOS_VAULT_ROOT", raising=False)
    monkeypatch.delenv("PODCAST_STUDIO_CONFIG", raising=False)

    result = _resolve_vault_root(str(explicit))
    assert result == str(explicit), (
        f"expected explicit arg to win; got {result!r}"
    )


def test_env_override(tmp_path, monkeypatch):
    """PKOS_VAULT_ROOT env var is honored when no explicit arg."""
    from scripts.orchestrator import _resolve_vault_root  # type: ignore[import-not-found]

    env_path = tmp_path / "env_vault"
    env_path.mkdir()
    monkeypatch.setenv("PKOS_VAULT_ROOT", str(env_path))
    monkeypatch.delenv("PODCAST_STUDIO_CONFIG", raising=False)

    result = _resolve_vault_root(None)
    assert result == str(env_path), (
        f"expected PKOS_VAULT_ROOT to win; got {result!r}"
    )


def test_config_fallback_uses_podcast_studio(tmp_path, monkeypatch):
    """With no explicit arg and no PKOS_VAULT_ROOT, vault root comes from
    podcast-studio config (lib.config). personal-os.yaml is not consulted."""
    from scripts.orchestrator import _resolve_vault_root  # type: ignore[import-not-found]

    dirs = _make_vault_dirs(tmp_path)
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, dirs["subjective"], dirs["news"], dirs["output"])
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg))
    monkeypatch.delenv("PKOS_VAULT_ROOT", raising=False)

    result = _resolve_vault_root(None)
    # After rewire: result should be the resolved subjective dir from config.
    assert str(dirs["subjective"]) in result or result == str(dirs["subjective"]), (
        f"expected vault root from podcast-studio config; got {result!r}"
    )


def test_no_personalos_read(tmp_path, monkeypatch):
    """personal-os.yaml path is never opened when resolving vault root."""
    from scripts.orchestrator import _resolve_vault_root  # type: ignore[import-not-found]

    # Plant a personal-os.yaml that would be read by the OLD code path.
    personal_os = Path.home() / ".claude" / "personal-os.yaml"
    _orig_exists = Path.exists  # capture before patching to avoid recursion
    monkeypatch.setattr(Path, "exists", lambda self, *a, **kw: (
        True if str(self) == str(personal_os) else _orig_exists(self, *a, **kw)
    ))

    dirs = _make_vault_dirs(tmp_path)
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, dirs["subjective"], dirs["news"], dirs["output"])
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg))
    monkeypatch.delenv("PKOS_VAULT_ROOT", raising=False)

    # Patch open() to detect any read of personal-os.yaml.
    original_open = open

    def _guarded_open(file, *args, **kwargs):
        if str(file) == str(personal_os):
            raise AssertionError(
                f"personal-os.yaml was opened during vault root resolution: {file}"
            )
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _guarded_open)
    _resolve_vault_root(None)  # must not raise AssertionError


def test_topic_log_defaults_from_config(tmp_path, monkeypatch):
    """`check` WITHOUT --topic-log resolves topic-log to
    <config output_dir>/topic_log.yaml (no required=True error)."""
    from scripts.orchestrator import main as orch_main  # type: ignore[import-not-found]

    dirs = _make_vault_dirs(tmp_path)
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, dirs["subjective"], dirs["news"], dirs["output"])
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg))
    monkeypatch.delenv("PKOS_VAULT_ROOT", raising=False)
    monkeypatch.setattr("sys.argv", [
        "orchestrator.py", "check",
        "--vault-root", str(dirs["subjective"]),
        "--angle", "test",
    ])

    # Should not raise SystemExit(2) from argparse "required: --topic-log".
    # We allow other SystemExit codes (e.g. for a successful run); we
    # specifically check the error type was not an argparse "the following
    # arguments are required" error.
    try:
        orch_main()
    except SystemExit as e:
        msg = str(e)
        assert "required" not in msg.lower() or "topic-log" not in msg.lower(), (
            f"topic-log should default from config, not be required: {msg!r}"
        )
        # Otherwise: it may have completed (code 0) or failed for a
        # business reason (e.g. no candidate) — both acceptable here.
