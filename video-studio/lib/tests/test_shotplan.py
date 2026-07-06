"""Test-first contract for lib.shotplan.plan_windows (Phase 1 shot-timeline).

plan_windows(beat, clip_len, director_shots, keywords, ledger=None) splits a beat's
narration duration into N content windows (N derived from clip_len / TARGET_WINDOW,
clamped to max_shots(clip_len)), each window getting a distinct visual: director shots
first, then "interim" narration-derived prompts to fill out to N. This is the fix for
POC-era hold/loop bugs (⑤/⑧/⑨) — each beat clip becomes an internal concat of N
different visuals instead of ~3 director shots stretched via Ken-Burns hold.

Window dict contract (R-5): {beat_id, idx, text, dur, prompt, source, visual_type,
with_character, pace, camera_move, camera_intensity, shot_type}. Director-sourced
windows MUST retain the originating shot's camera_move/camera_intensity/shot_type/pace
(losing these degrades POC5/6 camera-move rhythm); interim windows get defaults
pace="normal", camera_move="静止", camera_intensity=1.0, shot_type="".

The `dur` this module assigns is authoritative — render layer must reuse it verbatim
(Task 3/MR-2), not recompute via shot_durations.
"""
import pytest

from lib.shotplan import plan_windows
from lib.visual_ledger import VisualLedger, sig as visual_sig


def _beat(text: str, beat_id: str = "b1") -> dict:
    return {"id": beat_id, "text": text}


def _director_shot(prompt: str, pace: str = "normal", camera_move: str = "推近",
                    camera_intensity: float = 1.0, shot_type: str = "wide",
                    with_character: bool = True) -> dict:
    return {
        "prompt": prompt,
        "pace": pace,
        "camera_move": camera_move,
        "camera_intensity": camera_intensity,
        "shot_type": shot_type,
        "with_character": with_character,
    }


# 44s narration ~= enough for a few hundred Chinese characters; use a long-ish text
# so _split_narration has plenty to slice into N pieces.
_LONG_TEXT = (
    "跑步的呼吸节奏决定了配速的稳定性。"
    "很多新手在起跑阶段用力过猛，导致后半程掉速严重。"
    "科学配速的核心是把全程功率分布均匀，而不是前快后慢。"
    "心率区间训练可以帮助你找到长期可持续的节奏。"
    "对于马拉松选手来说，補水和补给同样重要。"
    "肌肉在长时间运动中会积累乳酸，恢复能力决定了你能跑多远。"
    "呼吸法、步频、配速三者环环相扣，缺一不可。"
)


def test_44s_clip_yields_7_to_8_windows_summing_to_clip_len():
    beat = _beat(_LONG_TEXT)
    clip_len = 44.0
    shots = [_director_shot("远景晨跑者剪影")]
    windows = plan_windows(beat, clip_len, shots, keywords=["跑步", "配速"])

    assert 7 <= len(windows) <= 8
    for w in windows:
        assert 0.8 <= w["dur"] <= 7.0
    assert abs(sum(w["dur"] for w in windows) - clip_len) < 1e-6


def test_director_shots_fewer_than_n_fills_with_distinct_interim():
    beat = _beat(_LONG_TEXT)
    clip_len = 44.0  # -> n_target = round(44/5.5) = 8
    shots = [
        _director_shot("镜头一：晨跑者剪影"),
        _director_shot("镜头二：城市天际线"),
        _director_shot("镜头三：运动手表特写"),
    ]
    windows = plan_windows(beat, clip_len, shots, keywords=["跑步", "配速"])

    assert len(windows) == 8
    director_windows = windows[:3]
    interim_windows = windows[3:]
    assert len(interim_windows) == 5

    for w in director_windows:
        assert w["source"] == "director"

    prompts = []
    for w in interim_windows:
        assert w["source"] == "interim"
        assert w["prompt"]
        prompts.append(w["prompt"])
    assert len(set(prompts)) == len(prompts), "interim prompts must be mutually distinct"


def test_director_shots_more_than_n_truncated_to_n_no_interim():
    beat = _beat("短句旁白。仅十秒左右的内容。")
    clip_len = 10.0  # -> n_target = round(10/5.5) = 2, but let's check max_shots too
    shots = [_director_shot(f"镜头{i}") for i in range(10)]
    windows = plan_windows(beat, clip_len, shots, keywords=["跑步"])

    # n should be small for a 10s beat (short beat -> few windows), clamped from 10
    # down via n_target/max_shots, and no interim windows should appear since
    # director supplied far more shots than needed.
    assert len(windows) == 4 or len(windows) <= 4
    assert all(w["source"] == "director" for w in windows)
    assert len(windows) < len(shots)


def test_single_still_shot_interim_fills_to_n_no_duplicate_prompts():
    beat = _beat(_LONG_TEXT)
    clip_len = 30.0
    shots = [_director_shot("单张静图：跑者出发", with_character=True)]
    windows = plan_windows(beat, clip_len, shots, keywords=["起跑", "训练"])

    assert len(windows) >= 5
    prompts = [w["prompt"] for w in windows]
    assert len(set(prompts)) == len(prompts), "all window prompts must be distinct (no dup visuals)"


def test_window_durations_respect_pace_weights_and_min_dwell():
    beat = _beat(_LONG_TEXT)
    clip_len = 33.0
    shots = [
        _director_shot("金句镜头：定住", pace="hold"),
        _director_shot("常规镜头", pace="normal"),
        _director_shot("列举镜头：快切", pace="quick"),
    ]
    windows = plan_windows(beat, clip_len, shots, keywords=["跑步"])

    by_pace = {}
    for w in windows[:3]:
        by_pace[w["pace"]] = w["dur"]

    assert by_pace["hold"] > by_pace["normal"] > by_pace["quick"]
    for w in windows:
        assert w["dur"] >= 0.8 - 1e-9


def test_n_clamped_to_max_shots_for_very_short_beat():
    # A beat only 2 seconds long: max_shots(2) with MIN_DWELL=0.8 -> floor(2/0.8)=2 (+eps)
    beat = _beat("很短。")
    clip_len = 2.0
    shots = [_director_shot("镜头A"), _director_shot("镜头B"), _director_shot("镜头C")]
    windows = plan_windows(beat, clip_len, shots, keywords=[])

    assert len(windows) <= 2
    assert abs(sum(w["dur"] for w in windows) - clip_len) < 1e-6
    for w in windows:
        assert w["dur"] >= 0.8 - 1e-9


def test_window_dict_structure_and_r5_field_provenance():
    beat = _beat(_LONG_TEXT, beat_id="beat-7")
    clip_len = 44.0
    shots = [
        _director_shot(
            "director镜头", pace="hold", camera_move="推近",
            camera_intensity=0.7, shot_type="close", with_character=True,
        ),
    ]
    windows = plan_windows(beat, clip_len, shots, keywords=["跑步", "补给"])

    required_keys = {
        "beat_id", "idx", "text", "dur", "prompt", "source", "visual_type",
        "with_character", "pace", "camera_move", "camera_intensity", "shot_type",
    }
    for w in windows:
        assert required_keys <= set(w.keys())
        assert w["beat_id"] == "beat-7"

    director_window = windows[0]
    assert director_window["source"] == "director"
    assert director_window["pace"] == "hold"
    assert director_window["camera_move"] == "推近"
    assert director_window["camera_intensity"] == 0.7
    assert director_window["shot_type"] == "close"

    interim_windows = [w for w in windows if w["source"] == "interim"]
    assert interim_windows, "expected interim windows to exercise default fields"
    for w in interim_windows:
        assert w["pace"] == "normal"
        assert w["camera_move"] == "静止"
        assert w["camera_intensity"] == 1.0
        assert w["shot_type"] == ""
        assert w["with_character"] is False


# ---------------------------------------------------------------------------
# Task 5: resume reproducibility — VisualLedger(load_own=False) must let
# plan_windows re-derive the exact same interim prompts as the first render.
# ---------------------------------------------------------------------------


def test_resume_load_own_false_reproduces_first_render_interim_prompts(tmp_path):
    ep_dir = tmp_path / "ep-resume-shotplan"
    ep_dir.mkdir()

    beat = _beat(_LONG_TEXT)
    clip_len = 44.0
    shots = [_director_shot("单张静图：跑者出发", with_character=True)]

    # First render: fresh ledger (nothing of its own preloaded yet).
    first_ledger = VisualLedger(ep_dir)
    first_windows = plan_windows(beat, clip_len, shots, keywords=["起跑", "训练"],
                                  ledger=first_ledger)
    first_prompts = [w["prompt"] for w in first_windows]

    for w in first_windows:
        # Match production: wiring.step_shotplan records the SIGNATURE, not the
        # raw prompt (wiring.py:677 etc.), and _derive_interim_prompt looks up
        # seen(_ledger_sig(candidate)). Recording the raw prompt here would leave
        # the seen-set keyed on raw strings that the sig-based lookup never hits,
        # so the poison (load_own=True) would silently not reproduce.
        first_ledger.record(visual_sig(w["prompt"]))
    first_ledger.save()

    # Naive resume (default load_own=True) preloads the just-saved signatures
    # into `seen`, poisoning _derive_interim_prompt's derivation -> different
    # prompts (this is the bug being fixed).
    poisoned_ledger = VisualLedger(ep_dir, load_own=True)
    poisoned_windows = plan_windows(beat, clip_len, shots, keywords=["起跑", "训练"],
                                     ledger=poisoned_ledger)
    poisoned_prompts = [w["prompt"] for w in poisoned_windows]
    assert poisoned_prompts != first_prompts, (
        "expected load_own=True (naive resume) to poison interim derivation"
    )

    # Fixed resume: load_own=False -> does not preload this episode's own
    # ledger -> plan_windows re-derives the exact same prompts as first render.
    resumed_ledger = VisualLedger(ep_dir, load_own=False)
    resumed_windows = plan_windows(beat, clip_len, shots, keywords=["起跑", "训练"],
                                    ledger=resumed_ledger)
    resumed_prompts = [w["prompt"] for w in resumed_windows]
    assert resumed_prompts == first_prompts
