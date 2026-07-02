"""Config resolver tests for video-studio (test-FAIL-first contract — written
before lib/config.py exists; collection must fail at import time).

Import surface: `from lib.config import load_config` invoked from the
plugin root (parent of `lib/`).
"""
from __future__ import annotations

import dataclasses
import sys
import textwrap
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.config import load_config` resolves.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.config import load_config  # noqa: E402
try:
    from lib.config import ConfigNotFound  # noqa: E402  # present after Task 1-impl
except ImportError:
    ConfigNotFound = None  # type: ignore[assignment,misc]


# ---------- helpers ----------

def _write_config(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body))


def _make_out_dir(root: Path) -> Path:
    out = root / "output"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _minimal_yaml(out_dir: Path, **overrides) -> str:
    """Return a minimal valid YAML string for video-studio config."""
    data = {
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
        },
        "ffmpeg": {
            "ffmpeg_bin": "/usr/local/bin/ffmpeg",
            "ffprobe_bin": "/usr/local/bin/ffprobe",
        },
        "character_ref": "/tmp/character-ref-placeholder.jpg",
        "style": "cinematic",
        "output_dir": str(out_dir),
    }
    data.update(overrides)
    # Build YAML manually (no PyYAML dependency in test helpers).
    lines = []
    for section, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{section}:")
            for k, v in value.items():
                if isinstance(v, str):
                    lines.append(f"  {k}: {v}")
                else:
                    lines.append(f"  {k}: {v}")
        else:
            if isinstance(value, str):
                lines.append(f"{section}: {value}")
            else:
                lines.append(f"{section}: {value}")
    return "\n".join(lines) + "\n"


# ---------- valid config: load + field values ----------

def test_valid_config_loads(tmp_path):
    """A complete valid YAML loads without error."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg is not None


def test_valid_config_render_fields(tmp_path):
    """Render section values are accessible."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg.render.width == 1920
    assert cfg.render.height == 1080
    assert cfg.render.fps == 30
    assert cfg.render.subtitle_font == "PingFang SC"


def test_valid_config_audio_fields(tmp_path):
    """Audio section values are accessible."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg.audio.voice_id == "male-qn-jingying-jingpin"
    assert cfg.audio.bgm_duck_db == 18.0


def test_valid_config_media_provider(tmp_path):
    """media.provider is doubao (from YAML)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg.media.provider == "doubao"


def test_valid_config_output_dir(tmp_path):
    """output_dir resolves to the path given."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg.output_dir == str(out)


# ---------- derived subdirs: episodes / syllabus / state ----------

def test_derived_subdirs_are_created(tmp_path):
    """episodes_dir, syllabus_dir, state_dir derived from output_dir and auto-created."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))

    for attr in ("episodes_dir", "syllabus_dir", "state_dir"):
        subdir_path = getattr(cfg, attr)
        assert subdir_path is not None, f"{attr} must not be None"
        assert Path(subdir_path).exists(), f"{attr} must be created on disk: {subdir_path}"
        assert Path(subdir_path).is_dir(), f"{attr} must be a directory: {subdir_path}"


def test_derived_subdirs_live_under_output_dir(tmp_path):
    """Derived subdirs sit under output_dir."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))

    for attr in ("episodes_dir", "syllabus_dir", "state_dir"):
        subdir_path = Path(getattr(cfg, attr))
        assert subdir_path.parent == out, (
            f"{attr} must be a direct child of output_dir, got {subdir_path}"
        )


# ---------- fail-closed: missing required keys ----------

def test_missing_output_dir_key_raises_naming_key(tmp_path):
    """Missing output_dir key raises ConfigError naming 'output_dir'."""
    cfg_path = tmp_path / "video-studio.yaml"
    cfg_path.write_text(textwrap.dedent("""
        render:
          width: 1920
          height: 1080
          fps: 30
          subtitle_font: PingFang SC
        audio:
          voice_id: male-qn-jingying-jingpin
          bgm_duck_db: 18.0
        media:
          provider: doubao
        budget:
          video_shots_per_episode: 2
        ffmpeg:
          ffmpeg_bin: /usr/local/bin/ffmpeg
          ffprobe_bin: /usr/local/bin/ffprobe
        character_ref: /tmp/char.jpg
        style: cinematic
    """))

    with pytest.raises(Exception) as exc:
        load_config(str(cfg_path))
    assert "output_dir" in str(exc.value)


def test_nonexistent_output_dir_raises(tmp_path):
    """output_dir path not on disk raises (fail-closed)."""
    missing_out = tmp_path / "no-such-dir"
    # Do NOT create missing_out
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(missing_out))

    with pytest.raises(Exception) as exc:
        load_config(str(cfg_path))
    assert "output_dir" in str(exc.value)


def test_nonexistent_output_dir_not_auto_created(tmp_path):
    """fail-closed: missing output_dir must NOT be created as a side-effect."""
    missing_out = tmp_path / "no-such-dir"
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(missing_out))

    with pytest.raises(Exception):
        load_config(str(cfg_path))
    assert not missing_out.exists(), "fail-closed violated: output_dir was created by subdir mkdir"


def test_invalid_media_provider_raises(tmp_path):
    """media.provider not in {doubao, minimax} raises ConfigError."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    # Write config with bad provider
    yaml_text = _minimal_yaml(out)
    yaml_text = yaml_text.replace("provider: doubao", "provider: openai")
    _write_config(cfg_path, yaml_text)

    with pytest.raises(Exception) as exc:
        load_config(str(cfg_path))
    assert "provider" in str(exc.value)


# ---------- lenient: character_ref not validated for existence ----------

def test_nonexistent_character_ref_still_loads(tmp_path):
    """character_ref path that doesn't exist is OK at load time (Stage 0/Phase 6 artifact)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    yaml_text = _minimal_yaml(out)
    # Override character_ref to a path that definitely doesn't exist
    yaml_text = yaml_text.replace(
        "character_ref: /tmp/character-ref-placeholder.jpg",
        "character_ref: /tmp/does-not-exist-at-all-12345.jpg",
    )
    _write_config(cfg_path, yaml_text)

    # Should NOT raise
    cfg = load_config(str(cfg_path))
    assert cfg is not None


# ---------- cfg.style retirement (Phase 1 — visual decoupling) ----------

def test_style_field_not_on_config(tmp_path):
    """Config object must NOT have a `style` attribute after retirement.

    Art-style is now carried by syllabus module.art_style; cfg.style is gone.
    """
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert not hasattr(cfg, "style"), (
        "cfg.style must be retired; art-style is now module.art_style in syllabus"
    )


def test_config_with_style_key_loads_without_error(tmp_path):
    """A legacy config.yaml that still has a `style:` key must load without raising.

    Backward-compatibility: load_config silently ignores unknown/retired keys.
    The returned Config must NOT expose cfg.style.
    """
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    # _minimal_yaml already includes `style: cinematic` — use it directly
    _write_config(cfg_path, _minimal_yaml(out))

    # Must not raise despite the legacy `style:` key being present in YAML
    cfg = load_config(str(cfg_path))
    assert cfg is not None
    assert not hasattr(cfg, "style"), (
        "retired `style` key in YAML must be silently ignored; cfg must not expose it"
    )


# ---------- defaults ----------

def test_media_provider_defaults_to_doubao(tmp_path):
    """When media.provider is omitted, it defaults to 'doubao'."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    cfg_path.write_text(textwrap.dedent(f"""
        render:
          width: 1920
          height: 1080
          fps: 30
          subtitle_font: PingFang SC
        audio:
          voice_id: male-qn-jingying-jingpin
          bgm_duck_db: 18.0
        media: {{}}
        budget:
          video_shots_per_episode: 2
        ffmpeg:
          ffmpeg_bin: /usr/local/bin/ffmpeg
          ffprobe_bin: /usr/local/bin/ffprobe
        character_ref: /tmp/char.jpg
        style: cinematic
        output_dir: {out}
    """))

    cfg = load_config(str(cfg_path))
    assert cfg.media.provider == "doubao"


def test_audio_bgm_duck_db_defaults_to_18(tmp_path):
    """When audio.bgm_duck_db is omitted, it defaults to 18.0."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    cfg_path.write_text(textwrap.dedent(f"""
        render:
          width: 1920
          height: 1080
          fps: 30
          subtitle_font: PingFang SC
        audio:
          voice_id: male-qn-jingying-jingpin
        media:
          provider: doubao
        budget:
          video_shots_per_episode: 2
        ffmpeg:
          ffmpeg_bin: /usr/local/bin/ffmpeg
          ffprobe_bin: /usr/local/bin/ffprobe
        character_ref: /tmp/char.jpg
        style: cinematic
        output_dir: {out}
    """))

    cfg = load_config(str(cfg_path))
    assert cfg.audio.bgm_duck_db == 18.0


# ---------- frozen dataclass ----------

def test_config_is_frozen_dataclass(tmp_path):
    """load_config returns a frozen dataclass (is_dataclass + FrozenInstanceError on mutation)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))

    assert dataclasses.is_dataclass(cfg), "returned object must be a dataclass"

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        cfg.output_dir = "/tmp/mutated"  # type: ignore[misc]


def test_sub_config_is_frozen_dataclass(tmp_path):
    """Sub-configs (render, audio, media) are also frozen dataclasses."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))

    for sub in (cfg.render, cfg.audio, cfg.media):
        assert dataclasses.is_dataclass(sub), f"{sub!r} must be a dataclass"

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        cfg.render.width = 640  # type: ignore[misc]


# ---------- env var resolution ----------

def test_video_studio_config_env_var_used(tmp_path, monkeypatch):
    """VIDEO_STUDIO_CONFIG env var is used when no explicit path given."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))

    monkeypatch.setenv("VIDEO_STUDIO_CONFIG", str(cfg_path))

    cfg = load_config()
    assert cfg.output_dir == str(out)


# ---------- budget.video_motion_threshold (optional key, DP-001=C) ----------

def test_budget_motion_threshold_missing_defaults_to_0_6(tmp_path):
    """budget.video_motion_threshold is optional; missing → defaults to 0.6.

    Pre-existing user configs that don't have this key must still load
    without ConfigError (fail-open on optional keys).
    """
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    # _minimal_yaml sets budget.video_shots_per_episode=2 but no video_motion_threshold
    _write_config(cfg_path, _minimal_yaml(out))

    cfg = load_config(str(cfg_path))
    assert cfg.budget.video_motion_threshold == 0.6


def test_budget_motion_threshold_explicit_value(tmp_path):
    """budget.video_motion_threshold explicit value is parsed correctly."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    yaml_text = _minimal_yaml(out)
    # Inject video_motion_threshold under the budget section
    yaml_text = yaml_text.replace(
        "  video_shots_per_episode: 2",
        "  video_shots_per_episode: 2\n  video_motion_threshold: 0.75",
    )
    _write_config(cfg_path, yaml_text)

    cfg = load_config(str(cfg_path))
    assert cfg.budget.video_motion_threshold == 0.75


# ---------- budget.video_floor (optional key, video 保底安全网) ----------

def test_budget_video_floor_missing_defaults_to_1(tmp_path):
    """budget.video_floor is optional; missing → defaults to 1 (每期保底 1 段视频)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))
    cfg = load_config(str(cfg_path))
    assert cfg.budget.video_floor == 1


def test_budget_video_floor_explicit_value(tmp_path):
    """budget.video_floor explicit value is parsed correctly."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    yaml_text = _minimal_yaml(out).replace(
        "  video_shots_per_episode: 2",
        "  video_shots_per_episode: 2\n  video_floor: 2",
    )
    _write_config(cfg_path, yaml_text)
    cfg = load_config(str(cfg_path))
    assert cfg.budget.video_floor == 2


# ---------- host persona (optional section; configurable, not hardcoded) ----------

def test_host_missing_defaults_to_empty(tmp_path):
    """No host section → cfg.host has empty name/channel/tone (no plugin default)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(out))
    cfg = load_config(str(cfg_path))
    assert cfg.host.name == ""
    assert cfg.host.channel == ""
    assert cfg.host.tone == ""


def test_host_parsed_from_config(tmp_path):
    """host section is parsed into cfg.host (name/channel/tone)."""
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    _write_config(cfg_path, _minimal_yaml(
        out, host={"name": "卞旸", "channel": "跑者学堂", "tone": "亲切"}))
    cfg = load_config(str(cfg_path))
    assert cfg.host.name == "卞旸"
    assert cfg.host.channel == "跑者学堂"
    assert cfg.host.tone == "亲切"


# ---------- config discovery: cascade + deep-merge + ConfigNotFound ----------
#
# These tests are written BEFORE Task 1-impl (TDD-FAIL-first).
# They fail until _find_project_root / _discover_config_layers / _deep_merge
# and ConfigNotFound are added to lib/config.py (Task 1-impl).


def _write_yaml_file(path: Path, content: str) -> None:
    """Write dedented YAML content to path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def _complete_yaml(out_dir: Path,
                   voice_id: str = "male-qn-jingying-jingpin",
                   provider: str = "doubao",
                   bgm_duck_db: float = 18.0,
                   width: int = 1920,
                   fps: int = 30,
                   ffmpeg_bin: str = "/usr/local/bin/ffmpeg",
                   ffprobe_bin: str = "/usr/local/bin/ffprobe",
                   shots: int = 2) -> str:
    """Return a complete, valid YAML config string."""
    return textwrap.dedent(f"""
        render:
          width: {width}
          height: 1080
          fps: {fps}
          subtitle_font: PingFang SC
        audio:
          voice_id: {voice_id}
          bgm_duck_db: {bgm_duck_db}
        media:
          provider: {provider}
        budget:
          video_shots_per_episode: {shots}
        ffmpeg:
          ffmpeg_bin: {ffmpeg_bin}
          ffprobe_bin: {ffprobe_bin}
        character_ref: /tmp/char.jpg
        output_dir: {out_dir}
    """)


def test_config_not_found_class_exists():
    """ConfigNotFound must be defined in lib.config (fails pre-impl)."""
    assert ConfigNotFound is not None, (
        "ConfigNotFound is not defined in lib.config — add it in Task 1-impl"
    )


def test_config_not_found_is_config_error_subclass():
    """ConfigNotFound must be a subclass of ConfigError."""
    assert ConfigNotFound is not None, "ConfigNotFound not defined yet"
    from lib.config import ConfigError
    assert issubclass(ConfigNotFound, ConfigError), (
        "ConfigNotFound must subclass ConfigError so existing except ConfigError handlers catch it"
    )


def test_discovery_uses_project_root_config(tmp_path, monkeypatch):
    """load_config() with no args finds <project_root>/.video-studio/config.yaml."""
    home = tmp_path / "home"
    home.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    # Marker file that _find_project_root recognises
    (project_root / "CLAUDE.md").write_text("# project marker\n")

    out = project_root / "output"
    out.mkdir()

    _write_yaml_file(
        project_root / ".video-studio" / "config.yaml",
        _complete_yaml(out, voice_id="project-voice", shots=3),
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("VIDEO_STUDIO_CONFIG", raising=False)

    cfg = load_config()
    assert cfg.audio.voice_id == "project-voice"
    assert cfg.budget.video_shots_per_episode == 3


def test_discovery_deep_merges_user_and_project(tmp_path, monkeypatch):
    """Deep merge: project overrides at leaf level; user fills gaps.

    Discriminating test for DEEP merge (recursive) vs shallow merge:
    - User-level has audio.voice_id + audio.bgm_duck_db
    - Project overrides ONLY audio.bgm_duck_db (no voice_id key)
    - Shallow merge replaces entire audio block → voice_id dropped → validation fails
    - Deep merge preserves audio.voice_id from user + takes bgm_duck_db from project
    """
    home = tmp_path / "home"
    home.mkdir()
    machine_out = home / "output"
    machine_out.mkdir()

    # User-level (base): complete machine config, provider=minimax, voice=machine-voice
    _write_yaml_file(
        home / ".claude" / "video-studio" / "config.yaml",
        _complete_yaml(
            machine_out,
            voice_id="machine-voice",
            provider="minimax",
            bgm_duck_db=18.0,
        ),
    )

    # Project (override): only overrides media.provider and audio.bgm_duck_db;
    # does NOT supply ffmpeg section → ffmpeg must come from user-level via merge.
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# marker\n")
    project_out = project_root / "output"
    project_out.mkdir()

    _write_yaml_file(
        project_root / ".video-studio" / "config.yaml",
        textwrap.dedent(f"""
            media:
              provider: doubao
            audio:
              bgm_duck_db: 25.0
            output_dir: {project_out}
            character_ref: /tmp/project-char.jpg
        """),
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("VIDEO_STUDIO_CONFIG", raising=False)

    cfg = load_config()

    # project overrides media.provider leaf
    assert cfg.media.provider == "doubao", "project media.provider must override user"
    # deep merge: project sets audio.bgm_duck_db but NOT audio.voice_id
    assert cfg.audio.bgm_duck_db == 25.0, "project audio.bgm_duck_db must override"
    assert cfg.audio.voice_id == "machine-voice", (
        "audio.voice_id must survive from user-level (deep merge, not shallow)"
    )
    # ffmpeg entirely absent from project → comes from user-level
    assert cfg.ffmpeg.ffmpeg_bin == "/usr/local/bin/ffmpeg", (
        "ffmpeg must come from user-level when project config omits it"
    )
    # project output_dir wins
    assert cfg.output_dir == str(project_out)


def test_discovery_no_configs_raises_config_not_found(tmp_path, monkeypatch):
    """No config anywhere (no user-level, no project-level) → ConfigNotFound."""
    assert ConfigNotFound is not None, "ConfigNotFound not defined yet"

    home = tmp_path / "home"
    home.mkdir()
    # No .claude/video-studio/config.yaml under home

    project_root = tmp_path / "project"
    project_root.mkdir()
    # No CLAUDE.md → _find_project_root returns None
    # No .video-studio/config.yaml

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("VIDEO_STUDIO_CONFIG", raising=False)

    with pytest.raises(ConfigNotFound):
        load_config()


def test_explicit_missing_path_raises_config_error_not_file_not_found_error(tmp_path):
    """Explicit path that doesn't exist → ConfigError (not FileNotFoundError, advisory #3)."""
    from lib.config import ConfigError
    missing = tmp_path / "no-such-config.yaml"

    with pytest.raises(ConfigError):
        load_config(str(missing))

    # Specifically must NOT surface as FileNotFoundError
    try:
        load_config(str(missing))
    except FileNotFoundError:
        raise AssertionError(
            "load_config must wrap FileNotFoundError in ConfigError — got raw FileNotFoundError"
        )
    except Exception:
        pass  # Any other exception (ConfigError or subclass) is acceptable


def test_explicit_path_overrides_project_config(tmp_path, monkeypatch):
    """Explicit load_config(path) wins over project-level discovery."""
    home = tmp_path / "home"
    home.mkdir()

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# marker\n")
    project_out = project_root / "output"
    project_out.mkdir()

    _write_yaml_file(
        project_root / ".video-studio" / "config.yaml",
        _complete_yaml(project_out, voice_id="project-voice", provider="doubao"),
    )

    explicit_out = tmp_path / "explicit_out"
    explicit_out.mkdir()
    explicit_cfg = tmp_path / "explicit.yaml"
    _write_yaml_file(
        explicit_cfg,
        _complete_yaml(explicit_out, voice_id="explicit-voice", provider="minimax", width=1280),
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("VIDEO_STUDIO_CONFIG", raising=False)

    cfg = load_config(str(explicit_cfg))
    assert cfg.audio.voice_id == "explicit-voice"
    assert cfg.media.provider == "minimax"
    assert cfg.render.width == 1280


def test_env_var_overrides_project_config(tmp_path, monkeypatch):
    """VIDEO_STUDIO_CONFIG env var overrides project-level discovery."""
    home = tmp_path / "home"
    home.mkdir()

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# marker\n")
    project_out = project_root / "output"
    project_out.mkdir()

    _write_yaml_file(
        project_root / ".video-studio" / "config.yaml",
        _complete_yaml(project_out, voice_id="project-voice", provider="doubao"),
    )

    env_out = tmp_path / "env_out"
    env_out.mkdir()
    env_cfg = tmp_path / "env.yaml"
    _write_yaml_file(
        env_cfg,
        _complete_yaml(env_out, voice_id="env-voice", provider="minimax"),
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("VIDEO_STUDIO_CONFIG", str(env_cfg))

    cfg = load_config()
    assert cfg.audio.voice_id == "env-voice"
    assert cfg.media.provider == "minimax"


def test_budget_video_floor_exceeding_cap_raises(tmp_path):
    """R2 fix #3: floor > cap is contradictory (min > max) → ConfigError at load,
    not a silent cap violation at render time."""
    import pytest
    from lib.config import ConfigError
    out = _make_out_dir(tmp_path)
    cfg_path = tmp_path / "video-studio.yaml"
    yaml_text = _minimal_yaml(out).replace(
        "  video_shots_per_episode: 2",
        "  video_shots_per_episode: 2\n  video_floor: 5",
    )
    _write_config(cfg_path, yaml_text)
    with pytest.raises(ConfigError, match="video_floor"):
        load_config(str(cfg_path))
