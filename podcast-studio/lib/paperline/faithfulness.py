"""忠实门 (faithfulness gate) — the paper line's blocking quality gate (D-009).

The deterministic half of the 忠实门. Like `lib.factcheck.check_factcheck` for the
opinion line, the parts that must NOT rely on the LLM getting it right live here.
This module REUSES factcheck's PATTERN (recompute a deterministic floor, the agent
judge can only ADD flags, never clear one) but **does not import `factcheck`** —
factcheck's parser is news-section-specific and would couple the paper line to the
opinion data path (design "复用 factcheck 骨架" = reuse the pattern, not the module;
enforced by `test_module_does_not_import_factcheck`).

The deterministic floor (3 checks, recompute discipline — the mechanism was
probe-validated on the real `2606.19341` ledger, see
`.claude/p3-probes/faithfulness-probe-finding.json`):

  1. **Anchor traceability** — `lib.paperline.ledger.verify_anchors(ledger, fulltext)`:
     every ledger anchor must be grounded in the full text. Grounding is determined
     by `lib.paperline.ledger.verify_anchors`: numerics (tokens containing a digit,
     e.g. `50.5%`, `10×`, `72b`, `qwen2.5-vl-72b`, `2025`) must all appear in the
     normalized full text (zero tolerance — catches fabricated numbers / misattributed
     names), and pure-word tokens must be present at ≥ 80% containment (tolerates
     connecting words / case / pdftotext reflow — see DP-001). Verbatim-substring
     matching is no longer the rule; faithful rewrites now pass.
  2. **夸大 detection** — an absolute-strength phrase ("彻底解决" / "完全攻克" / …) in
     the draft body, while the paper's results are only HEDGED gains (comparative %),
     is an over-claim → flag.
Checks 1+2 are CODE-authoritative (deterministic floor; the agent can only ADD flags,
never clear one — D-009; mirrors factcheck's `contradicted` add-only discipline).

  3. **局限保留 (coverage) — AGENT-ASSESSED** (SF-2, user decision 2026-06-18). A code
     string-match CANNOT distinguish a paraphrased-but-present limitation from a
     dropped one: the finalizer rewrites limitations into 讲解者 大白话, and the body
     shares the paper's vocabulary, so verbatim / n-gram / entity coverage all either
     false-flag a faithful paraphrase OR false-pass a real drop (empirically confirmed).
     Concept-preservation is a semantic judgment. The faithfulness-judge reports
     `dropped_limitations` (which ledger limitations the body omits); the code flags
     those. ONLY coverage moved to the agent; 溯源 (1) + 夸大 (2) stay code floors.

Fail-closed: a non-string draft / non-dict ledger raises.
"""
from __future__ import annotations

from typing import Any

from lib.paperline.ledger import verify_anchors  # same line — NOT factcheck

# Absolute / unhedged strength phrases. A paper that reports a comparative gain
# ("超过基线 50.5% vs 47.3%") does NOT support "彻底解决 / 完全攻克 / 从根本上解决".
# Phrases (not bare chars) so hedged prose ("没真正解决问题") never false-flags.
_ABSOLUTE_STRENGTH = (
    "彻底解决", "完全攻克", "彻底攻克", "完全解决", "从根本上解决", "根本上解决",
    "一举解决", "完美解决", "终结了", "碾压", "秒杀", "吊打",
    "completely solves", "fully solves", "eliminates", "breakthrough", "revolutioniz",
)

# Verdict labels that mean "the agent flagged this claim" (add-only). Anything
# outside the OK set is treated as an agent-added flag.
_AGENT_OK_VERDICTS = {"ok", "verified", "traceable", "faithful", "pass", "supported"}


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def _inner_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Accept either the inner 4-section ledger or the wrapped artifact
    (`{arxiv_id, fetch, ledger:{...}, verdict}`) — return the section-bearing dict."""
    inner = ledger.get("ledger")
    if isinstance(inner, dict) and "limitations" in inner:
        return inner
    return ledger


def check_faithfulness(
    draft: Any,
    ledger: Any,
    fulltext: Any,
    agent_verdict: Any,
) -> dict[str, Any]:
    """Blocking faithfulness gate.

    Recomputes the deterministic floor over (`draft`, `ledger`, `fulltext`), then
    merges the agent judge's ADD-ONLY flags. Returns `{ok, reason, flagged}` (same
    shape family as `lib.factcheck.check_factcheck`). Fail-closed: a non-string
    `draft` or non-dict `ledger` raises `ValueError`.
    """
    if not isinstance(draft, str):
        raise ValueError(f"draft must be a string body, got {type(draft).__name__}")
    if not isinstance(ledger, dict):
        raise ValueError(f"ledger must be a dict, got {type(ledger).__name__}")
    if not isinstance(fulltext, str):
        raise ValueError(f"fulltext must be a string, got {type(fulltext).__name__}")

    led = _inner_ledger(ledger)
    body_norm = _norm_ws(draft)
    flagged: list[dict[str, Any]] = []

    # --- floor 1: anchor traceability (the ledger must be grounded) ---
    try:
        anchors = verify_anchors(led, fulltext)
        if not anchors.get("ok", False):
            for f in anchors.get("flagged", []):
                flagged.append({
                    "reason": "untraceable anchor (ledger not grounded in full text)",
                    "anchor": f.get("anchor"),
                    "section": f.get("section"),
                })
    except Exception as e:  # noqa: BLE001 — fail-closed: an unverifiable ledger flags
        flagged.append({"reason": f"anchor verification failed: {e}"})

    # --- floor 2: 夸大 (absolute-strength over-claim) ---
    for phrase in _ABSOLUTE_STRENGTH:
        if phrase in draft:
            flagged.append({
                "reason": f"夸大: absolute-strength claim '{phrase}' — the paper reports "
                          f"only hedged/comparative gains, not an absolute solve",
                "phrase": phrase,
            })

    # --- 局限保留 (coverage) — AGENT-ASSESSED (SF-2 fix, user decision 2026-06-18) ---
    # NOT a code string-match: the finalizer paraphrases each limitation into 讲解者
    # 大白话, and the body shares the paper's vocabulary — so verbatim/n-gram/entity
    # coverage all either false-flag a faithful paraphrase OR false-pass a dropped
    # limitation (empirically confirmed). Concept-preservation is a SEMANTIC judgment
    # code can't make. The faithfulness-judge reports `dropped_limitations` (which
    # ledger limitations the body actually omits); the code flags those. The 溯源 +
    # 夸大 floors above STAY code-authoritative (agent ADD-only); ONLY coverage —
    # inherently semantic — is the agent's call. With no agent verdict (empty), no
    # coverage is asserted (the live gate relies on the judge reporting drops).
    n_limitations = len(led.get("limitations") or []) if isinstance(led, dict) else 0
    dropped = agent_verdict.get("dropped_limitations") if isinstance(agent_verdict, dict) else None
    if isinstance(dropped, list):
        for d in dropped:
            idx = d.get("index") if isinstance(d, dict) else d
            flagged.append({
                "source": "agent",
                "reason": f"limitation coverage: ledger limitation "
                          f"#{idx} dropped from the body (agent-assessed)"
                          + (f" — {d.get('reason')}" if isinstance(d, dict) and d.get("reason") else ""),
                "limitation_index": idx,
            })

    # --- agent ADD-ONLY merge (can ADD flags, can NEVER clear a deterministic one) ---
    if isinstance(agent_verdict, dict):
        for c in agent_verdict.get("claims", []) or []:
            if not isinstance(c, dict):
                continue
            v = str(c.get("verdict", "")).strip().lower()
            if v and v not in _AGENT_OK_VERDICTS:
                flagged.append({
                    **c,
                    "source": "agent",
                    "reason": f"agent-flagged: {c.get('verdict')}"
                              + (f" — {c.get('reason')}" if c.get("reason") else ""),
                })

    ok = len(flagged) == 0
    n_det = sum(1 for f in flagged if f.get("source") != "agent")
    n_agent = sum(1 for f in flagged if f.get("source") == "agent")
    reason = (
        f"{'pass' if ok else 'FAIL'}: deterministic_flags={n_det} "
        f"agent_added_flags={n_agent} (忠实门: traceability + 夸大 + 局限保留)"
    )
    return {"ok": ok, "reason": reason, "flagged": flagged}
