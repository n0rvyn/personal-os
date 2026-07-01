"""Tests for step_chart spec-driven dispatch (Task 3-tests).

These tests assert that _chart_content_for_beat(beat, ctx) — a pure helper
extracted from step_chart — routes chart content correctly:
- zone_boundary beat → build_zone_boundary_chart builder (title contains "强度边界")
- vdot_pace beat with params.vdot=50 → build_pace_chart_from_vdot (title "VDOT 50")
- unknown chart.type → RuntimeError naming the unknown type and "权威表"
- fixture beat (chart_title/chart_rows present) → those verbatim (hand-authored)
- doc chart beat with NO chart spec → RuntimeError (no blanket VDOT-50 fallback)

These tests FAIL initially with ImportError / AttributeError because the helper
is not yet extracted. Once Task 3-impl adds _chart_content_for_beat to wiring,
all five cases pass.
"""

from __future__ import annotations

import pytest

# Import via attribute access so the initial FAIL is isolated to each test call
# (AttributeError at call time, not an ImportError that kills the whole collection).
import lib.wiring as wiring_mod

# ---------------------------------------------------------------------------
# Minimal authoritative data fixtures (mirrors vdot_pace / zone_boundary shapes)
# ---------------------------------------------------------------------------

_VDOT_DATA = {
    "data": [
        {
            "vdot": 50,
            "paces": {
                "easyMin": 4.95,
                "easyMax": 5.57,
                "marathon": 4.52,
                "threshold": 4.25,
                "interval": 3.95,
                "repetition400m": 88.0,
            },
        }
    ]
}

_ZONE_DATA = {
    "version": "1.0",
    "source": "Daniels' Running Formula (2021, 4th ed)",
    "zones": [
        {"code": "E",   "name": "Easy",           "vo2max": "59–74%",   "hrmax": "65–79%"},
        {"code": "M",   "name": "Marathon",        "vo2max": "75–84%",   "hrmax": "80–89%"},
        {"code": "T",   "name": "Threshold",       "vo2max": "83–88%",   "hrmax": "88–92%"},
        {"code": "10K", "name": "10K",             "vo2max": "88–92%",   "hrmax": "91–94%"},
        {"code": "I",   "name": "Interval",        "vo2max": "98–100%",  "hrmax": "98–100%"},
        {"code": "H",   "name": "Hard",            "vo2max": "≈I",       "hrmax": "98–100%"},
        {"code": "R",   "name": "Repetition",      "vo2max": "105–110%", "hrmax": "—"},
        {"code": "FR",  "name": "Fast Repetition", "vo2max": "115–120%", "hrmax": "—"},
    ],
}


def _make_ctx(vdot_data=None, zone_data=None):
    """Build a minimal ctx dict for _chart_content_for_beat tests."""
    ctx = {}
    if vdot_data is not None:
        ctx["vdot_data"] = vdot_data
    if zone_data is not None:
        ctx["zone_data"] = zone_data
    return ctx


# ---------------------------------------------------------------------------
# Test 1: zone_boundary beat → zone builder (title contains "强度边界")
# ---------------------------------------------------------------------------

def test_chart_content_zone_boundary_returns_zone_rows():
    """A doc beat with chart.type='zone_boundary' resolves to the zone builder.

    Title must contain '强度边界'; rows include 'E' code with real boundary string.
    """
    beat = {
        "id": "beat-01",
        "visual_type": "chart",
        "chart": {"type": "zone_boundary", "params": {}},
    }
    ctx = _make_ctx(zone_data=_ZONE_DATA)
    title, rows = wiring_mod._chart_content_for_beat(beat, ctx)
    assert "强度边界" in title, f"Expected '强度边界' in title, got: {title!r}"
    codes = [r[0] for r in rows]
    assert "E" in codes
    assert "M" in codes


# ---------------------------------------------------------------------------
# Test 2: vdot_pace beat with params.vdot=50 → pace table (title "VDOT 50")
# ---------------------------------------------------------------------------

def test_chart_content_vdot_pace_uses_params_vdot():
    """A doc beat with chart.type='vdot_pace' and params.vdot=50 → VDOT 50 title."""
    beat = {
        "id": "beat-02",
        "visual_type": "chart",
        "chart": {"type": "vdot_pace", "params": {"vdot": 50}},
    }
    ctx = _make_ctx(vdot_data=_VDOT_DATA)
    title, rows = wiring_mod._chart_content_for_beat(beat, ctx)
    assert "VDOT 50" in title, f"Expected 'VDOT 50' in title, got: {title!r}"
    assert len(rows) > 0


# ---------------------------------------------------------------------------
# Test 3: unknown chart.type → graceful degrade to still (DP-002=B), returns None
# ---------------------------------------------------------------------------

def test_chart_content_unknown_type_degrades_to_still():
    """An unknown chart.type degrades gracefully: returns None (upstream → still), no raise.

    DP-002=B (Task 7): unknown/unsupported chart types must NOT crash the run —
    they log a warning and fall back to a still image. (A completely missing
    chart spec still fails loud — see test_chart_content_missing_spec_raises_runtime_error.)
    """
    beat = {
        "id": "beat-03",
        "visual_type": "chart",
        "chart": {"type": "mystery", "params": {}},
    }
    ctx = _make_ctx(vdot_data=_VDOT_DATA, zone_data=_ZONE_DATA)
    result = wiring_mod._chart_content_for_beat(beat, ctx)
    assert result is None, (
        f"Unknown chart type must degrade to None (still fallback), got: {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: fixture beat (chart_title + chart_rows) → verbatim hand-authored values
# ---------------------------------------------------------------------------

def test_chart_content_fixture_beat_returns_verbatim():
    """A beat with hand-authored chart_title/chart_rows passes through unchanged.

    The fixture-mode branch must not be altered by the spec-driven dispatch.
    """
    beat = {
        "id": "beat-04",
        "visual_type": "chart",
        "chart_title": "我的自定义图表",
        "chart_rows": [("行A", "值1"), ("行B", "值2")],
    }
    ctx = _make_ctx()
    title, rows = wiring_mod._chart_content_for_beat(beat, ctx)
    assert title == "我的自定义图表"
    assert rows == [("行A", "值1"), ("行B", "值2")]


# ---------------------------------------------------------------------------
# Test 5: doc chart beat with NO chart spec → RuntimeError (no VDOT-50 fallback)
# ---------------------------------------------------------------------------

def test_chart_content_missing_spec_raises_runtime_error():
    """A doc-mode chart beat with no chart spec fails loud (no blanket VDOT-50 fallback).

    The blanket build_pace_chart_from_vdot fallback was removed; an un-specced
    chart beat must tell the author what's wrong, not silently show a VDOT-50 table.
    """
    beat = {
        "id": "beat-05",
        "visual_type": "chart",
        # no chart_title/chart_rows AND no chart spec
    }
    ctx = _make_ctx(vdot_data=_VDOT_DATA)
    with pytest.raises(RuntimeError) as exc_info:
        wiring_mod._chart_content_for_beat(beat, ctx)
    msg = str(exc_info.value)
    # Message should indicate the beat lacks a chart spec, guiding the author
    assert "chart" in msg.lower() or "spec" in msg.lower() or "导演" in msg, (
        f"Expected chart-spec guidance in error, got: {msg!r}"
    )
