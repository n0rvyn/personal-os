"""科普 select — the paper line's OWN deterministic draft selector (D-011).

Physically isolated from `lib.episode.select_draft`: this module imports NOTHING
from `episode` (enforced by `test_select_does_not_import_episode`). It applies the
SAME recompute discipline as `select_draft` — the winner is the candidate with the
max rubric total, and the scoring LLM's `selected`/`chosen` flag is IGNORED (it can
mislabel it) — but over the 科普 four-dimension rubric (准确 / 清晰 / 框架还原 / 可读),
tiebreak on 准确 then the canonical candidate order 稿-A < 稿-B < 稿-C.

The verdict shape (produced by `agents/papers/digest-scorer.md`):

    {"candidates": [
        {"candidate_id": "稿-A",
         "scores": {"准确": int, "清晰": int, "框架还原": int, "可读": int, "total": int?}},
        ...]}

`total` is honored when present (explicit total takes precedence, matching
`select_draft`); otherwise it is recomputed as the sum of the four dims.
"""
from __future__ import annotations

from typing import Any

# The 科普 rubric dimensions (1-5 each). Distinct from the opinion line's
# 洞察/命名/跨域/思考问句 — physically separate scoring (D-011).
RUBRIC_DIMS = ("准确", "清晰", "框架还原", "可读")

# Canonical candidate order (same shape as `episode._CANDIDATE_ORDER`); the
# committee fans out across these exact ids.
_CANDIDATE_ORDER = ("稿-A", "稿-B", "稿-C")


def _total_of(scores: dict[str, Any]) -> int:
    """Rubric total: explicit `scores.total` wins (the scorer may carry it);
    else the sum of the four dims. Non-numeric/missing dims count as 0."""
    explicit = scores.get("total")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return int(explicit)
    return sum(int(scores.get(d, 0)) for d in RUBRIC_DIMS)


def select_digest(
    verdict: dict[str, Any],
    candidates: dict[str, str],
) -> tuple[str, str]:
    """Pick the winning 解读稿 from a digest-scorer verdict.

    Semantics (D-011 — paper line's own select, isolated from select_draft):
    - **Ignore any `selected`/`chosen` flag** (verdict-level OR per-candidate) —
      the scoring LLM can mislabel it. Winner is determined by rubric total alone.
    - Max rubric `total` wins (explicit total honored, else sum of the 4 dims).
    - Tiebreak: higher `准确` (the 科普 命门 dimension).
    - Tiebreak: canonical order 稿-A < 稿-B < 稿-C (lowest index wins).

    `candidates` maps `candidate_id` ("稿-A"/"稿-B"/"稿-C") to its value (e.g. a
    draft path). Returns `(chosen_id, chosen_value)`.

    Malformed/empty verdict → `ValueError` (never silently picks the first).
    """
    if not isinstance(verdict, dict):
        raise ValueError(f"verdict must be a dict, got {type(verdict).__name__}")
    if not isinstance(candidates, dict) or not candidates:
        raise ValueError("candidates must be a non-empty dict")

    cands = verdict.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise ValueError("verdict.candidates must be a non-empty list")

    # (total, 准确, -order_index) — reverse-sorted so max total, then max 准确,
    # then min order index (稿-A=0 → -0=0 beats 稿-B's -1) wins. order_index is
    # unique per candidate, so this fully orders without consulting any flag.
    scored: list[tuple[int, int, int, str]] = []
    seen: set[str] = set()
    for c in cands:
        if not isinstance(c, dict):
            raise ValueError(
                f"verdict.candidates entries must be dicts, got {type(c).__name__}"
            )
        cid = c.get("candidate_id")
        if cid not in _CANDIDATE_ORDER:
            raise ValueError(
                f"candidate_id must be one of {_CANDIDATE_ORDER}, got {cid!r}"
            )
        if cid in seen:
            raise ValueError(f"duplicate candidate_id {cid!r}")
        seen.add(cid)
        if cid not in candidates:
            continue  # scored but not in the provided mapping → not selectable
        scores = c.get("scores")
        if not isinstance(scores, dict):
            raise ValueError(f"candidate {cid!r} `scores` must be a dict")
        order_idx = _CANDIDATE_ORDER.index(cid)
        scored.append((_total_of(scores), int(scores.get("准确", 0)), -order_idx, cid))

    if not scored:
        raise ValueError("no scored candidate matched the candidates mapping")

    scored.sort(reverse=True)
    chosen_id = scored[0][3]
    return chosen_id, candidates[chosen_id]
