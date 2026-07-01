"""Tests for lib/steps.py — render step function invariants (fake seams).

Written before lib/steps.py exists; collection must fail at this point
(`No module named 'lib.steps'`). Task 3-impl resolves this.

These tests nail down the key rendering invariants of the ported
spike_run.py logic without starting real subprocesses or calling APIs.
All ffmpeg execution and media generation calls are injected as fakes.

Invariant groups covered:
  1. Timeline: clip_len formula; start_ms initial offset; probe-driven accumulation.
  2. Cards: title/end bracket clip + mp3 lists; total_audio_ms formula.
  3. 空镜: with_character=False shots get the no-person disclaimer appended.
  4. Chart vs still grade: chart must NOT include colorbalance; still must.
  5. Shot rhythm: shot count capped by max_shots; shot_durations drives dwell split.
  6. BGM ducking: volume halves each round when gap < target; stops on target.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.steps import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly with all required public names.

    Failing here is the test-FAIL-first contract — Task 3-impl resolves this.
    """
    from lib import steps  # noqa: F401
    required = [
        "initial_start_ms",
        "compute_clip_len",
        "total_audio_ms",
        "build_shot_prompt",
        "insert_cards",
        "step_tts",
        "step_render_shots_clip",
        "step_chart_clip",
        "step_still_clip",
        "step_bgm_calib",
    ]
    for name in required:
        assert hasattr(steps, name), f"lib.steps must export {name!r}"


# ===========================================================================
# 1. Timeline invariants
# ===========================================================================

def test_clip_len_formula():
    """clip_len == dur_s + DISSOLVE_FADE for every beat segment."""
    from lib.steps import compute_clip_len
    from lib.ffcmd import DISSOLVE_FADE

    for dur_s in [1.5, 3.0, 5.7, 10.234]:
        result = compute_clip_len(dur_s)
        assert result == pytest.approx(dur_s + DISSOLVE_FADE), (
            f"compute_clip_len({dur_s}) expected {dur_s + DISSOLVE_FADE}, got {result}"
        )


def test_initial_start_ms_is_title_dur_offset():
    """initial_start_ms() == round(TITLE_DUR * 1000).

    The title card occupies the front of the timeline, so the first beat's
    subtitles must start at TITLE_DUR, not 0.
    """
    from lib.steps import initial_start_ms
    from lib.cards import TITLE_DUR

    expected = round(TITLE_DUR * 1000)
    assert initial_start_ms() == expected, (
        f"initial_start_ms() expected {expected} (TITLE_DUR={TITLE_DUR}s), "
        f"got {initial_start_ms()}"
    )


def test_timeline_uses_actual_probe_duration_not_reported(tmp_path):
    """start_ms in srt_beats is driven by probe_fn (actual duration),
    NOT by the synth_fn's reported audio_length.

    API-reported and probed durations can differ by ~tens of ms. Using the
    wrong value drifts every subtitle that follows. This test injects
    reported != actual and asserts the PROBE value drives the timeline.
    """
    from lib.steps import step_tts
    from lib.cards import TITLE_DUR

    beats = [
        {"id": "b01", "text": "第一段旁白", "visual_type": "still"},
        {"id": "b02", "text": "第二段旁白", "visual_type": "still"},
    ]

    # Reported by synth API vs actual as probed from the mp3 file
    REPORTED_MS = [4000, 3500]
    ACTUAL_MS   = [4137, 3892]   # differ by ~100-400 ms
    reported_iter = iter(REPORTED_MS)
    actual_iter   = iter(ACTUAL_MS)

    def fake_synth(text: str, raw_mp3_path: str) -> int:
        Path(raw_mp3_path).write_bytes(b"\xff\xfb stub raw mp3")
        return next(reported_iter)

    def fake_probe(mp3_path: str) -> int:
        return next(actual_iter)

    def fake_run_ff(argv: list[str], label: str) -> None:
        # Create the polished mp3 output file so callers don't crash.
        for arg in argv:
            if arg.endswith(".mp3") and not arg.endswith(".raw.mp3"):
                Path(arg).write_bytes(b"\xff\xfb polished mp3")

    mp3s, dur_s_list, srt_beats, final_start_ms = step_tts(
        beats=beats,
        scratch=str(tmp_path),
        synth_fn=fake_synth,
        probe_fn=fake_probe,
        run_ff_fn=fake_run_ff,
    )

    title_ms = round(TITLE_DUR * 1000)

    # Beat 0: start_ms = TITLE offset; dur_ms = ACTUAL (not REPORTED)
    assert srt_beats[0]["start_ms"] == title_ms, (
        f"First beat start_ms must be title offset {title_ms}, "
        f"got {srt_beats[0]['start_ms']}"
    )
    assert srt_beats[0]["dur_ms"] == ACTUAL_MS[0], (
        f"dur_ms must be probe value {ACTUAL_MS[0]}, not reported {REPORTED_MS[0]}"
    )

    # Beat 1: start_ms accumulates the ACTUAL probe duration of beat 0
    expected_start_1 = title_ms + ACTUAL_MS[0]
    assert srt_beats[1]["start_ms"] == expected_start_1, (
        f"Second beat start_ms must be {expected_start_1} (title + actual[0]), "
        f"got {srt_beats[1]['start_ms']}"
    )
    assert srt_beats[1]["dur_ms"] == ACTUAL_MS[1], (
        f"dur_ms must be probe value {ACTUAL_MS[1]}, not reported {REPORTED_MS[1]}"
    )

    # final_start_ms = TITLE + sum(ACTUAL)
    expected_final = title_ms + ACTUAL_MS[0] + ACTUAL_MS[1]
    assert final_start_ms == expected_final, (
        f"final_start_ms must be {expected_final}, got {final_start_ms}"
    )

    # dur_s_list entries are in ACTUAL seconds (probe-driven)
    for i, dur_s in enumerate(dur_s_list):
        expected_s = ACTUAL_MS[i] / 1000.0
        assert dur_s == pytest.approx(expected_s), (
            f"dur_s_list[{i}] must be probe-based {expected_s}s, got {dur_s}"
        )


# ===========================================================================
# 2. Cards: bracket invariants
# ===========================================================================

def test_cards_bracket_clip_list():
    """insert_cards places title_clip first and end_clip last in vid_clips.

    The xfade dissolve chain is: [title] + beats + [end].
    """
    from lib.steps import insert_cards
    from lib.ffcmd import DISSOLVE_FADE
    from lib.cards import TITLE_DUR, END_DUR

    beat_clips = ["c0.mp4", "c1.mp4", "c2.mp4"]
    beat_durs  = [3.5, 4.0, 2.8]
    beat_mp3s  = ["b0.mp3", "b1.mp3", "b2.mp3"]

    title_len = TITLE_DUR + DISSOLVE_FADE
    end_len   = END_DUR   + DISSOLVE_FADE

    vid_clips, clip_durs, mp3s = insert_cards(
        beat_clips, beat_durs, beat_mp3s,
        "title.mp4", title_len, "sil_title.mp3",
        "end.mp4",   end_len,   "sil_end.mp3",
    )

    assert vid_clips[0] == "title.mp4", "Title clip must be first"
    assert vid_clips[-1] == "end.mp4",  "End clip must be last"
    assert vid_clips[1:-1] == beat_clips, "Beat clips must be sandwiched between cards"


def test_cards_bracket_mp3_list():
    """insert_cards places sil_title first and sil_end last in mp3s.

    Narration track: [silence_title] + beats + [silence_end].
    """
    from lib.steps import insert_cards
    from lib.ffcmd import DISSOLVE_FADE
    from lib.cards import TITLE_DUR, END_DUR

    beat_clips = ["c0.mp4"]
    beat_durs  = [3.5]
    beat_mp3s  = ["b0.mp3"]

    title_len = TITLE_DUR + DISSOLVE_FADE
    end_len   = END_DUR   + DISSOLVE_FADE

    _, _, mp3s = insert_cards(
        beat_clips, beat_durs, beat_mp3s,
        "title.mp4", title_len, "sil_title.mp3",
        "end.mp4",   end_len,   "sil_end.mp3",
    )

    assert mp3s[0] == "sil_title.mp3", "Title silence must be first in narration"
    assert mp3s[-1] == "sil_end.mp3",  "End silence must be last in narration"
    assert mp3s[1:-1] == beat_mp3s,    "Beat mp3s must be sandwiched"


def test_cards_clip_durs_have_card_lengths():
    """insert_cards sets clip_durs[0] = title_len and clip_durs[-1] = end_len."""
    from lib.steps import insert_cards
    from lib.ffcmd import DISSOLVE_FADE
    from lib.cards import TITLE_DUR, END_DUR

    title_len = TITLE_DUR + DISSOLVE_FADE
    end_len   = END_DUR   + DISSOLVE_FADE
    beat_durs = [3.5]

    _, clip_durs, _ = insert_cards(
        ["c.mp4"], beat_durs, ["b.mp3"],
        "title.mp4", title_len, "sil_title.mp3",
        "end.mp4",   end_len,   "sil_end.mp3",
    )

    assert clip_durs[0]  == pytest.approx(title_len), "clip_durs[0] must be title_len"
    assert clip_durs[-1] == pytest.approx(end_len),   "clip_durs[-1] must be end_len"
    assert clip_durs[1:-1] == beat_durs,              "Middle durs must be beat_durs"


def test_total_audio_ms_formula():
    """total_audio_ms(start_ms_after_beats) == start_ms + round(END_DUR * 1000).

    Covers the full timeline: TITLE + Σbeats + END.
    """
    from lib.steps import total_audio_ms
    from lib.cards import TITLE_DUR, END_DUR

    beat_durs_ms = [3000, 4000, 2000]
    start_ms_after_beats = round(TITLE_DUR * 1000) + sum(beat_durs_ms)

    result = total_audio_ms(start_ms_after_beats)
    expected = start_ms_after_beats + round(END_DUR * 1000)

    assert result == expected, (
        f"total_audio_ms({start_ms_after_beats}) expected {expected}, got {result}"
    )


# ===========================================================================
# 3. 空镜 (no-character shots)
# ===========================================================================

def test_no_character_prompt_appended_and_ref_none():
    """with_character=False: prompt gets the no-person disclaimer; ref=None."""
    from lib.steps import build_shot_prompt

    shot = {"prompt": "清晨跑道细节", "with_character": False}
    prompt, ref = build_shot_prompt(shot, ref_url="http://example.com/ref.png")

    assert "画面中没有任何人物" in prompt, (
        "Empty-character shot must include '画面中没有任何人物' in prompt"
    )
    assert ref is None, (
        "Empty-character shot must pass ref=None so the image model omits people"
    )


def test_character_shot_keeps_ref_and_no_disclaimer():
    """with_character=True: ref_url is preserved; prompt unchanged."""
    from lib.steps import build_shot_prompt

    ref_url = "http://example.com/ref.png"
    shot = {"prompt": "跑者冲刺画面", "with_character": True}
    prompt, ref = build_shot_prompt(shot, ref_url=ref_url)

    assert ref == ref_url, "Character shot must keep ref_url"
    assert "画面中没有任何人物" not in prompt, (
        "Character shot must not have the no-person disclaimer"
    )


def test_default_with_character_is_true():
    """A shot without `with_character` defaults to character=True (keep ref)."""
    from lib.steps import build_shot_prompt

    ref_url = "http://example.com/ref.png"
    shot = {"prompt": "跑者冲刺画面"}  # key absent → default True
    _, ref = build_shot_prompt(shot, ref_url=ref_url)

    assert ref == ref_url, "Default (no with_character key) must keep ref"


# ===========================================================================
# 4. Chart vs still grade
# ===========================================================================

def test_chart_clip_excludes_color_grade(tmp_path):
    """step_chart_clip must NOT pass colorbalance/curves/eq to ffmpeg.

    Charts are excluded from the series look so data colours stay readable.
    """
    from lib.steps import step_chart_clip

    chart_argv: list[str] = []

    def fake_run_ff(argv: list[str], label: str) -> None:
        chart_argv.extend(argv)
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    png = str(tmp_path / "chart.png")
    Path(png).write_bytes(b"\x89PNG stub")

    step_chart_clip(
        png=png,
        clip_len=3.5,
        out_clip=str(tmp_path / "clip_chart.mp4"),
        run_ff_fn=fake_run_ff,
    )

    assert chart_argv, "step_chart_clip must call run_ff_fn"
    combined = " ".join(chart_argv)
    for grade_token in ("colorbalance", "curves", "eq="):
        assert grade_token not in combined, (
            f"Chart clip must NOT include grade filter token '{grade_token}'"
        )
    # Charts are held STATIC: a Ken Burns push-in would crop the axes / units out.
    assert "zoompan" not in combined, "chart clip must be static (no zoompan)"


def test_still_clip_includes_color_grade(tmp_path):
    """step_still_clip with a non-empty grade_vf must include colorbalance in argv.

    This is the contrast side: photographic stills get the series look;
    charts (test above) are excluded. The difference is the invariant.
    """
    from lib.steps import step_still_clip
    from lib.grade import grade_filter

    grade_vf = grade_filter()   # contains "colorbalance"
    still_argv: list[str] = []

    def fake_run_ff(argv: list[str], label: str) -> None:
        still_argv.extend(argv)
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    png = str(tmp_path / "photo.png")
    Path(png).write_bytes(b"\x89PNG stub")

    step_still_clip(
        png=png,
        clip_len=3.5,
        out_clip=str(tmp_path / "clip_still.mp4"),
        grade_vf=grade_vf,
        run_ff_fn=fake_run_ff,
    )

    combined = " ".join(still_argv)
    assert "colorbalance" in combined, (
        "Still clip with grade_vf must include 'colorbalance' in ffmpeg argv"
    )


# ===========================================================================
# 5. Shot rhythm: cap + shot_durations
# ===========================================================================

def test_shot_count_capped_by_max_shots(tmp_path):
    """step_render_shots_clip caps shots to max_shots(clip_len).

    Giving more shots than the clip can hold without a shot dropping below
    MIN_DWELL must result in only max_shots(clip_len) gen_still calls.
    """
    from lib.steps import step_render_shots_clip
    from lib.ffcmd import max_shots

    clip_len = 2.0   # max_shots(2.0) = 2 (2.0 / 0.8 = 2.5 → 2)
    n_allowed = max_shots(clip_len)

    shots = [{"prompt": f"镜头{i}", "with_character": False} for i in range(8)]

    gen_still_calls: list[str] = []

    def fake_gen_still(prompt: str, ref, out: str) -> str:
        gen_still_calls.append(prompt)
        Path(out).write_bytes(b"\x89PNG stub")
        return out

    def fake_run_ff(argv: list[str], label: str) -> None:
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    step_render_shots_clip(
        shots=shots,
        clip_len=clip_len,
        out_clip=str(tmp_path / "out.mp4"),
        bid="b01",
        scratch=str(tmp_path),
        ref_url=None,
        grade_vf=None,
        gen_still_fn=fake_gen_still,
        run_ff_fn=fake_run_ff,
    )

    assert len(gen_still_calls) == n_allowed, (
        f"Expected {n_allowed} gen_still calls (max_shots({clip_len})), "
        f"got {len(gen_still_calls)}"
    )


def test_shot_durations_fallback_to_equal_when_below_min_dwell(tmp_path):
    """When weighted split would produce a slice < MIN_DWELL, falls back to equal.

    Scenario: 2 shots, weights [0.6 (quick), 2.0 (hold)] with clip_len=2.0s.
    Weighted: quick=0.46s < 0.8 MIN_DWELL → fallback to equal 1.0/1.0.
    The -t values in the ffmpeg argv for each sub-clip must be equal.
    """
    from lib.steps import step_render_shots_clip
    from lib.ffcmd import MIN_DWELL

    clip_len = 2.0
    shots = [
        {"prompt": "列举快切", "with_character": False, "pace": "quick"},
        {"prompt": "金句保持", "with_character": False, "pace": "hold"},
    ]

    def fake_gen_still(prompt: str, ref, out: str) -> str:
        Path(out).write_bytes(b"\x89PNG stub")
        return out

    slice_lens: list[float] = []

    def fake_run_ff(argv: list[str], label: str) -> None:
        # Extract per-shot -t arguments (still_clip_cmd has -t at position 4)
        try:
            t_idx = argv.index("-t")
            val = float(argv[t_idx + 1])
            # Only record sub-clip durations (they equal the shot durations)
            if label.endswith(("-shot0", "-shot1", "-shot2", "-shot3")):
                slice_lens.append(val)
        except (ValueError, IndexError):
            pass
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    step_render_shots_clip(
        shots=shots,
        clip_len=clip_len,
        out_clip=str(tmp_path / "out.mp4"),
        bid="b02",
        scratch=str(tmp_path),
        ref_url=None,
        grade_vf=None,
        gen_still_fn=fake_gen_still,
        run_ff_fn=fake_run_ff,
    )

    assert len(slice_lens) == len(shots), (
        f"Expected {len(shots)} shot slices, captured {slice_lens}"
    )

    # Equal split (fallback from weighted where quick < MIN_DWELL)
    for dur in slice_lens:
        assert dur >= MIN_DWELL, (
            f"No shot slice may drop below MIN_DWELL={MIN_DWELL}; got {dur}"
        )
    assert slice_lens[0] == pytest.approx(slice_lens[1], abs=0.01), (
        f"Fallback must produce equal durations; got {slice_lens}"
    )
    assert sum(slice_lens) == pytest.approx(clip_len, abs=0.01), (
        f"Shot durations must sum to clip_len={clip_len}; got sum={sum(slice_lens)}"
    )


# ===========================================================================
# 6. BGM ducking calibration
# ===========================================================================

def test_bgm_ducking_volume_used_in_each_round(tmp_path):
    """Volume passed to measure_gap_fn halves each round when gap < target.

    Round 1 uses initial_volume; round 2 uses initial * 0.5; round 3 uses
    initial * 0.25. This mirrors spike_run.py's `bgm_volume *= 0.5` after
    each below-target round.
    """
    from lib.steps import step_bgm_calib

    base_mp4  = str(tmp_path / "base.mp4")
    ass_path  = str(tmp_path / "subs.ass")
    bgm       = str(tmp_path / "bgm.mp3")
    final_mp4 = str(tmp_path / "final.mp4")

    GAP_TARGET  = 18.0
    INIT_VOL    = 0.12
    MAX_ROUNDS  = 3

    volumes_in_rounds: list[float] = []

    def fake_measure_gap(base: str, bgm_path: str, bgm_volume: str) -> float:
        volumes_in_rounds.append(float(bgm_volume))
        return 10.0   # always < GAP_TARGET → all rounds run

    def fake_run_ff(argv: list[str], label: str) -> None:
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    result = step_bgm_calib(
        base_mp4=base_mp4,
        ass_path=ass_path,
        bgm=bgm,
        final_mp4=final_mp4,
        initial_volume=INIT_VOL,
        max_rounds=MAX_ROUNDS,
        target_db=GAP_TARGET,
        measure_gap_fn=fake_measure_gap,
        run_ff_fn=fake_run_ff,
    )

    assert result["rounds"] == MAX_ROUNDS, (
        f"All {MAX_ROUNDS} rounds must run when gap never reaches target; "
        f"got rounds={result['rounds']}"
    )

    assert len(volumes_in_rounds) == MAX_ROUNDS

    # Round 1 uses initial_volume
    assert volumes_in_rounds[0] == pytest.approx(INIT_VOL, rel=1e-4), (
        f"Round 1 must use initial_volume {INIT_VOL}, got {volumes_in_rounds[0]}"
    )
    # Round 2 uses half
    assert volumes_in_rounds[1] == pytest.approx(INIT_VOL * 0.5, rel=1e-4), (
        f"Round 2 must use {INIT_VOL * 0.5}, got {volumes_in_rounds[1]}"
    )
    # Round 3 uses quarter
    assert volumes_in_rounds[2] == pytest.approx(INIT_VOL * 0.25, rel=1e-4), (
        f"Round 3 must use {INIT_VOL * 0.25}, got {volumes_in_rounds[2]}"
    )

    # History records each round
    assert len(result["history"]) == MAX_ROUNDS


def test_bgm_ducking_stops_when_target_reached(tmp_path):
    """Calibration loop exits early when gap >= target_db.

    Round 1: gap=10 < 18 → halve. Round 2: gap=20 >= 18 → break.
    Only 2 rounds must run (not max_rounds=3).
    """
    from lib.steps import step_bgm_calib

    base_mp4  = str(tmp_path / "base.mp4")
    ass_path  = str(tmp_path / "subs.ass")
    bgm       = str(tmp_path / "bgm.mp3")
    final_mp4 = str(tmp_path / "final.mp4")

    GAP_TARGET = 18.0
    gaps       = [10.0, 20.0]   # round 1 fails, round 2 passes
    gap_iter   = iter(gaps)

    def fake_measure_gap(base: str, bgm_path: str, bgm_volume: str) -> float:
        return next(gap_iter)

    def fake_run_ff(argv: list[str], label: str) -> None:
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    result = step_bgm_calib(
        base_mp4=base_mp4,
        ass_path=ass_path,
        bgm=bgm,
        final_mp4=final_mp4,
        initial_volume=0.12,
        max_rounds=3,
        target_db=GAP_TARGET,
        measure_gap_fn=fake_measure_gap,
        run_ff_fn=fake_run_ff,
    )

    assert result["rounds"] == 2, (
        f"Loop must stop at round 2 when gap >= target; got {result['rounds']} rounds"
    )
    assert result["final_gap"] >= GAP_TARGET, (
        f"final_gap must be the value that triggered the stop; "
        f"got {result['final_gap']}"
    )


def test_bgm_ducking_first_round_uses_initial_volume(tmp_path):
    """The first round runs with exactly initial_volume (no pre-halving)."""
    from lib.steps import step_bgm_calib

    base_mp4  = str(tmp_path / "base.mp4")
    ass_path  = str(tmp_path / "subs.ass")
    bgm       = str(tmp_path / "bgm.mp3")
    final_mp4 = str(tmp_path / "final.mp4")

    INIT_VOL   = 0.12
    GAP_TARGET = 18.0

    first_round_vol: list[float] = []

    def fake_measure_gap(base: str, bgm_path: str, bgm_volume: str) -> float:
        first_round_vol.append(float(bgm_volume))
        return 25.0   # above target → stops after round 1

    def fake_run_ff(argv: list[str], label: str) -> None:
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    step_bgm_calib(
        base_mp4=base_mp4,
        ass_path=ass_path,
        bgm=bgm,
        final_mp4=final_mp4,
        initial_volume=INIT_VOL,
        max_rounds=3,
        target_db=GAP_TARGET,
        measure_gap_fn=fake_measure_gap,
        run_ff_fn=fake_run_ff,
    )

    assert len(first_round_vol) == 1, "Should stop after 1 round"
    assert first_round_vol[0] == pytest.approx(INIT_VOL, rel=1e-4), (
        f"Round 1 must use initial_volume {INIT_VOL}, got {first_round_vol[0]}"
    )


# ===========================================================================
# 7. Camera-move render wiring (Phase 5)
# ===========================================================================

def _capture_vf_args(tmp_path, shots, clip_len):
    """Helper: run step_render_shots_clip with fakes; return list of -vf values."""
    from lib.steps import step_render_shots_clip

    vf_values: list[str] = []

    def fake_gen_still(prompt: str, ref, out: str) -> str:
        Path(out).write_bytes(b"\x89PNG stub")
        return out

    def fake_run_ff(argv: list[str], label: str) -> None:
        # Collect -vf argument values from per-shot still_clip_cmd calls.
        # still_clip_cmd argv layout: [..., "-vf", <filtergraph>, ...]
        try:
            idx = argv.index("-vf")
            vf_values.append(argv[idx + 1])
        except (ValueError, IndexError):
            pass
        for arg in argv:
            if arg.endswith(".mp4"):
                Path(arg).write_bytes(b"\x00mp4 stub")

    step_render_shots_clip(
        shots=shots,
        clip_len=clip_len,
        out_clip=str(tmp_path / "out.mp4"),
        bid="b_cm",
        scratch=str(tmp_path),
        ref_url=None,
        grade_vf=None,
        gen_still_fn=fake_gen_still,
        run_ff_fn=fake_run_ff,
    )
    return vf_values


def test_camera_move_threaded_to_filtergraph(tmp_path):
    """step_render_shots_clip passes shot camera_move into still_clip_cmd's -vf.

    A shot with camera_move='横移' must produce a zoompan z='1.1' + x='on*
    expression in the ffmpeg -vf argument (横移 signature: fixed z=1.1, pan-x).
    Currently FAILS because steps.py ignores camera_move entirely (Task 3-impl).
    """
    shots = [{"prompt": "晨雾跑道全景", "with_character": False,
               "camera_move": "横移"}]
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    # 横移 signature: z='1.2' fixed zoom, x='on*... horizontal pan
    assert "z='1.2'" in vf, (
        f"横移 camera_move must produce fixed z='1.2' in filtergraph; got: {vf}"
    )
    assert re.search(r"x='on\*", vf), (
        f"横移 camera_move must produce x='on*...' pan expression; got: {vf}"
    )
    # Must NOT produce push-in increment (that is 推近, not 横移)
    assert "zoom+" not in vf, (
        f"横移 must not produce push-in zoom+ expression; got: {vf}"
    )


def test_camera_move_竖摇_threaded_to_filtergraph(tmp_path):
    """A shot with camera_move='竖摇' produces fixed z=1.1 + pan-y expression."""
    shots = [{"prompt": "脚步细节特写", "with_character": False,
               "camera_move": "竖摇"}]
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    assert "z='1.2'" in vf, (
        f"竖摇 camera_move must produce fixed z='1.2'; got: {vf}"
    )
    assert re.search(r"y='on\*", vf), (
        f"竖摇 camera_move must produce y='on*...' pan-y expression; got: {vf}"
    )


def test_shot_type_fallback_全景_maps_to_横移(tmp_path):
    """Shot with shot_type='全景' and no camera_move → code falls back to 横移.

    The _shot_type_to_move helper (Task 3-impl) maps 全景/远景 → 横移.
    Currently FAILS because _shot_type_to_move doesn't exist yet.
    """
    shots = [{"prompt": "城市晨景全景", "with_character": False,
               "shot_type": "全景"}]  # NO camera_move key
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    # 横移 signature in the filtergraph (z='1.2' + pan-x)
    assert "z='1.2'" in vf, (
        f"全景 shot_type fallback must map to 横移 (z='1.2'); got: {vf}"
    )
    assert re.search(r"x='on\*", vf), (
        f"全景 shot_type fallback must map to 横移 (x='on*...'); got: {vf}"
    )


def test_shot_type_fallback_特写_maps_to_竖摇_or_推近(tmp_path):
    """Shot with shot_type='特写' and no camera_move → maps to 竖摇 or 推近.

    The plan maps 特写/手部/脚步 → 竖摇 or 推近.  We assert the result is NOT
    横移 (pan-x) — either 竖摇 or 推近 is acceptable for close-up shots.
    Currently FAILS because _shot_type_to_move doesn't exist yet.
    """
    shots = [{"prompt": "心率表盘特写", "with_character": False,
               "shot_type": "特写"}]  # NO camera_move key
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    # Must NOT be 横移 (that's a wide-shot move, wrong for 特写)
    has_pan_x = bool(re.search(r"x='on\*", vf))
    assert not has_pan_x, (
        f"特写 fallback must NOT produce 横移 (pan-x); got: {vf}"
    )
    # Must be either 竖摇 or 推近 (both are valid for 特写/局部)
    has_pan_y   = bool(re.search(r"y='on\*", vf))
    has_push_in = "zoom+" in vf
    assert has_pan_y or has_push_in, (
        f"特写 fallback must produce 竖摇 (y='on*) or 推近 (zoom+); got: {vf}"
    )


def test_degenerate_short_beat_forces_静止(tmp_path):
    """clip_len < MIN_DWELL (0.8s) with a single shot → camera_move forced to 静止.

    This is the ONLY path where code overrides director's camera_move:
    a degenerate whole-beat clip_len < 0.8s single shot.

    Construction: clip_len=0.5 (< 0.8 MIN_DWELL), single shot with camera_move='推近'.
    The render layer must detect slice_s < 0.8 and force 静止 regardless of the
    shot's camera_move field.

    静止 signature (Task 3, DP 统一微推): gentle δ=0.02 drift, cap 1.02 —
    NOT the director's full 推近 push-in (cap 1.1 at intensity 1.0). The
    degenerate guard forces 静止, so the beat gets the gentle drift, not the
    director's 推近.

    Currently FAILS because steps.py doesn't implement the <0.8s guard yet.

    NOTE (verifier constraint): Multi-shot beats are NOT used here because the
    max_shots/shot_durations invariant guarantees multi-shot slices are always
    >= MIN_DWELL (test_shot_durations_fallback_to_equal_when_below_min_dwell:431).
    Only a degenerate single-shot whole-beat clip_len < 0.8 can reach this branch.
    """
    shots = [{"prompt": "快切碎片", "with_character": False,
               "camera_move": "推近"}]  # director said 推近, but clip too short
    clip_len = 0.5   # < MIN_DWELL=0.8 → must force 静止

    vf_values = _capture_vf_args(tmp_path, shots, clip_len=clip_len)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    # 静止 signature (Task 3 统一微推): gentle drift, cap 1.02 — NOT the
    # director's full 推近 push-in (cap 1.1). Forcing to 静止 = gentle drift.
    assert ",1.02)" in vf, (
        f"Degenerate clip_len={clip_len} < 0.8 must force gentle 静止 (δ=0.02 drift, cap 1.02); got: {vf}"
    )
    assert ",1.2)" not in vf, (
        f"Degenerate short clip must not get director's full 推近 push-in (cap 1.2); got: {vf}"
    )


# ---------------------------------------------------------------------------
# camera_intensity threading (Phase 5) — director-chosen move MAGNITUDE flows
# from the shot dict through still_clip_cmd's -vf. Absent → 1.0 (current).
# ---------------------------------------------------------------------------

def test_camera_intensity_threaded_to_filtergraph(tmp_path):
    """A shot with camera_intensity scales the zoom delta in the -vf expression.

    clip_len=3.0 (frames=90), 推近 at intensity=0.5 → inc=0.05/90=0.000556,
    cap=1.05. Proves steps.py reads camera_intensity and passes it down."""
    shots = [{"prompt": "晨光跑道全景", "with_character": False,
              "camera_move": "推近", "camera_intensity": 0.5}]
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    assert "zoom+0.001111" in vf and ",1.1)" in vf, (
        f"camera_intensity=0.5 must halve the 推近 delta (inc=0.001111 cap=1.1); got: {vf}"
    )


def test_absent_camera_intensity_defaults_to_1_0(tmp_path):
    """No camera_intensity on the shot → render uses 1.0 (current amounts).

    clip_len=3.0 (frames=90), 推近 at default 1.0 → inc=0.001111, cap=1.1."""
    shots = [{"prompt": "晨光跑道全景", "with_character": False,
              "camera_move": "推近"}]  # NO camera_intensity
    vf_values = _capture_vf_args(tmp_path, shots, clip_len=3.0)

    assert vf_values, "step_render_shots_clip must call run_ff_fn with -vf"
    vf = vf_values[0]
    assert "zoom+0.002222" in vf and ",1.2)" in vf, (
        f"absent camera_intensity must default to 1.0 (current amounts); got: {vf}"
    )
