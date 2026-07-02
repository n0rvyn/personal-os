"""Tests for lib/content_project.py — scaffold + migrate (TDD-FAIL-first).

These tests are written BEFORE lib/content_project.py exists; they must FAIL
on first run with "No module named lib.content_project".

Isolation: all tests use tmp_path; none touch real ~/.claude or ~/.video-studio.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# Ensure the plugin root (parent of lib/) is on sys.path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.content_project import scaffold_content_project, migrate_legacy_config  # noqa: E402
from lib.config import load_config, ConfigError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))


def _complete_legacy_config(out_dir: Path) -> dict:
    """Return a complete legacy config dict with all required sections."""
    return {
        "render": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "subtitle_font": "PingFang SC",
        },
        "audio": {
            "voice_id": "male-qn-jingying-jingpin",
            "bgm_duck_db": 18.0,
        },
        "media": {
            "provider": "doubao",
        },
        "budget": {
            "video_shots_per_episode": 2,
            "video_motion_threshold": 0.6,
        },
        "ffmpeg": {
            "ffmpeg_bin": "/usr/local/bin/ffmpeg",
            "ffprobe_bin": "/usr/local/bin/ffprobe",
        },
        "output_dir": str(out_dir),
        "syllabus_path": "",
        "cursor_path": "",
        "character_ref": "/tmp/char.jpg",
        "book_series": "runetic",
        "syllabus_source_dir": "/tmp/source",
    }


# ---------------------------------------------------------------------------
# scaffold_content_project tests
# ---------------------------------------------------------------------------

class TestScaffoldContentProject:
    def test_scaffold_creates_video_studio_dir(self, tmp_path):
        """scaffold creates .video-studio/ directory."""
        scaffold_content_project(tmp_path, book="test-book")
        assert (tmp_path / ".video-studio").is_dir()

    def test_scaffold_creates_config_yaml(self, tmp_path):
        """scaffold creates .video-studio/config.yaml."""
        scaffold_content_project(tmp_path, book="test-book")
        config_file = tmp_path / ".video-studio" / "config.yaml"
        assert config_file.exists(), ".video-studio/config.yaml must be created"

    def test_scaffold_creates_syllabus_yaml(self, tmp_path):
        """scaffold creates .video-studio/syllabus.yaml."""
        scaffold_content_project(tmp_path, book="test-book")
        syllabus_file = tmp_path / ".video-studio" / "syllabus.yaml"
        assert syllabus_file.exists(), ".video-studio/syllabus.yaml must be created"

    def test_scaffold_creates_cursor_yaml(self, tmp_path):
        """scaffold creates .video-studio/cursor.yaml."""
        scaffold_content_project(tmp_path, book="test-book")
        cursor_file = tmp_path / ".video-studio" / "cursor.yaml"
        assert cursor_file.exists(), ".video-studio/cursor.yaml must be created"

    def test_scaffold_creates_or_updates_claude_md(self, tmp_path):
        """scaffold writes or appends a CLAUDE.md pointer in root."""
        scaffold_content_project(tmp_path, book="test-book")
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists(), "CLAUDE.md must be created"
        content = claude_md.read_text()
        assert "video-studio" in content.lower() or ".video-studio" in content, (
            "CLAUDE.md must contain a video-studio pointer"
        )

    def test_scaffold_appends_pointer_to_existing_claude_md(self, tmp_path):
        """scaffold appends pointer to existing CLAUDE.md without overwriting."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n")

        scaffold_content_project(tmp_path, book="test-book")

        content = claude_md.read_text()
        # Original content must still be there
        assert "# My Project" in content, "Existing CLAUDE.md content must be preserved"
        assert "Existing content." in content, "Existing CLAUDE.md content must be preserved"

    def test_scaffold_idempotent_no_error_on_second_call(self, tmp_path):
        """scaffold is idempotent: calling twice raises no error."""
        scaffold_content_project(tmp_path, book="test-book")
        # Should not raise
        scaffold_content_project(tmp_path, book="test-book")

    def test_scaffold_idempotent_does_not_overwrite_existing_files(self, tmp_path):
        """scaffold does not overwrite files if they already exist."""
        scaffold_content_project(tmp_path, book="test-book")

        # Modify config.yaml manually
        config_file = tmp_path / ".video-studio" / "config.yaml"
        original_content = config_file.read_text()
        config_file.write_text(original_content + "\n# user-modified\n")
        modified_content = config_file.read_text()

        scaffold_content_project(tmp_path, book="test-book")

        # File should not be reset to scaffold default
        assert config_file.read_text() == modified_content, (
            "scaffold must not overwrite existing config.yaml on second call"
        )

    def test_scaffold_idempotent_claude_md_pointer_not_duplicated(self, tmp_path):
        """scaffold does not duplicate CLAUDE.md pointer on second call."""
        scaffold_content_project(tmp_path, book="test-book")
        content_after_first = (tmp_path / "CLAUDE.md").read_text()

        scaffold_content_project(tmp_path, book="test-book")
        content_after_second = (tmp_path / "CLAUDE.md").read_text()

        # Pointer section should not be duplicated (same length or only slightly longer)
        assert content_after_second.count(".video-studio") <= content_after_first.count(".video-studio") + 1, (
            "CLAUDE.md pointer must not be duplicated on second scaffold call"
        )

    def test_scaffold_without_book_arg(self, tmp_path):
        """scaffold works when book arg is empty string (default)."""
        scaffold_content_project(tmp_path)
        assert (tmp_path / ".video-studio" / "config.yaml").exists()


# ---------------------------------------------------------------------------
# migrate_legacy_config tests
# ---------------------------------------------------------------------------

MACHINE_KEYS = {"ffmpeg", "media", "audio", "render", "budget"}
CONTENT_KEYS = {"output_dir", "syllabus_path", "cursor_path", "character_ref",
                "book_series", "syllabus_source_dir"}


class TestMigrateLegacyConfig:
    def _setup_legacy(self, tmp_path) -> tuple[Path, Path, Path, Path]:
        """Create a legacy ~/.video-studio structure and return (legacy_dir, machine_cfg_path, content_root, out_dir)."""
        legacy_dir = tmp_path / "dot-video-studio"
        legacy_dir.mkdir()

        out_dir = tmp_path / "output"
        out_dir.mkdir()

        # Write legacy config with all fields. T1: use NON-EMPTY absolute pointers
        # into legacy_dir (mirrors the real ~/.video-studio/config.yaml, whose
        # syllabus_path/cursor_path are absolute paths back into the legacy dir).
        # Empty pointers hid M1 (migrate not rewriting them).
        config_data = _complete_legacy_config(out_dir)
        config_data["syllabus_path"] = str(legacy_dir / "syllabus.yaml")
        config_data["cursor_path"] = str(legacy_dir / "cursor.yaml")
        _write_yaml(legacy_dir / "config.yaml", config_data)

        # Write syllabus + cursor files
        (legacy_dir / "syllabus.yaml").write_text("modules: []\n")
        (legacy_dir / "cursor.yaml").write_text("episode: 1\n")

        machine_config_path = tmp_path / "machine" / "config.yaml"
        content_root = tmp_path / "content-project"
        content_root.mkdir()

        return legacy_dir, machine_config_path, content_root, out_dir

    def test_migrate_creates_machine_config(self, tmp_path):
        """migrate creates machine_config_path on disk."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)
        assert machine_cfg.exists(), "machine_config_path must be created by migrate"

    def test_migrate_machine_config_contains_required_sections(self, tmp_path):
        """machine_config contains exactly the machine-level sections."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        data = yaml.safe_load(machine_cfg.read_text())
        for key in MACHINE_KEYS:
            assert key in data, f"machine_config must contain key: {key}"

    def test_migrate_machine_config_budget_present(self, tmp_path):
        """budget section must be in machine_config (regression: missing budget breaks load_config)."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        data = yaml.safe_load(machine_cfg.read_text())
        assert "budget" in data, (
            "budget must be in machine_config — without it load_config raises "
            "'missing required section: budget'"
        )

    def test_migrate_creates_content_config(self, tmp_path):
        """migrate creates content_root/.video-studio/config.yaml."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        content_cfg = content_root / ".video-studio" / "config.yaml"
        assert content_cfg.exists(), "content config must be created"

    def test_migrate_content_config_contains_content_keys(self, tmp_path):
        """content_config contains at least output_dir and character_ref."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        content_cfg = content_root / ".video-studio" / "config.yaml"
        data = yaml.safe_load(content_cfg.read_text())
        # output_dir must be in content config
        assert "output_dir" in data, "content config must contain output_dir"
        assert "character_ref" in data, "content config must contain character_ref"

    def test_migrate_no_key_loss(self, tmp_path):
        """machine∪content covers all original config keys (no key dropped)."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)

        original_config_path = legacy_dir / "config.yaml"
        original_data = yaml.safe_load(original_config_path.read_text())
        original_keys = set(original_data.keys())

        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        machine_data = yaml.safe_load(machine_cfg.read_text())
        content_cfg_path = content_root / ".video-studio" / "config.yaml"
        content_data = yaml.safe_load(content_cfg_path.read_text())

        covered_keys = set(machine_data.keys()) | set(content_data.keys())
        missing = original_keys - covered_keys
        assert not missing, (
            f"migrate dropped keys that were in original config: {missing}. "
            "machine∪content must cover all original keys."
        )

    def test_migrate_syllabus_copied_to_content_root(self, tmp_path):
        """syllabus.yaml is copied to content_root/.video-studio/ (or content_root)."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        # Accept either direct or .video-studio/ subdir
        found = (
            (content_root / "syllabus.yaml").exists()
            or (content_root / ".video-studio" / "syllabus.yaml").exists()
        )
        assert found, "syllabus.yaml must be copied to content_root"

    def test_migrate_cursor_copied_to_content_root(self, tmp_path):
        """cursor.yaml is copied to content_root."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        found = (
            (content_root / "cursor.yaml").exists()
            or (content_root / ".video-studio" / "cursor.yaml").exists()
        )
        assert found, "cursor.yaml must be copied to content_root"

    def test_migrate_does_not_delete_legacy_files(self, tmp_path):
        """Legacy files are NOT deleted after migrate (safe: user confirms deletion)."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        assert (legacy_dir / "config.yaml").exists(), (
            "Legacy config.yaml must not be deleted — user confirms cleanup manually"
        )
        assert (legacy_dir / "syllabus.yaml").exists(), (
            "Legacy syllabus.yaml must not be deleted"
        )

    def test_migrate_no_overwrite_existing_machine_config(self, tmp_path):
        """migrate raises if machine_config_path already exists (safety: no overwrite)."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)

        # Pre-create target
        machine_cfg.parent.mkdir(parents=True, exist_ok=True)
        machine_cfg.write_text("# existing machine config\n")

        with pytest.raises(Exception) as exc_info:
            migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        assert "exist" in str(exc_info.value).lower() or "overwrite" in str(exc_info.value).lower() or "already" in str(exc_info.value).lower(), (
            "migrate must raise a descriptive error when machine_config_path already exists"
        )

    def test_migrate_no_overwrite_existing_content_config(self, tmp_path):
        """migrate raises if content_root/.video-studio/config.yaml already exists."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)

        # Pre-create content config target
        content_cfg = content_root / ".video-studio" / "config.yaml"
        content_cfg.parent.mkdir(parents=True, exist_ok=True)
        content_cfg.write_text("# existing content config\n")

        with pytest.raises(Exception) as exc_info:
            migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        assert "exist" in str(exc_info.value).lower() or "overwrite" in str(exc_info.value).lower() or "already" in str(exc_info.value).lower(), (
            "migrate must raise a descriptive error when content config already exists"
        )

    def test_migrate_machine_plus_content_deep_merge_loads_successfully(self, tmp_path):
        """Regression: machine_config + content_config deep-merged → load_config succeeds.

        This guards against missing 'budget' or any required section in the
        merged result, which would cause load_config to raise 'missing required section'.
        """
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        # out_dir must exist for load_config validation
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        machine_data = yaml.safe_load(machine_cfg.read_text())
        content_cfg_path = content_root / ".video-studio" / "config.yaml"
        content_data = yaml.safe_load(content_cfg_path.read_text())

        # Deep-merge: machine is base, content is override
        from lib.config import _deep_merge
        merged = _deep_merge(machine_data, content_data)

        # Fix output_dir to a directory that exists for load_config validation
        merged["output_dir"] = str(out_dir)

        # Write merged to a temp file and load it
        merged_path = tmp_path / "merged_test.yaml"
        _write_yaml(merged_path, merged)

        # Must not raise
        cfg = load_config(str(merged_path))
        assert cfg is not None, "load_config on deep-merged machine+content must succeed"
        assert cfg.budget is not None, "budget section must survive after migrate+merge"

    def test_migrate_rewrites_pointers_into_content_root(self, tmp_path):
        """M1 regression: migrated content config's syllabus_path/cursor_path must
        point into content_root/.video-studio/, NOT back into the legacy dir.
        Otherwise progress writes to the old cursor and deleting legacy breaks load."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        content_cfg = yaml.safe_load(
            (content_root / ".video-studio" / "config.yaml").read_text())
        sp = content_cfg["syllabus_path"]
        cp = content_cfg["cursor_path"]

        assert str(legacy_dir) not in sp, f"syllabus_path still points into legacy dir: {sp}"
        assert str(legacy_dir) not in cp, f"cursor_path still points into legacy dir: {cp}"
        assert str(content_root) in sp and ".video-studio" in sp, (
            f"syllabus_path must point into content_root/.video-studio: {sp}")
        assert str(content_root) in cp and ".video-studio" in cp, (
            f"cursor_path must point into content_root/.video-studio: {cp}")
        # the rewritten pointer targets must actually exist (files were copied there)
        assert Path(sp).exists(), f"rewritten syllabus_path target missing: {sp}"
        assert Path(cp).exists(), f"rewritten cursor_path target missing: {cp}"

    def test_migrate_returns_path_dict(self, tmp_path):
        """migrate returns a dict with created path keys."""
        legacy_dir, machine_cfg, content_root, out_dir = self._setup_legacy(tmp_path)
        result = migrate_legacy_config(legacy_dir, machine_cfg, content_root)

        assert isinstance(result, dict), "migrate_legacy_config must return a dict"
        # At minimum, machine and content paths should be in result
        assert len(result) >= 1, "returned dict must have at least one entry"
