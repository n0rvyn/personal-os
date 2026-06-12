"""pytest tests for personal_os_config.py session_reflect keys.

Each test must include the pytest_main_guard so direct execution (python file.py)
does not silently pass without running tests.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "personal_os_config.py"


@pytest.fixture(autouse=True)
def _isolate_personal_os_root(monkeypatch):
    """Phase 4: the grafted `_resolve_config_path` reads `PERSONAL_OS_ROOT`. Clear it for
    every test so ambient env can't leak into these home-fallback / namespace cases. Tests
    that exercise the env anchor re-set it explicitly in their own body (which runs after
    this autouse fixture)."""
    monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)


class TestLoadConfig:
    """Test load_config() via direct function call (reimport per test to reset CONFIG_PATH)."""

    def test_defaults_when_no_yaml_file(self, tmp_path, monkeypatch):
        """No ~/.claude/personal-os.yaml -> returns DEFAULTS with expanded paths."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        # Monkeypatch BEFORE reload so Path.home() is patched during module load.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        # CONFIG_PATH was already bound at import time; patch it directly too.
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"
        personal_os_config.CONFIG_PATH.unlink(missing_ok=True)

        cfg = personal_os_config.load_config()

        assert cfg["exchange_dir"].endswith("Obsidian/PKOS/.exchange")
        assert "~" not in cfg["exchange_dir"]
        assert cfg["session_reflect"]["output_dir"].endswith(".claude/session-reflect/reflections")
        assert cfg["session_reflect"]["session_report_json_path"].endswith("session-report.json")
        assert "session-report.json" in cfg["session_reflect"]["session_report_json_path"]

    def test_user_override_session_reflect_output_dir(self, tmp_path, monkeypatch):
        """yaml has session_reflect: { output_dir: ~/custom/dir } -> returned."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        custom_yaml = tmp_path / ".claude" / "personal-os.yaml"
        custom_yaml.parent.mkdir(parents=True, exist_ok=True)
        custom_yaml.write_text("session_reflect:\n  output_dir: ~/custom/reflect\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        cfg = personal_os_config.load_config()

        assert cfg["session_reflect"]["output_dir"].endswith("custom/reflect")
        # session_report_json_path should still be default (macOS /tmp -> /private/tmp symlink)
        assert cfg["session_reflect"]["session_report_json_path"].endswith("session-report.json")

    def test_default_session_report_json_path(self, tmp_path, monkeypatch):
        """yaml present without session_reflect block -> default /tmp/session-report.json."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        plain_yaml = tmp_path / ".claude" / "personal-os.yaml"
        plain_yaml.parent.mkdir(parents=True, exist_ok=True)
        plain_yaml.write_text("exchange_dir: ~/my-exchange\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        cfg = personal_os_config.load_config()

        assert cfg["session_reflect"]["session_report_json_path"].endswith("session-report.json")
        assert "session-report.json" in cfg["session_reflect"]["session_report_json_path"]

    def test_partial_session_reflect_override(self, tmp_path, monkeypatch):
        """yaml has session_reflect: { output_dir: ... } only -> session_report_json_path falls back to default."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        partial_yaml = tmp_path / ".claude" / "personal-os.yaml"
        partial_yaml.parent.mkdir(parents=True, exist_ok=True)
        partial_yaml.write_text("session_reflect:\n  output_dir: ~/my-reflections\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        cfg = personal_os_config.load_config()

        assert cfg["session_reflect"]["output_dir"].endswith("my-reflections")
        assert cfg["session_reflect"]["session_report_json_path"].endswith("session-report.json")


class TestCliDottedGet:
    """Test CLI dotted key access."""

    def test_cli_dotted_get(self, tmp_path, monkeypatch):
        """Invoke module main with --get session_reflect.output_dir -> prints expanded path."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        # Provide a minimal yaml so we exercise the full load path
        cfg_yaml = tmp_path / ".claude" / "personal-os.yaml"
        cfg_yaml.parent.mkdir(parents=True, exist_ok=True)
        cfg_yaml.write_text("session_reflect:\n  output_dir: ~/my-test-reflect\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--get", "session_reflect.output_dir"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ.copy(), "HOME": str(tmp_path), "PERSONAL_OS_ROOT": ""},
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = result.stdout.strip()
        assert output.endswith("my-test-reflect")
        assert "~" not in output

    def test_cli_nested_key_session_report_json_path(self, tmp_path, monkeypatch):
        """--get session_reflect.session_report_json_path prints the path."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config

        empty_yaml = tmp_path / ".claude" / "personal-os.yaml"
        empty_yaml.parent.mkdir(parents=True, exist_ok=True)
        empty_yaml.write_text("{}\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--get", "session_reflect.session_report_json_path"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ.copy(), "HOME": str(tmp_path), "PERSONAL_OS_ROOT": ""},
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert result.stdout.strip().endswith("session-report.json")


class TestResolveConfigPath:
    """Phase 4: test the _resolve_config_path() anchor unit.

    Mirrors the pkos/scripts/test_personal_os_config.py TestResolverPath style.
    monkeypatch.chdir(tmp_path) FIRST isolates cwd from any stray
    personal-os.yaml on the test machine's real cwd chain. Path.home() is
    patched BEFORE reload so the module-level CONFIG_PATH binding and the
    in-body home fallback both resolve to tmp_path.
    """

    def test_env_takes_priority(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        env_root = tmp_path / "proj-root"
        env_root.mkdir()
        marker = env_root / "personal-os.yaml"
        marker.write_text("exchange_dir: /test/exchange\nsession_reflect:\n  output_dir: ~/reflect\n")
        monkeypatch.setenv("PERSONAL_OS_ROOT", str(env_root))
        assert personal_os_config._resolve_config_path() == marker

    def test_cwd_walk_finds_marker(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub" / "deep"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        marker = tmp_path / "personal-os.yaml"
        marker.write_text("vault:\n  root: .\nsession_reflect:\n  output_dir: ~/reflect\n")
        assert personal_os_config._resolve_config_path() == marker

    def test_sentinel_rejects_collision(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub"
        deep.mkdir()
        # No sentinel keys
        collision = tmp_path / "personal-os.yaml"
        collision.write_text("foo: 1\nbar: baz\n")
        monkeypatch.chdir(deep)
        expected = tmp_path / ".claude" / "personal-os.yaml"
        assert personal_os_config._resolve_config_path() == expected

    def test_sentinel_fail_soft_on_bad_yaml(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub"
        deep.mkdir()
        broken = tmp_path / "personal-os.yaml"
        broken.write_text(":\n  - [unterminated\n  : :")
        monkeypatch.chdir(deep)
        expected = tmp_path / ".claude" / "personal-os.yaml"
        assert personal_os_config._resolve_config_path() == expected

    def test_home_fallback_byte_identical_to_legacy_load(
        self, tmp_path, monkeypatch
    ):
        """Regression shield: with no env + no marker on cwd chain, load_config()
        must produce byte-identical output to the pre-Phase-2 session-reflect
        derivation (read home + DEFAULTS merge + recursive _expand_value +
        session_reflect nested-merge default fallback)."""
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "personal-os.yaml"
        cfg_file.write_text(
            "exchange_dir: /test/exchange\n"
            "scratch_dir: /test/scratch\n"
            "session_reflect:\n"
            "  output_dir: /test/reflect\n"
        )
        # Pre-Phase-2 session-reflect derivation
        raw = yaml.safe_load(cfg_file.read_text()) or {}
        legacy = {**personal_os_config.DEFAULTS, **raw}
        sr = {**personal_os_config.DEFAULTS["session_reflect"], **(raw.get("session_reflect") or {})}
        legacy["session_reflect"] = sr
        # Recursive expand (mirrors pre-Phase-2 _expand_value)
        def _expand(v):
            if isinstance(v, str):
                return str(Path(os.path.expanduser(v)).resolve())
            if isinstance(v, dict):
                return {k: _expand(x) for k, x in v.items()}
            return v
        legacy = {k: _expand(v) for k, v in legacy.items()}
        cfg = personal_os_config.load_config()
        assert cfg == legacy


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
