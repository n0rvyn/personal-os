"""Unit tests for MediaSeams style_prefix single-prepend-point (Task 3).

Verifies that:
1. gen_character_ref / gen_still / gen_video all prepend look's style_prefix.
2. The prefix appears exactly once (no double-prepend from providers).
3. 冷蓝晨光 seam output byte-matches the old media_image internal prepend.
4. No style_prefix (empty string) → prompt passed through unchanged.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from lib import grade
from lib.media_source import GenerateSource, MediaRouter
from lib.wiring import MediaSeams


# ---------------------------------------------------------------------------
# Fake provider helpers
# ---------------------------------------------------------------------------

class _FakeImg:
    """Records the prompt received by gen_character_ref / gen_still."""
    def __init__(self):
        self.char_ref_calls: list[str] = []
        self.still_calls: list[str] = []

    def gen_character_ref(self, prompt: str, out_path: str) -> str:
        self.char_ref_calls.append(prompt)
        return "fake://char_ref"

    def gen_still(self, prompt: str, ref_url, out_path: str) -> str:
        self.still_calls.append(prompt)
        return out_path


class _FakeVid:
    """Records the prompt received by gen_video."""
    def __init__(self):
        self.video_calls: list[str] = []

    def gen_video(self, prompt: str, ref_url: str, out_path: str) -> dict:
        self.video_calls.append(prompt)
        return {"ok": True, "path": out_path, "task_id": "fake"}


def _seam_with_fakes(look: str) -> tuple[MediaSeams, _FakeImg, _FakeVid]:
    """Build a live-mode MediaSeams with fake providers and look-derived style_prefix."""
    seam = MediaSeams(dry=False, provider="minimax", log=lambda _: None)
    fake_img = _FakeImg()
    fake_vid = _FakeVid()
    seam._img = fake_img
    seam._vid = fake_vid
    seam.style_prefix = grade.art_style_prefix(look)
    return seam, fake_img, fake_vid


# ---------------------------------------------------------------------------
# Tests: anime look — all three lines get style_prefix prepended
# ---------------------------------------------------------------------------

def test_gen_character_ref_prepends_anime_style():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_character_ref("主讲人物", "/tmp/ref.png")
    assert len(img.char_ref_calls) == 1
    received = img.char_ref_calls[0]
    assert received.startswith(grade.art_style_prefix("简笔日漫"))


def test_gen_still_prepends_anime_style():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_still("running track scene", None, "/tmp/still.png")
    assert len(img.still_calls) == 1
    received = img.still_calls[0]
    assert received.startswith(grade.art_style_prefix("简笔日漫"))


def test_gen_video_prepends_anime_style():
    seam, _, vid = _seam_with_fakes("简笔日漫")
    seam.gen_video("running coach explains pace", "fake://ref", "/tmp/vid.mp4")
    assert len(vid.video_calls) == 1
    received = vid.video_calls[0]
    assert received.startswith(grade.art_style_prefix("简笔日漫"))


# ---------------------------------------------------------------------------
# Tests: single prepend — prefix appears exactly once
# ---------------------------------------------------------------------------

def test_gen_still_no_double_prepend():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    original_prompt = "a runner on a track"
    seam.gen_still(original_prompt, None, "/tmp/still.png")
    received = img.still_calls[0]
    prefix = grade.art_style_prefix("简笔日漫")
    # prefix appears exactly once
    assert received.count(prefix) == 1


def test_gen_character_ref_no_double_prepend():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_character_ref("主讲人物 male coach", "/tmp/ref.png")
    received = img.char_ref_calls[0]
    prefix = grade.art_style_prefix("简笔日漫")
    assert received.count(prefix) == 1


# ---------------------------------------------------------------------------
# Tests: 冷蓝晨光 regression — seam output byte-matches old internal prepend
# ---------------------------------------------------------------------------

def test_gen_still_cool_morning_byte_matches_old_behavior():
    """Seam-prepended 冷蓝晨光 still == old f'{STYLE_PREFIX}, {prompt}' exact output."""
    seam, img, _ = _seam_with_fakes("冷蓝晨光")
    original_prompt = "runner in morning mist"
    seam.gen_still(original_prompt, None, "/tmp/still.png")
    received = img.still_calls[0]
    expected = f"{grade.art_style_prefix('冷蓝晨光')}, {original_prompt}"
    assert received == expected


# ---------------------------------------------------------------------------
# Tests: empty style_prefix → prompt unchanged
# ---------------------------------------------------------------------------

def test_gen_still_empty_prefix_passthrough():
    seam, img, _ = _seam_with_fakes("冷蓝晨光")
    seam.style_prefix = ""  # override to empty
    seam.gen_still("a plain scene", None, "/tmp/still.png")
    assert img.still_calls[0] == "a plain scene"


def test_gen_character_ref_empty_prefix_passthrough():
    seam, img, _ = _seam_with_fakes("冷蓝晨光")
    seam.style_prefix = ""
    seam.gen_character_ref("主讲人物", "/tmp/ref.png")
    assert img.char_ref_calls[0] == "主讲人物"


def test_gen_video_empty_prefix_passthrough():
    seam, _, vid = _seam_with_fakes("冷蓝晨光")
    seam.style_prefix = ""
    seam.gen_video("running coach", "fake://ref", "/tmp/vid.mp4")
    assert vid.video_calls[0] == "running coach"


# ===========================================================================
# Task 3-tests: step_extract visual resolution + director color_grade override
# ===========================================================================
#
# step_extract tests: FAIL before Task 3-impl with
#   AttributeError: module 'lib.grade' has no attribute 'resolve_look'
# Integration tests: FAIL before Task 3-impl because parse_shotlist strips
#   color_grade and there is no back-prop / apply_director_color_grade helper.
# All tests go GREEN after Task 3-impl.
# ===========================================================================

import json as _json_mod
import os
import types as _types_mod
from pathlib import Path as _Path
from unittest.mock import MagicMock as _MagicMock

_PLUGIN_ROOT_WIRING = _Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step_extract_harness(tmp_path, *, art_style="写实电影", color_grade="",
                                narrative_style=""):
    """Return (step_fns, seams, ctx) wired for step_extract tests.

    Uses dry=True MediaSeams so gen_character_ref doesn't call the real API.
    selection is a SimpleNamespace so we can freely set art_style / color_grade
    without dataclass constraints.
    """
    from lib.wiring import build_step_fns, MediaSeams

    fixture = {
        "character_ref": {"prompt": "主讲人物"},
        "beats": [
            {
                "id": "b00", "text": "hello world", "visual_type": "still",
                "image_prompt": "runner on track", "video_prompt": "",
            }
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(_json_mod.dumps(fixture), encoding="utf-8")

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)

    selection = _types_mod.SimpleNamespace(
        art_style=art_style,
        style=narrative_style,
        color_grade=color_grade,
        doc_refs=None,
        character_ref="主讲人物",
        seq="01", slug="test",
        source="", verify_status="", learning_goal="", topic="",
    )

    cfg = _MagicMock()
    cfg.style = ""

    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=lambda *a, **kw: None,
        probe_ms=lambda _p: 5000,
        log=lambda m: None,
        selection=selection, dry=True,
    )

    ctx = {
        "ep": {
            "final": tmp_path / "final.mp4",
            "assets_dir": assets_dir,
            "shotlist": tmp_path / "shotlist.json",
        },
        "doc_refs": None,
    }
    return step_fns, seams, ctx


def _shenjing_step():
    return {
        "name": "分镜", "kind": "agent", "agent": "director",
        "inputs": ["script", "config"], "artifact": "shotlist_art",
        "gate": [{"fn": "check_artifact"}], "retry": 1,
    }


def _beats_live(n=2):
    """Live-mode beats (no visual_type → trigger director dispatch path)."""
    return [
        {"beat_id": f"b{i:02d}", "text": f"text {i}", "anchor": f"anchor {i}"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# step_extract: art style → seams.style_prefix
# ---------------------------------------------------------------------------

def test_step_extract_anime_art_style_sets_seams_prefix(tmp_path, monkeypatch):
    """step_extract with art_style='简笔日漫' → seams.style_prefix contains 'anime'.

    FAILS before Task 3-impl: AttributeError on grade.resolve_look.
    PASSES after Task 3-impl: uses grade.resolve_art_style(selection.art_style).
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    step_fns, seams, ctx = _make_step_extract_harness(tmp_path, art_style="简笔日漫")
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert "anime" in seams.style_prefix, (
        f"Expected 'anime' in seams.style_prefix for art_style='简笔日漫', "
        f"got: {seams.style_prefix!r}"
    )


def test_step_extract_color_grade_sets_grade_vf(tmp_path, monkeypatch):
    """step_extract with color_grade='暖金' → ctx['grade_vf'] matches warm-gold filter.

    FAILS before Task 3-impl: AttributeError on grade.resolve_look.
    PASSES after Task 3-impl: uses grade.resolve_color_grade for ctx['grade_vf'].
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    from lib import grade
    step_fns, seams, ctx = _make_step_extract_harness(tmp_path, color_grade="暖金")
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    expected_vf = grade.resolve_color_grade("暖金")
    assert ctx.get("grade_vf") == expected_vf, (
        f"Expected grade_vf={expected_vf!r} for color_grade='暖金', "
        f"got: {ctx.get('grade_vf')!r}"
    )


def test_step_extract_narrative_style_does_not_affect_art_prefix(tmp_path, monkeypatch):
    """Narrative style (selection.style) must NOT influence seams.style_prefix.

    art_style='简笔日漫' + style='写实电影 irrelevant narrative' → prefix has 'anime'.

    FAILS before Task 3-impl: AttributeError on grade.resolve_look.
    PASSES after Task 3-impl: art_style drives style_prefix, style field ignored.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    step_fns, seams, ctx = _make_step_extract_harness(
        tmp_path,
        art_style="简笔日漫",
        narrative_style="写实电影 irrelevant narrative text",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert "anime" in seams.style_prefix, (
        f"seams.style_prefix must come from art_style, not narrative style, "
        f"got: {seams.style_prefix!r}"
    )


def test_step_extract_wiring_does_not_reference_resolve_look():
    """build_step_fns source must not reference grade.resolve_look (retired symbol).

    FAILS before Task 3-impl: wiring.py:352 still calls grade.resolve_look.
    PASSES after Task 3-impl: resolve_look references removed.
    """
    import inspect
    from lib.wiring import build_step_fns
    source = inspect.getsource(build_step_fns)
    assert "resolve_look" not in source, (
        "wiring.build_step_fns must not reference grade.resolve_look (retired in Task 3-impl)"
    )


# ---------------------------------------------------------------------------
# Director color_grade override — DP-001 full chain integration
# ---------------------------------------------------------------------------

def test_director_color_grade_survives_backprop_and_overrides_grade_vf(
        tmp_path, monkeypatch):
    """Director assigns color_grade='暖金' on first beat:
      1. parse_shotlist preserves it in result[0]['color_grade']
      2. back-prop copies it into ctx['beats'][0]['color_grade']
      3. apply_director_color_grade overrides ctx['grade_vf'] to 暖金 filter

    Full chain via real make_storyboard_dispatch (fake persona only).

    FAILS before Task 3-impl:
      - parse_shotlist strips color_grade (result[0] has none)
      - no back-prop for color_grade in the loop at wiring.py:1100-1107
      - no apply_director_color_grade helper
    PASSES after Task 3-impl.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch
    from lib import grade

    n = 2
    # Director output: first beat carries episode-level color_grade override
    director_shotlist = [
        {
            "visual_type": "still",
            "shots": [{"prompt": f"shot {i}", "shot_type": "全景",
                        "with_character": False, "pace": "normal"}],
            "keywords": [],
            **({"color_grade": "暖金"} if i == 0 else {}),
        }
        for i in range(n)
    ]

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = _Path(scratch_dir) / expected
        out.write_text(_json_mod.dumps(director_shotlist), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _MagicMock()
    cfg.budget.video_shots_per_episode = 2
    cfg.budget.video_motion_threshold = 0.6
    cfg.budget.video_floor = 1

    beats = _beats_live(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx = {
        "beats": beats,
        "ep": ep,
        "grade_vf": grade.resolve_color_grade("冷蓝晨光"),  # series default pre-set
    }

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=_PLUGIN_ROOT_WIRING, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    # 1. color_grade must survive parse+back-prop into beats
    assert ctx["beats"][0].get("color_grade") == "暖金", (
        f"color_grade='暖金' must survive parse + back-prop into beats[0], "
        f"got beats[0]: {ctx['beats'][0]!r}"
    )
    # 2. grade_vf must be overridden from cool-morning to warm-gold
    expected_warm = grade.resolve_color_grade("暖金")
    assert ctx["grade_vf"] == expected_warm, (
        f"ctx['grade_vf'] must be overridden to 暖金 (={expected_warm!r}), "
        f"got: {ctx['grade_vf']!r}"
    )


def test_director_no_color_grade_keeps_series_default(tmp_path, monkeypatch):
    """Director gives no color_grade → ctx['grade_vf'] keeps the series default.

    Regression net: series default must not be clobbered when director omits override.
    PASSES both before and after Task 3-impl.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch
    from lib import grade

    n = 2
    # Director shotlist with NO color_grade on any beat
    director_shotlist = [
        {
            "visual_type": "still",
            "shots": [{"prompt": f"shot {i}", "shot_type": "全景",
                        "with_character": False, "pace": "normal"}],
            "keywords": [],
        }
        for i in range(n)
    ]

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = _Path(scratch_dir) / expected
        out.write_text(_json_mod.dumps(director_shotlist), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _MagicMock()
    cfg.budget.video_shots_per_episode = 2
    cfg.budget.video_motion_threshold = 0.6
    cfg.budget.video_floor = 1

    cool_morning_vf = grade.resolve_color_grade("冷蓝晨光")
    beats = _beats_live(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx = {
        "beats": beats,
        "ep": ep,
        "grade_vf": cool_morning_vf,  # series default pre-set
    }

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=_PLUGIN_ROOT_WIRING, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert ctx["grade_vf"] == cool_morning_vf, (
        f"Without director color_grade, grade_vf must keep series default, "
        f"got: {ctx['grade_vf']!r}"
    )


def test_mediaseams_synth_passes_configured_voice_id(monkeypatch):
    """cfg.audio.voice_id must reach media_tts.synth — else changing the config
    voice silently does nothing (the pre-fix bug: synth ignored the config voice)."""
    from lib import wiring as _wiring
    captured = {}

    def fake_synth(text, out_path, timeout=120, voice_id=None):
        captured["voice_id"] = voice_id
        return 1000

    monkeypatch.setattr(_wiring.media_tts, "synth", fake_synth)
    seams = _wiring.MediaSeams(dry=False, provider="doubao", log=lambda _m: None,
                               voice_id="Chinese (Mandarin)_Warm_Girl")
    seams.synth("你好", "/tmp/x.mp3")
    assert captured["voice_id"] == "Chinese (Mandarin)_Warm_Girl"


def test_mediaseams_synth_empty_voice_passes_none(monkeypatch):
    """Empty voice_id → None (media_tts falls back to its own default)."""
    from lib import wiring as _wiring
    captured = {}
    monkeypatch.setattr(_wiring.media_tts, "synth",
                        lambda text, out, timeout=120, voice_id=None: captured.update(voice_id=voice_id) or 1)
    seams = _wiring.MediaSeams(dry=False, provider="doubao", log=lambda _m: None, voice_id="")
    seams.synth("你好", "/tmp/x.mp3")
    assert captured["voice_id"] is None


# ===========================================================================
# Task 3-tests: 镜头计划接入 pipeline + gen-still 消费 windows (Phase 1
# shot-timeline). FAIL before Task 3-impl:
#   - step_fns["镜头计划"] KeyError (no such step_fn registered yet)
#   - ctx["windows"] never written
#   - step_render_shots_clip called with old sb[i]["shots"]/beat shots (few),
#     not the N expanded window shots
# All green after Task 3-impl.
# ===========================================================================

def _make_shotplan_harness(tmp_path, *, beats, clip_len_by_index, sb=None):
    """Build (step_fns, ctx) wired for step_shotplan / step_gen_still tests.

    dry=True MediaSeams so no live API calls; cfg is a bare MagicMock with no
    syllabus_path/cursor_path configured (so `_done_episode_dirs` fail-closes
    to () per MF-4 — ctx also carries no "seq" key, which alone is sufficient).
    """
    from lib.wiring import build_step_fns, MediaSeams

    fixture = {
        "character_ref": {"prompt": "主讲人物"},
        "beats": beats,
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(_json_mod.dumps(fixture), encoding="utf-8")

    ep_dir = tmp_path / "episode"
    ep_dir.mkdir(exist_ok=True)
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""

    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=lambda *a, **kw: None,
        probe_ms=lambda _p: 5000,
        log=lambda m: None,
        selection=None, dry=True,
    )

    ctx = {
        "beats": beats,
        "clip_len_by_index": dict(clip_len_by_index),
        "clips_by_index": {},
        "ep": {"final": ep_dir / "final.mp4", "assets_dir": assets_dir},
        "ref_url": None,
        "grade_vf": None,
    }
    if sb is not None:
        ctx["sb"] = sb
    return step_fns, ctx


def _shotplan_step():
    return {"name": "镜头计划", "kind": "code", "agent": None,
            "inputs": ["tts", "分镜", "config"], "artifact": None, "gate": []}


def _gen_still_step():
    return {"name": "gen-still", "kind": "code", "agent": None,
            "inputs": ["tts", "config", "scratch_dir"],
            "artifact": "stills_dir", "gate": [{"fn": "check_artifact"}]}


_LONG_BEAT_TEXT = (
    "跑步的呼吸节奏决定了配速能否长久维持。"
    "很多初学者一开始就用力过猛，前五分钟很快，后面却掉速严重。"
    "科学配速的核心是把体感强度控制在可持续区间，而不是凭一时兴奋加速。"
    "呼吸要均匀，脚步要放松，核心保持稳定。"
    "长期坚持这种节奏，才能把耐力真正训练出来。"
)


def _three_beats_one_long():
    return [
        {
            "id": "b00", "visual_type": "still", "text": _LONG_BEAT_TEXT,
            "image_prompt": "跑者清晨慢跑", "keywords": ["呼吸", "配速", "核心"],
        },
        {
            "id": "b01", "visual_type": "still", "text": "短句示范。",
            "image_prompt": "教练示范配速", "keywords": [],
        },
        {
            "id": "b02", "visual_type": "multi", "text": "三种跑姿对比讲解。",
            "shots": ["跑姿A侧面", "跑姿B侧面", "跑姿C侧面"], "keywords": ["跑姿"],
        },
    ]


def test_step_shotplan_writes_ctx_windows_long_beat_multiple_distinct(tmp_path):
    """step_shotplan(step, ctx, scratch) with 3 beats (one long still) + fake
    clip_len_by_index → writes ctx['windows']; the long beat's window list has
    >1 entries with distinct prompts.

    FAILS before Task 3-impl: step_fns has no '镜头计划' key → KeyError.
    """
    beats = _three_beats_one_long()
    clip_len_by_index = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index,
    )

    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    assert "windows" in ctx, "step_shotplan must write ctx['windows']"
    windows0 = ctx["windows"][0]
    assert len(windows0) > 1, (
        f"long beat (clip_len=44s) must split into >1 windows, got {len(windows0)}"
    )
    prompts0 = [w["prompt"] for w in windows0]
    assert len(set(prompts0)) == len(prompts0), (
        f"all windows for one beat must have distinct prompts, got {prompts0}"
    )
    assert sum(w["dur"] for w in windows0) == pytest.approx(44.0, abs=1e-6), (
        "window durations must sum to clip_len"
    )


def test_step_gen_still_consumes_N_windows_not_old_shots(tmp_path, monkeypatch):
    """step_gen_still must read ctx['windows'][i] (N expanded shots), not the
    old sb[i]['shots']/beat-native shots (which is far fewer for the long
    beat) — assert the shots list handed to step_render_shots_clip has
    length == len(ctx['windows'][i]) for every still/multi beat.

    FAILS before Task 3-impl: step_gen_still still reads the old few-shot
    lists (director sb or beat['shots']/beat['image_prompt']), never
    ctx['windows'].
    """
    from lib import wiring as _wiring

    beats = _three_beats_one_long()
    clip_len_by_index = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index,
    )
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    recorded_calls = []

    def fake_render(shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
                     gen_still_fn=None, run_ff_fn=None, durs=None, fetch_lib_clip_fn=None):
        recorded_calls.append({"bid": bid, "shots": shots, "durs": durs})
        Path(out_clip).write_bytes(b"\x00mp4 stub")

    monkeypatch.setattr(_wiring.steps_mod, "step_render_shots_clip", fake_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    assert len(recorded_calls) == 3, (
        f"expected one render call per still/multi beat, got {len(recorded_calls)}"
    )
    by_bid = {c["bid"]: c for c in recorded_calls}
    for i, beat in enumerate(beats):
        bid = beat["id"]
        n_windows = len(ctx["windows"][i])
        n_shots = len(by_bid[bid]["shots"])
        assert n_shots == n_windows, (
            f"{bid}: step_render_shots_clip got {n_shots} shots, "
            f"expected N={n_windows} (ctx['windows'][{i}])"
        )
        # MR-2: durs= must be passed and match the windows' authoritative dur.
        expected_durs = [w["dur"] for w in ctx["windows"][i]]
        assert by_bid[bid]["durs"] == expected_durs, (
            f"{bid}: durs= must be the windows' authoritative dur list verbatim, "
            f"got {by_bid[bid]['durs']!r} expected {expected_durs!r}"
        )


def test_step_gen_still_windows_preserve_camera_fields_R5(tmp_path, monkeypatch):
    """R-5: window dicts passed from step_shotplan through step_gen_still into
    step_render_shots_clip must retain camera_move/camera_intensity/shot_type
    from the director shot — not silently defaulted.

    FAILS before Task 3-impl: gen-still never wires ctx['windows'] through,
    so this never even executes the new code path (KeyError first).
    """
    from lib import wiring as _wiring

    beats = [
        {
            "id": "b00", "visual_type": "still", "text": "教练讲解摆臂动作要领。",
            "image_prompt": "摆臂特写", "keywords": ["摆臂"],
        },
    ]
    clip_len_by_index = {0: 6.0}
    sb = [{
        "shots": [{
            "prompt": "教练侧面演示摆臂", "with_character": True,
            "camera_move": "横移", "camera_intensity": 1.6,
            "shot_type": "全景", "pace": "hold",
        }],
        "keywords": ["摆臂"],
    }]
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index, sb=sb,
    )
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    recorded_calls = []

    def fake_render(shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
                     gen_still_fn=None, run_ff_fn=None, durs=None, fetch_lib_clip_fn=None):
        recorded_calls.append(shots)
        Path(out_clip).write_bytes(b"\x00mp4 stub")

    monkeypatch.setattr(_wiring.steps_mod, "step_render_shots_clip", fake_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    assert len(recorded_calls) == 1
    director_shot = recorded_calls[0][0]
    assert director_shot.get("source") == "director"
    assert director_shot["camera_move"] == "横移", (
        f"director camera_move must survive shotplan→gen-still, got {director_shot!r}"
    )
    assert director_shot["camera_intensity"] == 1.6
    assert director_shot["shot_type"] == "全景"


def test_step_shotplan_fixture_mode_no_sb_derives_N_from_beat_own_shots(tmp_path):
    """No regression: fixture mode (beats already carry visual_type,
    director not run — ctx has no 'sb' key) → step_shotplan still derives N
    windows from the beat's own shots/image_prompt (not dependent on ctx['sb']).
    """
    beats = _three_beats_one_long()
    clip_len_by_index = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index,
    )
    assert "sb" not in ctx

    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    assert "windows" in ctx
    for i in range(len(beats)):
        assert len(ctx["windows"][i]) >= 1


# ===========================================================================
# Task 4-tests: video beat 去 loop（S2V once + 接力）+ 单窗口封顶 (FAIL-first)
#
# step_gen_video must no longer render one loop/tpad-filled clip spanning the
# whole beat. It must render the S2V window ONCE (capped at src_dur, itself
# capped at lib.shotplan.WINDOW_MAX), then cover the beat's remaining
# duration with relay still windows (via steps_mod.step_render_shots_clip),
# concatenated into a single beat clip. ctx["clips_by_index"][i] stays a
# single clip path (contract unchanged).
#
# FAILS before Task 4-impl: step_gen_video currently renders exactly one
# ffcmd.video_clip_cmd call spanning the FULL clip_len (loop/tpad-filled) and
# never calls step_render_shots_clip on the success path — so the "≥1 still
# render call" and "S2V window capped at src_dur" assertions below fail.
# ===========================================================================

def _video_beat_step():
    return {"name": "gen-video", "kind": "code", "agent": None,
            "inputs": ["gen-still", "config", "scratch_dir"],
            "artifact": "clips_dir", "gate": [{"fn": "check_artifact"}]}


def test_step_gen_video_s2v_once_plus_relay_stills_cover_full_beat(tmp_path, monkeypatch):
    """clip_len=30, S2V src_dur=6 → 1 video_clip render (the S2V window, ~6s)
    + >=1 relay still render(s) whose durations sum with the S2V window to
    clip_len (30s). ctx['clips_by_index'][i] remains a single clip path.
    """
    from lib import wiring as _wiring

    beats = [
        {
            "id": "b04", "visual_type": "video",
            "text": "教练示范配速节奏。",
            "video_prompt": "教练慢跑示范配速", "image_prompt": "教练慢跑",
        },
    ]
    clip_len_by_index = {0: 30.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index,
    )

    # probe_ms is wired via build_step_fns closure inside the harness at 5000ms;
    # rebuild step_fns with probe_ms=6000ms (src_dur=6.0s) for this beat's S2V clip.
    from lib.wiring import build_step_fns, MediaSeams
    fixture_path = tmp_path / "fixture.json"
    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    run_ff_calls = []

    def _spy_run_ff(argv, label):
        run_ff_calls.append({"argv": argv, "label": label})
        # ffmpeg output path is the last argv element; touch it so any
        # downstream copy/concat step finds a real file.
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=_spy_run_ff,
        probe_ms=lambda _p: 6000,
        log=lambda m: None,
        selection=None, dry=True,
    )

    render_calls = []

    def _spy_render(shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
                     gen_still_fn=None, run_ff_fn=None, durs=None, fetch_lib_clip_fn=None):
        render_calls.append({"bid": bid, "clip_len": clip_len, "durs": durs})
        _Path(out_clip).write_bytes(b"\x00mp4 stub")

    monkeypatch.setattr(_wiring.steps_mod, "step_render_shots_clip", _spy_render)

    step_fns["gen-video"](_video_beat_step(), ctx, tmp_path)

    # 1 video_clip render (S2V window) — identified by the "clip-{bid}" run_ff label.
    video_calls = [c for c in run_ff_calls if c["label"] == "clip-b04"]
    assert len(video_calls) == 1, (
        f"expected exactly 1 video_clip render for the S2V window, got "
        f"{len(video_calls)}: {run_ff_calls}"
    )
    video_argv = " ".join(str(x) for x in video_calls[0]["argv"])
    assert "-t 6.0" in video_argv, (
        f"S2V window must be capped at src_dur=6.0s (not the full clip_len=30s): "
        f"{video_argv}"
    )

    # >=1 relay still render(s) covering the beat's remaining duration.
    assert len(render_calls) >= 1, (
        "expected >=1 relay still render (step_render_shots_clip) to cover the "
        "beat's remaining duration after the S2V window, got 0"
    )
    relay_total = sum(
        sum(c["durs"]) if c["durs"] else c["clip_len"] for c in render_calls
    )
    assert relay_total == pytest.approx(30.0 - 6.0, abs=1e-6), (
        f"relay still window(s) must cover the remaining beat duration "
        f"(30 - 6 = 24s), got {relay_total}"
    )

    # Total S2V window + relay total == full clip_len.
    assert 6.0 + relay_total == pytest.approx(30.0, abs=1e-6)

    # Relay must be WINDOWED into distinct ~5s content windows, not rendered as
    # ONE still held for the whole remainder (the ⑤ hold, relay edition — a
    # regression the first Task-4 impl shipped: it passed _beat_shots (1 shot)
    # for `rem`, producing a ~24s held still). rem=24s → >=3 windows, each
    # capped at WINDOW_MAX. A held-still relay has len(durs)==1 and fails here.
    from lib.shotplan import WINDOW_MAX
    relay_durs = [d for c in render_calls if c["durs"] for d in c["durs"]]
    assert len(relay_durs) >= 3, (
        f"relay must be windowed into multiple ~5s content windows (rem=24s → "
        f">=3), not held as one still; got {len(relay_durs)} window(s): {relay_durs}"
    )
    assert max(relay_durs) <= WINDOW_MAX + 1e-6, (
        f"each relay window must be <= WINDOW_MAX ({WINDOW_MAX}s), got {relay_durs}"
    )

    # ctx['clips_by_index'][0] contract unchanged: a single clip path.
    assert 0 in ctx["clips_by_index"], "clips_by_index[0] must be set"
    assert isinstance(ctx["clips_by_index"][0], str), (
        "clips_by_index[0] must remain a single clip path (str), not a list"
    )


def test_step_gen_video_s2v_failure_fallback_is_windowed(tmp_path, monkeypatch):
    """When live S2V generation FAILS, the still fallback must be WINDOWED over
    the full clip_len (like still/multi + the relay), NOT one image held for the
    whole beat (~40s = the ⑤ hold on the failure path). The cached-S2V reuse
    branch (SPIKE_S2V_CLIP) never exercises this path, so only a forced-failure
    test catches a regression here. clip_len=40 → >=3 windows, each <= WINDOW_MAX.
    """
    from lib import wiring as _wiring
    from lib.wiring import build_step_fns, MediaSeams
    from lib.shotplan import WINDOW_MAX

    monkeypatch.delenv("SPIKE_S2V_CLIP", raising=False)

    beats = [
        {
            "id": "b04", "visual_type": "video",
            "text": "教练在跑道上示范标准跑姿，前脚掌落地重心前移步频保持稳定，动作连贯自然。",
            "video_prompt": "教练慢跑示范", "image_prompt": "教练慢跑定格",
            "keywords": ["跑姿", "步频"],
        },
    ]
    _, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index={0: 40.0},
    )

    # Force S2V failure so the fallback branch (wiring.py ~709-721) runs.
    monkeypatch.setattr(
        MediaSeams, "gen_video",
        lambda self, prompt, ref_url, out: {"ok": False, "reason": "forced"},
    )

    fixture_path = tmp_path / "fixture.json"
    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    def _spy_run_ff(argv, label):
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path, seq="01", slug="test",
        run_ff=_spy_run_ff, probe_ms=lambda _p: 6000, log=lambda m: None,
        selection=None, dry=True,
    )

    render_calls = []

    def _spy_render(shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
                     gen_still_fn=None, run_ff_fn=None, durs=None, fetch_lib_clip_fn=None):
        render_calls.append({"bid": bid, "clip_len": clip_len, "durs": durs})
        _Path(out_clip).write_bytes(b"\x00mp4 stub")

    monkeypatch.setattr(_wiring.steps_mod, "step_render_shots_clip", _spy_render)

    step_fns["gen-video"](_video_beat_step(), ctx, tmp_path)

    # Exactly one clip produced, and it is a single path (contract preserved).
    assert isinstance(ctx["clips_by_index"][0], str)
    # The fallback render must be WINDOWED, not one held still.
    fb_durs = [d for c in render_calls if c["durs"] for d in c["durs"]]
    assert len(fb_durs) >= 3, (
        f"S2V-failure fallback must window the full beat into multiple ~5s "
        f"windows (clip_len=40 → >=3), not hold one still; got {fb_durs}"
    )
    assert max(fb_durs) <= WINDOW_MAX + 1e-6, (
        f"each fallback window must be <= WINDOW_MAX ({WINDOW_MAX}s), got {fb_durs}"
    )
    assert sum(fb_durs) == pytest.approx(40.0, abs=1e-6), (
        f"fallback windows must cover the full clip_len (40s), got {sum(fb_durs)}"
    )


def test_step_chart_long_beat_followup_is_windowed(tmp_path, monkeypatch):
    """Long chart beat (clip_len > CHART_MAX+1.5): the chart holds for
    CHART_MAX_SECONDS, then the REMAINDER must be WINDOWED into distinct ~5s
    content windows (⑧ '其余窗口切内容'), NOT one still held for
    (clip_len - 12s). clip_len=40 → rem=28 → >=3 windows, each <= WINDOW_MAX.
    """
    from lib import wiring as _wiring
    from lib.wiring import build_step_fns, MediaSeams, CHART_MAX_SECONDS
    from lib.shotplan import WINDOW_MAX

    beats = [{
        "id": "c1", "visual_type": "chart",
        "text": "查 VDOT 表反推各档训练配速，轻松跑马拉松配速阈值间歇都有对应区间，照着练效率更高更稳。",
        "chart_title": "VDOT 训练配速参考表",
        "chart_rows": [["轻松跑 E", "6:30"], ["阈值 T", "4:50"]],
        "keywords": ["配速"],
    }]
    _, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index={0: 40.0},
    )

    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    def _touch_run_ff(argv, label):
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=tmp_path / "fixture.json",
        seq="01", slug="test", run_ff=_touch_run_ff, probe_ms=lambda _p: 5000,
        log=lambda m: None, selection=None, dry=True,
    )

    # Stub the heavy chart PNG render + the chart-hold clip so only the followup
    # windowing is under test.
    monkeypatch.setattr(_wiring.media_chart, "render_pace_table",
                        lambda *a, **k: _Path(a[-1]).write_bytes(b"\x00png"))
    monkeypatch.setattr(_wiring.steps_mod, "step_chart_clip",
                        lambda png, dur, out, run_ff_fn=None: _Path(out).write_bytes(b"\x00"))

    render_calls = []

    def _spy_render(shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
                     gen_still_fn=None, run_ff_fn=None, durs=None, fetch_lib_clip_fn=None):
        render_calls.append({"durs": durs})
        _Path(out_clip).write_bytes(b"\x00mp4 stub")

    monkeypatch.setattr(_wiring.steps_mod, "step_render_shots_clip", _spy_render)

    step_fns["chart"]({"name": "chart"}, ctx, tmp_path)

    fu_durs = [d for c in render_calls if c["durs"] for d in c["durs"]]
    rem = 40.0 - CHART_MAX_SECONDS
    assert len(fu_durs) >= 3, (
        f"chart followup remainder must be windowed (rem={rem} → >=3 windows), "
        f"not one held still; got {fu_durs}"
    )
    assert max(fu_durs) <= WINDOW_MAX + 1e-6, (
        f"each followup window must be <= WINDOW_MAX ({WINDOW_MAX}s), got {fu_durs}"
    )
    assert sum(fu_durs) == pytest.approx(rem, abs=1e-6), (
        f"followup windows must cover the post-chart remainder ({rem}s), "
        f"got {sum(fu_durs)}"
    )


def test_video_clip_window_dur_never_exceeds_window_max(tmp_path, monkeypatch):
    """单窗口封顶: any rendered window's duration must be <= WINDOW_MAX (7.0s) —
    a long S2V src_dur (9s, > WINDOW_MAX) must NOT produce a video_clip render
    longer than WINDOW_MAX; the remainder must be relay stills.
    """
    from lib import wiring as _wiring
    from lib.shotplan import WINDOW_MAX
    from lib.wiring import build_step_fns, MediaSeams

    beats = [
        {
            "id": "b05", "visual_type": "video",
            "text": "教练讲解长距离配速策略变化。",
            "video_prompt": "教练长镜头讲解配速策略", "image_prompt": "教练讲解",
        },
    ]
    clip_len_by_index = {0: 20.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index,
    )

    fixture_path = tmp_path / "fixture.json"
    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    run_ff_calls = []

    def _spy_run_ff(argv, label):
        run_ff_calls.append({"argv": argv, "label": label})
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    # src_dur=9.0s (> WINDOW_MAX=7.0s) for this beat's S2V source.
    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=_spy_run_ff,
        probe_ms=lambda _p: 9000,
        log=lambda m: None,
        selection=None, dry=True,
    )

    monkeypatch.setattr(
        _wiring.steps_mod, "step_render_shots_clip",
        lambda shots, clip_len, out_clip, bid, scratch, ref_url, grade_vf,
               gen_still_fn=None, run_ff_fn=None, durs=None:
            _Path(out_clip).write_bytes(b"\x00mp4 stub"),
    )

    step_fns["gen-video"](_video_beat_step(), ctx, tmp_path)

    video_calls = [c for c in run_ff_calls if c["label"] == "clip-b05"]
    assert len(video_calls) == 1
    video_argv = " ".join(str(x) for x in video_calls[0]["argv"])
    assert f"-t {WINDOW_MAX}" in video_argv, (
        f"S2V window must cap at WINDOW_MAX={WINDOW_MAX}, not src_dur=9.0 or "
        f"clip_len=20.0: {video_argv}"
    )
    assert "-t 9.0" not in video_argv and "-t 20.0" not in video_argv


# ---------------------------------------------------------------------------
# Task 5: step_assemble — video >= audio assertion + padding (D-008: mux's
# `-shortest` must never silently truncate the narration; if the xfade'd
# video track comes up short of the narration track, pad the video's tail).
# ---------------------------------------------------------------------------

def _make_assemble_harness(tmp_path, *, monkeypatch, probe_ms):
    """Build (step_fns, ctx) wired for step_assemble tests.

    dry=True MediaSeams (card bg gen uses the cached .spike still, $0).
    ensure_bgm / measure_gap / step_bgm_calib are monkeypatched to short-
    circuit the BGM ducking loop to exactly 1 round (irrelevant to the
    video>=audio guard under test) so these tests stay isolated to the
    padding decision, not the whole downstream pipeline.

    `probe_ms` is the caller-supplied probe function (path -> int ms) that
    drives the video_track-vs-narration duration comparison inside
    step_assemble.
    """
    from lib import wiring as _wiring
    from lib.wiring import build_step_fns, MediaSeams

    fixture = {
        "character_ref": {"prompt": "主讲人物"},
        "beats": [
            {"id": "b00", "visual_type": "still", "text": "第一段旁白。",
             "image_prompt": "跑者清晨慢跑"},
            {"id": "b01", "visual_type": "still", "text": "第二段旁白。",
             "image_prompt": "教练示范配速"},
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(_json_mod.dumps(fixture), encoding="utf-8")

    ep_dir = tmp_path / "episode"
    ep_dir.mkdir(exist_ok=True)
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    clip0 = tmp_path / "clip0.mp4"
    clip1 = tmp_path / "clip1.mp4"
    mp3_0 = tmp_path / "b00.mp3"
    mp3_1 = tmp_path / "b01.mp3"
    for p in (clip0, clip1, mp3_0, mp3_1):
        p.write_bytes(b"\x00stub")

    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    cfg.audio.bgm_duck_db = -24.0

    run_ff_calls = []

    def _spy_run_ff(argv, label):
        run_ff_calls.append({"argv": argv, "label": label})
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=_spy_run_ff,
        probe_ms=probe_ms,
        log=lambda m: None,
        selection=None, dry=True,
    )

    # Short-circuit the BGM ducking calibration loop to 1 round — irrelevant
    # to the video>=audio guard, and it needs a real measure_gap_fn.
    monkeypatch.setattr(_wiring, "ensure_bgm",
                        lambda assets_dir, run_ff: str(assets_dir / "bgm.mp3"))
    monkeypatch.setattr("lib.ffprobe_util.measure_gap",
                        lambda *a, **kw: -20.0)  # >= target_db=-24.0 → 1 round

    ctx = {
        "beats": fixture["beats"],
        "fixture": fixture,
        "grade_vf": None,
        "clips_by_index": {0: str(clip0), 1: str(clip1)},
        "clip_len_by_index": {0: 10.0, 1: 8.0},
        "mp3s": [str(mp3_0), str(mp3_1)],
        "ep": {"final": ep_dir / "final.mp4", "assets_dir": assets_dir},
        "ref_url": None,
        "start_ms": 20000,
        "ass_path": str(tmp_path / "subs.ass"),
    }
    return step_fns, ctx, run_ff_calls


def _assemble_step():
    return {"name": "assemble", "kind": "code", "agent": None,
            "inputs": ["gen-still", "gen-video", "tts"], "artifact": None, "gate": []}


def test_step_assemble_pads_video_track_when_shorter_than_narration(tmp_path, monkeypatch):
    """video_track probes 0.3s shorter than narration → step_assemble must
    trigger a pad (tpad-clone tail) BEFORE mux, so the final mux's video
    input duration >= the narration duration — `-shortest` must never be
    the thing that reconciles a short video against the narration (that
    silently truncates the narration, D-008's exact failure mode).
    """
    from lib import wiring as _wiring

    # video.mp4 probes to 29.7s; narration.m4a probes to 30.0s (short by 0.3s,
    # beyond the default tol=0.05 in ffcmd.assert_video_ge_audio).
    def _probe_ms(path):
        p = str(path)
        if "narration" in p:
            return 30000
        if "video" in p:
            return 29700
        return 5000  # cards / other probes, irrelevant here

    step_fns, ctx, run_ff_calls = _make_assemble_harness(
        tmp_path, monkeypatch=monkeypatch, probe_ms=_probe_ms,
    )

    step_fns["assemble"](_assemble_step(), ctx, tmp_path)

    pad_calls = [c for c in run_ff_calls if "tpad" in " ".join(str(x) for x in c["argv"])
                 and c["label"] not in ("card-title", "card-end")]
    assert pad_calls, (
        "expected a tpad-clone padding run_ff call when video_track (29.7s) "
        f"is short of narration (30.0s) beyond tol: calls={run_ff_calls}"
    )

    mux_calls = [c for c in run_ff_calls if c["label"] == "mux-base"]
    assert len(mux_calls) == 1
    mux_video_input = mux_calls[0]["argv"][1]  # ["-i", video, "-i", audio, ...]
    assert "pad" in mux_video_input or mux_video_input != os.path.join(
        str(tmp_path), "video.mp4"
    ), (
        "mux-base must consume the PADDED video track (not the original short "
        f"video.mp4): {mux_calls[0]['argv']}"
    )


def test_step_assemble_no_pad_when_video_ge_audio(tmp_path, monkeypatch):
    """Normal scenario (video_track >= narration) → no padding call, mux-base
    consumes the original xfade'd video_track unchanged (no regression)."""
    def _probe_ms(path):
        p = str(path)
        if "narration" in p:
            return 30000
        if "video" in p:
            return 30100  # video >= audio (no shortfall)
        return 5000

    step_fns, ctx, run_ff_calls = _make_assemble_harness(
        tmp_path, monkeypatch=monkeypatch, probe_ms=_probe_ms,
    )

    step_fns["assemble"](_assemble_step(), ctx, tmp_path)

    pad_calls = [c for c in run_ff_calls if "tpad" in " ".join(str(x) for x in c["argv"])
                 and c["label"] not in ("card-title", "card-end")]
    assert not pad_calls, (
        f"video_track (30.1s) already >= narration (30.0s) — no padding call "
        f"expected, got: {pad_calls}"
    )

    mux_calls = [c for c in run_ff_calls if c["label"] == "mux-base"]
    assert len(mux_calls) == 1
    mux_video_input = mux_calls[0]["argv"][1]
    assert mux_video_input == os.path.join(str(tmp_path), "video.mp4"), (
        f"mux-base must consume the original video_track unchanged when no "
        f"padding is needed: {mux_calls[0]['argv']}"
    )


# ===========================================================================
# Task 3 (Phase 2 media-source-layer plan): MediaRouter wiring —
# prepare_render / build_step_fns / call sites.
#
# All harness-based tests below FAIL before Task 3-impl with a hard
# `TypeError: build_step_fns() got an unexpected keyword argument 'router'`
# (build_step_fns doesn't accept `router` yet). They go green once Task
# 3-impl threads `router` through build_step_fns and step_extract calls
# `router.set_style(...)`.
# ===========================================================================

class _RouterFakeSeams:
    """Fake seams for router call-site wiring tests. Records gen_still /
    gen_character_ref calls; style_prefix/provider are plain settable attrs
    like the real MediaSeams so step_extract can set/read them.
    """

    def __init__(self):
        self.gen_still_calls: list[str] = []
        self.gen_character_ref_calls: list[str] = []
        self.style_prefix = ""
        self.provider = "minimax"

    def gen_still(self, prompt, ref_url, out_path):
        self.gen_still_calls.append(prompt)
        _Path(out_path).write_bytes(b"\x00fake-still")
        return out_path

    def gen_character_ref(self, prompt, out_path):
        self.gen_character_ref_calls.append(prompt)
        _Path(out_path).write_bytes(b"\x00fake-ref")
        return out_path

    def gen_video(self, prompt, ref_url, out_path):
        return {"ok": False, "reason": "unused in Task 3 router wiring tests"}


def _make_router_harness(tmp_path, *, beats, library=None, art_style="写实电影",
                          character_refs=None, fixture_character_ref=None,
                          host_enabled=True):
    """Build (step_fns, ctx, router, fake_seams) with a MediaRouter wired
    through build_step_fns(router=...) (Task 3). `library` seeds cfg.library
    (style -> kind), consumed by router.set_style via LibrarySource.from_config
    once step_extract runs. `character_refs` seeds cfg.character_refs (Task 1,
    Phase 5, DP-P5-002 — style -> ref path/prompt binding); `fixture_character_ref`
    overrides the fixture's own character_ref.prompt (fallback-path source).
    `host_enabled` seeds cfg.host_enabled (Task 2, Phase 5, DP-P5-003 —
    with_character/host-window gate on fetch_lib_clip_fn).
    """
    from lib.wiring import build_step_fns
    from lib.media_source import MediaRouter, GenerateSource

    fixture = {
        "character_ref": {"prompt": fixture_character_ref or "主讲人物"},
        "beats": beats,
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(_json_mod.dumps(fixture), encoding="utf-8")

    ep_dir = tmp_path / "episode"
    ep_dir.mkdir(exist_ok=True)
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    cfg = _MagicMock()
    cfg.syllabus_path = ""
    cfg.cursor_path = ""
    cfg.library = library or {}
    # MR#4: explicit real dict/bool, never rely on MagicMock auto-attr truthiness.
    cfg.character_refs = character_refs if character_refs is not None else {}
    cfg.host_enabled = bool(host_enabled)

    fake_seams = _RouterFakeSeams()
    router = MediaRouter(GenerateSource(fake_seams), cfg=cfg)

    selection = _types_mod.SimpleNamespace(
        art_style=art_style, style="", color_grade="",
        doc_refs=None, character_ref="主讲人物",
        seq="01", slug="test", source="", verify_status="",
        learning_goal="", topic="",
    )

    def _spy_run_ff(argv, label):
        # ffmpeg output path is the last argv element; touch it so any
        # downstream copy/concat in step_render_shots_clip finds a real file.
        _Path(argv[-1]).write_bytes(b"\x00mp4 stub")

    step_fns = build_step_fns(
        cfg, seams=fake_seams, fixture_path=fixture_path,
        seq="01", slug="test",
        run_ff=_spy_run_ff,
        probe_ms=lambda _p: 5000,
        log=lambda m: None,
        selection=selection, dry=True,
        router=router,
    )

    ctx = {
        "ep": {"final": ep_dir / "final.mp4", "assets_dir": assets_dir,
               "shotlist": ep_dir / "shotlist.json"},
        "doc_refs": None,
        "clips_by_index": {},
    }
    return step_fns, ctx, router, fake_seams


def test_step_extract_calls_router_set_style(tmp_path, monkeypatch):
    """step_extract must call router.set_style(selection.art_style) so the
    router binds the episode's LibrarySource at the same time seams.style_prefix
    is set (MR-1: router uses its constructor-injected self.cfg, not a closure).

    FAILS before Task 3-impl: build_step_fns() has no `router` kwarg → TypeError.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, _fake_seams = _make_router_harness(
        tmp_path, beats=beats, art_style="简笔日漫",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert router.style == "简笔日漫", (
        f"step_extract must call router.set_style(selection.art_style), "
        f"got router.style={router.style!r}"
    )


def test_build_step_fns_call_sites_use_router_gen_still_five_times():
    """MR-2 (grep-confirmed in the plan): exactly 5 still call sites — content
    still, S2V-fail fallback, relay, chart degrade, chart followup — route
    through `router.gen_still`; the 1 gen_video call site routes through
    `router.gen_video`. `resolve_character_ref_url` (character ref) and
    `_card_bg` (cards) must stay direct on `seams.gen_character_ref` /
    `seams.gen_still` (D-013 — presenter ref + cards are never library-sourced).

    FAILS before Task 3-impl: source has 0 'router.gen_still' call sites
    (all 5 still read 'gen_still_fn=seams.gen_still') and 'seams.gen_video('
    at the video call site, not 'router.gen_video('.
    """
    import inspect
    from lib.wiring import build_step_fns
    source = inspect.getsource(build_step_fns)

    assert source.count("gen_still_fn=router.gen_still") == 5, (
        f"expected exactly 5 call sites wired to router.gen_still (MR-2), got "
        f"{source.count('gen_still_fn=router.gen_still')}"
    )
    assert source.count("gen_still_fn=seams.gen_still") == 0, (
        "no still call site may still read gen_still_fn=seams.gen_still after "
        "Task 3-impl (all 5 must route through router)"
    )
    assert "router.gen_video(" in source, (
        "the gen_video call site must route through router.gen_video"
    )
    assert "seams.gen_video(" not in source, (
        "seams.gen_video must no longer be called directly (routes through router)"
    )
    # D-013: character_ref + cards stay direct on seams (never library-sourced).
    assert "seams.gen_character_ref" in source, (
        "resolve_character_ref_url must keep calling seams.gen_character_ref "
        "directly (D-013)"
    )
    assert "actual = seams.gen_still(prompt, card_ref, out)" in source, (
        "_card_bg must keep calling seams.gen_still directly (D-013)"
    )


def test_step_gen_still_no_regression_when_library_empty(tmp_path, monkeypatch):
    """无回归硬门: with no library bound for this episode's style, every
    content-still window must still generate exactly once via the router's
    transparent generate fallback, with the same prompt as before routing
    (router pass-through == old direct seams.gen_still behavior).

    FAILS before Task 3-impl: build_step_fns() has no `router` kwarg → TypeError.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = _three_beats_one_long()
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={},  # no binding for 写实电影 → all-generate
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    assert router.library is None, (
        "no library binding for this style → router.library must stay None"
    )
    ctx["clip_len_by_index"] = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)
    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    total_windows = sum(len(ctx["windows"][i]) for i in ctx["windows"])
    assert len(fake_seams.gen_still_calls) == total_windows, (
        f"no library bound → every window must still generate exactly once "
        f"via router's transparent generate fallback; "
        f"calls={len(fake_seams.gen_still_calls)} windows={total_windows}"
    )
    # Expected = what step_render_shots_clip hands to gen_still_fn, which is
    # build_shot_prompt(shot, ref_url)[0] (pre-existing 空镜 disclaimer for
    # with_character=False windows) — NOT the raw window prompt. The router
    # forwards this value verbatim (media_source.MediaRouter.gen_still), so
    # exact equality here proves router transparency; the disclaimer is applied
    # identically whether gen_still_fn is router.gen_still or seams.gen_still.
    from lib.steps import build_shot_prompt
    all_prompts = [
        build_shot_prompt(w, ctx["ref_url"])[0]
        for i in sorted(ctx["windows"]) for w in ctx["windows"][i]
    ]
    assert fake_seams.gen_still_calls == all_prompts, (
        "router pass-through must not alter the prompts handed to generate"
    )


def test_step_gen_still_persists_pexels_ids_for_cross_episode_dedup_MF1(tmp_path, monkeypatch):
    """MF-1: step_gen_still must call ledger.save() so Pexels ids recorded during
    fetch_clip persist to disk — otherwise a pure still/multi 写实 episode (all
    b-roll, no S2V/chart save site) writes "ids":[] and the NEXT episode re-picks
    the same clips (cross-episode dedup silently broken; a single-episode live
    render only exercises the in-memory set, so it can't surface this).
    """
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)
    beats = _three_beats_one_long()
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={},
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    ctx["clip_len_by_index"] = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)
    # simulate a Pexels fetch_clip having recorded an id during this episode's
    # gen-still (fetch_lib_clip_fn → PexelsSource.fetch_clip → ledger.record_id).
    ctx["visual_ledger"].record_id(4242)
    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    # A fresh ledger loaded from this episode's dir (== the next episode's
    # prior_dirs source) must see the id — proving step_gen_still persisted it.
    from lib.visual_ledger import VisualLedger
    ep_dir = ctx["ep"]["final"].parent
    reloaded = VisualLedger(str(ep_dir))
    assert reloaded.seen_id(4242), (
        "step_gen_still must ledger.save() so recorded Pexels ids persist to disk; "
        "without it the on-disk ledger keeps ids:[] and cross-episode dedup breaks"
    )


def test_step_gen_still_routes_to_library_stub_when_bound(tmp_path, monkeypatch):
    """D-003 (library-first): once config binds this episode's art_style to a
    stub library, every content-still window must be served by LibrarySource
    (stub), never falling through to generate.gen_still.

    FAILS before Task 3-impl: build_step_fns() has no `router` kwarg → TypeError.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = _three_beats_one_long()
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={"写实电影": "stub"}, art_style="写实电影",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    assert router.library is not None, (
        "a stub-bound style must make router.set_style build a LibrarySource"
    )
    ctx["clip_len_by_index"] = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)
    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    assert fake_seams.gen_still_calls == [], (
        f"a library-bound style must serve every still window from the stub "
        f"library, never calling generate.gen_still; got "
        f"{fake_seams.gen_still_calls}"
    )


def test_character_ref_and_cards_stay_direct_on_seams_when_library_bound(
        tmp_path, monkeypatch):
    """D-013: even when the episode's style is library-bound, the presenter
    character ref must still be generated directly via seams (never sourced
    from the library) — resolve_character_ref_url doesn't go through router.

    FAILS before Task 3-impl: build_step_fns() has no `router` kwarg → TypeError.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={"写实电影": "stub"}, art_style="写实电影",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert len(fake_seams.gen_character_ref_calls) == 1, (
        f"resolve_character_ref_url must call seams.gen_character_ref directly "
        f"exactly once, unaffected by the style's library binding; got "
        f"{fake_seams.gen_character_ref_calls}"
    )


# ===========================================================================
# Phase 5 Task 1-tests: config character_refs + host_enabled + per-style ref
# resolution in step_extract (DP-P5-002). FAIL-first — written before Task
# 1-impl adds cfg.character_refs consultation to step_extract's _ensure_ref_url
# call sites.
# ===========================================================================


def test_step_extract_per_style_ref_resolves_from_config(tmp_path, monkeypatch):
    """cfg.character_refs binds art_style='写实电影' to a per-style host prompt
    → step_extract's _ensure_ref_url must resolve THAT prompt (not the
    fixture's per-module character_ref).

    FAILS before Task 1-impl: cfg.character_refs isn't consulted; ctx['ref_url']
    resolves from the fixture's '主讲人物' fallback instead of the per-style ref.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, art_style="写实电影",
        character_refs={"写实电影": "写实主持人"},
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert fake_seams.gen_character_ref_calls == ["写实主持人"], (
        f"step_extract must resolve ctx['ref_url'] from "
        f"cfg.character_refs[art_style] ('写实主持人'), not the fixture's "
        f"per-module character_ref ('主讲人物'); got calls="
        f"{fake_seams.gen_character_ref_calls}"
    )


def test_step_extract_no_per_style_ref_falls_back_to_fixture_character_ref(
        tmp_path, monkeypatch):
    """No per-style binding for this art_style → falls back to the fixture's
    character_ref.prompt (Phase 4 backward-compat, MR#3: fixture-mode fallback
    keeps the '.prompt' tail / '主讲人物' default — no silent-downgrade to a
    unified cross-mode formula that would drop it)."""
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, art_style="简笔日漫",
        character_refs={"写实电影": "写实主持人"},  # no binding for 简笔日漫
        fixture_character_ref="教练阿May",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)

    assert fake_seams.gen_character_ref_calls == ["教练阿May"], (
        f"no per-style ref for art_style='简笔日漫' → must fall back to the "
        f"fixture's character_ref.prompt ('教练阿May'), unchanged from Phase 4; "
        f"got calls={fake_seams.gen_character_ref_calls}"
    )


# ===========================================================================
# Phase-3 query-writer plan — Task 3-tests: query-writer persona +
# make_querywriter_dispatch wiring (content-matched query + constraint).
# FAIL before Task 3-impl:
#   - agents/query-writer.md absent
#   - lib.wiring has no make_querywriter_dispatch
# All green after Task 3-impl.
# ===========================================================================

def test_query_writer_persona_md_exists_and_has_constraints():
    """agents/query-writer.md must exist, be non-empty, and encode the hard
    constraint (禁 office/code, 要 running/scene) per crystal D-007."""
    md_path = _PLUGIN_ROOT_WIRING / "agents" / "query-writer.md"
    assert md_path.is_file(), f"agents/query-writer.md must exist at {md_path}"
    text = md_path.read_text(encoding="utf-8")
    assert text.strip(), "agents/query-writer.md must be non-empty"
    lower = text.lower()
    assert "office" in lower and "code" in lower, (
        "persona must name banned visual categories (office/code) as constraints"
    )
    assert "running" in lower or "runner" in lower, (
        "persona must name the required visual category (running/runner scenes)"
    )


def _mk_qw_window(source, prompt, text="旁白文本", idx=0, beat_id="b00"):
    return {
        "beat_id": beat_id, "idx": idx, "text": text, "prompt": prompt,
        "dur": 5.0, "source": source, "visual_type": "image",
        "with_character": False, "pace": "normal",
        "camera_move": "静止", "camera_intensity": 1.0, "shot_type": "",
    }


def _qw_step():
    return {"name": "query-writer", "kind": "agent", "agent": "query-writer",
            "inputs": ["镜头计划", "config"], "artifact": None, "gate": []}


def _fake_querywriter_persona(entries, called=None):
    def fake(agent, prompt, scratch_dir, expected, **kw):
        if called is not None:
            called.append((agent, prompt))
        assert agent == "query-writer"
        out = _Path(scratch_dir) / expected
        out.write_text(_json_mod.dumps(entries), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}
    return fake


def test_make_querywriter_dispatch_fixture_short_circuit(tmp_path):
    """ctx['fixture'] truthy → closure must NOT call dispatch_persona_fn
    (MR#2: zero live claude -p in fixture render eval); windows untouched."""
    from lib.wiring import make_querywriter_dispatch

    called = []
    fake = _fake_querywriter_persona(
        [{"beat_i": 0, "idx": 0, "query": "runner on track"}], called)
    interim_w = _mk_qw_window("interim", "旧-interim-prompt")
    ctx = {
        "fixture": {"beats": []},
        "windows": {0: [interim_w]},
    }
    cfg = _MagicMock()

    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)

    assert called == [], "fixture pass-through must not invoke dispatch_persona_fn"
    assert "query" not in ctx["windows"][0][0], (
        "fixture pass-through must not write query"
    )
    assert ctx["windows"][0][0]["prompt"] == "旧-interim-prompt", (
        "fixture pass-through must not alter interim prompt"
    )


def test_make_querywriter_dispatch_writes_query_overwrites_interim_keeps_director(
        tmp_path):
    """Success path: interim window gets ['query'] + overwritten ['prompt'];
    director window gets ['query'] but ['prompt'] stays untouched (#3)."""
    from lib.wiring import make_querywriter_dispatch

    interim_w = _mk_qw_window("interim", "旧-interim-prompt", beat_id="b00", idx=0)
    director_w = _mk_qw_window("director", "导演镜头-prompt", beat_id="b01", idx=0)
    ctx = {
        "windows": {0: [interim_w], 1: [director_w]},
    }
    entries = [
        {"beat_i": 0, "idx": 0, "query": "runner coding on laptop"},  # banned
        {"beat_i": 1, "idx": 0, "query": "sunrise road jogger"},
    ]
    fake = _fake_querywriter_persona(entries)
    cfg = _MagicMock()

    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)

    w0 = ctx["windows"][0][0]
    assert "query" in w0 and w0["query"], "interim window must get a sanitized query"
    for banned in ("coding", "laptop"):
        assert banned not in w0["query"], (
            f"query must be sanitized, got {w0['query']!r}"
        )
    assert w0["prompt"] == w0["query"], (
        "interim window prompt must be overwritten to the query (DP-P1-001 closure)"
    )
    assert w0["prompt"] != "旧-interim-prompt"

    w1 = ctx["windows"][1][0]
    assert w1.get("query") == "sunrise road jogger", (
        "director window must also get a query"
    )
    assert w1["prompt"] == "导演镜头-prompt", (
        "director window prompt must NOT be overwritten (#3 no-regression hard door)"
    )


def test_make_querywriter_dispatch_swallows_dispatch_failure_no_halt(tmp_path):
    """Dispatch failure (ok=False) must not raise and must not write query;
    interim prompt is preserved (fallback, MR#1); ctx['queries_art'] unset."""
    from lib.wiring import make_querywriter_dispatch

    interim_w = _mk_qw_window("interim", "旧-interim-prompt")
    ctx = {"windows": {0: [interim_w]}}

    def fake_fail(agent, prompt, scratch_dir, expected, **kw):
        return {"ok": False, "reason": "boom"}

    cfg = _MagicMock()
    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake_fail, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)  # must not raise

    assert "query" not in ctx["windows"][0][0]
    assert ctx["windows"][0][0]["prompt"] == "旧-interim-prompt"
    assert "queries_art" not in ctx


def test_make_querywriter_dispatch_swallows_exception_no_raise(tmp_path):
    """dispatch_persona_fn raising must be swallowed (try/except), not crash
    the pipeline — same fallback contract as an ok=False result."""
    from lib.wiring import make_querywriter_dispatch

    interim_w = _mk_qw_window("interim", "旧-interim-prompt")
    ctx = {"windows": {0: [interim_w]}}

    def fake_raise(agent, prompt, scratch_dir, expected, **kw):
        raise RuntimeError("claude -p exploded")

    cfg = _MagicMock()
    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake_raise, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)  # must not raise

    assert "query" not in ctx["windows"][0][0]
    assert ctx["windows"][0][0]["prompt"] == "旧-interim-prompt"


def test_make_querywriter_dispatch_no_windows_key_no_keyerror(tmp_path):
    """video/chart beats never populate ctx['windows'] (built elsewhere/
    skipped) — the closure must tolerate ctx with no 'windows' key at all,
    without raising KeyError."""
    from lib.wiring import make_querywriter_dispatch

    ctx = {}  # no "windows" key at all
    fake = _fake_querywriter_persona([])
    cfg = _MagicMock()
    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)  # must not raise


# ===========================================================================
# Phase-3 query-writer plan — Task 4-tests: 生成整合端到端 — query 经 render
# 消费 (DP-P1-001 收尾). Verifies the closure's w["prompt"]=query overwrite
# (Task 3) actually reaches the prompt gen_still_fn receives (via
# build_shot_prompt @ steps.py:130), and that director windows + the
# no-query fallback path are unaffected (no-regression hard door, #3).
#
# FAILS before Task 4-impl only if the in-place ctx["windows"] mutation from
# make_querywriter_dispatch does not survive into step_gen_still's render
# call — per Task 4-impl step 1, this is expected to already hold (same ctx
# object, in-place mutation), so this task is primarily an end-to-end
# connectivity proof, not new source.
# ===========================================================================

def _qw_e2e_beat_and_sb():
    """One beat, one director shot, clip_len sized so plan_windows emits
    2 windows (1 director verbatim + 1 interim fill) — mixed coexistence."""
    beats = [{
        "id": "b00", "visual_type": "still",
        "text": "教练讲解节奏控制的核心原理与工程系数换算方法。",
        "image_prompt": "跑者清晨慢跑", "keywords": ["节奏"],
    }]
    sb = [{
        "shots": [{"prompt": "导演镜头-prompt", "with_character": True}],
        "keywords": ["节奏"],
    }]
    return beats, sb


def test_querywriter_query_e2e_flows_into_gen_still_prompt(tmp_path, monkeypatch):
    """Success path: querywriter overwrites the interim window's prompt to
    its sanitized query, and that query — not the stale interim text — is
    what gen_still_fn actually receives; the sibling director window keeps
    its own director prompt untouched (no-regression, #3), proving director
    and interim coexist correctly in one beat."""
    from lib import wiring as _wiring
    from lib.wiring import make_querywriter_dispatch

    recorded_prompts: list[str] = []

    def fake_gen_still(self, prompt, ref_url, out_path):
        recorded_prompts.append(prompt)
        dst = os.path.splitext(out_path)[0] + ".jpg"
        Path(dst).write_bytes(b"\xff\xd8stub")
        return dst

    monkeypatch.setattr(_wiring.MediaSeams, "gen_still", fake_gen_still)

    beats, sb = _qw_e2e_beat_and_sb()
    clip_len_by_index = {0: 11.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index, sb=sb,
    )
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    windows0 = ctx["windows"][0]
    assert len(windows0) == 2, (
        f"expected 1 director + 1 interim window (mixed), got {windows0}"
    )
    director_w = next(w for w in windows0 if w["source"] == "director")
    interim_w = next(w for w in windows0 if w["source"] == "interim")
    old_interim_prompt = interim_w["prompt"]
    old_director_prompt = director_w["prompt"]

    entries = [
        {"beat_i": 0, "idx": director_w["idx"], "query": "sunrise road jogger"},
        # banned tokens — sanitize_query must strip them before it lands in prompt
        {"beat_i": 0, "idx": interim_w["idx"], "query": "runner coding on laptop"},
    ]
    fake_persona = _fake_querywriter_persona(entries)
    cfg = _MagicMock()
    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake_persona, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)

    assert interim_w["prompt"] == interim_w["query"], (
        "interim window prompt must be overwritten to its sanitized query"
    )
    assert interim_w["prompt"] != old_interim_prompt
    for banned in ("coding", "laptop"):
        assert banned not in interim_w["prompt"]
    assert director_w["prompt"] == old_director_prompt, (
        "director window prompt must NOT be touched by query-writer (#3)"
    )

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    # build_shot_prompt appends a no-person disclaimer for with_character=False
    # shots (interim windows) — expected, unrelated to query-writer — so match
    # by prefix/substring rather than exact equality.
    assert any(p.startswith(interim_w["prompt"]) for p in recorded_prompts), (
        f"gen_still_fn must receive the overwritten interim query prompt "
        f"{interim_w['prompt']!r}; got {recorded_prompts!r}"
    )
    assert not any(p.startswith(old_interim_prompt) for p in recorded_prompts), (
        "gen_still_fn must not receive the stale pre-query interim prompt"
    )
    assert old_director_prompt in recorded_prompts, (
        "gen_still_fn must still receive the untouched director prompt "
        "(no-regression, #3)"
    )


def test_querywriter_query_e2e_no_query_keeps_interim_prompt_for_render(
        tmp_path, monkeypatch):
    """No query (fixture short-circuit / dispatch failure) → interim window
    render must receive the original Phase-1 interim prompt, unchanged —
    the closure never overwrote it, so render must not either."""
    from lib import wiring as _wiring
    from lib.wiring import make_querywriter_dispatch

    recorded_prompts: list[str] = []

    def fake_gen_still(self, prompt, ref_url, out_path):
        recorded_prompts.append(prompt)
        dst = os.path.splitext(out_path)[0] + ".jpg"
        Path(dst).write_bytes(b"\xff\xd8stub")
        return dst

    monkeypatch.setattr(_wiring.MediaSeams, "gen_still", fake_gen_still)

    beats, sb = _qw_e2e_beat_and_sb()
    clip_len_by_index = {0: 11.0}
    step_fns, ctx = _make_shotplan_harness(
        tmp_path, beats=beats, clip_len_by_index=clip_len_by_index, sb=sb,
    )
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    windows0 = ctx["windows"][0]
    director_w = next(w for w in windows0 if w["source"] == "director")
    interim_w = next(w for w in windows0 if w["source"] == "interim")
    old_interim_prompt = interim_w["prompt"]
    old_director_prompt = director_w["prompt"]

    def fake_fail(agent, prompt, scratch_dir, expected, **kw):
        return {"ok": False, "reason": "boom"}

    cfg = _MagicMock()
    dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=fake_fail, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("query-writer", _qw_step(), ctx, tmp_path)

    assert interim_w["prompt"] == old_interim_prompt, (
        "dispatch failure must not touch the interim prompt"
    )

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    # build_shot_prompt appends a no-person disclaimer for with_character=False
    # shots (interim windows) — expected, unrelated to query-writer — so match
    # by prefix rather than exact equality.
    assert any(p.startswith(old_interim_prompt) for p in recorded_prompts), (
        "gen_still_fn must receive the Phase-1 interim prompt unchanged when "
        "query-writer produced no query"
    )
    assert old_director_prompt in recorded_prompts


# ===========================================================================
# Phase 4 (P4-pexels plan) — Task 2-tests: render library-first wiring.
# step_gen_still must thread a fetch_lib_clip_fn=... kwarg into
# step_render_shots_clip built on router.fetch_library_clip (MR#3: ledger
# from ctx["visual_ledger"] threaded through so id-dedup isn't silently inert
# in production). No library bound (generate-only style) → the same
# threaded callable, calling through to router.fetch_library_clip, returns
# None for every window (router.library is None) → falls through to
# generate unchanged (no-regression, already covered by
# test_step_gen_still_no_regression_when_library_empty).
#
# FAILS before Task 2-impl: build_step_fns' "gen-still" step calls
# steps_mod.step_render_shots_clip(...) with no fetch_lib_clip_fn kwarg —
# spying on the call captures kwargs.get("fetch_lib_clip_fn") is None.
# ===========================================================================

def test_step_gen_still_threads_fetch_lib_clip_fn_from_router(tmp_path, monkeypatch):
    """step_gen_still must pass a fetch_lib_clip_fn built on
    router.fetch_library_clip into step_render_shots_clip — spy on the
    render call to capture the kwarg, then invoke it and assert it
    delegates to router.fetch_library_clip with the same (window, dur_s,
    out_path) plus run_ff/grade/ledger threaded from ctx.
    """
    from lib import wiring as wiring_mod

    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={}, art_style="写实电影",
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    ctx["clip_len_by_index"] = {0: 6.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    fetch_library_clip_calls: list[tuple] = []
    orig_fetch_library_clip = router.fetch_library_clip

    def spy_fetch_library_clip(window, dur_s, out_path, run_ff=None,
                                grade=None, ledger=None):
        fetch_library_clip_calls.append((window, dur_s, out_path, run_ff, grade, ledger))
        return orig_fetch_library_clip(window, dur_s, out_path, run_ff, grade, ledger)

    monkeypatch.setattr(router, "fetch_library_clip", spy_fetch_library_clip)

    captured_kwargs: dict = {}
    orig_render = wiring_mod.steps_mod.step_render_shots_clip

    def spy_render(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_render(*args, **kwargs)

    monkeypatch.setattr(wiring_mod.steps_mod, "step_render_shots_clip", spy_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    assert "fetch_lib_clip_fn" in captured_kwargs, (
        "step_gen_still must pass fetch_lib_clip_fn=... into step_render_shots_clip"
    )
    fetch_lib_clip_fn = captured_kwargs["fetch_lib_clip_fn"]
    assert fetch_lib_clip_fn is not None, (
        "fetch_lib_clip_fn must be a callable built on router.fetch_library_clip "
        "(not the literal None) even when no library is bound"
    )

    # Invoke the threaded callable directly for one window and confirm it
    # delegates to router.fetch_library_clip with the ledger from ctx (MR#3).
    # Phase 5 Task 2: the fetch_lib_clip_fn now gates on with_character (host
    # windows skip the library entirely and never reach router.fetch_library_
    # clip) — force this probe window to a b-roll window (with_character=
    # False) so the gate doesn't short-circuit before the delegation we're
    # asserting on.
    window0 = dict(ctx["windows"][0][0], with_character=False)
    out_path = str(tmp_path / "probe_lib_clip.mp4")
    fetch_lib_clip_fn(window0, 6.0, out_path)

    assert fetch_library_clip_calls, (
        "invoking the threaded fetch_lib_clip_fn must call router.fetch_library_clip"
    )
    called_window, called_dur, called_out, called_run_ff, called_grade, called_ledger = (
        fetch_library_clip_calls[-1]
    )
    assert called_window is window0
    assert called_dur == 6.0
    assert called_out == out_path
    assert called_ledger is ctx["visual_ledger"], (
        "MR#3: fetch_lib_clip_fn must thread ctx['visual_ledger'] through to "
        "router.fetch_library_clip — else id-dedup is silently inert in prod"
    )


def test_step_gen_still_fetch_lib_clip_fn_none_library_falls_back_to_generate(
        tmp_path, monkeypatch):
    """When no library is bound for this style (router.library stays None),
    the threaded fetch_lib_clip_fn must itself return None for every window
    (router.fetch_library_clip's own no-library short-circuit) so
    step_render_shots_clip falls back to generate — no-regression, same
    assertion surface as test_step_gen_still_no_regression_when_library_empty
    but exercised through the actual fetch_lib_clip_fn callable.
    """
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = _three_beats_one_long()
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={},  # no binding for 写实电影 → all-generate
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    assert router.library is None
    ctx["clip_len_by_index"] = {0: 44.0, 1: 4.0, 2: 20.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)
    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    total_windows = sum(len(ctx["windows"][i]) for i in ctx["windows"])
    assert len(fake_seams.gen_still_calls) == total_windows, (
        "fetch_lib_clip_fn returning None for every window (no library bound) "
        "must fall back to generate for every window unchanged"
    )


# ===========================================================================
# Phase 5 Task 2-tests: host/b-roll gate on fetch_lib_clip_fn (DP-P5-001,
# DP-P5-003). A with_character=True (host/出镜人) window must skip the
# library entirely (never reach router.fetch_library_clip) and generate via
# ref_url; a with_character=False (b-roll/空镜) window is unaffected and still
# routes to the library. host_enabled=False disables the gate — every window
# (host or b-roll) goes through the library.
#
# FAILS before Task 2-impl: the fetch_lib_clip_fn lambda (wiring.py) has no
# with_character/host_enabled gate — it unconditionally calls
# router.fetch_library_clip for every window, so the host-window assertion
# (must NOT call fetch_library_clip) fails.
# ===========================================================================

def test_gen_still_fetch_lib_clip_fn_with_character_gate_host_skips_library(tmp_path, monkeypatch):
    """host_enabled=True (default) + with_character=True window → the threaded
    fetch_lib_clip_fn must return None WITHOUT calling router.fetch_library_clip
    at all (out-and-out short-circuit, not merely a miss)."""
    from lib import wiring as wiring_mod

    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={}, art_style="写实电影",
        host_enabled=True,
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    ctx["clip_len_by_index"] = {0: 6.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    fetch_library_clip_calls: list = []
    monkeypatch.setattr(
        router, "fetch_library_clip",
        lambda *a, **kw: fetch_library_clip_calls.append((a, kw)) or None,
    )

    captured_kwargs: dict = {}
    orig_render = wiring_mod.steps_mod.step_render_shots_clip

    def spy_render(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_render(*args, **kwargs)

    monkeypatch.setattr(wiring_mod.steps_mod, "step_render_shots_clip", spy_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)
    fetch_lib_clip_fn = captured_kwargs["fetch_lib_clip_fn"]

    host_window = {"prompt": "host shot", "with_character": True}
    result = fetch_lib_clip_fn(host_window, 6.0, str(tmp_path / "probe.mp4"))

    assert result is None, "a host window must never be served by the library"
    assert fetch_library_clip_calls == [], (
        "a host window (with_character=True, host_enabled=True) must short-"
        "circuit BEFORE calling router.fetch_library_clip at all; got calls="
        f"{fetch_library_clip_calls}"
    )


def test_gen_still_fetch_lib_clip_fn_with_character_gate_broll_reaches_library(
        tmp_path, monkeypatch):
    """A with_character=False (b-roll/空镜) window is unaffected by the gate —
    fetch_lib_clip_fn must still delegate to router.fetch_library_clip."""
    from lib import wiring as wiring_mod

    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={}, art_style="写实电影",
        host_enabled=True,
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    ctx["clip_len_by_index"] = {0: 6.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    fetch_library_clip_calls: list = []
    monkeypatch.setattr(
        router, "fetch_library_clip",
        lambda *a, **kw: fetch_library_clip_calls.append((a, kw)) or None,
    )

    captured_kwargs: dict = {}
    orig_render = wiring_mod.steps_mod.step_render_shots_clip

    def spy_render(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_render(*args, **kwargs)

    monkeypatch.setattr(wiring_mod.steps_mod, "step_render_shots_clip", spy_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)
    fetch_lib_clip_fn = captured_kwargs["fetch_lib_clip_fn"]

    broll_window = {"prompt": "empty street", "with_character": False}
    fetch_lib_clip_fn(broll_window, 6.0, str(tmp_path / "probe.mp4"))

    assert len(fetch_library_clip_calls) == 1, (
        "a b-roll window (with_character=False) must still reach "
        f"router.fetch_library_clip; got calls={fetch_library_clip_calls}"
    )


def test_gen_still_fetch_lib_clip_fn_with_character_gate_host_enabled_false_routes_all_to_library(
        tmp_path, monkeypatch):
    """host_enabled=False disables the gate entirely — even a with_character=
    True (host) window must reach router.fetch_library_clip (acceptance:
    "关掉出镜人 → 纯 b-roll")."""
    from lib import wiring as wiring_mod

    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "still", "text": "t",
              "image_prompt": "p", "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={}, art_style="写实电影",
        host_enabled=False,
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    ctx["clip_len_by_index"] = {0: 6.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    fetch_library_clip_calls: list = []
    monkeypatch.setattr(
        router, "fetch_library_clip",
        lambda *a, **kw: fetch_library_clip_calls.append((a, kw)) or None,
    )

    captured_kwargs: dict = {}
    orig_render = wiring_mod.steps_mod.step_render_shots_clip

    def spy_render(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_render(*args, **kwargs)

    monkeypatch.setattr(wiring_mod.steps_mod, "step_render_shots_clip", spy_render)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)
    fetch_lib_clip_fn = captured_kwargs["fetch_lib_clip_fn"]

    # Isolate the probe call from whatever the real pipeline run above already
    # threaded through fetch_library_clip.
    fetch_library_clip_calls.clear()

    host_window = {"prompt": "host shot", "with_character": True}
    fetch_lib_clip_fn(host_window, 6.0, str(tmp_path / "probe.mp4"))

    assert len(fetch_library_clip_calls) == 1, (
        "host_enabled=False must disable the gate — even a host window "
        f"(with_character=True) must reach router.fetch_library_clip; got "
        f"calls={fetch_library_clip_calls}"
    )


def test_step_gen_still_host_broll_mixed_source_routes_by_with_character(
        tmp_path, monkeypatch):
    """Full-pipeline mixed-source integration (D-013/D-005): one episode with
    a host window (with_character=True) and a b-roll window (with_character=
    False), config bound to a library ("pexels", faked here so the gate is
    what's under test — not a real network call). The host window must
    generate via seams.gen_still using ctx['ref_url']; the b-roll window must
    be served by the (faked) library and never call seams.gen_still. Both
    still share the episode's single art_style (D-005 style consistency)."""
    monkeypatch.delenv("VIDEO_PDF", raising=False)
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)

    beats = [{"id": "b00", "visual_type": "multi", "text": "t",
              "shots": ["host shot", "broll shot"], "keywords": []}]
    step_fns, ctx, router, fake_seams = _make_router_harness(
        tmp_path, beats=beats, library={"写实电影": "pexels"},
        art_style="写实电影", host_enabled=True,
    )
    step_fns["extract"]({"name": "extract"}, ctx, tmp_path)
    assert router.style == "写实电影", "D-005: episode must resolve a single style"
    ctx["clip_len_by_index"] = {0: 8.0}
    step_fns["镜头计划"](_shotplan_step(), ctx, tmp_path)

    # Force a deterministic mixed-source window set: window 0 stays the
    # fixture default (with_character=True, host); window 1 is forced to
    # b-roll (with_character=False).
    ctx["windows"][0][0]["with_character"] = True
    ctx["windows"][0][1]["with_character"] = False

    # Fake the library clip hit for b-roll windows — bypass real PexelsSource
    # network; the gate is what's under test, not PexelsSource itself.
    lib_calls: list = []

    def fake_fetch_library_clip(window, dur_s, out_path, run_ff=None,
                                 grade=None, ledger=None):
        lib_calls.append(window)
        from pathlib import Path as _P
        _P(out_path).write_bytes(b"\x00mp4 stub")
        return out_path

    monkeypatch.setattr(router, "fetch_library_clip", fake_fetch_library_clip)

    gen_still_ref_calls: list = []
    orig_gen_still = router.gen_still

    def spy_gen_still(prompt, ref_url, out_path):
        gen_still_ref_calls.append(ref_url)
        return orig_gen_still(prompt, ref_url, out_path)

    monkeypatch.setattr(router, "gen_still", spy_gen_still)

    step_fns["gen-still"](_gen_still_step(), ctx, tmp_path)

    assert len(lib_calls) == 1 and lib_calls[0].get("with_character") is False, (
        f"only the b-roll window (with_character=False) may reach "
        f"router.fetch_library_clip; got lib_calls={lib_calls}"
    )
    assert len(fake_seams.gen_still_calls) == 1, (
        f"the host window (with_character=True) must generate via seams."
        f"gen_still (never library-sourced); got {fake_seams.gen_still_calls}"
    )
    assert gen_still_ref_calls == [ctx["ref_url"]], (
        f"the host window's generate call must use the episode's (per-style) "
        f"ref_url; got {gen_still_ref_calls}"
    )


# ===========================================================================
# Anti-homogenization plan — Task B4-tests: covered-ground distiller pipeline
# step dispatch closure (make_distiller_dispatch) + prepare_render routing.
#
# FAIL before Task B4-impl:
#   - lib.wiring has no make_distiller_dispatch (ImportError)
#   - prepare_render dispatch raises 'unknown agent for dispatch:
#     covered-ground-distiller' (no elif branch)
# All green after Task B4-impl.
# ===========================================================================

def _distill_step():
    return {"name": "distill", "kind": "agent", "agent": "covered-ground-distiller",
            "inputs": ["script", "config"], "artifact": None, "gate": []}


def _distiller_beats():
    return [
        {"id": "b00", "text": "心率区间就像汽车的转速表，红线区不能久待。"},
        {"id": "b01", "text": "周末长距离拉练最容易撞墙，配速别一上来就冲爆。"},
    ]


def _fake_distiller_persona(anchors, called=None):
    def fake(agent, prompt, scratch_dir, expected, **kw):
        if called is not None:
            called.append((agent, prompt))
        assert agent == "covered-ground-distiller"
        out = _Path(scratch_dir) / expected
        out.write_text(_json_mod.dumps({"anchors": anchors}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}
    return fake


def _distiller_cfg(tmp_path):
    cfg = _MagicMock()
    cfg.output_dir = str(tmp_path)
    return cfg


def test_make_distiller_dispatch_fixture_short_circuit(tmp_path):
    """ctx['fixture'] truthy → closure must NOT call dispatch_persona_fn
    (zero live claude -p in fixture render eval) and must NOT set covered_art."""
    from lib.wiring import make_distiller_dispatch

    called = []
    fake = _fake_distiller_persona(["心率像转速表"], called)
    ctx = {"fixture": {"beats": []}, "beats": _distiller_beats()}
    cfg = _distiller_cfg(tmp_path)

    dispatch = make_distiller_dispatch(
        cfg, dispatch_persona_fn=fake, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("covered-ground-distiller", _distill_step(), ctx, tmp_path)

    assert called == [], "fixture pass-through must not invoke dispatch_persona_fn"
    assert "covered_art" not in ctx, "fixture pass-through must not persist covered.json"


def test_make_distiller_dispatch_swallows_raising_dispatch_no_bubble(tmp_path):
    """MR-5 (critical): a RAISING dispatch_persona_fn must be swallowed by the
    closure's whole-body try/except — the runner does NOT wrap steps, so a
    bubble would abort an already-rendered episode. ctx must reach the dispatch
    call (no fixture, non-empty beats) and stay unpolluted (no covered_art, no
    new keys) after the swallow."""
    from lib.wiring import make_distiller_dispatch

    ep_dir = tmp_path / "episodes" / "001-test"
    ctx = {
        "beats": _distiller_beats(),
        "ep": {"final": ep_dir / "001-test.mp4"},
    }
    before = set(ctx.keys())

    def fake_raise(agent, prompt, scratch_dir, expected, **kw):
        raise RuntimeError("claude -p exploded mid-distill")

    cfg = _distiller_cfg(tmp_path)
    dispatch = make_distiller_dispatch(
        cfg, dispatch_persona_fn=fake_raise, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    # Must NOT raise — the closure swallows it (fail-soft, run continues).
    dispatch("covered-ground-distiller", _distill_step(), ctx, tmp_path)

    assert "covered_art" not in ctx, "a raising dispatch must not set covered_art"
    assert set(ctx.keys()) == before, (
        f"a swallowed distill failure must not pollute ctx; new keys="
        f"{set(ctx.keys()) - before}"
    )


def test_make_distiller_dispatch_swallows_ok_false_no_persist(tmp_path):
    """A dispatch that returns ok=False (persona failed cleanly) must not
    persist covered.json and must not raise (fail-soft)."""
    from lib.wiring import make_distiller_dispatch

    ep_dir = tmp_path / "episodes" / "001-test"
    ctx = {
        "beats": _distiller_beats(),
        "ep": {"final": ep_dir / "001-test.mp4"},
    }

    def fake_fail(agent, prompt, scratch_dir, expected, **kw):
        return {"ok": False, "reason": "boom"}

    cfg = _distiller_cfg(tmp_path)
    dispatch = make_distiller_dispatch(
        cfg, dispatch_persona_fn=fake_fail, plugin_root=_PLUGIN_ROOT_WIRING,
        log=lambda m: None,
    )
    dispatch("covered-ground-distiller", _distill_step(), ctx, tmp_path)  # no raise

    assert "covered_art" not in ctx
    assert not (ep_dir / "covered.json").exists()


def test_prepare_render_routes_distiller_to_covered_closure(tmp_path, monkeypatch):
    """prepare_render dispatch routes 'covered-ground-distiller' → distiller
    closure. Success path persists covered.json into the episode dir and sets
    ctx['covered_art'].

    Pre-impl: dispatch raises 'unknown agent for dispatch:
    covered-ground-distiller' → RED. Green after Task B4-impl wires the branch.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    import lib.episode as ep_mod
    from lib.wiring import prepare_render

    cfg = _MagicMock()
    cfg.ffmpeg.ffmpeg_bin = "ffmpeg"
    cfg.ffmpeg.ffprobe_bin = "ffprobe"
    cfg.media.provider = "minimax"
    cfg.output_dir = str(tmp_path / "output")
    cfg.budget.video_shots_per_episode = 2

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(_json_mod.dumps({"beats": [], "cards": {}}),
                            encoding="utf-8")

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    monkeypatch.setattr(ep_mod, "make_scratch", lambda *a, **kw: scratch_dir)

    bundle = prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq="001", slug="test",
        dry=True, log=lambda m: None,
        dispatch_persona_fn=_fake_distiller_persona(["心率区间像转速表"]),
    )
    dispatch = bundle["dispatch"]

    ep_dir = tmp_path / "episodes" / "001-test"
    ctx = {
        "beats": _distiller_beats(),
        "ep": {"final": ep_dir / "001-test.mp4"},
    }
    dispatch("covered-ground-distiller", _distill_step(), ctx, scratch_dir)

    assert "covered_art" in ctx, (
        "distiller dispatch must set ctx['covered_art'] via the distiller closure"
    )
    persisted = ep_dir / "covered.json"
    assert persisted.exists(), "covered.json must be persisted into the episode dir"
    got = _json_mod.loads(persisted.read_text(encoding="utf-8"))
    assert got.get("anchors") == ["心率区间像转速表"]


# ===========================================================================
# Anti-homogenization plan — Task B5-tests: avoid-memo read + inject into
# digest-writer. A pre-script CODE step (step_avoid_memo) writes
# render_memo(load_store(output_dir), int(ctx['seq'])) into ctx['avoid_memo'];
# make_script_dispatch appends it as a named section of the writer's
# user_prompt.
#
# FAIL before Task B5-impl:
#   - build_step_fns return dict has no 'avoid-memo' key (KeyError / None)
#   - make_script_dispatch user_prompt lacks the covered-ground section
# All green after Task B5-impl.
#
# MR-5 (critical): the make_script_dispatch injection tests MUST use a DOC-MODE
# ctx (has 'fulltext', NO 'beats'). The fixture short-circuit (wiring.py ~1253
# `if ctx.get("beats")`) returns BEFORE the user_prompt is built — a beats-
# carrying ctx would make the spy see nothing = false-green.
# ===========================================================================

def _avoid_memo_step_fns(tmp_path, output_dir):
    """Build step_fns wired with a REAL cfg.output_dir (so step_avoid_memo can
    load a real store), returning (step_fns, cfg)."""
    from lib.wiring import build_step_fns, MediaSeams

    fixture_path = tmp_path / "avoid_fixture.json"
    fixture_path.write_text(_json_mod.dumps({"beats": []}), encoding="utf-8")

    selection = _types_mod.SimpleNamespace(
        art_style="", style="", color_grade="", doc_refs=None,
        character_ref="", seq="005", slug="test",
        source="", verify_status="", learning_goal="", topic="",
    )
    cfg = _MagicMock()
    cfg.output_dir = str(output_dir)
    cfg.style = ""

    seams = MediaSeams(dry=True, provider="minimax", log=lambda m: None)
    router = MediaRouter(GenerateSource(seams), cfg=cfg)

    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path,
        seq="005", slug="test",
        run_ff=lambda *a, **kw: None, probe_ms=lambda _p: 5000,
        log=lambda m: None, selection=selection, dry=True,
    )
    return step_fns, cfg


def test_step_avoid_memo_hot_store_nonempty(tmp_path):
    """A hot store (an anchor used within the recent seq window) → non-empty
    ctx['avoid_memo'] naming that anchor. int(ctx['seq']) is used — seq is a
    zero-padded STRING ('005')."""
    from lib import coveredground

    out = tmp_path / "out"
    out.mkdir()
    store = {"anchors": {}}
    coveredground.update_store(
        store, ["心率像转速表"], "2026-07-01",
        {"date": "2026-07-01", "seq": 4},
    )
    coveredground.write_store(str(out), store)

    step_fns, _cfg = _avoid_memo_step_fns(tmp_path, out)
    ctx = {"seq": "005"}
    step_fns["avoid-memo"]({"name": "avoid-memo"}, ctx, tmp_path)

    assert ctx["avoid_memo"], "hot store must produce a non-empty avoid_memo"
    assert "心率像转速表" in ctx["avoid_memo"], ctx["avoid_memo"]


def test_step_avoid_memo_empty_store_is_empty(tmp_path):
    """No store file → load_store returns empty → render_memo '' → avoid_memo=''."""
    out = tmp_path / "out"
    out.mkdir()

    step_fns, _cfg = _avoid_memo_step_fns(tmp_path, out)
    ctx = {"seq": "005"}
    step_fns["avoid-memo"]({"name": "avoid-memo"}, ctx, tmp_path)

    assert ctx["avoid_memo"] == "", (
        f"empty store must yield avoid_memo='', got {ctx['avoid_memo']!r}"
    )


def test_step_avoid_memo_store_raises_returns_empty(tmp_path, monkeypatch):
    """load_store raising → step swallows → avoid_memo='' and no raise
    (fail-soft)."""
    import lib.coveredground as _cg

    out = tmp_path / "out"
    out.mkdir()
    step_fns, _cfg = _avoid_memo_step_fns(tmp_path, out)

    def _boom(*_a, **_kw):
        raise RuntimeError("store read exploded")

    monkeypatch.setattr(_cg, "load_store", _boom)

    ctx = {"seq": "005"}
    step_fns["avoid-memo"]({"name": "avoid-memo"}, ctx, tmp_path)  # no raise
    assert ctx["avoid_memo"] == ""


def _script_dispatch_capture(tmp_path, ctx):
    """Dispatch make_script_dispatch against a DOC-MODE ctx with a spy persona
    that captures the built user_prompt then stops (ok=False) — returns the
    captured prompt."""
    from lib.wiring import make_script_dispatch

    captured = {}

    def spy(agent, prompt, scratch_dir, expected, **kw):
        captured["prompt"] = prompt
        return {"ok": False, "reason": "stop-after-capture"}

    cfg = _MagicMock()
    cfg.host = None  # no fixed-host line

    dispatch = make_script_dispatch(cfg, dispatch_persona_fn=spy, log=lambda m: None)
    dispatch("digest-writer", {"name": "script"}, ctx, tmp_path)
    return captured


def test_make_script_dispatch_memo_inject_named_section(tmp_path):
    """DOC MODE (fulltext, NO beats) → make_script_dispatch builds user_prompt
    and appends the covered-ground avoid section carrying the memo text."""
    ctx = {
        "fulltext": "跑步教材原文……",
        "avoid_memo": "- 心率像转速表（累计 3 次，最近 2026-07-01）",
    }
    captured = _script_dispatch_capture(tmp_path, ctx)

    assert "prompt" in captured, (
        "doc mode must reach dispatch_persona_fn — fixture short-circuit would "
        "return before building user_prompt (false-green)"
    )
    prompt = captured["prompt"]
    assert "## 反复用过的类比/例子/钩子——本期避让(covered-ground)" in prompt, prompt
    assert "心率像转速表" in prompt, prompt


def test_make_script_dispatch_memo_inject_empty_placeholder(tmp_path):
    """Empty avoid_memo → the named section still appears with the placeholder
    '(无 covered-ground 避让约束)'."""
    ctx = {"fulltext": "跑步教材原文……", "avoid_memo": ""}
    captured = _script_dispatch_capture(tmp_path, ctx)

    assert "prompt" in captured
    prompt = captured["prompt"]
    assert "## 反复用过的类比/例子/钩子——本期避让(covered-ground)" in prompt, prompt
    assert "(无 covered-ground 避让约束)" in prompt, prompt
