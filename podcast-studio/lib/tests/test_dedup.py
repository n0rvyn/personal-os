"""Tests for lib/dedup.py — dedup 站内+跨期重复检查.

Written before lib/dedup.py exists; collection must fail at this point
(`No module named 'lib.dedup'`).

Pins:
- check_intra_dup: verbatim repeat (e.g. "17.2万" / "占GDP" each x2) flagged;
  near-dup (similarity_fn ≥ 0.93) flagged; truly distinct paragraphs clean;
  n-gram Jaccard ≥ 0.5 catches verbatim WITHOUT an injected similarity_fn
  (主信号不依赖嵌入)
- check_cross_dup: hot anchor (covered-ground is_stale=True) present in
  script → flagged; cool anchor present → clean; empty/missing store → ok
- check_dedup: composes intra + cross
- Temperature shield: repeated subjective opinion (different wording, not a
  known anchor) is NOT flagged as intra-dup — dedup targets 逐字/近似段落
  与 招牌锚, never 观点复述
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.dedup import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import dedup  # noqa: F401
    assert hasattr(dedup, "check_intra_dup")
    assert hasattr(dedup, "check_cross_dup")
    assert hasattr(dedup, "check_dedup")


# ---------- helpers: build a covered-ground-shaped store ----------

def _hot_store() -> dict:
    """A store whose only anchor is `is_stale=True` against today.

    Shape mirrors `load_store` output (coveredground.py: `{"anchors": {name: entry}}`).
    Uses a 3-episode-in-window pattern so the count predicate fires without
    relying on the recency predicate's lookback guard.
    """
    return {
        "anchors": {
            "1956苏伊士": {
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


def _cool_store() -> dict:
    """A store whose only anchor is NOT stale (count=1, only 1 episode date).

    Same real-schema shape as `_hot_store` — only the data differs.
    """
    return {
        "anchors": {
            "1956苏伊士": {
                "first_used": "2026-06-05",
                "last_used": "2026-06-05",
                "count": 1,
                "episodes": [{"date": "2026-06-05", "show": "evening"}],
            },
        }
    }


# ---------- check_intra_dup: verbatim repeat ----------

def test_intra_verbatim_repeat_flagged():
    """Two identical sentences within a body (仿 06-14 「17.2万」「占GDP」各 x2)
    must be flagged by check_intra_dup. The verbatim copy is the 06-14 root
    cause: the body genuinely repeated the same statistic twice. We don't
    inject a similarity_fn here — n-gram Jaccard alone must catch it.
    """
    from lib.dedup import check_intra_dup
    body = (
        "## ① 开场\n"
        "今天的数字很惊人，全行业一年损失高达17.2万。\n\n"
        "## ② 展开\n"
        "我们继续看数据：全行业一年损失高达17.2万。\n"
    )
    result = check_intra_dup(body)
    assert result["ok"] is False, "verbatim repeat must be flagged"
    assert len(result["hits"]) > 0, "hits must be populated"
    assert result["score"] > 0, "score must be > 0"
    # The repeated fragment should appear in at least one hit
    assert any("17.2万" in h for h in result["hits"])


# ---------- check_intra_dup: near-dup via similarity_fn ----------

def test_intra_near_dup_paragraph_flagged():
    """Two paragraphs with high semantic similarity (injected similarity_fn
    returning ≥ 0.93) must be flagged. The high-bar confirm path catches
    换词不换意的近义复制 (n-gram may not pick this up cleanly).

    A similarity_fn returning 0.5 must NOT flag — confirms the 0.93 bar.
    """
    from lib.dedup import check_intra_dup

    body_high = (
        "## ① 甲\n"
        "苏伊士运河关闭一周，全球供应链受创严重。\n\n"
        "## ② 乙\n"
        "苏伊士运河断航七天，全球供应链受创严重。\n"
    )
    # similarity_fn always reports high similarity → confirm path fires
    high_sim = lambda a, b: 0.95
    result = check_intra_dup(body_high, similarity_fn=high_sim)
    assert result["ok"] is False, "near-dup with sim=0.95 must be flagged"
    assert len(result["hits"]) > 0

    # Same body, similarity_fn returns 0.5 → BELOW the 0.93 confirm bar.
    # Jaccard is also likely below 0.5 here (paragraphs have distinct ngrams).
    # The result should be clean.
    low_sim = lambda a, b: 0.5
    result_low = check_intra_dup(body_high, similarity_fn=low_sim)
    assert result_low["ok"] is True, (
        "sim=0.5 must NOT trigger the high-bar confirm; "
        "near-dup threshold is 0.93"
    )
    assert result_low["hits"] == []


# ---------- check_intra_dup: distinct paragraphs ----------

def test_intra_distinct_paragraphs_clean():
    """Two paragraphs on truly different topics must NOT be flagged. No
    similarity_fn injected (simulate non-macOS / no helper)."""
    from lib.dedup import check_intra_dup
    body = (
        "## ① 半导体\n"
        "台积电的产能扩张计划进入第二季度，3nm 制程良率稳步提升。\n\n"
        "## ② 能源\n"
        "北海油田的维护周期延长两周，Brent 油价应声上涨 2.3%。\n"
    )
    result = check_intra_dup(body)
    assert result["ok"] is True
    assert result["hits"] == []


# ---------- check_intra_dup: Jaccard catches verbatim without embedding ----------

def test_intra_jaccard_catches_without_embedding():
    """Without a similarity_fn (the embedded helper unavailable path),
    the n-gram Jaccard 主信号 must still catch verbatim / near-verbatim
    repeats. This is the 06-14 防 regression pin: 即使嵌入不可用,
    17.2万·占GDP 逐字重复仍被抓.
    """
    from lib.dedup import check_intra_dup
    body = (
        "## ①\n"
        "占GDP的比重约百分之三，这是个惊人的数字。\n\n"
        "## ②\n"
        "占GDP的比重约百分之三，这是个惊人的数字。\n"
    )
    result = check_intra_dup(body)  # NO similarity_fn injected
    assert result["ok"] is False
    assert len(result["hits"]) > 0
    assert result["score"] > 0


# ---------- check_cross_dup: hot anchor in script ----------

def test_cross_hot_anchor_presence():
    """A script containing a hot covered-ground anchor (is_stale=True) must
    be flagged. Uses the load_store-schema-shaped dict so the test exercises
    the real `is_stale` predicate path — NOT a hand-rolled shortcut that
    would let dict-iter / entry-shape bugs slip through (Phase-2 GAP-2).
    """
    from lib.dedup import check_cross_dup
    today = _dt.date(2026, 6, 14).isoformat()
    store = _hot_store()
    # Script literally mentions 苏伊士 (a substring of the anchor "1956苏伊士").
    # The simplest in-script match: store keys are substrings of the script.
    # We use the exact key string here to confirm the membership check.
    script = (
        "主播开场：今天我们回看一段历史——1956苏伊士运河危机。\n"
        "（旁白继续……）\n"
    )
    result = check_cross_dup(script, store, today)
    assert result["ok"] is False, "hot anchor '1956苏伊士' present in script → must flag"
    assert any("苏伊士" in h for h in result["hits"])


# ---------- check_cross_dup: cool anchor in script ----------

def test_cross_clean_anchor_not_flagged():
    """An anchor present in the store but NOT hot must not be flagged.
    check_cross_dup is only an in-script presence check for hot anchors —
    not a generic 'is this anchor in store' check."""
    from lib.dedup import check_cross_dup
    today = _dt.date(2026, 6, 14).isoformat()
    store = _cool_store()  # count=1, not stale
    script = "主播：今天聊聊 1956苏伊士 那段往事。\n"
    result = check_cross_dup(script, store, today)
    assert result["ok"] is True, "cool anchor present in script must not flag"
    assert result["hits"] == []


# ---------- check_cross_dup: empty store ----------

def test_cross_no_store_safe():
    """An empty store ({anchors: {}}) must produce ok=True and no hits.
    Must not crash. This is the 'no covered-ground.yaml on disk' baseline
    state for first-run shows."""
    from lib.dedup import check_cross_dup
    today = _dt.date(2026, 6, 14).isoformat()
    store = {"anchors": {}}
    script = "主播：今天讲讲半导体供应链。\n"
    result = check_cross_dup(script, store, today)
    assert result["ok"] is True
    assert result["hits"] == []


# ---------- temperature shield: subjective opinion restated ----------

def test_temperature_shield_repeated_opinion_not_dup():
    """Temperature shield (acceptance #5): a body that REPEATS the same
    subjective judgment / bet in two different wordings (not verbatim, not
    a known apparatus anchor) must NOT be flagged as intra-dup. dedup
    targets 逐字 / 近似段落 与 招牌锚 only — never 观点复述.

    The two restatements share NO long verbatim substring, and the
    subject matter is opinion / stance, not an apparatus anchor in any
    covered-ground store.
    """
    from lib.dedup import check_intra_dup
    body = (
        "## ①\n"
        "我的判断是，这波监管收紧对头部三家平台反而是利好，"
        "原因在于规模效应会进一步固化头部地位。\n\n"
        "## ②\n"
        "换个角度再说一次——头部三家的护城河在这种压力下只会变厚，"
        "中小玩家的生存空间被压缩得更小了。\n"
    )
    result = check_intra_dup(body)
    assert result["ok"] is True, (
        "temperature shield: restating a subjective opinion in different "
        "wording must NOT be flagged as intra-dup"
    )
    assert result["hits"] == []