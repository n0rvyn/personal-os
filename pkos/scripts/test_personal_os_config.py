"""Unit tests for personal_os_config.py dotted-namespace key lookup.

Tests the three cases: existing flat key, dotted namespace key (existing), dotted namespace key (missing).
Run via: python3 -m pytest test_personal_os_config.py -v
"""
import pytest, subprocess, tempfile, pathlib, yaml, os, sys

SCRIPT = pathlib.Path(__file__).parent / "personal_os_config.py"
sys.path.insert(0, str(SCRIPT.parent))

import personal_os_config as poc  # noqa: E402


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


class TestResolverPath:
    """Phase 2: test the _resolve_config_path() precedence + sentinel logic.

    Every case calls monkeypatch.chdir(tmp_path) FIRST to isolate cwd from any
    stray personal-os.yaml on the test machine's real cwd chain. HOME is also
    patched (via monkeypatch) so that the home fallback resolves to a tmp dir
    we control, not the real ~. The real CONFIG_PATH constant is never touched.
    """

    def test_env_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PERSONAL_OS_ROOT", str(tmp_path))
        marker = tmp_path / "personal-os.yaml"
        marker.write_text(yaml.safe_dump({"exchange_dir": str(tmp_path / "ex")}))
        assert poc._resolve_config_path() == marker

    def test_cwd_walk_finds_marker(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub" / "deep"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        marker = tmp_path / "personal-os.yaml"
        marker.write_text(yaml.safe_dump({"vault": {"root": "."}}))
        assert poc._resolve_config_path() == marker

    def test_home_fallback_when_no_marker(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = pathlib.Path(str(tmp_path)) / ".claude" / "personal-os.yaml"
        assert poc._resolve_config_path() == expected

    def test_home_fallback_byte_identical_to_legacy_load(
        self, monkeypatch, tmp_path
    ):
        """Regression shield: with no env + no marker on cwd chain, load_config()
        must produce byte-identical output to the pre-Phase-2 implementation
        (read home + DEFAULTS merge + flat-key expansion). Namespace blocks and
        _get_dotted lookups must still work unchanged."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        home = tmp_path
        monkeypatch.setenv("HOME", str(home))
        cfg_dir = home / ".claude"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "personal-os.yaml"
        cfg_file.write_text(yaml.safe_dump({
            "exchange_dir": str(tmp_path / "ex"),
            "scratch_dir":  str(tmp_path / "sc"),
            "pkos": {"notion_databases": {"inbox": "abc-123"}},
        }))
        # Direct read + DEFAULTS merge + flat expansion — the pre-Phase-2 logic.
        raw = yaml.safe_load(cfg_file.read_text()) or {}
        legacy = {**poc.DEFAULTS, **raw}
        for k, v in legacy.items():
            if k in poc.DEFAULTS and isinstance(v, str):
                legacy[k] = str(pathlib.Path(os.path.expanduser(v)).resolve())
        cfg = poc.load_config()
        # Full-equivalence shield: load_config output must equal the pre-Phase-2
        # derivation field-for-field, not just on sampled keys.
        assert cfg == legacy
        # Kept as diagnostics (pinpoint which field broke if the dict-eq fails).
        assert cfg["exchange_dir"] == legacy["exchange_dir"]
        assert cfg["scratch_dir"] == legacy["scratch_dir"]
        assert cfg["pkos"] == {"notion_databases": {"inbox": "abc-123"}}
        assert poc._get_dotted(cfg, "pkos.notion_databases.inbox") == "abc-123"
        assert poc._get_dotted(cfg, "pkos.missing.key") == ""

    def test_env_wins_over_cwd_marker(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        # cwd marker
        cwd_marker = tmp_path / "personal-os.yaml"
        cwd_marker.write_text(yaml.safe_dump({"scratch_dir": str(tmp_path / "cwd")}))
        # env points to a different file
        env_dir = tmp_path / "env-root"
        env_dir.mkdir()
        env_marker = env_dir / "personal-os.yaml"
        env_marker.write_text(yaml.safe_dump({"scratch_dir": str(tmp_path / "env")}))
        monkeypatch.setenv("PERSONAL_OS_ROOT", str(env_dir))
        assert poc._resolve_config_path() == env_marker

    def test_cwd_walk_bounded_termination(self, monkeypatch, tmp_path):
        """Deep cwd with no marker on the chain → falls to home in finite steps.

        Path.cwd().parents terminates at filesystem root by construction, so
        bounded termination is structural. This test pins the observable
        contract: returns home constant, no hang, no exception."""
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = pathlib.Path(str(tmp_path)) / ".claude" / "personal-os.yaml"
        # Will resolve in O(depth) steps, returns home.
        assert poc._resolve_config_path() == expected

    def test_sentinel_rejects_collision(self, monkeypatch, tmp_path):
        """A personal-os.yaml on the cwd chain WITHOUT sentinel keys must be
        skipped — otherwise an unrelated same-named file would hijack resolution."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub"
        deep.mkdir()
        # Contains none of exchange_dir/scratch_dir/vault/tts
        collision = tmp_path / "personal-os.yaml"
        collision.write_text(yaml.safe_dump({"foo": 1, "bar": "baz"}))
        monkeypatch.chdir(deep)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = pathlib.Path(str(tmp_path)) / ".claude" / "personal-os.yaml"
        assert poc._resolve_config_path() == expected

    def test_sentinel_fail_soft_on_bad_yaml(self, monkeypatch, tmp_path):
        """A personal-os.yaml on the cwd chain with unparseable YAML must NOT
        raise — fail-soft, skip, continue. Falls to home."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        deep = tmp_path / "sub"
        deep.mkdir()
        broken = tmp_path / "personal-os.yaml"
        broken.write_text(":\n  - [unterminated\n  : :")
        monkeypatch.chdir(deep)
        monkeypatch.setenv("HOME", str(tmp_path))
        expected = pathlib.Path(str(tmp_path)) / ".claude" / "personal-os.yaml"
        assert poc._resolve_config_path() == expected

    def test_sentinel_skips_collision_accepts_higher(self, monkeypatch, tmp_path):
        """Compound sentinel: a collision marker (no known keys) at a DEEPER level
        is skipped, and a valid marker HIGHER on the chain is accepted — the walk
        must not stop at the first same-named file, only at the first VALID one."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PERSONAL_OS_ROOT", raising=False)
        # valid marker at the top
        valid = tmp_path / "personal-os.yaml"
        valid.write_text(yaml.safe_dump({"exchange_dir": str(tmp_path / "ex")}))
        # collision marker one level down (deeper = checked first)
        mid = tmp_path / "mid"
        mid.mkdir()
        (mid / "personal-os.yaml").write_text(yaml.safe_dump({"foo": 1}))
        deep = mid / "deep"
        deep.mkdir()
        monkeypatch.chdir(deep)
        # walk: deep (none) → mid (collision, skipped) → tmp_path (valid, accepted)
        assert poc._resolve_config_path() == valid

    def test_e2e_load_config_consumes_resolved_path(self, monkeypatch, tmp_path):
        """End-to-end: set env, write a unique key into the env-rooted file,
        call load_config() — it must surface that key, proving load_config
        went through _resolve_config_path() (not the home constant)."""
        monkeypatch.chdir(tmp_path)
        env_dir = tmp_path / "proj-root"
        env_dir.mkdir()
        marker = env_dir / "personal-os.yaml"
        marker.write_text(yaml.safe_dump({
            "exchange_dir": str(env_dir / "ex"),
            "scratch_dir":  str(env_dir / "sc"),
            "vault": {"unique_marker_key": "value-from-env-root-9f3a2"},
        }))
        monkeypatch.setenv("PERSONAL_OS_ROOT", str(env_dir))
        # load_config does not write to home; the file exists so it just reads.
        cfg = poc.load_config()
        assert cfg["vault"]["unique_marker_key"] == "value-from-env-root-9f3a2"


if __name__ == "__main__":
    # Allow `python3 test_personal_os_config.py` to actually run pytest, not silently exit 0.
    raise SystemExit(pytest.main([__file__, "-v"]))
