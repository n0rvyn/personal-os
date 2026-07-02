"""Unit tests for MediaSeams style_prefix single-prepend-point (Task 3).

Verifies that:
1. gen_character_ref / gen_still / gen_video all prepend look's style_prefix.
2. The prefix appears exactly once (no double-prepend from providers).
3. 冷蓝晨光 seam output byte-matches the old media_image internal prepend.
4. No style_prefix (empty string) → prompt passed through unchanged.
"""
from __future__ import annotations

import types
from lib import grade
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

    step_fns = build_step_fns(
        cfg, seams=seams, fixture_path=fixture_path,
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
