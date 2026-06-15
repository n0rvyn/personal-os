"""lib/tests/test_scorecard_integration.py — 回归 fixtures + 集成测试.

Task 7-tests contract: three vendored fixtures (06-14 regression, clean
baseline, temperature-shield) feed into `lib.scorecard.build_scorecard`
end-to-end. The scorecard must distinguish:

- 06-14 regression → 不达标 by hard gates alone (段数/草稿头/下注段/
  念稿时长/站内逐字重复); the verdict of "不达标" does NOT depend on
  the judge LLM being alive (06-14 acceptance #1: 确定性硬门独扛).
- clean baseline → 全绿 (every hard gate green; judge 3 维 each ≥3).
- temperature-shield → 全绿 (重复主观立场 + 织入判断 body must NOT
  trigger intra_dup, structural gates must NOT misfire on the woven
  judgment — acceptance #5).

All fixtures live under `lib/tests/fixtures/` so paths are repo-local
(no absolute paths leaking — see plan Task 7-tests Non-goals).

Why this test exists in addition to `test_scorecard.py`:
  test_scorecard.py uses inline strings (`_morning_5seg_with_draft_and_betting`,
  `_short_script_text`, etc.) for fast unit-style verification. This
  integration test exercises REAL vendored files the way the runner will:
  the fixture file is the input the runner reads off disk at step 13a.
  Boundary differences (encoding, trailing newline, BOM, mixed line
  endings) that unit tests miss are caught here.

FAIL-first contract: this file's tests must FAIL pre-impl because the
fixtures don't exist yet (collection error: `FileNotFoundError` on
fixture paths). Task 7-impl may tweak fixture content to align thresholds
without widening them (诚信: 不要凑绿).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.scorecard import ...` resolves.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# fixture paths (relative to THIS test file — no machine-absolute paths)
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

REGRESSION_BODY = FIXTURES_DIR / "2026-06-14-morning-regression.md"
REGRESSION_SCRIPT = FIXTURES_DIR / "2026-06-14-morning-broadcast.txt"

CLEAN_BODY = FIXTURES_DIR / "clean-morning.md"
CLEAN_SCRIPT = FIXTURES_DIR / "clean-morning-broadcast.txt"

SHIELD_BODY = FIXTURES_DIR / "temperature-shield.md"
SHIELD_SCRIPT = FIXTURES_DIR / "temperature-shield-broadcast.txt"


# ---------------------------------------------------------------------------
# helpers: covered-ground-shaped store + verdict builders
# ---------------------------------------------------------------------------

def _hot_suez_store() -> dict:
    """A covered-ground store where 苏伊士 is `is_stale=True` against 2026-06-14.

    Mirrors `load_store` output: `{"anchors": {name: entry}}`. Entry shape
    matches what the covered-ground distiller writes — `first_used`,
    `last_used`, `count`, `episodes: [{date, show}, ...]`. 3 distinct
    episode dates inside the 14-day window trigger the count predicate.
    """
    return {
        "anchors": {
            "苏伊士": {
                "first_used": "2026-06-10",
                "last_used": "2026-06-13",
                "count": 3,
                "episodes": [
                    {"date": "2026-06-10", "show": "morning"},
                    {"date": "2026-06-12", "show": "evening"},
                    {"date": "2026-06-13", "show": "morning"},
                ],
            },
        }
    }


def _empty_store() -> dict:
    """Empty covered-ground store — no cross-anchor flags possible."""
    return {"anchors": {}}


def _score_verdict(total: int = 15) -> dict:
    """A score-verdict-shaped dict (qianzhongshu 钱钟书 step output).

    Mirrors the dict the runner reads from `score-verdict.json` at step 13a
    — `candidates[*].scores.total` is the max-of-candidates signal reused
    for the qianzhongshu axis (NOT rejudged).
    """
    return {
        "candidates": [
            {
                "candidate_id": "稿-A",
                "scores": {"洞察": 4, "命名": 3, "跨域": 4, "思考问句": 4, "total": total},
                "selected": True,
            },
            {
                "candidate_id": "稿-B",
                "scores": {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3, "total": 12},
                "selected": False,
            },
            {
                "candidate_id": "稿-C",
                "scores": {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3, "total": 12},
                "selected": False,
            },
        ]
    }


def _factcheck_verdict(ok: bool = True) -> dict:
    """A factcheck verdict dict (shape mirrors `lib.factcheck.check_factcheck`)."""
    if ok:
        return {
            "ok": True,
            "reason": "pass: traceable=1 subjective-skip=1 flagged=0",
            "flagged": [],
        }
    return {
        "ok": False,
        "reason": "FAIL: traceable=0 subjective-skip=0 flagged=1",
        "flagged": [{"claim": "占GDP的比重约百分之三", "reason": "untraceable"}],
    }


def _clean_judge_verdict() -> dict:
    """A fully scored judge verdict (all 3 dims ≥3)."""
    return {
        "有观点": 4,
        "有温度": 4,
        "不同质化": 4,
        "notes": "well-positioned body with explicit framing",
    }


def _gate(result: dict, name: str) -> dict:
    """Lookup helper: pull a hard gate by `name` from a `build_scorecard` result."""
    return next(g for g in result["hard_gates"] if g.get("name") == name)


# ---------------------------------------------------------------------------
# 06-14 regression: hard gates alone must flag (acceptance #1)
# ---------------------------------------------------------------------------

def test_06_14_regression_fails():
    """The real 06-14 morning regression sample must be judged 不达标 by
    hard gates alone, with `judge_verdict=None` (模拟判官未跑/失败).

    Pins:
    - `passed=False` even when the judge is dead.
    - All four deterministic gates red: 段数, 草稿头, 下注段, 念稿时长.
    - 站内重复 (intra_dup) gate red (06-14 「17.2万」/「占GDP」verbatim repeats).
    - The fixture must be vendored — file exists at `lib/tests/fixtures/`.
    """
    from lib.scorecard import build_scorecard

    # Pre-condition: fixtures exist on disk (FAIL-first contract: missing
    # fixture ⇒ FileNotFoundError ⇒ test fails, NOT silently green).
    assert REGRESSION_BODY.exists(), (
        f"missing regression body fixture: {REGRESSION_BODY} (Task 7-tests creates it)"
    )
    assert REGRESSION_SCRIPT.exists(), (
        f"missing regression script fixture: {REGRESSION_SCRIPT}"
    )

    body = REGRESSION_BODY.read_text(encoding="utf-8")
    script = REGRESSION_SCRIPT.read_text(encoding="utf-8")

    result = build_scorecard(
        body=body,
        script_text=script,
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_hot_suez_store(),
        today="2026-06-14",
        judge_verdict=None,  # 模拟判官失败 — 硬门必须独扛
    )

    # 06-14 不达标 (acceptance #1): 不依赖判官同意。
    assert result["passed"] is False, (
        f"06-14 regression must FAIL hard gates alone (judge=None), "
        f"got passed={result['passed']!r}; reason={result['reason']!r}; "
        f"hard_gates={result['hard_gates']}"
    )

    # Every deterministic regression gate must fire red.
    for required in ("sections", "draft_marker", "betting_section", "duration", "intra_dup"):
        gate = _gate(result, required)
        assert gate["ok"] is False, (
            f"06-14 regression must flag {required!r} red; "
            f"got gate={gate!r}"
        )

    # The cross-anchor 苏伊士 gate ALSO fires (06-14 used 苏伊士 12 times —
    # the distiller marked it stale, and the script still contains it).
    cross_dup = _gate(result, "cross_dup")
    assert cross_dup["ok"] is False, (
        f"06-14 regression must flag cross_dup red (苏伊士 hot anchor); "
        f"got gate={cross_dup!r}"
    )


# ---------------------------------------------------------------------------
# Clean baseline: passes
# ---------------------------------------------------------------------------

def test_clean_fixture_passes():
    """The vendored clean morning fixture (4段, no 草稿头, no ⑤段,
    ≥6570 念稿, no 苏伊士, no verbatim repeats) must produce passed=True
    with every hard gate green and every judge dim ≥3.

    Pins:
    - All 6 hard gates green.
    - All 5 judge dims green and scored ≥3.
    - `passed=True`.
    """
    from lib.scorecard import build_scorecard

    # Pre-condition: fixtures exist.
    assert CLEAN_BODY.exists(), f"missing clean body fixture: {CLEAN_BODY}"
    assert CLEAN_SCRIPT.exists(), f"missing clean script fixture: {CLEAN_SCRIPT}"

    body = CLEAN_BODY.read_text(encoding="utf-8")
    script = CLEAN_SCRIPT.read_text(encoding="utf-8")

    result = build_scorecard(
        body=body,
        script_text=script,
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )

    assert result["passed"] is True, (
        f"clean fixture must pass, got passed={result['passed']!r}; "
        f"reason={result['reason']!r}; hard_gates={result['hard_gates']}"
    )

    # Every hard gate green.
    assert result["hard_gates"], "expected hard_gates to be populated"
    for g in result["hard_gates"]:
        assert g.get("ok") is True, (
            f"clean fixture must green every hard gate; red: {g!r}"
        )

    # Every judge dim green and scored ≥3 (1..5 量表 floor=3).
    assert result["judge_dims"], "expected judge_dims to be populated"
    for d in result["judge_dims"]:
        assert d.get("ok") is True, (
            f"clean judge dim must ok; red: {d!r}"
        )
        assert d.get("score", 0) >= 3, (
            f"clean judge dim must score ≥3; got: {d!r}"
        )


# ---------------------------------------------------------------------------
# Temperature shield: restated opinion must NOT trigger false-positive dup
# ---------------------------------------------------------------------------

def test_temperature_shield_passes():
    """The temperature-shield fixture (4段, body restates the SAME subjective
    opinion in different wording across ③/④, contains a woven falsifiable
    judgment, ≥6570 念稿) must produce passed=True.

    Pins (acceptance #5 — 温度回归盾):
    - `passed=True` even though the body repeats the same 主观判断 in
      different wording — dedup's段/句 overlap check is字面-only (shared
      n-gram or shared short run); restated opinions share neither and
      must NOT be flagged as 重复.
    - 结构门 does NOT misfire on the woven judgment (no `## …我下注`
      section header — the judgment is in ④正文).
    - Cross-anchor gate stays green (the script contains no 苏伊士).
    - This is the regression shield for the 温度原则: scoring must
      never punish repeated 主观立场 or woven judgments.
    """
    from lib.scorecard import build_scorecard

    # Pre-condition: fixtures exist.
    assert SHIELD_BODY.exists(), f"missing shield body fixture: {SHIELD_BODY}"
    assert SHIELD_SCRIPT.exists(), f"missing shield script fixture: {SHIELD_SCRIPT}"

    body = SHIELD_BODY.read_text(encoding="utf-8")
    script = SHIELD_SCRIPT.read_text(encoding="utf-8")

    result = build_scorecard(
        body=body,
        script_text=script,
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_hot_suez_store(),  # even with a hot 苏伊士, the shield body/script avoid it
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )

    # The headline: passed=True. The fixture exists specifically to pin
    # that dedup/结构门 do NOT misfire on a body with restated 主观判断.
    assert result["passed"] is True, (
        f"temperature-shield fixture must pass (acceptance #5 — 温度盾), "
        f"got passed={result['passed']!r}; reason={result['reason']!r}; "
        f"hard_gates={result['hard_gates']}"
    )

    # Spot-check: the four gates most at risk of false-positive.
    for name in ("sections", "draft_marker", "betting_section", "duration", "intra_dup", "cross_dup"):
        gate = _gate(result, name)
        assert gate["ok"] is True, (
            f"temperature-shield must green {name!r}; got red: {gate!r}. "
            f"This is the 温度盾 acceptance #5 — repeated 主观判断 / woven "
            f"judgment must NOT trigger dedup or结构门 false-positive."
        )

    # Bonus: intra_dup MUST have empty hits — proving the restated opinion
    # is 字面-only-different (not verbatim). The hit count is the operator-
    # facing signal: a non-empty `hits` list would surface in the scorecard
    # markdown and look like a duplicate-fragment warning even when `ok=True`.
    intra_dup_gate = _gate(result, "intra_dup")
    assert intra_dup_gate.get("hits", []) == [], (
        f"temperature-shield intra_dup hits must be empty "
        f"(no verbatim repeats across restated opinions); got: {intra_dup_gate!r}"
    )