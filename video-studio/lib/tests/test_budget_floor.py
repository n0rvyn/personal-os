"""Tests for the video floor safety net (apply_video_floor).

Complements test_budget.py (apply_video_budget = downgrade). The floor is the
opposite direction: when the director returns fewer than `floor` video beats,
promote the best-candidate still beat(s) to video so every episode has at least
`floor` real running clips (the "视频感保底").

Candidate priority (per plan Task 5):
  (i)   still shot whose prompt has running imagery (跑/奔/跑道/track/run ...) — best
  (ii)  still shot with_character=True
  (iii) any still
NOT limited to with_character (doc mode is 空镜-heavy — the discriminating test
below would fail if the impl only looked at with_character shots).
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.budget import apply_video_floor


def _still(prompt: str, with_character: bool = False) -> dict:
    return {
        "visual_type": "still",
        "shots": [{"prompt": prompt, "shot_type": "", "with_character": with_character,
                   "pace": "normal"}],
        "keywords": [],
    }


def _video(motion: float = 0.8) -> dict:
    return {
        "visual_type": "video",
        "shots": [{"prompt": "教练跑步", "with_character": True, "pace": "hold"}],
        "keywords": [],
        "motion_value": motion,
    }


def _chart() -> dict:
    return {"visual_type": "chart", "shots": [{"prompt": "图表"}], "keywords": [],
            "chart": {"type": "bar", "params": {}}}


def test_floor_promotes_a_running_still():
    """No video, floor=1 → the running-imagery still beat becomes video."""
    sb = [_still("手机屏幕特写，加量提醒"), _still("晨光跑道，教练从容奔跑，侧跟拍"),
          _still("训练日历本摊开")]
    out = apply_video_floor(sb, floor=1)
    videos = [e for e in out if e["visual_type"] == "video"]
    assert len(videos) == 1
    promoted = videos[0]
    assert "奔跑" in promoted["video_prompt"]          # took the running shot's prompt
    assert promoted.get("motion_value", 0) >= 0.6      # survives the budget threshold


def test_floor_promotes_running_still_even_when_all_no_character():
    """Discriminator: all shots with_character=False, but one has running imagery →
    it must still be promoted. An impl limited to with_character=True would find 0
    candidates and leave video count at 0, failing this assertion."""
    sb = [_still("训练日志本页面特写", with_character=False),
          _still("晨光跑道，无人奔跑的空镜", with_character=False),
          _still("手机 App 截图", with_character=False)]
    out = apply_video_floor(sb, floor=1)
    videos = [e for e in out if e["visual_type"] == "video"]
    assert len(videos) == 1, "running-imagery 空镜 must be promotable without with_character"
    assert "跑道" in videos[0]["video_prompt"] or "奔跑" in videos[0]["video_prompt"]


def test_floor_noop_when_already_enough_video():
    sb = [_video(0.8), _still("训练日历本")]
    out = apply_video_floor(sb, floor=1)
    assert sum(1 for e in out if e["visual_type"] == "video") == 1
    # the still beat must remain still (no needless promotion)
    assert out[1]["visual_type"] == "still"


def test_floor_no_candidate_returns_original_no_raise():
    """All chart, no still to promote → return unchanged, no exception."""
    sb = [_chart(), _chart()]
    out = apply_video_floor(sb, floor=1)
    assert all(e["visual_type"] == "chart" for e in out)


def test_floor_reaches_target_count():
    sb = [_still("跑道奔跑一"), _still("公路奔跑二"), _still("日历"), _still("日志")]
    out = apply_video_floor(sb, floor=2)
    assert sum(1 for e in out if e["visual_type"] == "video") == 2


def test_floor_skips_score0_stills_no_junk_promotion():
    """R2 fix #4: floor must NOT promote a score-0 still (notebook/calendar/phone) —
    animating a static-detail prompt as b-roll is worse than no video. floor stays
    unmet (caller logs it); the director b-roll mandate is the real video source."""
    sb = [_still("训练日历本摊开"), _still("手机屏幕特写，加量提醒"), _still("训练日志本翻页")]
    out = apply_video_floor(sb, floor=1)
    assert all(e["visual_type"] == "still" for e in out), \
        "no running/character candidate → must leave all still, not promote a notebook"
