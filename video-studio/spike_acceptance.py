#!/usr/bin/env python3
"""Spike acceptance probe: verify final.mp4 against UX-001 and UX-003.

UX-001: 1920x1080 (16:9 1080p) AND duration ≈ Σ audio_length_ms (< 1.5s drift).
UX-003: narration_mean − bgm_ducked_mean ≥ 18 dB (BGM well below voice).

Run AFTER spike_run.py (which writes .spike/calib.json + .spike/meta.json).
Reads calib.json for the final BGM gap (does NOT re-run the calibration).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(HERE, ".spike")

# Match spike_run.py: allow pointing at a libass-enabled ffmpeg build.
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


def main() -> int:
    final_mp4 = os.path.join(SPIKE, "final.mp4")
    calib_path = os.path.join(SPIKE, "calib.json")
    meta_path = os.path.join(SPIKE, "meta.json")

    for p in (final_mp4, calib_path, meta_path):
        if not os.path.exists(p):
            print(f"❌ missing artifact: {p} — run spike_run.py first", file=sys.stderr)
            return 1

    with open(calib_path) as f:
        calib = json.load(f)
    with open(meta_path) as f:
        meta = json.load(f)

    # ---- UX-001: dimensions + duration ----
    w, h = probe_dimensions(final_mp4)
    dur_s = probe_duration_s(final_mp4)
    expected_s = meta["total_audio_ms"] / 1000.0
    drift = abs(dur_s - expected_s)
    dim_ok = (w == 1920 and h == 1080)
    dur_ok = drift < 1.5
    ux001 = dim_ok and dur_ok

    # ---- UX-003: BGM gap (read from calib, do not re-measure) ----
    final_gap = calib["final_gap"]
    bgm_volume = calib["bgm_volume"]
    rounds = calib["rounds"]
    ux003 = final_gap >= 18.0

    # ---- print acceptance table ----
    print("\n===== Spike Acceptance =====")
    print(f"{'指标':<10} {'实测值':<28} {'阈值':<22} 结果")
    print("-" * 72)
    print(f"{'分辨率':<10} {f'{w}x{h}':<28} {'1920x1080':<22} {'PASS' if dim_ok else 'FAIL'}")
    print(f"{'时长':<10} {f'{dur_s:.2f}s (期望 {expected_s:.2f}s)':<28} "
          f"{'drift<1.5s ('+f'{drift:.2f}s)':<22} {'PASS' if dur_ok else 'FAIL'}")
    print(f"{'UX-001':<10} {'分辨率+时长':<28} {'both PASS':<22} {'PASS' if ux001 else 'FAIL'}")
    print(f"{'BGM gap':<10} {f'{final_gap:.1f} dB':<28} {'>=18 dB':<22} {'PASS' if ux003 else 'FAIL'}")
    print(f"{'UX-003':<10} {f'bgm_vol={bgm_volume}, {rounds} 轮校准':<28} {'gap>=18dB':<22} {'PASS' if ux003 else 'FAIL'}")
    print("-" * 72)

    print("\n校准记录 (每轮):")
    for r in calib.get("history", []):
        print(f"  round {r['round']}: bgm_volume={r['bgm_volume']}, gap={r['gap']:.1f} dB")

    if not ux003 and rounds >= 3:
        print("\n⚠️ 校准发现: 3 轮后 BGM 仍未达 -18dB —— 占位 BGM 偏响/需换源。"
              f"最终 bgm_volume={bgm_volume}。建议 Stage 1 config 用更低默认值。")

    print(f"\nUX-001: {'PASS' if ux001 else 'FAIL'}")
    print(f"UX-003: {'PASS' if ux003 else 'FAIL'}")

    return 0 if (ux001 and ux003) else 2


if __name__ == "__main__":
    sys.exit(main())
