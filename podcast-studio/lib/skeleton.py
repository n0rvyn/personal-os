"""podcast-studio evening skeleton pool (Phase 6, 反同质化 —— 骨架层).

The evening essay had ONE fixed骨架 (冷开场 → 旧笔记自我怀疑 → 今日新闻 →
给现象命名 → 可证伪下注 → 散场留作业), so a week of episodes felt structurally
identical even when the argument differed. This module gives the runner a POOL
of narrative approaches to rotate through.

Design decision (keeps the structlint段数门 untouched):
- All templates preserve the evening THREE-段 structure (①②③) + cold-open +
  woven下注 + 上期成绩单 — the段数门 (`structlint.check_sections`) still passes
  for every template. What varies is the NARRATIVE APPROACH inside those three
  sections (how the cold-open lands, how the turn works, how it resolves), NOT
  the section count. So P6 needs NO structlint change.

Selection (DP-003=B: 随机挑、避开最近 N-1 期):
- `pick_skeleton` is DETERMINISTIC per (date, show) — seeded by a stable hash so
  a resume/re-run of the SAME episode picks the SAME template (no churn), while
  different episodes vary. It excludes the recently-used templates; if every
  template is excluded (pool smaller than the window), it falls back to the
  least-recently-used eligible set.
- History persists to `state/skeleton-history.yaml` (atomic; mirrors covered-
  ground discipline) so avoid-recent survives across runs.

Morning is out of scope (event-centric 四段); this pool is evening-only.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

_HISTORY_FILENAME = "skeleton-history.yaml"

# How many recent episodes to avoid repeating (N-1 where N = pool size → never
# repeat until the whole pool has cycled).
_AVOID_WINDOW = 3

# The evening narrative-approach pool. `structure` is injected into the davinci
# draft prompt; all keep 三段 + cold-open + woven下注 (structlint-safe).
POOL: list[dict[str, str]] = [
    {
        "id": "spine-reversal",
        "name": "脊柱反转体",
        "structure": (
            "① 冷开场一个具体画面 + 抛出一个动摇中的信念/脊柱问题；"
            "② 用今天的证据一步步展开，中途一次视角反转（换个坐标系看同一件事）；"
            "③ 落在一个有温度的、诚实的观察上（可证伪判断织入收尾）。"
        ),
    },
    {
        "id": "dual-track",
        "name": "双线对照体",
        "structure": (
            "① 冷开场并置两个看似无关的画面/领域（各起一句）；"
            "② 两条线各自展开，在中段某个证据处让它们意外合流、互相照亮；"
            "③ 合流后落在一个只有对照才能看清的观察上（判断织入收尾）。"
            "全程保持三段，但①②的张力来自两线并行而非单线深挖。"
        ),
    },
    {
        "id": "reverse-time",
        "name": "时间轴倒叙体",
        "structure": (
            "① 冷开场直接给一个结局/现状的画面（把结果先摆出来）；"
            "② 回溯：一步步倒推是什么把它带到这里，每段揭开前一层成因；"
            "③ 倒推到源头后落回当下，给出'所以现在我怎么看'（判断织入收尾）。"
        ),
    },
    {
        "id": "one-question",
        "name": "一问到底体",
        "structure": (
            "① 冷开场抛一个尖锐的、贯穿全篇的问题，不急着答；"
            "② 每一段引一个证据去逼近这个问题、又始终不彻底放过它，层层加压；"
            "③ 收在'我现在的答案 + 还没想清的那部分'（可证伪判断织入这个收尾）。"
        ),
    },
]

_POOL_BY_ID = {t["id"]: t for t in POOL}


def history_path(state_dir: str | os.PathLike) -> Path:
    """`<state_dir>/skeleton-history.yaml` (creates nothing)."""
    return Path(str(state_dir)) / _HISTORY_FILENAME


def load_history(state_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Return the history list `[{date, show, id}]` in append order.
    Missing/empty/corrupt → `[]` (fail-soft)."""
    p = history_path(state_dir)
    if not p.exists():
        return []
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return []
    if not isinstance(parsed, dict):
        return []
    hist = parsed.get("history")
    if not isinstance(hist, list):
        return []
    return [h for h in hist if isinstance(h, dict)]


def _write_history(state_dir: str | os.PathLike, history: list[dict[str, Any]]) -> Path:
    out_dir = Path(os.path.realpath(str(state_dir)))
    if not out_dir.is_dir():
        raise NotADirectoryError(f"state_dir is not a directory: {out_dir}")
    target = history_path(out_dir)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(out_dir))
    tmp_p = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump({"history": history}, f, allow_unicode=True, sort_keys=False)
        os.replace(str(tmp_p), str(target))
    except Exception:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise
    return target


def recent_ids(history: list[dict[str, Any]], n: int = _AVOID_WINDOW) -> list[str]:
    """The template ids used in the last `n` history entries (most recent last)."""
    ids = [h.get("id") for h in history if isinstance(h.get("id"), str)]
    return ids[-n:]


def _seed_index(date: str, show: str, modulo: int) -> int:
    """Deterministic index in [0, modulo) from (date, show). Stable across
    resume/re-run (NOT Python's random — a re-run must pick the SAME template)."""
    h = hashlib.sha256(f"{date}-{show}".encode("utf-8")).hexdigest()
    return int(h, 16) % max(1, modulo)


def pick_skeleton(
    date: str,
    show: str,
    recent: list[str],
) -> dict[str, str]:
    """Pick an evening template deterministically-by-(date,show), avoiding the
    `recent` ids. If every template is in `recent` (window ≥ pool), fall back to
    the eligible set that drops the OLDEST-in-window first (so it still rotates).
    Returns a POOL entry dict."""
    eligible = [t for t in POOL if t["id"] not in recent]
    if not eligible:
        # Whole pool is in the avoid window — drop the oldest recent id(s) until
        # something is eligible (guarantees rotation even if window ≥ pool).
        drop = set()
        for rid in recent:  # oldest first
            drop.add(rid)
            eligible = [t for t in POOL if t["id"] not in (set(recent) - drop)]
            if eligible:
                break
        if not eligible:
            eligible = list(POOL)
    idx = _seed_index(date, show, len(eligible))
    return eligible[idx]


def record_skeleton(
    state_dir: str | os.PathLike,
    date: str,
    show: str,
    template_id: str,
) -> Path:
    """Idempotent upsert of (date, show) → template_id into the history.
    A re-run of the same episode updates in place (no duplicate append)."""
    history = load_history(state_dir)
    updated = False
    for h in history:
        if h.get("date") == date and h.get("show") == show:
            h["id"] = template_id
            updated = True
            break
    if not updated:
        history.append({"date": date, "show": show, "id": template_id})
    return _write_history(state_dir, history)


def get_by_id(template_id: str) -> Optional[dict[str, str]]:
    """Look up a POOL entry by id (None if unknown)."""
    return _POOL_BY_ID.get(template_id)
