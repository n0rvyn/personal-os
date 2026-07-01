"""
Tests for lib/budget.py — apply_video_budget() 视频预算门纯函数。
这是 TDD 红阶段：lib/budget.py 尚不存在，测试应收集到但失败（ModuleNotFoundError）。
"""

import copy
import pytest

from lib.budget import apply_video_budget


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_shot(visual_type: str, motion_value=None, idx: int = 0) -> dict:
    shot = {
        "beat_id": f"b{idx}",
        "visual_type": visual_type,
        "shots": [{"desc": "test shot"}],
        "keywords": ["test"],
    }
    if motion_value is not None:
        shot["motion_value"] = motion_value
    return shot


# A mixed shotlist: 2 still, 3 video (motion_values 0.9, 0.4, 0.7), 1 chart
MIXED_SHOTLIST = [
    make_shot("still", idx=0),
    make_shot("video", motion_value=0.9, idx=1),
    make_shot("chart", idx=2),
    make_shot("video", motion_value=0.4, idx=3),
    make_shot("still", idx=4),
    make_shot("video", motion_value=0.7, idx=5),
]


def fresh(shotlist=None):
    """Deep-copy so tests are independent."""
    return copy.deepcopy(shotlist or MIXED_SHOTLIST)


# ---------------------------------------------------------------------------
# Under-cap: no downgrade
# ---------------------------------------------------------------------------

def test_under_cap_above_threshold_returns_unchanged():
    """video 数 ≤ cap 且 motion_value >= threshold → 无降级。
    MIXED_SHOTLIST 中 b3(0.4) 低于默认 threshold=0.6 → 降 still；
    b1(0.9)/b5(0.7) 均高于阈值，cap=3 不砍尾 → 2 video 原样保留。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=3)
    videos = [s for s in result if s["visual_type"] == "video"]
    # b1(0.9) and b5(0.7) pass threshold; b3(0.4) is below threshold → still
    assert len(videos) == 2
    surviving = {s["beat_id"] for s in videos}
    assert "b1" in surviving
    assert "b5" in surviving
    assert "b3" not in surviving


def test_exactly_cap_no_downgrade():
    """video 数 == cap → 原样返回。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=3)
    # All three video beats keep visual_type=video
    for original, got in zip(sl, result):
        if original["visual_type"] == "video":
            assert got["visual_type"] == "video"


# ---------------------------------------------------------------------------
# Over-cap: top-K by motion_value kept as video, rest → still
# ---------------------------------------------------------------------------

def test_over_cap_video_count():
    """video 数 > cap → 保留 cap 个 video，其余降 still。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=2)
    videos = [s for s in result if s["visual_type"] == "video"]
    assert len(videos) == 2


def test_over_cap_keeps_highest_motion_value():
    """按 motion_value 降序保留：0.9 和 0.7 存活，0.4 降级。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=2)
    # beat_ids of surviving videos
    surviving = {s["beat_id"] for s in result if s["visual_type"] == "video"}
    assert "b1" in surviving  # motion_value=0.9
    assert "b5" in surviving  # motion_value=0.7
    assert "b3" not in surviving  # motion_value=0.4 → still


def test_over_cap_downgraded_becomes_still():
    """降级后 visual_type == 'still'，不是其他值。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=1)
    downgraded = [s for s in result if s["beat_id"] in ("b3", "b5")]
    for s in downgraded:
        assert s["visual_type"] == "still"


# ---------------------------------------------------------------------------
# cap=0: all video → still
# ---------------------------------------------------------------------------

def test_cap_zero_all_video_downgraded():
    """cap=0 → 所有 video 降 still。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=0)
    videos = [s for s in result if s["visual_type"] == "video"]
    assert len(videos) == 0


# ---------------------------------------------------------------------------
# Non-video items untouched
# ---------------------------------------------------------------------------

def test_non_video_items_unchanged():
    """still/chart/multi 项不受影响（visual_type 不变）。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=1)
    non_video_original = [(s["beat_id"], s["visual_type"]) for s in sl if s["visual_type"] != "video"]
    non_video_result = [(s["beat_id"], s["visual_type"]) for s in result if s["beat_id"] in {x[0] for x in non_video_original}]
    assert non_video_original == non_video_result


def test_list_length_preserved():
    """返回的 shotlist 长度不变。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=1)
    assert len(result) == len(sl)


def test_order_preserved():
    """beat_id 顺序不乱（返回顺序与输入一致）。"""
    sl = fresh()
    result = apply_video_budget(sl, cap=1)
    assert [s["beat_id"] for s in result] == [s["beat_id"] for s in sl]


# ---------------------------------------------------------------------------
# Missing motion_value → treated as 0.0 (lowest priority)
# ---------------------------------------------------------------------------

def test_missing_motion_value_treated_as_zero():
    """缺 motion_value 的 video 项视为 0.0，优先被降级，不 KeyError。"""
    sl = [
        make_shot("video", motion_value=0.8, idx=0),
        make_shot("video", idx=1),  # no motion_value
        make_shot("video", motion_value=0.5, idx=2),
    ]
    result = apply_video_budget(sl, cap=1)
    surviving = [s for s in result if s["visual_type"] == "video"]
    assert len(surviving) == 1
    assert surviving[0]["beat_id"] == "b0"  # highest motion_value=0.8
    # b1 (no motion_value=0.0) and b2 (0.5) both downgraded
    downgraded = [s for s in result if s["visual_type"] == "still"]
    assert len(downgraded) == 2


# ---------------------------------------------------------------------------
# Stable sort: tie-break by original index
# ---------------------------------------------------------------------------

def test_stable_sort_tie_break_by_index():
    """平分时按原 index 升序稳定排序（较早的 index 存活）。
    使用 motion_value=0.7（高于默认 threshold=0.6）确保全部通过阈值门，
    再由 cap 触发稳定排序降尾。"""
    sl = [
        make_shot("video", motion_value=0.7, idx=0),
        make_shot("video", motion_value=0.7, idx=1),
        make_shot("video", motion_value=0.7, idx=2),
    ]
    result = apply_video_budget(sl, cap=2)
    surviving = [s["beat_id"] for s in result if s["visual_type"] == "video"]
    # Should keep first two (index 0, 1) over the last (index 2)
    assert "b0" in surviving
    assert "b1" in surviving
    assert "b2" not in surviving


# ---------------------------------------------------------------------------
# No raise / no halt
# ---------------------------------------------------------------------------

def test_does_not_raise_when_over_cap():
    """超 cap 时不 raise，不 halt（UX-005 核心契约）。"""
    sl = fresh()
    # Should not raise any exception
    result = apply_video_budget(sl, cap=1)
    assert result is not None


def test_all_still_no_video():
    """全 still 的 shotlist，cap 无论多少都不变。"""
    sl = [make_shot("still", idx=i) for i in range(4)]
    result = apply_video_budget(sl, cap=0)
    assert all(s["visual_type"] == "still" for s in result)
    assert len(result) == 4


def test_single_video_under_cap():
    """只有 1 个 video，cap=2 → 不降级。"""
    sl = [make_shot("still", idx=0), make_shot("video", motion_value=0.6, idx=1)]
    result = apply_video_budget(sl, cap=2)
    assert result[1]["visual_type"] == "video"


# ---------------------------------------------------------------------------
# Threshold logic (DP-001=C)
# ---------------------------------------------------------------------------

def test_threshold_below_downgraded_regardless_of_cap():
    """motion_value < threshold → 降 still，无论 cap 是否还有余量。"""
    sl = [make_shot("video", motion_value=0.4, idx=0)]
    result = apply_video_budget(sl, cap=8, threshold=0.6)
    assert result[0]["visual_type"] == "still"


def test_threshold_at_boundary_kept():
    """motion_value == threshold → 保留为 video（边界包含）。"""
    sl = [make_shot("video", motion_value=0.6, idx=0)]
    result = apply_video_budget(sl, cap=8, threshold=0.6)
    assert result[0]["visual_type"] == "video"


def test_threshold_all_above_no_cap_pressure():
    """所有 video 均高于 threshold，且数量 ≤ cap → 全保留。"""
    sl = [
        make_shot("video", motion_value=0.7, idx=0),
        make_shot("video", motion_value=0.8, idx=1),
        make_shot("video", motion_value=0.9, idx=2),
    ]
    result = apply_video_budget(sl, cap=8, threshold=0.6)
    videos = [s for s in result if s["visual_type"] == "video"]
    assert len(videos) == 3


def test_threshold_mix_keeps_above_caps_below():
    """混合列表: 高于阈值的全保留(cap 宽松)，低于阈值的降 still。"""
    sl = [
        make_shot("video", motion_value=0.8, idx=0),
        make_shot("video", motion_value=0.3, idx=1),   # below threshold
        make_shot("video", motion_value=0.65, idx=2),
    ]
    result = apply_video_budget(sl, cap=8, threshold=0.6)
    surviving = {s["beat_id"] for s in result if s["visual_type"] == "video"}
    assert "b0" in surviving
    assert "b2" in surviving
    assert "b1" not in surviving  # 0.3 < 0.6


def test_threshold_cap_trim_after_threshold_gate():
    """阈值门后仍超 cap → 按 motion_value 降序砍尾。"""
    sl = [
        make_shot("video", motion_value=0.9, idx=0),
        make_shot("video", motion_value=0.75, idx=1),
        make_shot("video", motion_value=0.65, idx=2),  # all above threshold
        make_shot("video", motion_value=0.3, idx=3),   # below threshold → still
    ]
    result = apply_video_budget(sl, cap=2, threshold=0.6)
    surviving = {s["beat_id"] for s in result if s["visual_type"] == "video"}
    assert "b0" in surviving   # 0.9 kept
    assert "b1" in surviving   # 0.75 kept
    assert "b2" not in surviving  # 0.65 pruned by cap
    assert "b3" not in surviving  # 0.3 pruned by threshold
