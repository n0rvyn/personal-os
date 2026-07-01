"""
lib/budget.py — 视频预算门纯函数

apply_video_budget(shotlist, cap, threshold=0.6) -> shotlist

两阶段逻辑(DP-001=C):
  1. 阈值门: motion_value ≥ threshold 的 video 全保留; < threshold 的直接降 still。
  2. 宽松硬顶: 阈值门后剩余 video 数超 cap → 按 motion_value 降序砍尾兜底。
纯函数、不 raise。
"""

from __future__ import annotations


def apply_video_budget(
    shotlist: list[dict],
    cap: int,
    threshold: float = 0.6,
) -> list[dict]:
    """
    Enforce video budget for a single episode.

    Two-phase logic (DP-001=C):
    1. Threshold gate: video beats with motion_value >= threshold are kept;
       those below threshold are downgraded to still unconditionally.
    2. Loose hard cap: if kept count still exceeds cap, drop lowest-motion
       beats (sorted descending by motion_value, stable by original index).

    Args:
        shotlist:  director 输出，1:1 对齐 beats 的 list，
                   每项 {visual_type, shots, keywords, motion_value?}。
        cap:       宽松硬顶——通过阈值门后的 video 数超此值时按 motion_value 降序砍尾
                   (来自 cfg.budget.video_shots_per_episode，建议设为 8 兜底)。
        threshold: motion_value 阈值——低于此值的 video 直接降 still
                   (来自 cfg.budget.video_motion_threshold，默认 0.6)。

    Returns:
        同长度 list，经双阶段约束后 video 数 ≤ cap；非 video 项原样不动。
    """
    # Collect (original_index, entry) for all video shots
    videos = [(i, e) for i, e in enumerate(shotlist) if e.get("visual_type") == "video"]

    # Phase 1: threshold gate — below threshold → still
    above = [(i, e) for i, e in videos if float(e.get("motion_value", 0.0)) >= threshold]
    below = [(i, e) for i, e in videos if float(e.get("motion_value", 0.0)) < threshold]

    for i, _ in below:
        shotlist[i]["visual_type"] = "still"

    # Phase 2: loose hard cap — if above-threshold count still exceeds cap, trim tail
    if len(above) <= cap:
        return shotlist

    sorted_above = sorted(
        above,
        key=lambda t: (-float(t[1].get("motion_value", 0.0)), t[0]),
    )

    to_downgrade = {i for i, _ in sorted_above[cap:]}
    for i in to_downgrade:
        shotlist[i]["visual_type"] = "still"

    return shotlist
