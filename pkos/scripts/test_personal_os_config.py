"""Unit tests for personal_os_config.py dotted-namespace key lookup.

Tests the three cases: existing flat key, dotted namespace key (existing), dotted namespace key (missing).
Run via: python3 -m pytest test_personal_os_config.py -v
"""
import pytest, subprocess, tempfile, pathlib, yaml, os

SCRIPT = pathlib.Path(__file__).parent / "personal_os_config.py"


def _run_get(key: str, env: dict) -> str:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--get", key],
        capture_output=True, text=True, env=env,
    )
    return result.stdout.strip()


class TestDottedNamespaceLookup:
    """Test dotted namespace key resolution via CLI --get."""

    def test_existing_flat_key_returns_value(self, tmp_path):
        cfg_file = tmp_path / "personal-os.yaml"
        cfg_file.write_text(yaml.safe_dump({"exchange_dir": "/test/exchange", "scratch_dir": "/test/scratch"}))
        env = {**os.environ, "HOME": str(tmp_path)}
        # Patch config path via env substitution won't work since the script uses Path.home()
        # Instead we test the dotted key path with a real config in the temp home
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        real_cfg = cfg_dir / "personal-os.yaml"
        real_cfg.write_text(yaml.safe_dump({
            "exchange_dir": "/test/exchange",
            "scratch_dir": "/test/scratch",
        }))
        result = _run_get("exchange_dir", env)
        assert result == "/test/exchange"

    def test_existing_dotted_key_returns_value(self, tmp_path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "personal-os.yaml"
        cfg_file.write_text(yaml.safe_dump({
            "pkos": {"notion_databases": {"inbox": "abc-123"}}
        }))
        env = {**os.environ, "HOME": str(tmp_path)}
        result = _run_get("pkos.notion_databases.inbox", env)
        assert result == "abc-123"

    def test_missing_dotted_key_returns_empty_string(self, tmp_path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "personal-os.yaml"
        cfg_file.write_text(yaml.safe_dump({}))  # empty config
        env = {**os.environ, "HOME": str(tmp_path)}
        result = _run_get("pkos.notion_databases.inbox", env)
        assert result == ""

    def test_partial_path_missing_returns_empty_string(self, tmp_path):
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "personal-os.yaml"
        cfg_file.write_text(yaml.safe_dump({"pkos": {}}))  # pkos key present but no sub-keys
        env = {**os.environ, "HOME": str(tmp_path)}
        result = _run_get("pkos.notion_databases.inbox", env)
        assert result == ""


if __name__ == "__main__":
    # Allow `python3 test_personal_os_config.py` to actually run pytest, not silently exit 0.
    raise SystemExit(pytest.main([__file__, "-v"]))
