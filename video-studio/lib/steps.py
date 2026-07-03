"""Render step functions for video-studio pipeline.

Ported from spike_run.py. All ffmpeg execution and media API calls are
injectable via function parameters so unit tests can run without real
subprocesses or API calls (fake-seam pattern mirroring podcast-studio).

Public API (all names required by lib/tests/test_steps.py):
  - initial_start_ms()
  - compute_clip_len(dur_s)
  - total_audio_ms(start_ms_after_beats)
  - build_shot_prompt(shot, ref_url)
  - insert_cards(...)
  - step_tts(...)
  - step_render_shots_clip(...)
  - step_chart_clip(...)
  - step_still_clip(...)
  - step_bgm_calib(...)

Design invariants preserved from spike_run.py:
  - Timing driven by probe (actual mp3 duration), not API-reported.
  - clip_len = narration_dur + DISSOLVE_FADE for xfade overlap.
  - start_ms pre-offset by TITLE_DUR so first subtitle is not at 0.
  - with_character=False shots get the no-person disclaimer + ref=None.
  - Charts excluded from color grade (data colours stay readable).
  - BGM volume halves each round when gap < target; loop exits on target.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Callable

from lib import ffcmd
from lib.cards import TITLE_DUR, END_DUR
from lib.ffcmd import CAMERA_MOVES, MIN_DWELL
from lib.storyboard import pace_weight


# ---------------------------------------------------------------------------
# Camera-move helpers (Phase 5)
# ---------------------------------------------------------------------------

def _shot_type_to_move(shot_type: str | None) -> str:
    """Map a shot_type string to a camera_move (content-match fallback).

    全景/远景 (wide, establishing) → 横移 (slow pan to reveal the scene).
    特写/手部/脚步/动作 (tight details) → 竖摇 (pan-y for vertical action).
    Everything else (including None) → 推近 (default gentle push-in).

    DP-001: code-side fallback only; director LLM has priority over this.
    """
    if shot_type in ("全景", "远景"):
        return "横移"
    if shot_type in ("特写", "手部", "脚步", "动作"):
        return "竖摇"
    return "推近"


def _resolve_camera_move(shot: dict, slice_s: float) -> str:
    """Resolve the camera_move for a single shot render slice.

    Priority (DP-003=A — code does NOT override director based on pace):
      ① slice_s < MIN_DWELL(0.8) → "静止"
            Degenerate single-shot fallback: whole-beat clip_len < 0.8s.
            Normal multi-shot beats never produce slice_s < MIN_DWELL
            (max_shots/shot_durations invariant, test_steps.py:431).
      ② shot.get("camera_move") in CAMERA_MOVES → use it directly.
            Includes any move the director assigned to a quick shot
            (code does NOT force 静止 based on pace — DP-003=A).
      ③ else → _shot_type_to_move(shot.get("shot_type"))
            Content-match fallback when director omitted camera_move.
    """
    if slice_s < MIN_DWELL:
        return "静止"
    director_move = shot.get("camera_move")
    if director_move in CAMERA_MOVES:
        return director_move
    return _shot_type_to_move(shot.get("shot_type"))


# ---------------------------------------------------------------------------
# Pure helpers (no side effects)
# ---------------------------------------------------------------------------

def initial_start_ms() -> int:
    """The first beat subtitle start time, pre-offset by the title card duration.

    The title card occupies the front of the timeline (TITLE_DUR seconds of
    silent narration), so beat subtitles must start at TITLE_DUR, not 0.
    Without this pre-offset the whole subtitle track drifts earlier by TITLE_DUR.
    """
    return round(TITLE_DUR * 1000)


def compute_clip_len(dur_s: float) -> float:
    """Beat video clip length: narration duration + DISSOLVE_FADE overlap.

    Each beat's video clip is slightly longer than its narration so adjacent
    clips can cross-dissolve (the overlap is xfaded back out → net = narration).
    """
    return dur_s + ffcmd.DISSOLVE_FADE


def total_audio_ms(start_ms_after_beats: int) -> int:
    """Full timeline length in ms: TITLE + Σbeats + END.

    start_ms_after_beats is the running cursor after all beat durations have
    been accumulated (= TITLE_ms + Σ actual_beat_ms).  Adding the trailing
    end-card silence gives the coverage that drives meta + acceptance checks.
    """
    return start_ms_after_beats + round(END_DUR * 1000)


def build_shot_prompt(
    shot: dict,
    ref_url: str | None,
) -> tuple[str, str | None]:
    """Build (prompt, ref) for one shot.

    with_character=False (空镜) → appends no-person disclaimer and sets ref=None
    so the image model does not invent a random person.

    with_character=True or absent → ref=ref_url, prompt unchanged.
    """
    with_char = shot.get("with_character", True)
    prompt = shot["prompt"]
    if not with_char:
        prompt += "，画面中没有任何人物，纯环境与物体细节"
        ref = None
    else:
        ref = ref_url
    return prompt, ref


def insert_cards(
    beat_clips: list[str],
    beat_durs: list[float],
    beat_mp3s: list[str],
    title_clip: str,
    title_len: float,
    sil_title: str,
    end_clip: str,
    end_len: float,
    sil_end: str,
) -> tuple[list[str], list[float], list[str]]:
    """Bracket the per-beat lists with title and end card entries.

    Returns (vid_clips, clip_durs, mp3s):
      vid_clips  = [title_clip] + beat_clips + [end_clip]
      clip_durs  = [title_len]  + beat_durs  + [end_len]
      mp3s       = [sil_title]  + beat_mp3s  + [sil_end]

    The narration track uses silence segments for the cards so mux's -shortest
    does not truncate them; the body audio/subtitle alignment is preserved
    because start_ms was already pre-offset by TITLE_DUR in step_tts.
    """
    vid_clips = [title_clip] + list(beat_clips) + [end_clip]
    clip_durs = [title_len] + list(beat_durs) + [end_len]
    mp3s = [sil_title] + list(beat_mp3s) + [sil_end]
    return vid_clips, clip_durs, mp3s


# ---------------------------------------------------------------------------
# Step functions with injectable seams
# ---------------------------------------------------------------------------

def step_tts(
    beats: list[dict],
    scratch: str,
    synth_fn: Callable[[str, str], int],
    probe_fn: Callable[[str], int],
    run_ff_fn: Callable[[list[str], str], None],
) -> tuple[list[str], list[float], list[dict], int]:
    """Synthesize narration audio for each beat.

    For each beat:
      1. synth_fn(text, raw_mp3_path) → reported_ms  (writes raw mp3)
      2. run_ff_fn(voice_polish_cmd(raw, polished), label)  → polished mp3
      3. probe_fn(polished_mp3_path)  → actual_ms  (drives the timeline)

    Timing is driven by the ACTUAL probed duration, not the API-reported
    audio_length (they can differ by ~tens of ms; using the wrong value
    drifts every subsequent subtitle).

    Returns (mp3s, dur_s_list, srt_beats, final_start_ms):
      mp3s            list of polished mp3 paths (one per beat)
      dur_s_list      actual durations in seconds (probe-driven)
      srt_beats       list of {text, start_ms, dur_ms, keywords}
      final_start_ms  running cursor after all beats (= TITLE_ms + Σ actual_ms)
    """
    mp3s: list[str] = []
    dur_s_list: list[float] = []
    srt_beats: list[dict] = []
    start_ms = initial_start_ms()

    for beat in beats:
        bid = beat["id"]
        raw_mp3 = os.path.join(scratch, f"{bid}.raw.mp3")
        mp3 = os.path.join(scratch, f"{bid}.mp3")

        _reported_ms = synth_fn(beat["text"], raw_mp3)
        run_ff_fn(ffcmd.voice_polish_cmd(raw_mp3, mp3), f"{bid}-voice")
        actual_ms = probe_fn(mp3)
        dur_s = actual_ms / 1000.0

        srt_beats.append({
            "text": beat["text"],
            "start_ms": start_ms,
            "dur_ms": actual_ms,
            "keywords": beat.get("keywords", []),
        })
        start_ms += actual_ms
        mp3s.append(mp3)
        dur_s_list.append(dur_s)

    return mp3s, dur_s_list, srt_beats, start_ms


def step_render_shots_clip(
    shots: list[dict],
    clip_len: float,
    out_clip: str,
    bid: str,
    scratch: str,
    ref_url: str | None,
    grade_vf: str | None,
    gen_still_fn: Callable[[str, str | None, str], str],
    run_ff_fn: Callable[[list[str], str], None],
    durs: list[float] | None = None,
) -> None:
    """Render N shots into one silent video clip of clip_len seconds.

    Shot rhythm (POC6):
      - Caps shot count to max_shots(clip_len) so no shot drops below MIN_DWELL.
      - Splits time by pace weights (hold=2.0, normal=1.0, quick=0.6).
      - Falls back to equal split when weighted split yields a slice < MIN_DWELL.

    空镜 (with_character=False):
      - Prompt gets "画面中没有任何人物" disclaimer appended.
      - ref=None so the image model omits people.

    Each shot → Ken Burns sub-clip (still_clip_cmd with optional grade_vf).
    Single shot → copy; multiple → hard-concat.
    Narration is muxed separately (the audio is a separate track).

    `durs` (Phase-1 shot-timeline, MR-2): when the caller (lib.wiring's
    step_gen_still, fed by lib.shotplan.plan_windows) supplies durs=
    explicitly, those durations are authoritative and reused verbatim — this
    function must NOT re-derive/truncate via max_shots/shot_durations, which
    would silently drop plan_windows' WINDOW_MAX cap. durs=None (default)
    preserves the old caller contract: internal n_max clamp + pace-weighted
    shot_durations recompute.
    """
    if durs is not None:
        durs = list(durs)
    else:
        n_max = ffcmd.max_shots(clip_len)
        if len(shots) > n_max:
            shots = shots[:n_max]

        weights = [pace_weight(s.get("pace", "normal")) for s in shots]
        durs = ffcmd.shot_durations(clip_len, weights)

    subs: list[str] = []
    for j, (shot, slice_s) in enumerate(zip(shots, durs)):
        prompt, ref = build_shot_prompt(shot, ref_url)
        spng = os.path.join(scratch, f"{bid}_shot{j}.png")
        try:
            actual_png = gen_still_fn(prompt, ref, spng)
        except Exception as exc:  # noqa: BLE001 — one shot's gen failure (e.g. content
            # moderation) must NOT kill the whole paid episode. Fall back to a neutral
            # brand-color placeholder via ffmpeg (never fails); the beat keeps its slot.
            actual_png = os.path.join(scratch, f"{bid}_shot{j}_placeholder.png")
            run_ff_fn(
                ["-f", "lavfi", "-i", "color=c=0x1B2A38:s=1920x1080",
                 "-frames:v", "1", actual_png],
                f"{bid}-shot{j}-placeholder",
            )
            logging.warning(
                "[gen-still] %s_shot%d 生成失败(%s) — 用中性占位图兜底，不中断整期",
                bid, j, exc,
            )
        sub = os.path.join(scratch, f"{bid}_sub{j}.mp4")
        move = _resolve_camera_move(shot, slice_s)
        intensity = float(shot.get("camera_intensity", 1.0))
        run_ff_fn(
            ffcmd.still_clip_cmd(actual_png, slice_s, sub, grade_vf,
                                  camera_move=move, intensity=intensity),
            f"{bid}-shot{j}",
        )
        subs.append(sub)

    if len(subs) == 1:
        shutil.copy(subs[0], out_clip)
    else:
        listf = os.path.join(scratch, f"{bid}_shots.txt")
        with open(listf, "w") as f:
            for s in subs:
                f.write(f"file '{s}'\n")
        run_ff_fn(
            ["-f", "concat", "-safe", "0", "-i", listf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", out_clip],
            f"{bid}-concat",
        )


def step_chart_clip(
    png: str,
    clip_len: float,
    out_clip: str,
    run_ff_fn: Callable[[list[str], str], None],
) -> None:
    """Render a chart image as a video-only STATIC clip (no camera move, no grade).

    Code-rendered scientific charts are held fully in frame the entire beat: a
    Ken Burns push-in zooms to 1.2x and crops the axes / units out of frame,
    which strips a precise data chart of its meaning. Charts are also excluded
    from the series look so matplotlib data colours stay readable (grade=None).
    """
    run_ff_fn(
        ffcmd.static_clip_cmd(png, clip_len, out_clip, None),
        "clip-chart",
    )


def step_still_clip(
    png: str,
    clip_len: float,
    out_clip: str,
    grade_vf: str | None,
    run_ff_fn: Callable[[list[str], str], None],
) -> None:
    """Render a photographic still as a video-only Ken Burns clip WITH color grade.

    Photographic stills (AI-generated, S2V, card backgrounds) get the series
    look applied so they don't drift warm↔cool shot to shot.
    """
    run_ff_fn(
        ffcmd.still_clip_cmd(png, clip_len, out_clip, grade_vf),
        "clip-still",
    )


def step_bgm_calib(
    base_mp4: str,
    ass_path: str,
    bgm: str,
    final_mp4: str,
    initial_volume: float,
    max_rounds: int,
    target_db: float,
    measure_gap_fn: Callable[[str, str, str], float],
    run_ff_fn: Callable[[list[str], str], None],
) -> dict:
    """BGM ducking calibration loop.

    Each round:
      1. Mux the output video with current bgm_volume (via mux_final_ass_cmd).
      2. Measure the voice-BGM gap via measure_gap_fn.
      3. If gap >= target_db: done (ducking is sufficient).
      4. Otherwise halve bgm_volume and continue.

    Volume halves AFTER each below-target round so round 1 uses
    initial_volume exactly (no pre-halving).

    Returns {rounds, history, final_gap}:
      rounds      number of rounds actually run
      history     list of {round, bgm_volume, gap} per round
      final_gap   the gap value that triggered the stop (or last round's gap)
    """
    bgm_volume = initial_volume
    history: list[dict] = []
    rounds = 0
    final_gap: float | None = None

    for rnd in range(1, max_rounds + 1):
        rounds = rnd
        run_ff_fn(
            ffcmd.mux_final_ass_cmd(
                base_mp4, ass_path, bgm, final_mp4,
                bgm_volume=f"{bgm_volume:.4f}",
            ),
            f"mux-r{rnd}",
        )
        gap = measure_gap_fn(base_mp4, bgm, f"{bgm_volume:.4f}")
        history.append({
            "round": rnd,
            "bgm_volume": round(bgm_volume, 4),
            "gap": round(gap, 2),
        })
        final_gap = gap
        if gap >= target_db:
            break
        bgm_volume *= 0.5

    return {"rounds": rounds, "history": history, "final_gap": final_gap}
