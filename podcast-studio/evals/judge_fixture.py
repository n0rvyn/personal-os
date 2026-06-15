"""Magnitude-judge fixture anvil (design 2026-06-13).

Builds the judge's input from REAL on-disk artifacts that capture the exact
situation we must get right:
  - prior stance cards (6/11 + 6/12): human-quality bets ("7/15 松动",
    "Brent 跌破 95").
  - 6/13 candidates + that day's news (skirmishes, "协议咫尺之遥", Brent still
    ~107) — i.e. NONE of the 6/12 bets moved.
  - 6/12 episode bodies — the anchor source (1956苏伊士 / 1973石油 live here,
    not in cards).

Expectation the judge MUST satisfy on this fixture:
  - 霍尔木兹 candidate  → magnitude == "light" (no bet moved; daily noise).

Usage:
  python3 evals/judge_fixture.py build           # print assembled judge input JSON
  python3 evals/judge_fixture.py check <verdict.json>   # assert expectations
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.stance import load_cards          # noqa: E402
from lib.magnitude import (                 # noqa: E402
    build_judge_input, parse_verdict, gather_recent_bodies,
)

OUTPUT_DIR = "/Users/norvyn/Code/Content/Podcasts"
TODAY = "2026-06-13"

# The 5 candidates 达芬奇 surfaced on 6/13 (from brief-A approved_topics).
CANDIDATES = [
    "霍尔木兹停火岌岌可危-美国再次对伊朗动武",
    "光子计算芯片突破-AI算力硬件新路径",
    "AI医疗诊断超越资深医生-急诊室测试结果",
    "布伦特原油维持高位-停滞谈判下的能源市场",
    "世界杯2026开幕-墨西哥主场胜南非",
]

# 6/13 当日新闻背景 (real, from .scratch-2026-06-13-morning/material-summary.md).
TODAY_NEWS = """- 美伊停火崩溃边缘：美国再次空袭伊朗南部军事目标；伊朗导弹打击科威特/巴林/约旦美军基地；特朗普"中东的霸凌者已死"，伊朗革命卫队"美军基地不再安全" (2026-06-10)
- 霍尔木兹危机第107天：6/7-8 最猛烈互打后停火濒临瓦解，伊斯兰堡谈判失败后尚无新轮会谈 (2026-06-07)
- 伊朗外长：协议"咫尺之遥"但批评美方"最大化要求"，无下一轮会谈确认 (2026-06-01)
- 布伦特原油：5月均价107美元/桶，市场担忧若停火彻底崩溃价格进一步攀升 (2026-05-23)
- 光子计算芯片：宾大全光开关，能耗~4飞焦耳，低数个量级 (2026-05-18)
- AI急诊诊断：o1 在76例真实急诊正确率67% vs 医生55%/50%，建议进入前瞻性临床试验 (2026-05-03)
- 世界杯2026：6/11 墨西哥2:0胜南非开幕 (2026-06-11)"""


def _prior_cards() -> list[dict]:
    """All stance cards strictly BEFORE today (the 6/13 card doesn't exist at
    judge time in the real pipeline; exclude it for the fixture)."""
    return [c for c in load_cards(OUTPUT_DIR)
            if (c.get("episode", {}) or {}).get("date", "") < TODAY]


# Breakthrough variant: today Brent REALLY breaks below 95 — this moves
# bet-20260611morning-2 / bet-20260612evening-1 ("Brent 跌破 95"). The judge
# must升档 (medium/heavy), NOT light. Proves the dial actually moves — a judge
# stuck on "always light" would pass the light case but is useless.
TODAY_NEWS_BREAKTHROUGH = """- 布伦特原油今日盘中跌破92美元/桶，为封锁以来首次跌破95关口，交易员称市场开始定价"封锁将被解决" (2026-06-13)
- 卡塔尔外交部正式宣布将于本周接待美伊新一轮会谈，并提出哈尔格岛"暂停框架"提案 (2026-06-13)
- 霍尔木兹危机第107天：前线交火明显降温，48小时无新增导弹打击 (2026-06-13)"""


def build(variant: str = "baseline") -> dict:
    judge_in = build_judge_input(
        cards=_prior_cards(),
        candidates=CANDIDATES,
        today=TODAY,
        window_days=14,
        recent_bodies=gather_recent_bodies(OUTPUT_DIR, TODAY, window_days=14),
    )
    judge_in["today_news"] = (
        TODAY_NEWS_BREAKTHROUGH if variant == "breakthrough" else TODAY_NEWS
    )
    return judge_in


def check_heavy(verdict_path: str) -> int:
    """Breakthrough variant: 霍尔木兹/布伦特 must升档 to medium/heavy (a bet moved)."""
    raw = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    verdicts = parse_verdict(raw)
    by = {v["candidate"]: v for v in verdicts}
    fails = []
    for needle in ("霍尔木兹", "布伦特"):
        v = next((x for k, x in by.items() if needle in k), None)
        if v is None:
            fails.append(f"{needle} candidate missing")
        elif v["magnitude"] not in ("medium", "heavy"):
            fails.append(f"{needle} magnitude={v['magnitude']!r}, expected medium/heavy "
                         f"(Brent broke 95 — a bet moved). what_moved={v['what_moved']!r}")
    if fails:
        print("HEAVY FIXTURE FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("HEAVY FIXTURE PASS — dial moves: 霍尔木兹/布伦特 升档当赌注被触动",
          {k: v["magnitude"] for k, v in by.items() if ("霍尔木兹" in k or "布伦特" in k)})
    return 0


def check(verdict_path: str) -> int:
    raw = json.loads(Path(verdict_path).read_text(encoding="utf-8"))
    verdicts = parse_verdict(raw)          # also validates schema
    by = {v["candidate"]: v for v in verdicts}

    fails = []
    # 1. 霍尔木兹 must be light (no 6/12 bet moved).
    hormuz = next((v for k, v in by.items() if "霍尔木兹" in k), None)
    if hormuz is None:
        fails.append("霍尔木兹 candidate missing from verdict")
    elif hormuz["magnitude"] != "light":
        fails.append(f"霍尔木兹 magnitude={hormuz['magnitude']!r}, expected 'light' "
                     f"(no 6/12 bet moved). what_moved={hormuz['what_moved']!r}")

    # DP-001=A: 量臣不再产 recent_anchors；anchor 抽取移交 covered-ground 蒸馏器。
    # 此处不再断言 recent_anchors 字段（量臣 verdict dict 不应含该键）。

    if fails:
        print("FIXTURE FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("FIXTURE PASS — 霍尔木兹=light")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        variant = sys.argv[2] if len(sys.argv) > 2 else "baseline"
        print(json.dumps(build(variant), ensure_ascii=False, indent=2))
    elif cmd == "check":
        sys.exit(check(sys.argv[2]))
    elif cmd == "check-heavy":
        sys.exit(check_heavy(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
