"""FFprobe / ffmpeg measurement utilities.

Originally in spike_acceptance.py; moved here in Phase 7 spike retirement.
Used by lib/wiring.py (assemble/BGM ducking) and evals/video_render_e2e.py.
"""
from __future__ import annotations

import os
import re
import subprocess

# Allow pointing at a libass-enabled ffmpeg build via env vars.
_FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
_FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")


def _ffprobe(args: list[str], timeout: int = 60) -> str:
    out = subprocess.run(
        [_FFPROBE, "-v", "error", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()}")
    return out.stdout.strip()


def probe_dimensions(path: str) -> tuple[int, int]:
    out = _ffprobe([
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", path,
    ])
    w, h = out.split("x")
    return int(w), int(h)


def probe_duration_s(path: str) -> float:
    out = _ffprobe([
        "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1", path,
    ])
    return float(out)


def _volumedetect_mean(ff_input_args: list[str], timeout: int = 120) -> float:
    """Run an ffmpeg graph ending in volumedetect; return mean_volume (dB)."""
    proc = subprocess.run(
        [_FFMPEG, "-hide_banner", "-nostats", *ff_input_args, "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout,
    )
    m = _MEAN_RE.search(proc.stderr)
    if not m:
        raise RuntimeError(
            "volumedetect produced no mean_volume; stderr tail:\n"
            + "\n".join(proc.stderr.splitlines()[-15:])
        )
    return float(m.group(1))


def measure_gap(base_mp4: str, bgm: str, bgm_volume: str,
                duck_threshold: float = 0.03) -> float:
    """narration_mean − bgm_ducked_mean, in dB (larger = BGM quieter).

    Builds two single-stem measurements over the base.mp4 duration:
      - narration stem = base.mp4's own audio (the per-beat voice track)
      - bgm ducked stem = bgm passed through the SAME sidechaincompress duck
        keyed by the narration, exactly as mux_final_cmd applies it.
    """
    base_dur = probe_duration_s(base_mp4)

    narration_mean = _volumedetect_mean([
        "-i", base_mp4, "-map", "0:a", "-af", "volumedetect",
    ])

    duck_graph = (
        f"[1:a]volume={bgm_volume},aloop=loop=-1:size=2e9[bg];"
        f"[bg][0:a]sidechaincompress="
        f"threshold={duck_threshold}:ratio=8:attack=5:release=300[duck];"
        f"[duck]volumedetect[m]"
    )
    bgm_mean = _volumedetect_mean([
        "-i", base_mp4, "-i", bgm,
        "-filter_complex", duck_graph,
        "-map", "[m]", "-t", str(base_dur),
    ])
    return narration_mean - bgm_mean
