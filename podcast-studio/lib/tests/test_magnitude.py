"""Tests for lib/magnitude.py — the magnitude-judge's pure helpers.

Written before lib/magnitude.py exists; collection must fail at this
point (`No module named 'lib.magnitude'`).

Pins (design 2026-06-13-podcast-anti-repetition):
- build_judge_input: window-filters cards by episode date, carries
  candidates + recent card bets/open_questions + recent episode body
  excerpts (the anchor source — anchors live in BODIES, not cards).
- parse_verdict: strict per-candidate verdict; magnitude ∈
  {none,light,medium,heavy}; fail-closed (raises, names field) on bad input.
- safe_parse_verdict: fail-SOFT — any error/None ⇒ all candidates light,
  degraded=True (never deadlocks the daily run).
- magnitude_to_airtime: none/light→brief, medium→segment, heavy→lead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- FAIL-first import ----------

def test_module_imports():
    from lib import magnitude  # noqa: F401
    for fn in ("build_judge_input", "parse_verdict",
               "safe_parse_verdict", "magnitude_to_airtime"):
        assert hasattr(magnitude, fn), fn


# ---------- fixtures ----------

def _card(date, show, bets=None, oqs=None):
    return {
        "episode": {"date": date, "show": show},
        "bets": bets or [],
        "open_questions": oqs or [],
        "settles": [],
        "named_concept": [],
        "topics": [],
    }

def _bet(bid, claim, settle_by, status="open"):
    return {"id": bid, "claim": claim, "settle_by": settle_by, "status": status}


# ---------- build_judge_input ----------

def test_build_judge_input_windows_cards_by_date():
    from lib.magnitude import build_judge_input
    cards = [
        _card("2026-05-01", "morning"),          # outside 14d window
        _card("2026-06-12", "morning",
              bets=[_bet("b1", "霍尔木兹7/15松动", "2026-07-15")]),
        _card("2026-06-12", "evening",
              bets=[_bet("b2", "Brent跌破95", "2026-08-11")]),
    ]
    out = build_judge_input(cards, candidates=["霍尔木兹停火"], today="2026-06-13",
                            window_days=14)
    dates = [c["date"] for c in out["recent_cards"]]
    assert "2026-05-01" not in dates
    assert "2026-06-12" in dates
    # bets are surfaced so the judge can check "did any move"
    flat_ids = [b["id"] for c in out["recent_cards"] for b in c["bets"]]
    assert {"b1", "b2"} <= set(flat_ids)
    assert out["candidates"] == ["霍尔木兹停火"]
    assert out["today"] == "2026-06-13"


def test_build_judge_input_carries_recent_bodies_for_anchor_extraction():
    """Anchors (1956苏伊士/1973石油) live in episode BODIES, not cards —
    build_judge_input must pass body excerpts through so the judge can
    surface recent_anchors. Without this the 达芬奇 anchor guard is a no-op."""
    from lib.magnitude import build_judge_input
    bodies = [{"date": "2026-06-12", "show": "morning",
               "excerpt": "……1956年苏伊士运河危机……1973年石油禁运……"}]
    out = build_judge_input([], candidates=["X"], today="2026-06-13",
                            recent_bodies=bodies)
    assert out["recent_bodies"], "body excerpts must be carried for anchor extraction"
    assert "苏伊士" in out["recent_bodies"][0]["excerpt"]


def test_build_judge_input_empty_is_noop_safe():
    from lib.magnitude import build_judge_input
    out = build_judge_input([], candidates=[], today="2026-06-13")
    assert out["recent_cards"] == []
    assert out["recent_bodies"] == []


# ---------- gather_recent_bodies ----------

def test_gather_recent_bodies_windows_excludes_today_and_nonepisodes(tmp_path):
    from lib.magnitude import gather_recent_bodies
    (tmp_path / "2026-06-12-节点危机.md").write_text("body 1973石油禁运", encoding="utf-8")
    (tmp_path / "2026-06-11-认知.md").write_text("older body 苏伊士", encoding="utf-8")
    (tmp_path / "2026-05-01-old.md").write_text("too old", encoding="utf-8")      # outside window
    (tmp_path / "2026-06-13-今天.md").write_text("today — must be excluded", encoding="utf-8")
    (tmp_path / "2026-06-12-morning.stance.yaml").write_text("episode: {}", encoding="utf-8")
    (tmp_path / "character-bible.md").write_text("not an episode", encoding="utf-8")

    out = gather_recent_bodies(tmp_path, today="2026-06-13", window_days=14)
    dates = [b["date"] for b in out]
    assert dates == ["2026-06-12", "2026-06-11"]                 # recent-first, today excluded
    assert "2026-05-01" not in dates                             # outside window
    assert all("today" not in b["excerpt"] for b in out)
    assert all("not an episode" not in b["excerpt"] for b in out)

def test_gather_recent_bodies_normalizes_escaped_newlines(tmp_path):
    from lib.magnitude import gather_recent_bodies
    (tmp_path / "2026-06-12-x.md").write_text("a\\n\\n1973石油禁运", encoding="utf-8")
    out = gather_recent_bodies(tmp_path, today="2026-06-13")
    assert "\\n" not in out[0]["excerpt"]
    assert "1973石油禁运" in out[0]["excerpt"]

def test_gather_recent_bodies_missing_dir():
    from lib.magnitude import gather_recent_bodies
    assert gather_recent_bodies("/nonexistent/dir/xyz", today="2026-06-13") == []


# ---------- parse_verdict (fail-closed) ----------

def _raw(verdicts):
    return {"verdicts": verdicts}

def test_parse_verdict_happy():
    from lib.magnitude import parse_verdict
    v = parse_verdict(_raw([
        {"candidate": "霍尔木兹停火", "matches_prior": "2026-06-12-morning",
         "magnitude": "light", "what_moved": "只是又一轮交火，无赌注变动",
         "recap_hook": "霍尔木兹昨天又打了一轮",
         "recent_anchors": ["1956苏伊士", "1973石油"]},
        {"candidate": "光子芯片", "matches_prior": None, "magnitude": "none",
         "what_moved": "", "recap_hook": None, "recent_anchors": []},
    ]))
    by = {x["candidate"]: x for x in v}
    assert by["霍尔木兹停火"]["magnitude"] == "light"
    assert by["霍尔木兹停火"]["matches_prior"] == "2026-06-12-morning"
    assert by["霍尔木兹停火"]["recent_anchors"] == ["1956苏伊士", "1973石油"]
    assert by["光子芯片"]["magnitude"] == "none"

def test_parse_verdict_accepts_json_string():
    from lib.magnitude import parse_verdict
    import json
    raw = json.dumps(_raw([{"candidate": "A", "magnitude": "medium"}]))
    v = parse_verdict(raw)
    assert v[0]["magnitude"] == "medium"

def test_parse_verdict_rejects_bad_magnitude():
    from lib.magnitude import parse_verdict
    with pytest.raises(ValueError, match="magnitude"):
        parse_verdict(_raw([{"candidate": "A", "magnitude": "big"}]))

def test_parse_verdict_rejects_missing_candidate():
    from lib.magnitude import parse_verdict
    with pytest.raises(ValueError, match="candidate"):
        parse_verdict(_raw([{"magnitude": "light"}]))

def test_parse_verdict_defaults_optional_fields():
    from lib.magnitude import parse_verdict
    v = parse_verdict(_raw([{"candidate": "A", "magnitude": "heavy"}]))
    assert v[0]["matches_prior"] is None
    assert v[0]["recent_anchors"] == []
    assert v[0]["what_moved"] == ""
    assert v[0]["recap_hook"] is None


# ---------- safe_parse_verdict (fail-soft → light) ----------

def test_safe_parse_verdict_degrades_to_light_on_none():
    from lib.magnitude import safe_parse_verdict
    v = safe_parse_verdict(None, candidates=["霍尔木兹", "光子芯片"])
    by = {x["candidate"]: x for x in v}
    assert by["霍尔木兹"]["magnitude"] == "light"
    assert by["光子芯片"]["magnitude"] == "light"
    assert all(x["degraded"] for x in v)

def test_safe_parse_verdict_degrades_on_garbage():
    from lib.magnitude import safe_parse_verdict
    v = safe_parse_verdict("{not json", candidates=["A"])
    assert v[0]["magnitude"] == "light"
    assert v[0]["degraded"] is True

def test_safe_parse_verdict_passes_through_valid():
    from lib.magnitude import safe_parse_verdict
    v = safe_parse_verdict(_raw([{"candidate": "A", "magnitude": "heavy"}]),
                           candidates=["A"])
    assert v[0]["magnitude"] == "heavy"
    assert v[0].get("degraded", False) is False


# ---------- magnitude_to_airtime ----------

@pytest.mark.parametrize("mag,expected", [
    ("none", "brief"), ("light", "brief"),
    ("medium", "segment"), ("heavy", "lead"),
])
def test_magnitude_to_airtime(mag, expected):
    from lib.magnitude import magnitude_to_airtime
    assert magnitude_to_airtime(mag) == expected

def test_magnitude_to_airtime_rejects_unknown():
    from lib.magnitude import magnitude_to_airtime
    with pytest.raises(ValueError, match="magnitude"):
        magnitude_to_airtime("enormous")
