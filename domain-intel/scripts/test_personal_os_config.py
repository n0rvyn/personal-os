"""pytest tests for personal_os_config.py (domain-intel flat base copy).

Verifies:
  1. PERSONAL_OS_ROOT env anchor takes priority
  2. cwd-walk finds a sentinel-bearing personal-os.yaml on the parent chain
  3. Bad YAML / non-dict / missing-sentinel on cwd chain → fail-soft skip → home
  4. No env + no marker on cwd chain → _resolve_config_path() == home constant
  5. load_config() byte-identical to pre-Phase-2 flat-DEFAULTS derivation
  6. --get exchange_dir / --get scratch_dir CLI surface unchanged (subprocess)

Each test must include the pytest_main_guard so direct execution
(`python3 test_personal_os_config.py`) does not silently pass.
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


class TestResolveConfigPath:
    """Test _resolve_config_path() precedence + sentinel logic.

    monkeypatch.chdir(tmp_path) FIRST isolates cwd from any stray
    personal-os.yaml on the test machine's real cwd chain. Path.home()
    is patched BEFORE reload so the module-level CONFIG_PATH binding and
    the in-body home fallback both resolve to tmp_path.
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
        marker.write_text(yaml.safe_dump({"exchange_dir": str(tmp_path / "ex")}))
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
        marker.write_text(yaml.safe_dump({"vault": {"root": "."}}))
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
        collision.write_text(yaml.safe_dump({"foo": 1, "bar": "baz"}))
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

    def test_home_fallback_when_no_marker(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        expected = tmp_path / ".claude" / "personal-os.yaml"
        assert personal_os_config._resolve_config_path() == expected

    def test_home_fallback_byte_identical_to_legacy_load(
        self, tmp_path, monkeypatch
    ):
        """Regression shield: with no env + no marker on cwd chain, load_config()
        must produce byte-identical output to the pre-Phase-2 implementation
        (read home + flat DEFAULTS merge + flat-key expansion)."""
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
        cfg_file.write_text(yaml.safe_dump({
            "exchange_dir": str(tmp_path / "ex"),
            "scratch_dir":  str(tmp_path / "sc"),
        }))
        # Pre-Phase-2 flat derivation
        raw = yaml.safe_load(cfg_file.read_text()) or {}
        legacy = {**personal_os_config.DEFAULTS, **raw}
        for k, v in legacy.items():
            if isinstance(v, str):
                legacy[k] = str(Path(os.path.expanduser(v)).resolve())
        cfg = personal_os_config.load_config()
        assert cfg == legacy


class TestCliGet:
    """Test CLI --get surface (exchange_dir / scratch_dir)."""

    def test_cli_exchange_dir(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        cfg_yaml = tmp_path / ".claude" / "personal-os.yaml"
        cfg_yaml.parent.mkdir(parents=True, exist_ok=True)
        cfg_yaml.write_text("exchange_dir: /test/exchange\nscratch_dir: /test/scratch\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--get", "exchange_dir"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(tmp_path), "PERSONAL_OS_ROOT": ""},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = result.stdout.strip()
        assert out and out.startswith("/"), f"got {out!r}"
        assert "exchange" in out

    def test_cli_scratch_dir(self, tmp_path, monkeypatch):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        import personal_os_config
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        importlib.reload(personal_os_config)
        personal_os_config.CONFIG_PATH = tmp_path / ".claude" / "personal-os.yaml"

        cfg_yaml = tmp_path / ".claude" / "personal-os.yaml"
        cfg_yaml.parent.mkdir(parents=True, exist_ok=True)
        cfg_yaml.write_text("exchange_dir: /test/exchange\nscratch_dir: /test/scratch\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--get", "scratch_dir"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(tmp_path), "PERSONAL_OS_ROOT": ""},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = result.stdout.strip()
        assert out and out.startswith("/"), f"got {out!r}"
        assert "scratch" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
