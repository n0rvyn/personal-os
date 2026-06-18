"""Tests for lib/paperline/faithfulness.py — the 忠实门 (faithfulness gate).

Written before `lib/paperline/faithfulness.py` exists. Pinned contracts (Task 6
plan + crystal D-009 + Threat Model §2 — recompute, retry=1, second-fail-stop):

  - `check_faithfulness(draft, ledger, fulltext, agent_verdict) -> {ok, reason,
    flagged}`: deterministic-floor + agent-ADD-only merge (mirrors
    `lib.factcheck.check_factcheck`'s pattern). The agent's verdict can ADD
    flags (夸大-suspected / contradicted) but NEVER clear a deterministic flag
    — D-009 recompute discipline.

  - Deterministic floor — 3 checks:
      1. Anchor traceability (reuse `lib.paperline.ledger.verify_anchors`).
      2. 夸大 detection: absolute-strength lexicon (e.g. "彻底解决" / "完全攻克")
         in the draft body while the matching ledger evidence is hedged.
      3. Per-ledger-limitation coverage: each `limitations[*]` entry must have
         a concept echoed in the body (substring of the limitation's `text`
         appears in the body).

  - Faithful draft (neutral wording, all limitations echoed) → PASS.
  - Exaggerated draft ("彻底解决了 / 完全攻克") → FLAGGED (夸大).
  - Dropped-limitation draft (no ledger limitation echoed) → FLAGGED (coverage).
  - Agent `faithful: true` self-label CANNOT clear a deterministic flag.

  - Module isolation (MF#4): `faithfulness.py` MUST NOT import `factcheck`
    (the existing `test_line_isolation` firewall does NOT cover `factcheck`).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.faithfulness import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


FIXTURES_DIR = PLUGIN_ROOT / "lib" / "tests" / "fixtures" / "faithfulness"
STAGED_LEDGER = PLUGIN_ROOT / ".claude" / "p2-samples" / "paper-ledger.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_draft(name: str) -> str:
    """Read a fixture draft by file-stem (e.g. 'faithful', 'exaggerated',
    'dropped_limitation'). Returns the markdown body."""
    p = FIXTURES_DIR / f"{name}-draft.md"
    return p.read_text(encoding="utf-8")


def _load_staged_ledger() -> dict[str, Any]:
    """Read the staged real ledger (`2606.19341`). Tests use this to feed the
    deterministic floor (anchor traceability + limitations coverage)."""
    return json.loads(STAGED_LEDGER.read_text(encoding="utf-8"))


def _fulltext() -> str:
    """Read the staged real fulltext (PDF text fallback). Used for the
    anchor traceability half of the deterministic floor."""
    p = PLUGIN_ROOT / ".claude" / "p2-samples" / "arxiv-2606.19341-pdftotext.txt"
    return p.read_text(encoding="utf-8")


def _empty_agent_verdict() -> dict[str, Any]:
    """A neutral agent verdict that adds NO flags. The deterministic floor is
    the SOLE authority on the verdict shape."""
    return {"claims": [], "faithful": True}


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

def test_module_imports():
    """`lib.paperline.faithfulness` must import cleanly; failing here is
    the FAIL-first contract — Task 6-impl resolves this.

    The module is the SOLE gate surface for the paper line (mirrors
    `lib.factcheck.check_factcheck`'s role for the opinion line)."""
    from lib.paperline import faithfulness  # noqa: F401

    assert hasattr(faithfulness, "check_faithfulness"), (
        "faithfulness.check_faithfulness is the gate's public surface "
        "(mirrors factcheck.check_factcheck); it must be exposed at module level"
    )
    assert callable(faithfulness.check_faithfulness)


def test_module_does_not_import_factcheck():
    """AST-scan `lib/paperline/faithfulness.py` and assert it does NOT
    import `factcheck` (the silent-divergence guard; MF#4). The design says
    "reuse the PATTERN + `verify_anchors`, not the module" — factcheck's
    news-section parser is opinion-specific and would couple the paper
    line to opinion's data path.

    The existing `test_line_isolation` firewall does NOT cover `factcheck`
    (only the 4 opinion-only modules stance/coveredground/magnitude/bible).
    Enforce the isolation here at the call site.
    """
    faith_path = PLUGIN_ROOT / "lib" / "paperline" / "faithfulness.py"
    if not faith_path.exists():
        pytest.skip(
            f"faithfulness.py does not exist yet (Task 6-impl): {faith_path}"
        )

    src = faith_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    # Forbidden forms (mirror test_paperline_select's isolation test).
    forbidden = {
        "lib.factcheck",
        "factcheck",
        "lib.factcheck.check_factcheck",
        "check_factcheck",
    }
    bad = sorted(
        imp for imp in imported
        if imp in forbidden
        or any(imp == f or imp.startswith(f + ".") for f in forbidden)
    )
    assert not bad, (
        "lib/paperline/faithfulness.py must NOT import factcheck "
        "(design says 'reuse the PATTERN, not the module'; "
        "the news-section parser is opinion-specific). Forbidden imports found: "
        f"{bad}"
    )


# ---------------------------------------------------------------------------
# check_faithfulness: faithful draft PASSES
# ---------------------------------------------------------------------------

def test_faithful_passes():
    """A faithful draft (neutral wording, all limitations echoed) MUST PASS
    the deterministic floor + agent add-only merge. Confirms the floor
    doesn't false-positive on benign prose."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("faithful")
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    verdict = _empty_agent_verdict()

    out = check_faithfulness(draft, ledger, fulltext, verdict)
    assert out["ok"] is True, (
        f"faithful draft must PASS; got {out!r}. The deterministic floor "
        f"should not false-positive on a draft that quotes limitations "
        f"verbatim and uses hedged (not absolute) wording."
    )
    assert out.get("flagged") in (None, []), (
        f"faithful draft must have NO flagged entries; got {out.get('flagged')!r}"
    )


# ---------------------------------------------------------------------------
# check_faithfulness: exaggerated draft is FLAGGED (夸大)
# ---------------------------------------------------------------------------

def test_exaggeration_flagged():
    """An exaggerated draft (absolute-strength lexicon like '彻底解决' /
    '完全攻克') MUST be FLAGGED by the deterministic floor even when the
    agent judge says `faithful: True`. Mirrors the factcheck pattern:
    agent add-only, deterministic flags are authoritative (D-009)."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("exaggerated")
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    # Agent says faithful — but the floor's lexicon MUST still flag.
    verdict = _empty_agent_verdict()

    out = check_faithfulness(draft, ledger, fulltext, verdict)
    assert out["ok"] is False, (
        f"exaggerated draft (contains '彻底解决' / '完全攻克') must be FLAGGED; "
        f"got ok=True with {out!r}. The deterministic 夸大 floor is the "
        f"authoritative signal; agent's `faithful: True` cannot clear it."
    )
    flagged = out.get("flagged") or []
    assert any(
        "夸" in str(f.get("reason", "")) or "absolute" in str(f.get("reason", "")).lower()
        for f in flagged
    ), (
        f"at least one flagged entry must name the 夸大 cause; got {flagged!r}"
    )


# ---------------------------------------------------------------------------
# check_faithfulness: dropped-limitation draft is FLAGGED (coverage)
# ---------------------------------------------------------------------------

def test_dropped_limitation_flagged():
    """A dropped limitation MUST be FLAGGED — but coverage is AGENT-ASSESSED
    (SF-2 fix): code string-matching can't tell a paraphrased-but-present
    limitation from a dropped one (the body shares the paper's vocabulary). The
    faithfulness-judge reports `dropped_limitations`; the code flags those (the
    溯源 + 夸大 floors stay code-authoritative). Here the judge caught all three
    drops in the dropped-limitation draft."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("dropped_limitation")
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    # The judge assessed the body and reports the dropped limitations.
    verdict = {
        "faithful": False,
        "dropped_limitations": [{"index": 0}, {"index": 1}, {"index": 2}],
    }

    out = check_faithfulness(draft, ledger, fulltext, verdict)
    assert out["ok"] is False, (
        f"dropped-limitation draft must be FLAGGED when the judge reports the "
        f"drops; got ok=True with {out!r}."
    )
    flagged = out.get("flagged") or []
    assert any(
        "limit" in str(f.get("reason", "")).lower()
        or "coverage" in str(f.get("reason", "")).lower()
        for f in flagged
    ), (
        f"at least one flagged entry must name the coverage cause; got {flagged!r}"
    )


def test_paraphrased_limitations_not_false_flagged():
    """SF-2 fix proof: a body that PARAPHRASES the limitations into 大白话 (not
    verbatim) MUST NOT be coverage-flagged when the judge reports no drops. This
    is the whole reason coverage moved to the agent — a finalizer's job is to
    paraphrase, and code string-coverage halted exactly these good drafts."""
    from lib.paperline.faithfulness import check_faithfulness

    # A faithful-but-paraphrased 局限段 (rewrites the wording, keeps the concepts;
    # NO absolute-strength lexicon → no 夸大 flag).
    paraphrased = (
        "# OmniAgent 科普解读\n\n## 意义与局限\n"
        "作者也老实交代了几条边界：要是直接拿强化学习去训这个智能体，它容易"
        "学崩掉，所以得先用监督微调暖个场；他们那条「熵高即关键决策点」的假设"
        "也不是处处成立，还有约两成的分叉步并不符合；论文里还自曝过一次看走眼，"
        "只盯着画面文字把阶段数数错了，后来靠声音线索才纠正。"
    )
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    verdict = {"faithful": True, "dropped_limitations": []}  # judge: nothing dropped

    out = check_faithfulness(paraphrased, ledger, fulltext, verdict)
    assert out["ok"] is True, (
        f"a paraphrased-but-faithful body must PASS (no false coverage flag); "
        f"got {out!r}. This is the SF-2 fix: coverage is the agent's semantic call."
    )


# ---------------------------------------------------------------------------
# check_faithfulness: agent self-label cannot clear a deterministic flag
# ---------------------------------------------------------------------------

def test_agent_self_label_cannot_clear_deterministic_flag():
    """The agent's `faithful: True` self-label is ADD-ONLY — it can NEVER
    clear a deterministic flag. An exaggerated draft + a `faithful: True`
    verdict still halts (mirrors factcheck's `contradicted` discipline:
    the agent judge can flag contradictions but never un-flag them)."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("exaggerated")
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    # Agent says faithful AND claims nothing was contradicted.
    verdict = {
        "claims": [],
        "faithful": True,
        "verified": True,
        "no_flags": True,
    }

    out = check_faithfulness(draft, ledger, fulltext, verdict)
    assert out["ok"] is False, (
        f"agent self-label cannot clear a deterministic 夸大 flag; "
        f"got ok=True with {out!r}. The agent's `faithful: True` is ADD-ONLY "
        f"(D-009; mirrors factcheck's contradicted discipline)."
    )
    # The deterministic flag MUST still be in the output.
    assert out.get("flagged"), (
        f"flagged entries must persist despite agent self-label; got {out!r}"
    )


def test_agent_can_add_flags():
    """The agent CAN ADD flags the deterministic floor missed (D-009 — the
    hybrid model). When the agent flags a claim the floor didn't, the
    verdict carries those agent-added flags too. This pins the
    'deterministic floor + agent ADD-only merge' merge semantics."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("faithful")
    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    # Agent flags a claim — this is ADD-ONLY; merged into flagged[].
    verdict = {
        "claims": [
            {
                "claim": "the 50.5% number is hard to read out of context",
                "verdict": "suspected_exaggeration",
                "reason": "agent-judge concern",
            },
        ],
        "faithful": False,
    }

    out = check_faithfulness(draft, ledger, fulltext, verdict)
    # Even though the floor passes (faithful draft), the agent's
    # `faithful: False` plus its flags must produce ok=False.
    assert out["ok"] is False, (
        f"agent's `faithful: False` must produce ok=False even when the "
        f"deterministic floor passes; got {out!r}"
    )
    flagged = out.get("flagged") or []
    assert any(
        "suspected" in str(f.get("reason", "")).lower()
        or f.get("source") == "agent"
        for f in flagged
    ), (
        f"agent-added flag must be in the merged flagged list; got {flagged!r}"
    )


# ---------------------------------------------------------------------------
# check_faithfulness: malformed inputs raise (fail-closed, never silent)
# ---------------------------------------------------------------------------

def test_non_dict_draft_raises():
    """A non-string draft body must raise ValueError — the floor cannot
    run a lexicon / coverage / anchor check on a non-string. Mirrors
    factcheck's discipline: never silently pass on garbage input."""
    from lib.paperline.faithfulness import check_faithfulness

    ledger = _load_staged_ledger()
    fulltext = _fulltext()
    verdict = _empty_agent_verdict()
    with pytest.raises((ValueError, TypeError)):
        check_faithfulness(None, ledger, fulltext, verdict)
    with pytest.raises((ValueError, TypeError)):
        check_faithfulness(123, ledger, fulltext, verdict)


def test_non_dict_ledger_raises():
    """A non-dict ledger must raise — the schema/anchor floor cannot run
    without a structured ledger to compare against."""
    from lib.paperline.faithfulness import check_faithfulness

    draft = _load_draft("faithful")
    fulltext = _fulltext()
    verdict = _empty_agent_verdict()
    with pytest.raises((ValueError, TypeError)):
        check_faithfulness(draft, None, fulltext, verdict)
    with pytest.raises((ValueError, TypeError)):
        check_faithfulness(draft, "not-a-dict", fulltext, verdict)


# ---------------------------------------------------------------------------
# Engine-level retry test (MF#3) — lives in test_runner.py (added below).
#
# The unit-level tests above pin the gate's verdict shape (PASS / FLAGGED).
# The engine-level test pins the retry-then-stop behavior: a flagged gate
# RE-DISPATCHES the finalize parent (via `_RETRY_PARENT`), and a second flag
# HALTS with no .md published. Without the engine-level test, the retry
# wiring could be broken while unit tests still pass — the P2 vacuous-
# firewall lesson ("looks wired, isn't").
# ---------------------------------------------------------------------------