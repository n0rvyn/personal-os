"""video-studio config resolver.

Single source of truth for the video-studio YAML config file. Used by all
pipeline steps and runner scripts via `from lib.config import load_config`.

Fails-closed: missing file / missing required key / nonexistent output_dir all
raise ConfigError naming the offending key. `character_ref` and `style` are
lenient (Stage 0 / Phase 6 artifacts; not existence-checked at load time).

API credentials (ARK_API_KEY, MINIMAX_API_KEY, etc.) live in shell env,
never in this YAML.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the config is missing, malformed, or fails validation."""


VALID_MEDIA_PROVIDERS = frozenset({"doubao", "minimax"})


# ---------------------------------------------------------------------------
# Frozen sub-config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderConfig:
    width: int
    height: int
    fps: int
    subtitle_font: str


@dataclass(frozen=True)
class AudioConfig:
    voice_id: str
    bgm_duck_db: float = 18.0


@dataclass(frozen=True)
class MediaConfig:
    provider: str = "doubao"


@dataclass(frozen=True)
class BudgetConfig:
    video_shots_per_episode: int


@dataclass(frozen=True)
class FfmpegConfig:
    ffmpeg_bin: str
    ffprobe_bin: str


@dataclass(frozen=True)
class VideoStudioConfig:
    render: RenderConfig
    audio: AudioConfig
    media: MediaConfig
    budget: BudgetConfig
    ffmpeg: FfmpegConfig
    character_ref: str
    style: str
    output_dir: str
    # derived subdirs — computed from output_dir, auto-created on load
    episodes_dir: str
    syllabus_dir: str
    state_dir: str


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_config_path(path: str | os.PathLike | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("VIDEO_STUDIO_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / "video-studio.yaml"


# ---------------------------------------------------------------------------
# YAML reading
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError(f"config {path} root must be a mapping")
        return loaded
    except ImportError:
        raise ConfigError(
            "PyYAML is required: pip install pyyaml"
        )


# ---------------------------------------------------------------------------
# Section validators
# ---------------------------------------------------------------------------


def _validate_render(raw: dict[str, Any]) -> RenderConfig:
    required = ("width", "height", "fps", "subtitle_font")
    for key in required:
        if key not in raw:
            raise ConfigError(f"missing required key: render.{key}")
    return RenderConfig(
        width=int(raw["width"]),
        height=int(raw["height"]),
        fps=int(raw["fps"]),
        subtitle_font=str(raw["subtitle_font"]),
    )


def _validate_audio(raw: dict[str, Any]) -> AudioConfig:
    if "voice_id" not in raw:
        raise ConfigError("missing required key: audio.voice_id")
    return AudioConfig(
        voice_id=str(raw["voice_id"]),
        bgm_duck_db=float(raw.get("bgm_duck_db", 18.0)),
    )


def _validate_media(raw: dict[str, Any]) -> MediaConfig:
    provider = str(raw.get("provider", "doubao"))
    if provider not in VALID_MEDIA_PROVIDERS:
        raise ConfigError(
            f"media.provider must be one of {sorted(VALID_MEDIA_PROVIDERS)}, got: {provider!r}"
        )
    return MediaConfig(provider=provider)


def _validate_budget(raw: dict[str, Any]) -> BudgetConfig:
    if "video_shots_per_episode" not in raw:
        raise ConfigError("missing required key: budget.video_shots_per_episode")
    return BudgetConfig(video_shots_per_episode=int(raw["video_shots_per_episode"]))


def _validate_ffmpeg(raw: dict[str, Any]) -> FfmpegConfig:
    for key in ("ffmpeg_bin", "ffprobe_bin"):
        if key not in raw:
            raise ConfigError(f"missing required key: ffmpeg.{key}")
    return FfmpegConfig(
        ffmpeg_bin=str(raw["ffmpeg_bin"]),
        ffprobe_bin=str(raw["ffprobe_bin"]),
    )


# ---------------------------------------------------------------------------
# Top-level loader
# ---------------------------------------------------------------------------


def load_config(path: str | os.PathLike | None = None) -> VideoStudioConfig:
    """Resolve and validate the video-studio config.

    Lookup order for the config path:
    1. explicit `path` arg
    2. VIDEO_STUDIO_CONFIG env var
    3. cwd/video-studio.yaml (fallback)

    Fails-closed on missing file, missing required keys, nonexistent output_dir,
    or invalid media.provider. character_ref and style are lenient (not
    existence-checked).
    """
    cfg_path = _resolve_config_path(path)

    if not cfg_path.exists():
        raise ConfigError(
            f"config file not found: {cfg_path}. "
            f"解析顺序: 显式 path 参数 → $VIDEO_STUDIO_CONFIG → cwd/video-studio.yaml。"
            f"把 $VIDEO_STUDIO_CONFIG 指向你的 config，或在默认路径建文件。"
        )

    raw = _read_yaml(cfg_path)

    # --- required sections ---
    for section in ("render", "audio", "media", "budget", "ffmpeg"):
        if section not in raw or not isinstance(raw[section], dict):
            raise ConfigError(f"missing required section: {section}")

    render = _validate_render(raw["render"])
    audio = _validate_audio(raw["audio"])
    media = _validate_media(raw["media"])
    budget = _validate_budget(raw["budget"])
    ffmpeg_cfg = _validate_ffmpeg(raw["ffmpeg"])

    # --- output_dir: required + must exist on disk (fail-closed) ---
    if "output_dir" not in raw:
        raise ConfigError("missing required key: output_dir")
    output_dir_raw = str(raw["output_dir"])
    output_dir_path = Path(os.path.expanduser(output_dir_raw))
    if not output_dir_path.exists():
        raise ConfigError(
            f"output_dir does not exist: {output_dir_path} "
            f"(create it first — video-studio will not auto-create output_dir)"
        )
    if not output_dir_path.is_dir():
        raise ConfigError(f"output_dir is not a directory: {output_dir_path}")
    output_dir = str(output_dir_path)

    # --- derived subdirs: auto-created AFTER output_dir existence check ---
    episodes_dir = str(output_dir_path / "episodes")
    syllabus_dir = str(output_dir_path / "syllabus")
    state_dir = str(output_dir_path / "state")
    for subdir in (episodes_dir, syllabus_dir, state_dir):
        Path(subdir).mkdir(parents=True, exist_ok=True)

    # --- character_ref + style: lenient, not existence-checked ---
    character_ref = str(raw.get("character_ref", ""))
    style = str(raw.get("style", ""))

    return VideoStudioConfig(
        render=render,
        audio=audio,
        media=media,
        budget=budget,
        ffmpeg=ffmpeg_cfg,
        character_ref=character_ref,
        style=style,
        output_dir=output_dir,
        episodes_dir=episodes_dir,
        syllabus_dir=syllabus_dir,
        state_dir=state_dir,
    )


# ---------------------------------------------------------------------------
# CLI: python -m lib.config --validate PATH
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="lib.config")
    parser.add_argument(
        "--validate",
        metavar="PATH",
        help="Validate the config at PATH; exit 0 if valid, 1 (with offending key on stderr) otherwise.",
    )
    args = parser.parse_args()

    if args.validate is not None:
        try:
            load_config(args.validate)
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            sys.exit(1)
        print("ok")
        sys.exit(0)

    # Default: load from env/cwd and print summary.
    try:
        c = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"output_dir    = {c.output_dir}")
    print(f"episodes_dir  = {c.episodes_dir}")
    print(f"media.provider = {c.media.provider}")
    print(f"render         = {c.render.width}x{c.render.height}@{c.render.fps}fps")
