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


# Running-imagery keywords: a still shot whose prompt mentions any of these is the
# best b-roll promotion candidate (a real running scene animates well as S2V).
_RUNNING_WORDS = ("跑", "奔", "步频", "冲刺", "迈步", "跨步", "run", "jog", "sprint", "track")


def _shot_score(shot: dict) -> int:
    """Rank a still shot as a video-promotion candidate.

    2 = prompt has running imagery (best — a real running scene)
    1 = shot involves the character (with_character)
    0 = any other still
    NOT gated on with_character: doc-mode shotlists are 空镜-heavy, so a running
    空镜 (with_character=False) must still score above a non-running character shot.
    """
    prompt = str(shot.get("prompt", ""))
    if any(w in prompt for w in _RUNNING_WORDS):
        return 2
    if shot.get("with_character"):
        return 1
    return 0


def apply_video_floor(
    shotlist: list[dict],
    floor: int,
    default_motion: float = 0.7,
) -> list[dict]:
    """Guarantee at least ``floor`` video beats (视频感保底 / b-roll 安全网).

    The director may return fewer video beats than desired (esp. for abstract
    episodes). When the current video count is below ``floor``, promote the
    best-candidate still beats to ``video`` — picking, across all still beats,
    the ones with the highest-scoring shot (running imagery > character > other),
    and using that shot's prompt as the beat's ``video_prompt``.

    This runs AFTER ``apply_video_budget`` (which only downgrades). No candidate
    (all chart / no still) → returns the shotlist unchanged (caller may log).

    Args:
        shotlist:       director output, 1:1 with beats.
        floor:          minimum number of video beats desired (cfg.budget.video_floor).
        default_motion: motion_value stamped on a promoted beat — must be >= the
                        budget threshold (0.6) so the floor survives a later gate.

    Returns:
        Same list; still beats promoted in place until video count reaches floor
        or no candidate remains.
    """
    if floor <= 0:
        return shotlist

    current = sum(1 for e in shotlist if e.get("visual_type") == "video")
    if current >= floor:
        return shotlist

    # Rank still beats by their best shot's score (desc), stable by original index.
    def _beat_best_shot(entry: dict):
        shots = entry.get("shots") or []
        if not shots:
            return None, -1
        best = max(shots, key=_shot_score)
        return best, _shot_score(best)

    candidates = []
    for i, entry in enumerate(shotlist):
        if entry.get("visual_type") != "still":
            continue
        best_shot, score = _beat_best_shot(entry)
        if best_shot is None:
            continue
        # Only promote a still whose best shot is a genuine motion candidate —
        # running imagery (score 2) or a character shot (score 1). A score-0 still
        # (e.g. a notebook/calendar/phone close-up) makes bad b-roll: animating a
        # static-detail prompt via S2V is worse than leaving it a still. If no such
        # candidate exists the floor stays unmet (the caller logs it) — the director
        # b-roll mandate is the primary source of real video, this is only the net.
        if score < 1:
            continue
        candidates.append((score, i, best_shot))

    # Highest score first; tie-break by original index (stable, earliest first).
    candidates.sort(key=lambda t: (-t[0], t[1]))

    need = floor - current
    for score, i, best_shot in candidates[:need]:
        shotlist[i]["visual_type"] = "video"
        shotlist[i]["video_prompt"] = str(best_shot.get("prompt", ""))
        shotlist[i]["motion_value"] = default_motion

    return shotlist
