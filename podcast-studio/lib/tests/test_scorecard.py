"""Tests for lib/scorecard.py — 记分卡组装 (硬门 + 判官维度).

Written before lib/scorecard.py exists; collection must fail at this point
(`No module named 'lib.scorecard'`).

Pins (Task 3-tests contract):
- build_scorecard composes hard gates (structlint + dedup intra + dedup cross +
  must-have artifacts) and judge dims (qianzhongshu total reused + factcheck ok
  reused + judge 3 dims).
- 06-14 regression input (5段 + 草稿头 + ⑤段 + 短念稿 + 站内逐字重复) with
  judge_verdict=None MUST be flagged passed=False by hard gates alone
  (06-14 不达标不依赖判官; see plan acceptance #1).
- Clean input (4段 + no 草稿头 + no ⑤段 + ≥6570 念稿 + judge 3 维 各=4) →
  passed=True.
- score-verdict total=15 → qianzhongshu axis ok; total=12 → 红.
  build_scorecard does NOT rejudge qianzhongshu; it READS total from the
  verdict the runner hands it (the "钱钟书 rejudge" landmine — separate LLM
  call would be a regression, see plan Task 3-impl Step 2).
- factcheck verdict.ok=False → 信息准确 axis 红 (fail-closed for the
  temperature principle; factcheck NEVER fakes success).
- safe_parse_scorecard: malformed / missing judge dims → that dim is marked
  `unscored`; renderable; hard gates still judge.
- render_scorecard_md: produces a markdown with hard-gates table + judge
  dims table + total verdict.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.scorecard import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 3-impl will resolve this."""
    from lib import scorecard  # noqa: F401
    assert hasattr(scorecard, "build_scorecard")
    assert hasattr(scorecard, "safe_parse_scorecard")
    assert hasattr(scorecard, "render_scorecard_md")


# ---------- fixtures: 06-14 regression shape + clean baseline ----------

def _morning_4seg_clean() -> str:
    """Clean 4-段 morning body (no 草稿头, no ⑤段, no 我下注 标题)."""
    return (
        "## ① 开场\n"
        "今天的开场我们聊聊一个所有人都关心的话题——AI 在企业内部的渗透。\n\n"
        "## ② 现象\n"
        "大厂已经把大模型塞进客服系统，效果参差不齐。\n\n"
        "## ③ 纵深\n"
        "为什么客服成了第一站？因为它最容易被量化、最不容易出错。\n\n"
        "## ④ 收束\n"
        "我的判断是接下来一年，HR 系统会成为下一个被 LLM 重塑的场景——"
        "这是可证伪的：18 个月内看 HR SaaS 的 LLM 化覆盖率能否翻倍。\n"
    )


def _morning_5seg_with_draft_and_betting() -> str:
    """06-14 regression shape: 5段 + 草稿头 + ⑤段 (我下注)."""
    return (
        "# 草稿 C — 今日话题\n"
        "## ① 开场\n"
        "今天的开场我们聊聊一个所有人都关心的话题。\n\n"
        "## ② 现象\n"
        "大厂已经把大模型塞进客服系统，效果参差不齐。\n\n"
        "## ③ 纵深\n"
        "为什么客服成了第一站？因为它最容易被量化、最不容易出错。\n\n"
        "## ④ 收束\n"
        "我的判断是接下来一年，HR 系统会成为下一个被 LLM 重塑的场景。\n\n"
        "## ⑤ 我下注什么\n"
        "我下注 HR 系统的 LLM 化会在 18 个月内完成。\n"
    )


def _long_script_text() -> str:
    """A 念稿 that clears the 6570 floor (≥18 minutes at 365 字/分)."""
    sentence = "今天我们讲一个关于产业升级的故事，涉及到供应链的多个环节。"
    # 28 chars each. 6570 / 28 = 234.6 → 235 sentences = 6580, above floor.
    text = sentence * 235
    assert len(text) >= 6570
    return text


def _short_script_text() -> str:
    """A 5455-字 念稿 (06-14 measured length) — below the 6570 floor."""
    sentence = "今天我们讲一个关于产业升级的故事，涉及到供应链的多个环节。"
    text = sentence * 188 + "一二三"  # 5452 + 3 = 5455
    assert len(text) == 5455
    return text


def _score_verdict(total: int = 15) -> dict:
    """A score-verdict-shaped dict with a 稿-A selected whose total is `total`.
    Mirrors what `lib/episode.select_draft` would consume in scratch."""
    return {
        "candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 4, "命名": 3, "跨域": 4,
             "思考问句": 4, "total": total}, "selected": True},
            {"candidate_id": "稿-B", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3,
             "思考问句": 3, "total": 12}, "selected": False},
        ]
    }


def _factcheck_verdict(ok: bool = True) -> dict:
    """A factcheck verdict dict — shape mirrors lib.factcheck.check_factcheck output."""
    if ok:
        return {
            "ok": True,
            "reason": "pass: traceable=1 subjective-skip=1 flagged=0",
            "flagged": [],
        }
    return {
        "ok": False,
        "reason": "FAIL: traceable=0 subjective-skip=0 flagged=1",
        "flagged": [{"claim": "占GDP的比重约百分之三", "reason": "untraceable: no recorded source"}],
    }


def _empty_store() -> dict:
    """An empty covered-ground store (no anchors → no cross-anchor flags)."""
    return {"anchors": {}}


def _clean_judge_verdict() -> dict:
    """A fully scored judge verdict (all 3 dims ≥3)."""
    return {
        "有观点": 4,
        "有温度": 4,
        "不同质化": 4,
        "notes": "well-positioned body with explicit framing",
    }


# ---------- 06-14 regression: hard gates alone must flag ----------

def test_06_14_fails_on_hard_gates_alone():
    """The 06-14 regression input (5段 + 草稿头 + ⑤段 + 短念稿 + 站内逐字重复)
    with judge_verdict=None must produce passed=False on hard gates alone.

    The plan's acceptance #1 explicitly pins this: '06-14 不达标由确定性硬门独
    扛,不依赖判官' — the scorecard CANNOT rely on the judge agreeing to flag
    a regression sample. If the judge is dead (None verdict), the hard gates
    must still produce passed=False.
    """
    from lib.scorecard import build_scorecard

    body = _morning_5seg_with_draft_and_betting()
    script = _short_script_text()
    result = build_scorecard(
        body=body,
        script_text=script,
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=None,
    )

    assert result["passed"] is False, (
        f"06-14 shape must FAIL hard gates alone, got passed={result['passed']!r}"
    )

    # Hard gates must fire on at least the four deterministic regressions.
    hard_gates = result.get("hard_gates", [])
    assert len(hard_gates) >= 3, (
        f"expected ≥3 hard-gate hits (段数/草稿头/下注段/念稿时长/站内重复), "
        f"got {len(hard_gates)}: {hard_gates}"
    )

    # Each hard gate is a {name, ok, detail}-shaped dict.
    gate_names = {g.get("name") for g in hard_gates}
    # At minimum these four must be present and red.
    for required in ("sections", "draft_marker", "betting_section", "duration"):
        assert required in gate_names, (
            f"missing required hard gate: {required!r}; got gates={gate_names}"
        )
        gate = next(g for g in hard_gates if g.get("name") == required)
        assert gate.get("ok") is False, (
            f"06-14 regression must flag {required!r} red"
        )


# ---------- Clean input passes ----------

def test_clean_input_passes():
    """Clean body + long script + judge 3 维 each=4 → passed=True.

    Pin: a body with 4段, no 草稿头, no ⑤段, woven judgment, ≥6570 念稿,
    judge verdict fully scored, score-verdict total=15, factcheck ok → all
    gates green, all dims ≥3, verdict passed=True.
    """
    from lib.scorecard import build_scorecard

    result = build_scorecard(
        body=_morning_4seg_clean(),
        script_text=_long_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )

    assert result["passed"] is True, (
        f"clean input must pass, got {result!r}"
    )
    # Every hard gate green.
    hard_gates = result.get("hard_gates", [])
    assert hard_gates, "expected hard_gates to be populated"
    for g in hard_gates:
        assert g.get("ok") is True, f"clean input must green every hard gate; red: {g!r}"

    # Every judge dim green and ≥ floor.
    dims = result.get("judge_dims", [])
    assert dims, "expected judge_dims to be populated"
    for d in dims:
        assert d.get("ok") is True, f"clean judge dim must ok; red: {d!r}"
        assert d.get("score", 0) >= 3, f"clean judge dim must score ≥3; got {d!r}"


# ---------- qianzhongshu total: reused, not rejudged ----------

def test_qianzhongshu_total_reused_not_rejudged():
    """build_scorecard must READ `total` from the passed-in score-verdict and
    NOT rejudge qianzhongshu. total=15 → qianzhongshu axis green; total=12 →
    qianzhongshu axis red. The runner passes the verdict `qianzhongshu` already
    produced; rejudging would be a redundant LLM call AND risk divergence from
    the step-9 verdict.

    The contract pin: there's NO `score_verdict` parameter that, when None,
    triggers a fresh qianzhongshu call. The scorecard consumes what it's given.
    """
    from lib.scorecard import build_scorecard

    # total=15 → ok
    result_ok = build_scorecard(
        body=_morning_4seg_clean(),
        script_text=_long_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )
    qzs_axis_ok = next(
        (d for d in result_ok["judge_dims"] if d.get("name") == "qianzhongshu_total"),
        None,
    )
    assert qzs_axis_ok is not None, (
        "judge_dims must contain a 'qianzhongshu_total' entry"
    )
    assert qzs_axis_ok["ok"] is True, (
        f"total=15 must green qianzhongshu axis, got {qzs_axis_ok!r}"
    )
    assert qzs_axis_ok.get("score") == 15

    # total=12 → red (below the floor, default QZS_TOTAL_FLOOR=14)
    result_low = build_scorecard(
        body=_morning_4seg_clean(),
        script_text=_long_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=12),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )
    qzs_axis_low = next(
        (d for d in result_low["judge_dims"] if d.get("name") == "qianzhongshu_total"),
        None,
    )
    assert qzs_axis_low is not None
    assert qzs_axis_low["ok"] is False, (
        f"total=12 must red qianzhongshu axis (below floor=14), got {qzs_axis_low!r}"
    )
    assert qzs_axis_low.get("score") == 12


# ---------- factcheck axis: from verdict.ok ----------

def test_factcheck_axis_from_verdict():
    """The 信息准确 axis reads from factcheck_verdict.ok. ok=False → red.

    Pin: build_scorecard consumes the verdict the factcheck step produced —
    it does not re-run factcheck. ok=False MUST propagate to the axis.
    """
    from lib.scorecard import build_scorecard

    result_red = build_scorecard(
        body=_morning_4seg_clean(),
        script_text=_long_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=False),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )
    fc_axis = next(
        (d for d in result_red["judge_dims"] if d.get("name") == "factcheck"),
        None,
    )
    assert fc_axis is not None, "judge_dims must contain a 'factcheck' entry"
    assert fc_axis["ok"] is False, (
        f"factcheck ok=False must red 信息准确 axis, got {fc_axis!r}"
    )


# ---------- judge fail-soft: safe_parse_scorecard + unscored ----------

def test_judge_failsoft():
    """safe_parse_scorecard must NEVER raise. Malformed / partial judge
    verdicts produce the dim as `unscored`; the scorecard still renders with
    the unscored dim flagged but not crashing the pipeline.

    Pin: hard gates still judge correctly even when the judge LLM is
    completely broken — that's the whole point of the deterministic /
    LLM-judge split.
    """
    from lib.scorecard import safe_parse_scorecard, build_scorecard

    # Malformed raw: missing dims entirely → safe_parse must return all unscored.
    parsed_empty = safe_parse_scorecard({})
    assert isinstance(parsed_empty, dict)
    for key in ("有观点", "有温度", "不同质化"):
        assert parsed_empty.get(key) == "unscored", (
            f"safe_parse with empty input must mark {key!r} as 'unscored', "
            f"got {parsed_empty!r}"
        )

    # Partially malformed: only one dim present → that one scored, others unscored.
    parsed_partial = safe_parse_scorecard({"有观点": 5})
    assert parsed_partial.get("有观点") == 5
    assert parsed_partial.get("有温度") == "unscored"
    assert parsed_partial.get("不同质化") == "unscored"

    # Out-of-range dim value (e.g. 0 or 7) → safe_parse must mark as unscored
    # (3 维 valid range is 1..5 per plan).
    parsed_oor = safe_parse_scorecard({"有观点": 0, "有温度": 4, "不同质化": 7})
    assert parsed_oor.get("有观点") == "unscored"
    assert parsed_oor.get("有温度") == 4
    assert parsed_oor.get("不同质化") == "unscored"

    # Non-dict raw input → safe_parse must return all unscored, NOT raise.
    parsed_garbage = safe_parse_scorecard("not a dict at all")  # type: ignore[arg-type]
    assert isinstance(parsed_garbage, dict)
    assert parsed_garbage.get("有观点") == "unscored"

    # End-to-end: build_scorecard with a malformed judge dict (some dims
    # marked 'unscored' rather than an int) → scorecard renders, hard gates
    # still judge correctly, judge dims with 'unscored' are NOT ok (so the
    # reason field surfaces the unscored state, not a silent green).
    malformed_judge = {"有观点": 4, "有温度": "unscored", "不同质化": "unscored"}
    result = build_scorecard(
        body=_morning_4seg_clean(),
        script_text=_long_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=malformed_judge,
    )
    # Build must not have raised.
    assert "passed" in result
    assert "hard_gates" in result
    assert "judge_dims" in result
    # Hard gates still green (clean input).
    for g in result["hard_gates"]:
        assert g.get("ok") is True, f"clean hard gate must remain green; red: {g!r}"
    # Two dims are unscored (not ok=True) — they must be visibly flagged.
    unscored_dims = [
        d for d in result["judge_dims"]
        if d.get("name") in ("有温度", "不同质化")
    ]
    assert len(unscored_dims) == 2
    for d in unscored_dims:
        assert d.get("ok") is False, (
            f"unscored judge dim must NOT silently green; got {d!r}"
        )
        assert d.get("score") == "unscored"


# ---------- render_scorecard_md: human-readable markdown ----------

def test_scorecard_md_renders():
    """render_scorecard_md produces a markdown string with hard-gates table,
    judge-dims table, and total verdict. The exact format is up to the
    implementation but these three sections MUST appear — the plan calls for
    the scorecard to be human-readable so the operator can diagnose which
    gates fired on a regression sample."""
    from lib.scorecard import build_scorecard, render_scorecard_md

    result = build_scorecard(
        body=_morning_5seg_with_draft_and_betting(),
        script_text=_short_script_text(),
        show="morning",
        score_verdict=_score_verdict(total=15),
        factcheck_verdict=_factcheck_verdict(ok=True),
        store=_empty_store(),
        today="2026-06-14",
        judge_verdict=_clean_judge_verdict(),
    )

    md = render_scorecard_md(result)
    assert isinstance(md, str) and md, "rendered scorecard must be a non-empty string"

    md_lower = md.lower()
    # Hard-gates section: at least the regression hits should be reflected.
    assert "草稿" in md or "draft" in md_lower, (
        f"rendered scorecard must mention 草稿头 hit, got:\n{md}"
    )
    assert "段数" in md or "section" in md_lower, (
        f"rendered scorecard must mention 段数 hit, got:\n{md}"
    )
    assert "念稿" in md or "broadcast" in md_lower or "duration" in md_lower, (
        f"rendered scorecard must mention 念稿时长 hit, got:\n{md}"
    )

    # Total verdict appears (passed / failed / 不达标 / pass / fail — any of).
    verdict_markers = ("不达标", "failed", "✗", "✘", "fail", "❌", "不通过")
    assert any(marker in md for marker in verdict_markers), (
        f"rendered scorecard must surface total verdict, got:\n{md}"
    )