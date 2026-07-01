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
    seam.style_prefix = grade.style_prefix(look)
    return seam, fake_img, fake_vid


# ---------------------------------------------------------------------------
# Tests: anime look — all three lines get style_prefix prepended
# ---------------------------------------------------------------------------

def test_gen_character_ref_prepends_anime_style():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_character_ref("主讲人物", "/tmp/ref.png")
    assert len(img.char_ref_calls) == 1
    received = img.char_ref_calls[0]
    assert received.startswith(grade.style_prefix("简笔日漫"))


def test_gen_still_prepends_anime_style():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_still("running track scene", None, "/tmp/still.png")
    assert len(img.still_calls) == 1
    received = img.still_calls[0]
    assert received.startswith(grade.style_prefix("简笔日漫"))


def test_gen_video_prepends_anime_style():
    seam, _, vid = _seam_with_fakes("简笔日漫")
    seam.gen_video("running coach explains pace", "fake://ref", "/tmp/vid.mp4")
    assert len(vid.video_calls) == 1
    received = vid.video_calls[0]
    assert received.startswith(grade.style_prefix("简笔日漫"))


# ---------------------------------------------------------------------------
# Tests: single prepend — prefix appears exactly once
# ---------------------------------------------------------------------------

def test_gen_still_no_double_prepend():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    original_prompt = "a runner on a track"
    seam.gen_still(original_prompt, None, "/tmp/still.png")
    received = img.still_calls[0]
    prefix = grade.style_prefix("简笔日漫")
    # prefix appears exactly once
    assert received.count(prefix) == 1


def test_gen_character_ref_no_double_prepend():
    seam, img, _ = _seam_with_fakes("简笔日漫")
    seam.gen_character_ref("主讲人物 male coach", "/tmp/ref.png")
    received = img.char_ref_calls[0]
    prefix = grade.style_prefix("简笔日漫")
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
    expected = f"{grade.style_prefix('冷蓝晨光')}, {original_prompt}"
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
