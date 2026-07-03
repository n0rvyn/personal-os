"""Unit tests for parse_shotlist chart spec contract (Task 2-tests).

These tests assert:
- parse_shotlist preserves a `chart` spec {type, params} on visual_type=="chart" beats
- chart spec is structurally validated (type kept as-is, params preserved)
- `chart` is dropped/ignored on non-chart beats
- a chart beat with no usable chart.type is REJECTED (error string returned)

Import via storyboard module attribute access so FAIL-first is an AttributeError
at call time rather than a whole-file ImportError.
"""
import json

from lib import storyboard


def _make_raw(entries: list[dict]) -> str:
    """Wrap a list of director-output dicts as a raw JSON string (as LLM would emit)."""
    return json.dumps(entries)


def _chart_beat(chart_spec=None, extra=None):
    """A minimal valid chart beat, optionally carrying a chart spec."""
    beat = {
        "visual_type": "chart",
        "shots": [{"prompt": "配速区间示意图", "shot_type": "全景", "with_character": False}],
        "keywords": ["强度区"],
    }
    if chart_spec is not None:
        beat["chart"] = chart_spec
    if extra:
        beat.update(extra)
    return beat


def _still_beat(chart_spec=None):
    """A minimal valid still beat, optionally carrying a stray chart field."""
    beat = {
        "visual_type": "still",
        "shots": [{"prompt": "跑者正面特写", "shot_type": "特写", "with_character": True}],
        "keywords": [],
    }
    if chart_spec is not None:
        beat["chart"] = chart_spec
    return beat


# ---------------------------------------------------------------------------
# Test 1: chart spec is preserved on a chart beat
# ---------------------------------------------------------------------------

def test_parse_shotlist_keeps_chart_spec_on_chart_beat():
    """A chart beat with a valid chart spec → spec preserved verbatim in result."""
    spec = {"type": "zone_boundary", "params": {}}
    raw = _make_raw([_chart_beat(chart_spec=spec)])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert result[0]["chart"] == spec


# ---------------------------------------------------------------------------
# Test 2: chart spec with non-empty params survives intact
# ---------------------------------------------------------------------------

def test_parse_shotlist_chart_spec_params():
    """A chart beat with params (e.g. vdot=50) → params survive intact."""
    spec = {"type": "vdot_pace", "params": {"vdot": 50}}
    raw = _make_raw([_chart_beat(chart_spec=spec)])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert result[0]["chart"] == {"type": "vdot_pace", "params": {"vdot": 50}}


# ---------------------------------------------------------------------------
# Test 3: chart field on a non-chart beat is dropped (not forwarded)
# ---------------------------------------------------------------------------

def test_parse_shotlist_ignores_chart_on_nonchart_beat():
    """A still beat carrying a stray chart field → chart key absent in result."""
    stray_spec = {"type": "zone_boundary", "params": {}}
    raw = _make_raw([_still_beat(chart_spec=stray_spec)])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert "chart" not in result[0], (
        f"chart should be dropped on non-chart beats, but got: {result[0]}"
    )


# ---------------------------------------------------------------------------
# Test 4: chart beat with no chart field (or empty type) is REJECTED
# ---------------------------------------------------------------------------

def test_parse_shotlist_chart_beat_missing_type_rejected():
    """A chart beat with NO chart field → parse_shotlist returns an error string."""
    # No chart field at all
    raw = _make_raw([_chart_beat(chart_spec=None)])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, str), (
        f"expected error string for missing chart.type, got: {result!r}"
    )
    lower = result.lower()
    assert "chart" in lower or "type" in lower, (
        f"error message should mention 'chart' or 'type', got: {result!r}"
    )


def test_parse_shotlist_chart_beat_empty_type_rejected():
    """A chart beat with chart.type='' → parse_shotlist returns an error string."""
    spec = {"type": "", "params": {}}
    raw = _make_raw([_chart_beat(chart_spec=spec)])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, str), (
        f"expected error string for empty chart.type, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# ⑧: chart_followup_prompt preserved on chart beats, dropped elsewhere
# ---------------------------------------------------------------------------

def test_parse_shotlist_keeps_chart_followup_prompt_on_chart_beat():
    """A chart beat's chart_followup_prompt (still shown after a long chart) is preserved."""
    spec = {"type": "bar", "params": {"categories": ["a", "b"], "values": [1, 2]}}
    beat = _chart_beat(chart_spec=spec, extra={"chart_followup_prompt": "两组跑者并排在跑道上"})
    result = storyboard.parse_shotlist(_make_raw([beat]), 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert result[0].get("chart_followup_prompt") == "两组跑者并排在跑道上"


def test_parse_shotlist_drops_chart_followup_on_nonchart_beat():
    """chart_followup_prompt on a non-chart beat is not forwarded (chart-only field)."""
    beat = _still_beat()
    beat["chart_followup_prompt"] = "stray"
    result = storyboard.parse_shotlist(_make_raw([beat]), 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert "chart_followup_prompt" not in result[0]


def test_parse_shotlist_chart_followup_absent_is_omitted():
    """No chart_followup_prompt → key absent (wiring falls back to a generic b-roll still)."""
    spec = {"type": "zone_boundary", "params": {}}
    result = storyboard.parse_shotlist(_make_raw([_chart_beat(chart_spec=spec)]), 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert "chart_followup_prompt" not in result[0]


# ---------------------------------------------------------------------------
# Task 3-tests: color_grade on first beat preserved (DP-001 parse contract)
# ---------------------------------------------------------------------------

def test_parse_shotlist_keeps_color_grade_on_first_beat():
    """First beat with color_grade='暖金' → result[0]['color_grade'] == '暖金'.

    This locks the DP-001 parse contract: parse_shotlist must NOT strip
    color_grade from the first entry (episode-level director override).

    FAILS before Task 3-impl (parse_shotlist drops all unknown fields).
    PASSES after Task 3-impl (white-lists color_grade on first entry only).
    """
    beat = {
        "visual_type": "still",
        "shots": [{"prompt": "晨跑跑道全景", "shot_type": "全景", "with_character": False}],
        "keywords": [],
        "color_grade": "暖金",
    }
    raw = _make_raw([beat])
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, list), f"expected list, got: {result!r}"
    assert result[0].get("color_grade") == "暖金", (
        f"color_grade='暖金' on first beat must survive parse_shotlist, "
        f"got result[0]: {result[0]!r}"
    )
