"""Tests for media_chart: render_pace_table produces a 16:9 PNG.

Verifies:
- File is created at out_path.
- File size > 5KB.
- ffprobe reads the image dimensions and the width:height ratio is 16:9
  (within 2px tolerance on the height axis at 1920x1080).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

import lib.media_chart as media_chart
from lib.media_chart import build_line_chart, build_pace_chart_from_vdot, render_pace_table
from lib.wiring import _chart_content_for_beat


def _ffprobe_size(png_path: str) -> tuple[int, int]:
    """Return (width, height) of a PNG as reported by ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            png_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(out.stdout)
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"])


def test_render_pace_table_creates_16x9_png() -> None:
    rows = [
        ("3:00-3:30/km", "全马 ~3:30"),
        ("3:30-4:00/km", "全马 ~4:00"),
        ("4:00-4:30/km", "全马 ~4:30"),
        ("4:30-5:00/km", "全马 ~5:00"),
        ("5:00-5:30/km", "全马 ~5:30"),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "pace_table.png")
        result = render_pace_table(
            title="VDOT 配速参考",
            rows=rows,
            out_path=out_path,
        )

        assert result == out_path
        assert os.path.exists(out_path)

        # Size check: must be a real image, not a stub.
        size_bytes = os.path.getsize(out_path)
        assert size_bytes > 5 * 1024, f"png too small: {size_bytes} bytes"

        # Aspect ratio: 16:9 at 1920x1080 → allow 2px tolerance on height axis.
        w, h = _ffprobe_size(out_path)
        expected_h = round(w * 9 / 16)
        assert abs(h - expected_h) <= 2, (
            f"aspect ratio off: got {w}x{h}, expected height ~{expected_h} for 16:9"
        )


_VDOT_FIXTURE = {
    "data": [
        {"vdot": 30, "paces": {"easyMin": 7.41, "easyMax": 8.29, "marathon": 6.86,
                               "threshold": 6.4, "interval": None, "repetition400m": None}},
        {"vdot": 50, "paces": {"easyMin": 4.95, "easyMax": 5.57, "marathon": 4.52,
                               "threshold": 4.25, "interval": 3.95, "repetition400m": 88.0}},
    ]
}


def test_build_pace_chart_from_vdot_pulls_authoritative_rows() -> None:
    # picks the row nearest target_vdot and lists its present pace zones
    result = build_pace_chart_from_vdot(_VDOT_FIXTURE, target_vdot=50)
    assert result is not None
    title, rows = result
    assert "VDOT 50" in title
    labels = [r[0] for r in rows]
    assert labels == ["轻松跑 E", "马拉松配速 M", "乳酸阈 T", "间歇 I", "重复 R"]
    # None paces are skipped (vdot=30 row has interval/rep None)
    title30, rows30 = build_pace_chart_from_vdot(_VDOT_FIXTURE, target_vdot=30)
    assert [r[0] for r in rows30] == ["轻松跑 E", "马拉松配速 M", "乳酸阈 T"]
    # values are M:SS strings, not raw decimals
    assert all(":" in r[1] for r in rows)


def test_build_pace_chart_from_vdot_empty_returns_none() -> None:
    assert build_pace_chart_from_vdot({}) is None
    assert build_pace_chart_from_vdot(None) is None
    assert build_pace_chart_from_vdot({"data": []}) is None


# ---------------------------------------------------------------------------
# zone_boundary chart builder — tests written first; impl in Task 1-impl.
# These tests FAIL initially with AttributeError (builder not yet defined).
# ---------------------------------------------------------------------------

_ZONE_FIXTURE = {
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


def test_build_zone_boundary_chart_from_authoritative_data() -> None:
    """Builder returns (title, rows) with real Daniels values from data dict."""
    result = media_chart.build_zone_boundary_chart(_ZONE_FIXTURE)
    assert result is not None
    title, rows = result
    # Title must mention intensity/zone concept
    assert "强度" in title
    # Verify row structure: list of (code, value_string) pairs
    codes = [r[0] for r in rows]
    assert "E" in codes
    assert "M" in codes
    # Pin real E-zone vo2max value to ensure numbers come from data, not invented
    e_row = next(r for r in rows if r[0] == "E")
    assert "59–74%" in e_row[1]
    # Pin M-zone value to confirm data-driven (not a hardcoded default)
    m_row = next(r for r in rows if r[0] == "M")
    assert "75–84%" in m_row[1]


def test_build_zone_boundary_chart_none_on_empty() -> None:
    """Returns None on None or empty zones list — never a fabricated table."""
    assert media_chart.build_zone_boundary_chart(None) is None
    assert media_chart.build_zone_boundary_chart({"zones": []}) is None


# ---------------------------------------------------------------------------
# build_line_chart — line/curve chart from inline data points
# ---------------------------------------------------------------------------

def test_build_line_chart_creates_16x9_png() -> None:
    """build_line_chart renders a 16:9 PNG from inline (x, y) points."""
    points = [(0, 6.0), (1, 5.5), (2, 5.2), (3, 5.0), (4, 5.3), (5, 5.8), (6, 6.2)]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "line_chart.png")
        result = build_line_chart(
            title="坡度与最优配速 U 曲线",
            x_label="坡度 (%)",
            y_label="配速 (min/km)",
            points=points,
            out_path=out_path,
        )
        assert result == out_path
        assert os.path.exists(out_path)

        # Size check: must be a real image.
        size_bytes = os.path.getsize(out_path)
        assert size_bytes > 5 * 1024, f"png too small: {size_bytes} bytes"

        # Aspect ratio: 16:9 at 1920x1080.
        w, h = _ffprobe_size(out_path)
        expected_h = round(w * 9 / 16)
        assert abs(h - expected_h) <= 2, (
            f"aspect ratio off: got {w}x{h}, expected height ~{expected_h} for 16:9"
        )


def test_superscripts_to_mathtext_converts_unit_exponents() -> None:
    """Unicode superscript runs → mathtext so CN fonts don't tofu them (□)."""
    from lib.media_chart import _superscripts_to_mathtext
    assert _superscripts_to_mathtext("能耗 (J·kg⁻¹·m⁻¹)") == "能耗 (J·kg$^{-1}$·m$^{-1}$)"
    assert _superscripts_to_mathtext("面积 m²") == "面积 m$^{2}$"
    # no superscripts → unchanged; a pre-existing $ (author formula) → left alone
    assert _superscripts_to_mathtext("配速 (min/km)") == "配速 (min/km)"
    assert _superscripts_to_mathtext("v $^{2}$") == "v $^{2}$"


def test_build_line_chart_superscript_label_no_missing_glyph() -> None:
    """A y-label with superscript units must render WITHOUT matplotlib's
    'Glyph … missing from font' warning — the box (□) the user reported."""
    import warnings
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "sup_chart.png")
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)  # promote glyph warning to error
            build_line_chart(
                title="上坡跑能量消耗",
                x_label="坡度 (%)",
                y_label="能耗 (J·kg⁻¹·m⁻¹)",
                points=[(0, 3.6), (10, 7.2), (20, 12.9)],
                out_path=out_path,
            )
        assert os.path.exists(out_path)


# ---------------------------------------------------------------------------
# _chart_content_for_beat: unknown type → None (graceful degradation, no raise)
# ---------------------------------------------------------------------------

def test_chart_content_for_beat_unknown_type_returns_none() -> None:
    """Unknown chart type must return None instead of raising RuntimeError."""
    beat = {
        "id": "b_test",
        "visual_type": "chart",
        "chart": {"type": "unsupported_future_type", "params": {}},
    }
    result = _chart_content_for_beat(beat, {})
    assert result is None, f"expected None for unknown type, got {result!r}"


def test_chart_content_for_beat_line_returns_sentinel() -> None:
    """Line type returns ('__line__', params) sentinel."""
    params = {
        "title": "坡度曲线",
        "x_label": "坡度",
        "y_label": "配速",
        "points": [[0, 6.0], [3, 5.0], [6, 6.2]],
    }
    beat = {
        "id": "b_line",
        "visual_type": "chart",
        "chart": {"type": "line", "params": params},
    }
    result = _chart_content_for_beat(beat, {})
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert result[0] == "__line__", f"expected '__line__' sentinel, got {result[0]!r}"
    assert result[1] == params
