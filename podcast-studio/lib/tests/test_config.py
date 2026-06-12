"""Config resolver tests (test-FAIL-first contract — written before
lib/config.py exists; collection must fail at this point).

Import surface: `from lib.config import load_config` invoked from the
plugin root (parent of `lib/`), with the config path injected via the
PODCAST_STUDIO_CONFIG env var.
"""
from __future__ import annotations

import os
import sys
import textwrap
import types
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.config import load_config` resolves.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.config import load_config  # noqa: E402  (test-FAIL-first expects this to fail pre-impl)


# ---------- helpers ----------

def _write_config(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _make_vault_dirs(root: Path) -> dict:
    subj = root / "subjective"
    news = root / "news"
    out = root / "output"
    for d in (subj, news, out):
        d.mkdir(parents=True, exist_ok=True)
    return {"subjective_dir": str(subj), "news_dir": str(news), "output_dir": str(out)}


# ---------- tests ----------

def test_valid_config_resolves(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()

    # Paths must be returned, expanded (~) and absolute.
    assert cfg.vault.subjective_dir == str(Path(dirs['subjective_dir']).expanduser().resolve()) or \
           cfg.vault.subjective_dir.endswith("subjective")
    assert cfg.vault.news_dir.endswith("news")
    assert cfg.vault.output_dir.endswith("output")
    assert cfg.tts.provider == "volc"
    assert cfg.tts.host_voice == "BV001_streaming"


def test_missing_file_raises(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(missing))
    with pytest.raises(Exception):
        load_config()


def test_missing_required_key_raises(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    # Omit vault.output_dir
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    with pytest.raises(Exception) as exc:
        load_config()
    # The error message must name the offending key.
    assert "output_dir" in str(exc.value)


def test_nonexistent_vault_dir_raises(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    real_subj = tmp_path / "subjective"
    real_subj.mkdir()
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {tmp_path / "does-not-exist"}
          news_dir: {real_subj}
          output_dir: {real_subj}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    with pytest.raises(Exception) as exc:
        load_config()
    assert "subjective_dir" in str(exc.value)


def test_tts_settings_resolve(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: minimax
          host_voice: male-qn-jingying
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.tts.provider == "minimax"
    assert cfg.tts.host_voice == "male-qn-jingying"


def test_nested_schema_minimal_parser(tmp_path, monkeypatch):
    """With PyYAML forced unavailable, the minimal reader still parses
    the nested vault:/tts: schema correctly."""
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    # Force PyYAML to be unavailable for this test.
    saved_yaml = sys.modules.pop("yaml", None)
    monkeypatch.setitem(sys.modules, "yaml", None)
    try:
        cfg = load_config()
        assert cfg.tts.provider == "volc"
        assert cfg.vault.output_dir.endswith("output")
    finally:
        if saved_yaml is not None:
            sys.modules["yaml"] = saved_yaml


def test_pyyaml_path_when_present(tmp_path, monkeypatch):
    """When PyYAML imports, the same config resolves identically (parity)."""
    pytest.importorskip("yaml")
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.tts.provider == "volc"
    assert cfg.tts.host_voice == "BV001_streaming"
    assert cfg.vault.subjective_dir.endswith("subjective")


# ---------- `--validate` CLI flag (consumed by config-studio editor) ----------

import subprocess  # noqa: E402


def _run_validate(cfg_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "lib.config", "--validate", str(cfg_path)],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )


def test_validate_flag_exit0_on_good_config(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    proc = _run_validate(cfg_path)
    assert proc.returncode == 0, proc.stderr


def test_validate_flag_exit1_names_missing_key(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)  # output_dir missing
    proc = _run_validate(cfg_path)
    assert proc.returncode == 1
    assert "output_dir" in proc.stderr


def test_validate_flag_exit1_on_nonexistent_dir(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {tmp_path / 'nope'}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    proc = _run_validate(cfg_path)
    assert proc.returncode == 1
    assert "subjective_dir" in proc.stderr


def test_no_args_still_prints_resolved_config(tmp_path, monkeypatch):
    # regression: the bare `python3 -m lib.config` smoke path must survive.
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    proc = subprocess.run(
        [sys.executable, "-m", "lib.config"],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "PODCAST_STUDIO_CONFIG": str(cfg_path)},
    )
    assert proc.returncode == 0, proc.stderr
    assert "tts.provider" in proc.stdout
