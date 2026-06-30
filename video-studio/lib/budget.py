"""
lib/budget.py — 视频预算门纯函数

apply_video_budget(shotlist, cap) -> shotlist

找出 visual_type == "video" 项，按 motion_value（缺省→0.0）降序稳定排序，
超 cap 的尾部把 visual_type 置 "still"；非 video 项不动；返回同长度 list。
纯函数、不 raise。
"""

from __future__ import annotations


def apply_video_budget(shotlist: list[dict], cap: int) -> list[dict]:
    """
    Enforce video shot cap for a single episode.

    Args:
        shotlist: director 输出，1:1 对齐 beats 的 list，
                  每项 {visual_type, shots, keywords, motion_value?}。
        cap:      单期允许的最大 video 镜头数（来自 cfg.budget.video_shots_per_episode）。

    Returns:
        同长度 list，video 数 ≤ cap，超出的 visual_type 改为 "still"。
        非 video 项（still/chart/multi）原样不动。
    """
    # Collect (original_index, entry) for all video shots
    videos = [(i, e) for i, e in enumerate(shotlist) if e.get("visual_type") == "video"]

    if len(videos) <= cap:
        # Under or at cap — nothing to downgrade
        return shotlist

    # Sort descending by motion_value, stable by original index (tie-break)
    sorted_videos = sorted(
        videos,
        key=lambda t: (-float(t[1].get("motion_value", 0.0)), t[0]),
    )

    # Indices that exceed the cap → downgrade to still
    to_downgrade = {i for i, _ in sorted_videos[cap:]}

    for i in to_downgrade:
        shotlist[i]["visual_type"] = "still"

    return shotlist
