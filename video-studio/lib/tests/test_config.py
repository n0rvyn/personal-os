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

from lib.config import load_config  # noqa: E402 (test-FAIL-first expects this to fail pre-impl)


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


# ---------- lenient: character_ref / style not validated for existence ----------

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
