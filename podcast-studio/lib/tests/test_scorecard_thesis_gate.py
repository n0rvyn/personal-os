"""Phase 5: thesis-advance带牙判官门 in the scorecard (lib/scorecard.py).

Pins (DP-001=A 拦内容重复不拦观点 / DP-002=B 判官判):
- _hard_gate_thesis_advance: 're-derivation' → ok=False; 'advance'/'n/a'/None
  /missing → ok=True (fail-soft, 宁放勿误伤)
- build_scorecard threads thesis_verdict into a hard gate named 'thesis_advance'
  that participates in `passed`
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def test_gate_reds_only_on_rederivation():
    from lib.scorecard import _hard_gate_thesis_advance
    assert _hard_gate_thesis_advance("re-derivation")["ok"] is False
    assert _hard_gate_thesis_advance("advance")["ok"] is True
    assert _hard_gate_thesis_advance("n/a")["ok"] is True
    assert _hard_gate_thesis_advance(None)["ok"] is True
    assert _hard_gate_thesis_advance("")["ok"] is True


def test_gate_reads_verdict_from_dict_key():
    """The judge writes its verdict as `论点推进` inside the shared
    scorecard-verdict JSON; the gate must read it from that dict."""
    from lib.scorecard import _hard_gate_thesis_advance
    assert _hard_gate_thesis_advance({"论点推进": "re-derivation"})["ok"] is False
    assert _hard_gate_thesis_advance({"论点推进": "advance"})["ok"] is True
    assert _hard_gate_thesis_advance({"有观点": 5})["ok"] is True  # no thesis key → 放行


def test_gate_failsoft_on_garbage():
    from lib.scorecard import _hard_gate_thesis_advance
    assert _hard_gate_thesis_advance(12345)["ok"] is True
    assert _hard_gate_thesis_advance(["re-derivation"])["ok"] is True  # wrong shape → 放行


def test_build_scorecard_includes_thesis_gate_and_it_can_red():
    from lib.scorecard import build_scorecard
    common = dict(
        body="## ① 开场\n正文", script_text="正文" * 100, show="evening",
        score_verdict=None, factcheck_verdict={"ok": True}, store={"anchors": {}},
        today="2026-06-30", judge_verdict=None,
    )
    red = build_scorecard(**common, thesis_verdict="re-derivation")
    gates = {g["name"]: g for g in red["hard_gates"]}
    assert "thesis_advance" in gates
    assert gates["thesis_advance"]["ok"] is False
    assert red["passed"] is False  # a red hard gate fails passed

    ok = build_scorecard(**common, thesis_verdict="advance")
    gates_ok = {g["name"]: g for g in ok["hard_gates"]}
    assert gates_ok["thesis_advance"]["ok"] is True
