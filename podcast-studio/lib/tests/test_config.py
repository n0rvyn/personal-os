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
from lib.config import _default_config_path, _resolve_personal_os_yaml  # noqa: E402


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


def test_vault_subdirs_derived_and_created(tmp_path, monkeypatch):
    """Phase 4: a loaded config exposes episodes_dir/state_dir/reports_dir,
    each resolving to <output_dir>/episodes|state|reports and existing on disk
    after load (derived + auto-created — NOT a new YAML key)."""
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

    out = Path(dirs['output_dir'])
    assert cfg.vault.episodes_dir == str(out / "episodes")
    assert cfg.vault.state_dir == str(out / "state")
    assert cfg.vault.reports_dir == str(out / "reports")
    for d in (cfg.vault.episodes_dir, cfg.vault.state_dir, cfg.vault.reports_dir):
        assert Path(d).exists() and Path(d).is_dir(), f"subdir not created: {d}"


def test_output_dir_still_fail_closed(tmp_path, monkeypatch):
    """Phase 4 regression: deriving subdirs must NOT weaken the output_dir
    fail-closed contract. A missing output_dir still raises naming
    vault.output_dir, and the subdir mkdir must NOT create the missing
    output_dir (mkdir runs AFTER existence validation)."""
    cfg_path = tmp_path / "config.yaml"
    subj = tmp_path / "subjective"; subj.mkdir()
    news = tmp_path / "news"; news.mkdir()
    missing_out = tmp_path / "no-such-output"  # deliberately not created
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {subj}
          news_dir: {news}
          output_dir: {missing_out}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    with pytest.raises(Exception) as exc:
        load_config()
    assert "output_dir" in str(exc.value)
    assert not missing_out.exists(), "fail-closed violated: output_dir was created by subdir mkdir"


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


# ---------- vault.voice_corpus_dir (optional bible voice-corpus override) ----------

def test_voice_corpus_dir_resolved_when_present(tmp_path, monkeypatch):
    """An existing voice_corpus_dir is resolved onto VaultConfig."""
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    voice = tmp_path / "voice-corpus"
    voice.mkdir()
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
          voice_corpus_dir: {voice}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.vault.voice_corpus_dir == str(voice)


def test_voice_corpus_dir_none_when_absent(tmp_path, monkeypatch):
    """No voice_corpus_dir key → None (legacy configs unaffected)."""
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
    assert cfg.vault.voice_corpus_dir is None


def test_voice_corpus_dir_missing_path_raises(tmp_path, monkeypatch):
    """A present-but-nonexistent voice_corpus_dir fails closed naming the key
    (mirrors config.py's 'never a silent default' contract; forces seed-first)."""
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
          voice_corpus_dir: {tmp_path / 'no-such-voice'}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    with pytest.raises(Exception) as exc:
        load_config()
    assert "voice_corpus_dir" in str(exc.value)


def test_voice_corpus_dir_empty_string_raises(tmp_path, monkeypatch):
    """A whitespace-only voice_corpus_dir is rejected as non-empty-string."""
    pytest.importorskip("yaml")
    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    _write_config(cfg_path, f'''
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
          voice_corpus_dir: "   "
        tts:
          provider: volc
          host_voice: BV001_streaming
    ''')
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    with pytest.raises(Exception) as exc:
        load_config()
    assert "voice_corpus_dir" in str(exc.value)


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


# ---------- Phase 3: project-anchor + exchange_dir ----------

def test_env_anchor_resolves_personal_os_yaml(tmp_path, monkeypatch):
    """PERSONAL_OS_ROOT env var points at a project-root file; load_config()
    reads vault/tts/exchange_dir from it (no PODCAST_STUDIO_CONFIG, no
    home fallback consulted)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PODCAST_STUDIO_CONFIG", raising=False)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    dirs = _make_vault_dirs(tmp_path)
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (tmp_path / "personal-os.yaml").write_text(textwrap.dedent(f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        exchange_dir: {exchange}
    """))
    monkeypatch.setenv("PERSONAL_OS_ROOT", str(tmp_path))

    cfg = load_config()
    assert cfg.vault.subjective_dir.endswith("subjective")
    assert cfg.tts.provider == "volc"
    assert cfg.exchange_dir is not None
    assert cfg.exchange_dir.endswith("exchange")


def test_cwd_walk_anchor_finds_marker_in_ancestor(tmp_path, monkeypatch):
    """No env, chdir into a deep subdir; marker `personal-os.yaml` lives
    in an ancestor; cwd-walk must find it."""
    monkeypatch.delenv("PODCAST_STUDIO_CONFIG", raising=False)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    deep = tmp_path / "sub" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    dirs = _make_vault_dirs(tmp_path)
    (tmp_path / "personal-os.yaml").write_text(textwrap.dedent(f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """))

    cfg = load_config()
    assert cfg.vault.subjective_dir.endswith("subjective")
    assert cfg.tts.provider == "volc"


def test_sentinel_rejects_fleet_only_marker(tmp_path, monkeypatch):
    """A `personal-os.yaml` with only `exchange_dir`/`scratch_dir` (no
    vault/tts) must NOT be adopted — sentinel fails the candidate and
    the function returns None (no podcast-private home fallback here)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    (tmp_path / "personal-os.yaml").write_text(textwrap.dedent("""
        exchange_dir: /tmp/somewhere
        scratch_dir: /tmp/scratch
    """))

    assert _resolve_personal_os_yaml() is None


def test_sentinel_fail_soft_on_bad_yaml(tmp_path, monkeypatch):
    """A broken `personal-os.yaml` at cwd must not raise; sentinel
    fail-soft skips the candidate, walk continues, returns None."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    (tmp_path / "personal-os.yaml").write_text("this: : is: not [valid yaml :::\n  - oops")

    assert _resolve_personal_os_yaml() is None


def test_home_fallback_unchanged(tmp_path, monkeypatch):
    """命门: 没有 env, cwd 链上无 sentinel-valid marker 时,
    `_resolve_config_path(None)` 必须仍返回 podcast 私有 home
    (`~/.podcast-studio/config.yaml`),不是 `~/.claude/personal-os.yaml`。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PODCAST_STUDIO_CONFIG", raising=False)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    from lib.config import _resolve_config_path

    resolved = _resolve_config_path(None)
    assert resolved == _default_config_path()
    # The default path is the podcast-private home, NOT ~/.claude/personal-os.yaml.
    assert ".podcast-studio/config.yaml" in str(resolved)
    assert ".claude/personal-os.yaml" not in str(resolved)


def test_podcast_studio_config_wins_over_anchor(tmp_path, monkeypatch):
    """PODCAST_STUDIO_CONFIG 仍居项目锚点之上(既有解析优先级保持)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    dirs = _make_vault_dirs(tmp_path)
    # Legacy config (no exchange_dir).
    legacy_cfg = tmp_path / "legacy.yaml"
    _write_config(legacy_cfg, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    # An anchor with exchange_dir that must NOT win.
    (tmp_path / "personal-os.yaml").write_text(textwrap.dedent(f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        exchange_dir: /tmp/anchor_exchange
    """))
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(legacy_cfg))

    cfg = load_config()
    assert cfg.exchange_dir is None  # legacy path read, not the anchor.


def test_exchange_dir_default_none_for_legacy(tmp_path, monkeypatch):
    """Legacy `~/.podcast-studio/config.yaml` (no exchange_dir) → cfg.exchange_dir
    is None (向后兼容,不报错)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    dirs = _make_vault_dirs(tmp_path)
    legacy_cfg = tmp_path / "legacy.yaml"
    _write_config(legacy_cfg, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(legacy_cfg))

    cfg = load_config()
    assert cfg.exchange_dir is None


def test_exchange_dir_non_str_fails_soft(tmp_path, monkeypatch):
    """exchange_dir 写成 list(或 dict)→ 不抛 TypeError,cfg.exchange_dir is None
    (类型守卫生效)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)

    dirs = _make_vault_dirs(tmp_path)
    cfg_path = tmp_path / "bad_exchange.yaml"
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        exchange_dir:
          - /some/path
          - /another/path
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.exchange_dir is None


# ---------- Phase 2 paper-line: optional `papers.*` config section ----------

def test_papers_section_absent_opinion_config_still_resolves(tmp_path, monkeypatch):
    """Zero-change: an existing opinion config WITHOUT a `papers` section
    resolves unchanged (cfg.papers is None)."""
    from lib.config import PapersConfig  # noqa: F401  (pre-impl FAIL signal)

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
    # papers must be a defined attribute (None when absent) — not a missing
    # field. The exact attribute name `papers` is the contract the paper
    # line's require_papers(cfg) helper relies on.
    assert cfg.papers is None
    # Existing fields untouched.
    assert cfg.tts.provider == "volc"
    assert cfg.vault.output_dir.endswith("output")


def test_papers_section_present_type_validated(tmp_path, monkeypatch):
    """When present, `papers` is parsed into a PapersConfig and type-validated:
    categories must be a non-empty list[str]; max_candidates a positive int."""
    from lib.config import PapersConfig  # noqa: F401  (pre-impl FAIL signal)

    cfg_path = tmp_path / "config.yaml"
    dirs = _make_vault_dirs(tmp_path)
    # Valid shape → resolves.
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        papers:
          categories:
            - cs.CL
            - cs.LG
          max_candidates: 30
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.papers is not None
    assert isinstance(cfg.papers, PapersConfig)
    assert list(cfg.papers.categories) == ["cs.CL", "cs.LG"]
    assert cfg.papers.max_candidates == 30

    # Bad shape: categories not a list → ConfigError.
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        papers:
          categories: cs.CL
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))
    with pytest.raises(Exception) as exc:
        load_config()
    assert "categories" in str(exc.value)

    # Bad shape: categories empty → ConfigError.
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        papers:
          categories: []
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))
    with pytest.raises(Exception) as exc:
        load_config()
    assert "categories" in str(exc.value)

    # Bad shape: categories contains non-string → ConfigError.
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        papers:
          categories:
            - 42
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))
    with pytest.raises(Exception) as exc:
        load_config()
    assert "categories" in str(exc.value)

    # Bad shape: max_candidates non-positive → ConfigError.
    _write_config(cfg_path, f"""
        vault:
          subjective_dir: {dirs['subjective_dir']}
          news_dir: {dirs['news_dir']}
          output_dir: {dirs['output_dir']}
        tts:
          provider: volc
          host_voice: BV001_streaming
        papers:
          categories:
            - cs.CL
          max_candidates: 0
    """)
    monkeypatch.setenv("PODCAST_STUDIO_CONFIG", str(cfg_path))
    with pytest.raises(Exception) as exc:
        load_config()
    assert "max_candidates" in str(exc.value)


def test_require_papers_raises_when_absent(tmp_path, monkeypatch):
    """require_papers(cfg) on a papers-less config raises ConfigError naming
    papers.categories — the paper-line fail-closed use site, not a resolve-time
    requirement."""
    from lib.config import require_papers  # noqa: F401  (pre-impl FAIL signal)

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
    assert cfg.papers is None

    with pytest.raises(Exception) as exc:
        require_papers(cfg)
    assert "papers.categories" in str(exc.value)
