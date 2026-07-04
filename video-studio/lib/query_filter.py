"""query-writer 输出的约束过滤（Task 1）+ L2 slug 重排（Task 2）。

Phase 3: 抽象论点 → 可拍场景查询；硬约束禁办公/屏幕/代码/抽象类词汇。
"""

from __future__ import annotations

import re

_BANNED_VISUAL = frozenset(
    {
        "office",
        "laptop",
        "code",
        "coding",
        "whiteboard",
        "screen",
        "keyboard",
        "desk",
        "monitor",
        "abstract",
        "diagram",
        "formula",
    }
)

_SAFE_FALLBACK = "runner on running track"


def _contains_banned(token: str) -> bool:
    lowered = token.lower()
    return any(banned in lowered for banned in _BANNED_VISUAL)


def sanitize_query(q: str) -> str:
    """剔除含 banned 子串的 token；全 banned 时回退安全兜底。"""
    tokens = q.split()
    kept = [tok for tok in tokens if not _contains_banned(tok)]
    if not kept:
        return _SAFE_FALLBACK
    return " ".join(kept)


_RUNNING_WORDS = frozenset({"run", "runner", "running", "jog", "jogger", "jogging", "sprint", "track", "race"})

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def _candidate_slug(c) -> str:
    """candidate 可为 str（slug 本身）或 dict（slug/url 字段）→ 返回 slug 字符串。"""
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        return str(c.get("slug") or c.get("url") or "")
    return ""


def _slug_tokens(slug: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.split(slug.lower()) if tok}


def token_overlap(query: str, slug: str) -> int:
    """Raw token-intersection count between query and slug (no running bonus).

    Distinct from rerank_candidates' scoring (which adds +1 for running words):
    the offline-pool hit gate needs GENUINE overlap, not the biased score.
    """
    return len(_slug_tokens(query) & _slug_tokens(slug))


def rerank_candidates(query: str, candidates: list) -> list:
    """L2 slug 重排：剔除违禁词候选，按 query↔slug token 重叠打分降序稳定排序。

    候选可为 str slug 或 dict（含 slug/url 字段）；原样返回排序后的候选对象。
    """
    if not candidates:
        return []

    query_tokens = _slug_tokens(query)

    scored = []
    for idx, c in enumerate(candidates):
        slug = _candidate_slug(c)
        slug_tokens = _slug_tokens(slug)
        if any(_contains_banned(tok) for tok in slug_tokens):
            continue
        score = len(query_tokens & slug_tokens)
        if any(w in slug_tokens for w in _RUNNING_WORDS):
            score += 1
        scored.append((score, idx, c))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored]
