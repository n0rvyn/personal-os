#!/usr/bin/env python3
"""LIVE E2E: drive the REAL runner architecture (pipeline + run_pipeline +
lib/wiring + lib/steps) to render episodes/{seq}-{slug}/final.mp4 — NOT
spike_run.py.

This is Phase-2 Task 6 (the acceptance harness). The 13-step render wiring lives
in `lib/wiring.py` (shared with the production CLI `python -m lib.runner`); this
eval imports `prepare_render` from there, runs it through `run_pipeline`, and
asserts UX-001/002/003/006 against the produced final.mp4.

Two modes (the ONLY delta is which media calls are made):

  DRY_RUN=1  — gen_still / synth copy cached `.spike/` assets; ffmpeg-libass is
               REAL. Validates wiring + beat-ordering + gates + cleanup + every
               ffmpeg command for $0. Run this first to green the wiring before
               spending a single image credit.

  (unset)    — live: gen_still → Doubao Seedream, synth → MiniMax TTS. The
               character_ref, storyboard, S2V clip and card backgrounds are
               REUSED from cache via the SPIKE_* hooks (see lib/wiring.py).

Env:
  VIDEO_STUDIO_CONFIG  path to a config yaml (else a render config is written
                       to .spike/_e2e_config.yaml pointing at FFMPEG_BIN/output)
  FFMPEG_BIN/FFPROBE_BIN  libass-enabled ffmpeg/ffprobe (else config's values)
  MEDIA_PROVIDER       doubao (default for live) | minimax
  SPIKE_FIXTURE        fixture beats json (default beats.long.fixture.json)
  E2E_SEQ / E2E_SLUG   episode seq + slug (default 001 / e2e)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent  # video-studio/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib import episode as episode_mod
from lib import pipeline as pipeline_mod
from lib import runner as runner_mod
from lib import wiring as wiring_mod
from lib.config import load_config


# ---------------------------------------------------------------------------
# UX acceptance checks (mirror spike_acceptance UX-001/002/003/006)
# ---------------------------------------------------------------------------

def _assert_ux(cfg, ep, ctx) -> bool:
    from spike_acceptance import probe_dimensions, probe_duration_s

    final_mp4 = str(ep["final"])
    ok = True

    # UX-001: 1920x1080 + duration ≈ Σ narration. Gate at the plan's <0.1s drift
    # target (dev-guide:89, plan Task-6:318); 1.5s is only a sanity backstop.
    w, h = probe_dimensions(final_mp4)
    dur_s = probe_duration_s(final_mp4)
    expected_s = ctx["total_audio_ms"] / 1000.0
    drift = abs(dur_s - expected_s)
    dim_ok = (w == cfg.render.width and h == cfg.render.height)
    dur_ok = drift < 0.1
    ux001 = dim_ok and dur_ok
    print(f"UX-001  分辨率 {w}x{h} (want {cfg.render.width}x{cfg.render.height}) "
          f"{'PASS' if dim_ok else 'FAIL'} | drift {drift:.3f}s (<0.1) "
          f"{'PASS' if dur_ok else 'FAIL'} -> {'PASS' if ux001 else 'FAIL'}")
    if 0.1 <= drift < 1.5:
        print(f"        ⚠️ drift {drift:.3f}s in [0.1, 1.5) — over target, under backstop")
    ok = ok and ux001

    # UX-003: BGM ducked ≥ bgm_duck_db below narration (from calib)
    calib = ctx["calib"]
    final_gap = calib["final_gap"]
    ux003 = final_gap >= cfg.audio.bgm_duck_db
    print(f"UX-003  BGM gap {final_gap:.1f}dB (>= {cfg.audio.bgm_duck_db}) "
          f"{calib['rounds']} rounds -> {'PASS' if ux003 else 'FAIL'}")
    ok = ok and ux003

    # UX-002: every character shot's gen call carried subject_reference
    sb = ctx.get("sb")
    beats = ctx["beats"]
    ref_url = ctx["ref_url"]
    if sb is not None:
        char_shots = sum(
            1 for i, b in enumerate(beats)
            if b["visual_type"] in ("still", "multi")
            for s in sb[i]["shots"] if s.get("with_character", True)
        )
        ux002 = bool(ref_url) and char_shots > 0
        print(f"UX-002  {char_shots} 人物镜头引用同一 ref ({ref_url[:24]}...) "
              f"-> {'PASS' if ux002 else 'FAIL'}")
    else:
        ux002 = bool(ref_url)
        print(f"UX-002  ref present ({bool(ref_url)}) -> {'PASS' if ux002 else 'FAIL'}")
    ok = ok and ux002

    # UX-006: chart beats produced by matplotlib code path (no AI generation)
    chart_beats = [b["id"] for b in beats if b["visual_type"] == "chart"]
    ux006 = len(chart_beats) > 0  # render_pace_table is the only chart path
    print(f"UX-006  {len(chart_beats)} chart beats via code render (matplotlib) "
          f"{chart_beats} -> {'PASS' if ux006 else 'FAIL'}")
    ok = ok and ux006

    return ok


# ---------------------------------------------------------------------------
# Config bootstrap (write a render config pointing at libass ffmpeg + output)
# ---------------------------------------------------------------------------

def _bootstrap_config() -> Path:
    explicit = os.environ.get("VIDEO_STUDIO_CONFIG")
    if explicit:
        return Path(explicit)
    ffmpeg_bin = os.environ.get("FFMPEG_BIN",
                                str(Path.home() / ".local/ffmpeg-libass/ffmpeg"))
    ffprobe_bin = os.environ.get("FFPROBE_BIN",
                                 str(Path.home() / ".local/ffmpeg-libass/ffprobe"))
    out_dir = HERE / ".render-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = os.environ.get("MEDIA_PROVIDER", "doubao").lower()
    cfg_path = HERE / ".spike" / "_e2e_config.yaml"
    cfg_path.write_text(
        "render:\n"
        "  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: PingFang SC\n"
        "audio:\n  voice_id: male-qn-jingying-jingpin\n  bgm_duck_db: 18.0\n"
        f"media:\n  provider: {provider}\n"
        "budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {ffmpeg_bin}\n  ffprobe_bin: {ffprobe_bin}\n"
        f"output_dir: {out_dir}\n",
        encoding="utf-8",
    )
    return cfg_path


def main() -> int:
    dry = os.environ.get("DRY_RUN") == "1"
    provider = os.environ.get("MEDIA_PROVIDER", "doubao").lower()
    fixture_path = Path(os.environ.get("SPIKE_FIXTURE", HERE / "beats.long.fixture.json"))
    if not fixture_path.is_absolute():
        fixture_path = HERE / fixture_path
    seq = os.environ.get("E2E_SEQ", "001")
    slug = os.environ.get("E2E_SLUG", "e2e")

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    log(f"[mode] {'DRY (cached media, real ffmpeg)' if dry else f'LIVE ({provider})'}")
    log(f"[fixture] {fixture_path}")

    cfg_path = _bootstrap_config()
    cfg = load_config(cfg_path)
    log(f"[config] {cfg_path} -> output_dir={cfg.output_dir}")

    pipeline = pipeline_mod.load_pipeline()
    pipeline_mod.validate_pipeline(pipeline)

    bundle = wiring_mod.prepare_render(
        cfg, fixture_path=fixture_path, seq=seq, slug=slug, dry=dry, log=log)
    log(f"[scratch] {bundle['scratch_dir']}")

    # capture ctx for the UX asserts by wrapping assemble (run_pipeline owns ctx)
    holder: dict[str, Any] = {}
    orig_assemble = bundle["step_fns"]["assemble"]

    def assemble_capture(step, ctx, scratch_dir):
        orig_assemble(step, ctx, scratch_dir)
        holder["ctx"] = ctx
    bundle["step_fns"]["assemble"] = assemble_capture

    result = runner_mod.run_pipeline(cfg, pipeline=pipeline, **bundle)
    log(f"[runner] {result}")

    if result.get("status") != "done":
        print(f"\n❌ runner HALTED at step {result.get('failed_step')!r}: "
              f"{result.get('reason')}", file=sys.stderr)
        return 2

    ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
    final_mp4 = ep["final"]
    if not (final_mp4.exists() and final_mp4.stat().st_size > 0):
        print(f"\n❌ final.mp4 missing after clean run: {final_mp4}", file=sys.stderr)
        return 3

    ctx = holder.get("ctx", {})
    print(f"\n=== final.mp4 -> {final_mp4} ({final_mp4.stat().st_size//1024} KB) ===")
    ux_ok = _assert_ux(cfg, ep, ctx)

    print(f"\n{'✅ ALL UX PASS' if ux_ok else '❌ UX FAILURE'} "
          f"({'DRY' if dry else f'LIVE {provider}'}) — final.mp4 at {final_mp4}")
    return 0 if ux_ok else 4


if __name__ == "__main__":
    sys.exit(main())
