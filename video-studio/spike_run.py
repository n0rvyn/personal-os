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

from lib import media_image, media_tts, media_video, media_chart, ffcmd, cards, grade
from lib.subtitle import build_ass
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
    # POC5: one locked color grade (the column's 冷蓝晨光 signature) applied to
    # every photographic shot (stills / S2V / card bg) so they don't drift
    # warm↔cool. Charts are excluded (keep data readable).
    grade_vf = grade.grade_filter()
    log(f"[grade] series look = {grade.SERIES_LOOK}")
    vid_clips: list[str] = []
    clip_durs: list[float] = []
    mp3s: list[str] = []
    srt_beats: list[dict] = []
    # The title card occupies the front of the timeline (TITLE_DUR of silent
    # narration), so the first beat's audio + subtitles start at TITLE_DUR, not
    # 0. Pre-offsetting start_ms here shifts EVERY beat subtitle by the card
    # length — otherwise the whole subtitle track drifts earlier by ~2.6s.
    start_ms = round(cards.TITLE_DUR * 1000)

    # ---- optional storyboard (分镜): content-aware, shot-varied, continuous ----
    sb = None
    if os.environ.get("SPIKE_STORYBOARD") == "1":
        from lib import storyboard as _storyboard
        sb_cache = os.path.join(SPIKE, "storyboard.json")
        # Reuse hook (mirror SPIKE_S2V_CLIP): the 分镜 step calls a SLOW LLM
        # (claude -p via the proxy — can take minutes / occasionally retries).
        # SPIKE_STORYBOARD_JSON lets a re-render (tuning grade/timing/cards)
        # reuse a saved plan instead of re-running it. Every fresh run also
        # writes the plan to sb_cache so the next run can point back at it.
        reuse = os.environ.get("SPIKE_STORYBOARD_JSON")
        if reuse:
            with open(reuse, encoding="utf-8") as f:
                sb = json.load(f)
            if len(sb) != len(beats):
                raise RuntimeError(
                    f"reused storyboard {reuse} has {len(sb)} entries but the "
                    f"fixture has {len(beats)} beats — regenerate (unset "
                    f"SPIKE_STORYBOARD_JSON) or use a matching plan")
            log(f"[storyboard] reusing {reuse} "
                f"({sum(len(e['shots']) for e in sb)} shots / {len(beats)} beats)")
        else:
            char_desc = fixture.get("character_ref", {}).get("prompt", "主讲人物")
            sb = _storyboard.build_storyboard(beats, char_desc)
            with open(sb_cache, "w", encoding="utf-8") as f:
                json.dump(sb, f, ensure_ascii=False, indent=2)
            log(f"[storyboard] {sum(len(e['shots']) for e in sb)} shots / "
                f"{len(beats)} beats -> {sb_cache}")

    def render_shots_clip(shots: list[dict], clip_len: float, out_clip: str, bid: str) -> None:
        """Render N shots (each its own Ken Burns slice, hard-cut) into ONE
        silent video clip of length clip_len. with_character=True locks the host
        via subject_reference; False = 空镜. Narration is muxed later (the audio
        is a separate hard-concat track so beats can cross-dissolve)."""
        import shutil
        from lib import storyboard as _sb_mod
        # POC6 shot-rhythm: cap shot count to what this beat's length can hold
        # without any shot dropping below MIN_DWELL (no subliminal flashes), then
        # split the time by each shot's pace weight (金句 hold long / 列举 quick
        # short). Capping the count keeps the equal-split fallback safe.
        n_max = ffcmd.max_shots(clip_len)
        if len(shots) > n_max:
            log(f"[{bid}] shots {len(shots)}→{n_max} (capped: clip {clip_len:.1f}s ÷ "
                f"min dwell {ffcmd.MIN_DWELL}s)")
            shots = shots[:n_max]
        n = len(shots)
        weights = [_sb_mod.pace_weight(s.get("pace", "normal")) for s in shots]
        durs = ffcmd.shot_durations(clip_len, weights)
        subs = []
        for j, (sh, slice_s) in enumerate(zip(shots, durs)):
            spng = os.path.join(SPIKE, f"{bid}_shot{j}.png")
            ref = ref_url if sh.get("with_character", True) else None
            # gen_still returns the ACTUAL path (real extension, e.g. .jpg) —
            # use it for ffmpeg, don't assume the .png name we passed in.
            spng = media_image.gen_still(sh["prompt"], ref, spng)
            sub = os.path.join(SPIKE, f"{bid}_sub{j}.mp4")
            _run_ff(ffcmd.still_clip_cmd(spng, slice_s, sub, grade_vf), f"{bid}-shot{j}")
            subs.append(sub)
        if len(subs) == 1:
            shutil.copy(subs[0], out_clip)
        else:
            listf = os.path.join(SPIKE, f"{bid}_shots.txt")
            with open(listf, "w") as f:
                for s in subs:
                    f.write(f"file '{s}'\n")
            _run_ff(["-f", "concat", "-safe", "0", "-i", listf,
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", out_clip],
                    f"{bid}-concat")
        kinds = " / ".join(
            f"{'人' if s.get('with_character', True) else '空'}·{s.get('shot_type', '')}"
            f"·{s.get('pace', 'normal')}·{d:.1f}s"
            for s, d in zip(shots, durs))
        log(f"[{bid}] shots×{n}: {kinds}")

    for i, beat in enumerate(beats):
        bid = beat["id"]
        vtype = beat["visual_type"]

        # ---- 2. narration -> mp3 (synth + light loudness polish) ----
        mp3 = os.path.join(SPIKE, f"{bid}.mp3")
        raw_mp3 = os.path.join(SPIKE, f"{bid}.raw.mp3")
        reported_ms = media_tts.synth(beat["text"], raw_mp3)
        # Light polish: consistency + broadcast loudness (NOT tonal — vendor
        # voice is already mastered; voice choice is the real timbre lever).
        _run_ff(ffcmd.voice_polish_cmd(raw_mp3, mp3), f"{bid}-voice")
        # Timing is driven by the ACTUAL mp3 duration, not the API-reported
        # audio_length (they differ by ~tens of ms). The video segment runs on
        # the real audio, so the srt must too — otherwise subtitles drift.
        dur_ms = _probe_ms(mp3)
        dur_s = dur_ms / 1000.0
        log(f"[{bid}] tts reported={reported_ms}ms actual={dur_ms}ms -> {mp3}")
        kws = sb[i].get("keywords", []) if sb is not None else []
        srt_beats.append({"text": beat["text"], "start_ms": start_ms,
                          "dur_ms": dur_ms, "keywords": kws})
        start_ms += dur_ms

        # Video clip is FADE-longer than its narration so adjacent beats can
        # cross-dissolve (the overlap is xfaded back out → net = narration len).
        clip_len = dur_s + ffcmd.DISSOLVE_FADE
        clip = os.path.join(SPIKE, f"clip_{i}_{bid}.mp4")

        # ---- 3. visual asset -> video-only clip of length clip_len ----
        if vtype == "chart":
            png = os.path.join(SPIKE, f"{bid}.png")
            media_chart.render_pace_table(
                beat["chart_title"], [tuple(r) for r in beat["chart_rows"]], png)
            # No grade on charts — keep the designed data colours readable.
            _run_ff(ffcmd.still_clip_cmd(png, clip_len, clip), f"clip-{bid}")
            log(f"[{bid}] chart -> {png}")
        elif vtype == "video":
            # S2V is a SELECTIVE enhancement (decision 2026-06-29): only beats
            # explicitly marked visual_type=video spend an S2V credit (key motion
            # shots like a running-form demo); everything else stays a still.
            # Video credits are scarce (21/week), so this is NOT default-on.
            used_still = True
            vmp4 = os.path.join(SPIKE, f"{bid}.mp4")
            # Reuse hook: SPIKE_S2V_CLIP lets a re-run drop in an already-paid
            # S2V clip for the video beat instead of spending another credit.
            reuse_clip = os.environ.get("SPIKE_S2V_CLIP")
            if reuse_clip:
                import shutil
                if os.path.abspath(reuse_clip) != os.path.abspath(vmp4):
                    shutil.copy(reuse_clip, vmp4)
                _run_ff(ffcmd.video_clip_cmd(vmp4, clip_len, clip, grade_vf), f"clip-{bid}")
                used_still = False
                log(f"[{bid}] reusing provided S2V clip {reuse_clip}")
            elif live_video:
                res = media_video.gen_video(beat["video_prompt"], ref_url, vmp4)
                if res.get("ok"):
                    _run_ff(ffcmd.video_clip_cmd(vmp4, clip_len, clip, grade_vf), f"clip-{bid}")
                    used_still = False
                    log(f"[{bid}] S2V ok -> {vmp4} (file_id={res.get('file_id')})")
                else:
                    log(f"[{bid}] S2V fallback=still — reason: {res.get('reason')}")
            else:
                log(f"[{bid}] RUN_LIVE_VIDEO unset -> still fallback (no video credit)")
            if used_still:
                fb = beat.get("image_prompt", beat["text"])
                render_shots_clip([{"prompt": fb, "with_character": True}], clip_len, clip, bid)
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
            render_shots_clip(shots, clip_len, clip, bid)

        vid_clips.append(clip)
        clip_durs.append(clip_len)
        mp3s.append(mp3)

    # ---- 3b. title + end cards (POC4): AI background + code design layer ----
    # Cards are video-only clips that join the xfade chain exactly like beats;
    # their narration slots are filled with silence so the body audio/subtitles
    # stay aligned (start_ms was pre-offset by TITLE_DUR above). gen_still with
    # character_ref=None makes a character-less (空镜) background.
    card_cfg = fixture.get("cards", {})
    tcfg = {**cards.DEFAULT_TITLE, **card_cfg.get("title", {})}
    ecfg = {**cards.DEFAULT_END, **card_cfg.get("end", {})}

    # Reuse hooks (mirror SPIKE_S2V_CLIP): point at an already-generated card bg
    # so POC5–8 / rebuild runs don't regenerate the backgrounds every time.
    def _card_bg(hook: str, prompt: str, out: str) -> str:
        reuse = os.environ.get(hook)
        if reuse:
            import shutil
            if os.path.abspath(reuse) != os.path.abspath(out):
                shutil.copy(reuse, out)
            log(f"[card] reusing bg {reuse}")
            return out
        # gen_still returns the ACTUAL saved path (real extension) — use it.
        actual = media_image.gen_still(prompt, None, out)
        log(f"[card] gen bg -> {actual}")
        return actual

    title_bg = _card_bg("SPIKE_CARD_TITLE_BG", tcfg["bg_prompt"],
                        os.path.join(SPIKE, "card_title_bg.png"))
    end_bg = _card_bg("SPIKE_CARD_END_BG", ecfg["bg_prompt"],
                      os.path.join(SPIKE, "card_end_bg.png"))

    title_ass = os.path.join(SPIKE, "card_title.ass")
    with open(title_ass, "w", encoding="utf-8") as f:
        f.write(cards.build_title_ass(tcfg["tag"], tcfg["title"], tcfg["subtitle"]))
    end_ass = os.path.join(SPIKE, "card_end.ass")
    with open(end_ass, "w", encoding="utf-8") as f:
        f.write(cards.build_end_ass(ecfg["tag"], ecfg["bullets"], ecfg["footer"]))

    # Card clip length = on-screen dur + fade (the overlap the xfade consumes),
    # same convention as a beat clip.
    title_clip = os.path.join(SPIKE, "clip_title.mp4")
    end_clip = os.path.join(SPIKE, "clip_end.mp4")
    title_len = cards.TITLE_DUR + ffcmd.DISSOLVE_FADE
    end_len = cards.END_DUR + ffcmd.DISSOLVE_FADE
    _run_ff(ffcmd.still_card_cmd(title_bg, title_len, title_ass, title_clip, grade_vf), "card-title")
    _run_ff(ffcmd.still_card_cmd(end_bg, end_len, end_ass, end_clip, grade_vf), "card-end")

    # Silence segments (mono/32k mp3, matching the TTS beats) fill the cards'
    # narration slots so mux's -shortest doesn't truncate them.
    sil_title = os.path.join(SPIKE, "card_sil_title.mp3")
    sil_end = os.path.join(SPIKE, "card_sil_end.mp3")
    _run_ff(ffcmd.silence_mp3_cmd(cards.TITLE_DUR, sil_title), "card-sil-title")
    _run_ff(ffcmd.silence_mp3_cmd(cards.END_DUR, sil_end), "card-sil-end")

    # Prepend the title / append the end card to BOTH tracks.
    vid_clips = [title_clip] + vid_clips + [end_clip]
    clip_durs = [title_len] + clip_durs + [end_len]
    mp3s = [sil_title] + mp3s + [sil_end]

    # start_ms after the loop = TITLE + Σbeats; add the trailing end-card silence
    # so total_audio_ms (drives meta + the acceptance duration check) covers the
    # full timeline: TITLE + Σbeats + END.
    total_audio_ms = start_ms + round(cards.END_DUR * 1000)
    log(f"[card] title {cards.TITLE_DUR}s + body + end {cards.END_DUR}s "
        f"-> total {total_audio_ms}ms")

    # ---- 4. assemble base.mp4: xfade video chain + hard-concat narration ----
    a_list = os.path.join(SPIKE, "narration.txt")
    with open(a_list, "w") as f:
        for m in mp3s:
            f.write(f"file '{m}'\n")
    narration = os.path.join(SPIKE, "narration.m4a")
    _run_ff(ffcmd.concat_audio_cmd(a_list, narration), "narration-concat")

    video_track = os.path.join(SPIKE, "video.mp4")
    _run_ff(ffcmd.xfade_chain_cmd(vid_clips, clip_durs, video_track), "xfade")

    base_mp4 = os.path.join(SPIKE, "base.mp4")
    _run_ff(ffcmd.mux_av_cmd(video_track, narration, base_mp4), "mux-base")
    log(f"[base] dissolve assembly: {len(vid_clips)} clips x-faded, {total_audio_ms}ms narration")

    # ---- 5. subtitles (ASS: style B + keyword emphasis) ----
    ass_path = os.path.join(SPIKE, "subs.ass")
    with open(ass_path, "w") as f:
        f.write(build_ass(srt_beats))
    n_kw = sum(len(b.get("keywords") or []) for b in srt_beats)
    log(f"[ass] {ass_path} ({n_kw} emphasis keywords)")

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
        _run_ff(ffcmd.mux_final_ass_cmd(base_mp4, ass_path, bgm, final_mp4,
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
