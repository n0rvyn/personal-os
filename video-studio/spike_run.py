#!/usr/bin/env python3
"""Spike driver: fixture beats -> real media -> ffmpeg -> .spike/final.mp4.

Env gates:
  RUN_LIVE=1        required — calls image-01 + speech-02-hd (real assets).
  RUN_LIVE_VIDEO=1  optional — the visual_type=video beat does a real S2V
                    (burns 1 video credit). Unset -> that beat falls back to a
                    still (image_prompt), so the whole chain runs end-to-end
                    with ZERO video quota (pipeline validation mode).

Time axis is narration-driven: each beat's on-screen duration = its TTS
audio_length (ms). All ffmpeg calls go through subprocess list args (no shell).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from lib import media_image, media_tts, media_video, media_chart, ffcmd
from lib.subtitle import build_srt
from spike_acceptance import measure_gap

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(HERE, ".spike")
ASSETS = os.path.join(SPIKE, "assets")

# Subtitle burn-in needs a libass-enabled ffmpeg; the system ffmpeg may lack
# it. FFMPEG_BIN lets the driver point at a libass build without touching the
# system binary. (Stage 1 prerequisite: ship/locate a libass-enabled ffmpeg.)
FFMPEG = [os.environ.get("FFMPEG_BIN", "ffmpeg"), "-y", "-hide_banner", "-loglevel", "error"]
FF_TIMEOUT = 300

BGM_START_VOLUME = 0.12
GAP_TARGET_DB = 18.0
MAX_CALIB_ROUNDS = 3


def _run_ff(argv: list[str], label: str) -> None:
    cmd = FFMPEG + argv
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FF_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg [{label}] exit={proc.returncode}\nCMD: {' '.join(cmd)}\n"
            f"STDERR:\n{proc.stderr.strip()}"
        )


def _probe_ms(path: str) -> int:
    """Actual media duration in ms via ffprobe. Drives timing (the API-reported
    audio_length differs from the real mp3 by ~tens of ms, which drifts srt)."""
    ffprobe = os.environ.get("FFPROBE_BIN", "ffprobe")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, timeout=30,
    )
    return round(float(out.stdout.strip()) * 1000)


def _ensure_bgm() -> tuple[str, bool]:
    """Return (bgm_path, is_placeholder). Synthesize a soft pad if none exists."""
    real = os.path.join(ASSETS, "bgm.mp3")
    if os.path.exists(real) and os.path.getsize(real) > 1024:
        return real, False
    _run_ff([
        "-f", "lavfi", "-i", "sine=frequency=220:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
        "-filter_complex", "[0:a][1:a]amix=inputs=2,volume=0.5[a]",
        "-map", "[a]", "-ar", "48000", "-ac", "2", real,
    ], "bgm-placeholder")
    return real, True


def main() -> int:
    if os.environ.get("RUN_LIVE") != "1":
        print("RUN_LIVE != 1 — refusing to run live spike. "
              "Set RUN_LIVE=1 (optionally RUN_LIVE_VIDEO=1).", file=sys.stderr)
        return 1
    live_video = os.environ.get("RUN_LIVE_VIDEO") == "1"

    os.makedirs(ASSETS, exist_ok=True)
    # Fixture is tracked INPUT (lives at the video-studio root); .spike/ holds
    # only generated outputs. SPIKE_FIXTURE overrides (e.g. a longer fixture).
    fixture_path = os.environ.get("SPIKE_FIXTURE")
    if fixture_path:
        if not os.path.isabs(fixture_path):
            fixture_path = os.path.join(HERE, fixture_path)
    else:
        fixture_path = os.path.join(HERE, "beats.fixture.json")
        if not os.path.exists(fixture_path):
            fixture_path = os.path.join(SPIKE, "beats.fixture.json")
    report_lines: list[str] = []

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)
        report_lines.append(msg)

    with open(fixture_path) as f:
        fixture = json.load(f)
    log(f"[fixture] {fixture_path}")

    # ---- 1. character_ref ----
    # Reuse hook: SPIKE_REF_PNG + SPIKE_REF_URL let a re-run anchor on an
    # existing character_ref (e.g. one already paid for) without a new image
    # call — keeps every beat on the SAME person for a coherent rebuild.
    cref = fixture["character_ref"]
    cref_out = os.path.join(SPIKE, "character_ref.png")
    ref_png_override = os.environ.get("SPIKE_REF_PNG")
    ref_url_override = os.environ.get("SPIKE_REF_URL")
    if ref_png_override and ref_url_override:
        import shutil
        shutil.copy(ref_png_override, cref_out)
        ref_url = ref_url_override
        log(f"[ref] reusing provided character_ref {ref_png_override}")
    else:
        log(f"[ref] generating character_ref -> {cref_out}")
        ref_url = media_image.gen_character_ref(cref["prompt"], cref_out)

    beats = fixture["beats"]
    seg_files: list[str] = []
    srt_beats: list[dict] = []
    start_ms = 0

    # ---- optional storyboard (分镜): content-aware, shot-varied, continuous ----
    sb = None
    if os.environ.get("SPIKE_STORYBOARD") == "1":
        from lib import storyboard as _storyboard
        char_desc = fixture.get("character_ref", {}).get("prompt", "主讲人物")
        sb = _storyboard.build_storyboard(beats, char_desc)
        log(f"[storyboard] {sum(len(e['shots']) for e in sb)} shots / {len(beats)} beats")

    def render_shots(shots: list[dict], dur_s: float, mp3: str, seg_out: str, bid: str) -> None:
        """Render N shots (each its own Ken Burns slice, hard-cut) and mux the
        narration. with_character=True locks the host via subject_reference;
        False = 空镜 (no person). One shot == a single still segment."""
        n = len(shots)
        slice_s = dur_s / n
        subs = []
        for j, sh in enumerate(shots):
            spng = os.path.join(SPIKE, f"{bid}_shot{j}.png")
            ref = ref_url if sh.get("with_character", True) else None
            media_image.gen_still(sh["prompt"], ref, spng)
            sub = os.path.join(SPIKE, f"{bid}_sub{j}.mp4")
            _run_ff(["-loop", "1", "-i", spng, "-t", f"{slice_s:.4f}", "-r", "30",
                     "-vf", ffcmd._still_or_chart_filtergraph(slice_s),
                     "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", sub],
                    f"{bid}-shot{j}")
            subs.append(sub)
        if len(subs) == 1:
            visual = subs[0]
        else:
            listf = os.path.join(SPIKE, f"{bid}_shots.txt")
            with open(listf, "w") as f:
                for s in subs:
                    f.write(f"file '{s}'\n")
            visual = os.path.join(SPIKE, f"{bid}_visual.mp4")
            _run_ff(["-f", "concat", "-safe", "0", "-i", listf,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", visual],
                    f"{bid}-concat")
        _run_ff(["-i", visual, "-i", mp3, "-map", "0:v", "-map", "1:a",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", seg_out],
                f"{bid}-mux")
        kinds = " / ".join(f"{'人' if s.get('with_character', True) else '空'}·{s.get('shot_type', '')}"
                           for s in shots)
        log(f"[{bid}] shots×{n}: {kinds}")

    for i, beat in enumerate(beats):
        bid = beat["id"]
        vtype = beat["visual_type"]

        # ---- 2. narration -> mp3 + audio_length ----
        mp3 = os.path.join(SPIKE, f"{bid}.mp3")
        reported_ms = media_tts.synth(beat["text"], mp3)
        # Timing is driven by the ACTUAL mp3 duration, not the API-reported
        # audio_length (they differ by ~tens of ms). The video segment runs on
        # the real audio, so the srt must too — otherwise subtitles drift.
        dur_ms = _probe_ms(mp3)
        dur_s = dur_ms / 1000.0
        log(f"[{bid}] tts reported={reported_ms}ms actual={dur_ms}ms -> {mp3}")
        srt_beats.append({"text": beat["text"], "start_ms": start_ms, "dur_ms": dur_ms})
        start_ms += dur_ms

        seg_out = os.path.join(SPIKE, f"seg_{i}_{bid}.mp4")

        # ---- 3. visual asset + segment cmd ----
        if vtype == "chart":
            png = os.path.join(SPIKE, f"{bid}.png")
            media_chart.render_pace_table(
                beat["chart_title"], [tuple(r) for r in beat["chart_rows"]], png)
            _run_ff(ffcmd.chart_segment_cmd(png, dur_s, seg_out, mp3), f"seg-{bid}")
            log(f"[{bid}] chart -> {png}")
        elif vtype == "video":
            used_still = True
            vmp4 = os.path.join(SPIKE, f"{bid}.mp4")
            # Reuse hook: SPIKE_S2V_CLIP lets a re-run drop in an already-paid
            # S2V clip for the video beat instead of spending another credit.
            reuse_clip = os.environ.get("SPIKE_S2V_CLIP")
            if reuse_clip:
                import shutil
                if os.path.abspath(reuse_clip) != os.path.abspath(vmp4):
                    shutil.copy(reuse_clip, vmp4)
                _run_ff(ffcmd.video_segment_cmd(vmp4, dur_s, seg_out, mp3), f"seg-{bid}")
                used_still = False
                log(f"[{bid}] reusing provided S2V clip {reuse_clip}")
            elif live_video:
                res = media_video.gen_video(beat["video_prompt"], ref_url, vmp4)
                if res.get("ok"):
                    _run_ff(ffcmd.video_segment_cmd(vmp4, dur_s, seg_out, mp3), f"seg-{bid}")
                    used_still = False
                    log(f"[{bid}] S2V ok -> {vmp4} (file_id={res.get('file_id')})")
                else:
                    log(f"[{bid}] S2V fallback=still — reason: {res.get('reason')}")
            else:
                log(f"[{bid}] RUN_LIVE_VIDEO unset -> still fallback (no video credit)")
            if used_still:
                fb = beat.get("image_prompt", beat["text"])
                render_shots([{"prompt": fb, "with_character": True}], dur_s, mp3, seg_out, bid)
        else:
            # still / multi / storyboard-driven → render a list of shots
            if sb is not None:
                shots = sb[i]["shots"]
            elif vtype == "multi":
                shots = [{"prompt": p, "with_character": True} for p in beat["shots"]]
            elif vtype == "still":
                shots = [{"prompt": beat["image_prompt"], "with_character": True}]
            else:
                raise RuntimeError(f"unknown visual_type: {vtype}")
            render_shots(shots, dur_s, mp3, seg_out, bid)

        seg_files.append(seg_out)

    total_audio_ms = start_ms

    # ---- 4. concat -> base.mp4 ----
    list_file = os.path.join(SPIKE, "segments.txt")
    with open(list_file, "w") as f:
        for s in seg_files:
            f.write(f"file '{s}'\n")
    base_mp4 = os.path.join(SPIKE, "base.mp4")
    _run_ff(ffcmd.concat_cmd(list_file, base_mp4), "concat")
    log(f"[concat] base.mp4 ({len(seg_files)} segs, {total_audio_ms}ms)")

    # ---- 5. srt ----
    srt_path = os.path.join(SPIKE, "subs.srt")
    with open(srt_path, "w") as f:
        f.write(build_srt(srt_beats))
    log(f"[srt] {srt_path}")

    # ---- 6. BGM + ducking calibration loop ----
    bgm, placeholder = _ensure_bgm()
    if placeholder:
        log("[bgm] using ffmpeg-synthesized placeholder pad (no real bgm.mp3)")
    final_mp4 = os.path.join(SPIKE, "final.mp4")
    bgm_volume = BGM_START_VOLUME
    history = []
    final_gap = None
    rounds = 0
    for rnd in range(1, MAX_CALIB_ROUNDS + 1):
        rounds = rnd
        _run_ff(ffcmd.mux_final_cmd(base_mp4, srt_path, bgm, final_mp4,
                                    bgm_volume=f"{bgm_volume:.4f}"), f"mux-r{rnd}")
        gap = measure_gap(base_mp4, bgm, f"{bgm_volume:.4f}")
        history.append({"round": rnd, "bgm_volume": round(bgm_volume, 4), "gap": round(gap, 2)})
        log(f"[bgm] round {rnd}: bgm_volume={bgm_volume:.4f} gap={gap:.1f}dB")
        final_gap = gap
        if gap >= GAP_TARGET_DB:
            break
        bgm_volume *= 0.5

    # ---- 7. persist calib + meta ----
    with open(os.path.join(SPIKE, "calib.json"), "w") as f:
        json.dump({"bgm_volume": round(bgm_volume, 4), "final_gap": round(final_gap, 2),
                   "rounds": rounds, "history": history}, f, indent=2)
    with open(os.path.join(SPIKE, "meta.json"), "w") as f:
        json.dump({"total_audio_ms": total_audio_ms,
                   "beats": [{"id": b["id"], "type": b["visual_type"]} for b in beats]},
                  f, indent=2)

    # ---- 8. report ----
    with open(os.path.join(SPIKE, "REPORT.md"), "w") as f:
        f.write("# Spike Run Report\n\n")
        f.write(f"- live_video: {live_video}\n")
        f.write(f"- total narration: {total_audio_ms} ms\n")
        f.write(f"- final BGM volume: {bgm_volume:.4f}, gap: {final_gap:.1f} dB, rounds: {rounds}\n\n")
        f.write("## Log\n\n")
        for line in report_lines:
            f.write(f"- {line}\n")
    log(f"[done] final.mp4 -> {final_mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
