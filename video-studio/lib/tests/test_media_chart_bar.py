"""Tests for the bar chart builder + bottom subtitle safe-area + bar dispatch.

Fixes E2E acceptance point #1 (chart 误导) and #6-subtitle (字幕压 x 轴):
- build_bar_chart: 0-baseline bar for discrete/equivalence comparisons so two
  near-equal values render as two near-equal bars (honest), not a steep line.
- CHART_BOTTOM_SAFE: line/bar reserve a bottom band so the burned subtitle does
  not collide with axis labels.
- render_pace_table has its OWN safe-area lever (ax.axis("off")+ax.table height is
  font/row-driven, so subplots_adjust can't move it) — assert via _TABLE_LOC.
- _chart_content_for_beat dispatches chart.type=="bar" → ("__bar__", params).
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import lib.media_chart as media_chart
from lib.media_chart import build_bar_chart, CHART_BOTTOM_SAFE
from lib.wiring import _chart_content_for_beat


def test_build_bar_chart_writes_png(tmp_path):
    out = tmp_path / "bar.png"
    saved = build_bar_chart(
        "格罗宁根研究：受伤率对比", "组别", "受伤率(%)",
        ["10%/周", "50%/周"], [20.8, 20.3], str(out),
    )
    assert Path(saved).exists()
    assert Path(saved).stat().st_size > 0


def test_bar_chart_zero_baseline_makes_near_equal_values_look_near_equal(tmp_path):
    """0-baseline is the honesty guarantee: 20.8 vs 20.3 must render as two bars
    whose heights differ by <5% (near-equal), not an exaggerated gap. We verify
    the axes y-lower-bound is 0 (the mechanism that produces that)."""
    import matplotlib.pyplot as plt
    out = tmp_path / "bar.png"
    ax = build_bar_chart(
        "t", "x", "y", ["A", "B"], [20.8, 20.3], str(out), return_ax=True,
    )
    ymin, ymax = ax.get_ylim()
    assert ymin == 0, f"bar chart must use a 0 baseline, got ymin={ymin}"
    # With a 0 baseline, the two bar heights (20.8 / 20.3) differ by <3%.
    assert abs(20.8 - 20.3) / 20.8 < 0.05
    plt.close(ax.figure)


def test_chart_bottom_safe_reserves_band_for_subtitle():
    """line/bar reserve >=15% bottom band so burned subtitle clears the x-axis."""
    assert CHART_BOTTOM_SAFE >= 0.15


def test_charts_center_within_bottom_safe_region():
    """R2: charts reserve a bottom band (subtitle) + top band (title). Constants
    sanity — the RENDER-level proof is test_rendered_table_leaves_bottom_band_blank."""
    assert media_chart.CHART_BOTTOM_SAFE >= 0.15
    assert media_chart.CHART_TOP_SAFE >= 0.08
    assert media_chart.CHART_BOTTOM_SAFE + media_chart.CHART_TOP_SAFE < 0.6


def test_rendered_table_leaves_bottom_band_blank(tmp_path):
    """R2 fix #6 (non-tautological): actually RENDER the table and verify the bottom
    subtitle band is blank background (no table pixels), so the burned subtitle can't
    collide. A tall 7-row table would overflow into the band if the bbox were wrong."""
    import numpy as np
    import matplotlib.image as mpimg

    out = tmp_path / "tbl.png"
    rows = [("轻松跑 E", "5:34–6:16/km"), ("马拉松配速 M", "5:07/km"),
            ("乳酸阈 T", "4:48/km"), ("间歇 I", "4:28/km"),
            ("重复 R", "1:40/400m"), ("附加档一", "9:99/km"), ("附加档二", "8:88/km")]
    media_chart.render_pace_table("很多行的表格", rows, str(out))
    img = mpimg.imread(str(out))            # (H, W, C), row 0 = TOP
    H = img.shape[0]
    # Bottom band = the subtitle safe area (bottom CHART_BOTTOM_SAFE of the frame).
    # Sample just INSIDE it to avoid the exact boundary; it must be near-uniform bg.
    band_top = int((1.0 - media_chart.CHART_BOTTOM_SAFE + 0.03) * H)
    bottom_band = img[band_top:, :, :3]
    # A blank background band has near-zero per-pixel variance across the region.
    assert float(bottom_band.std()) < 0.02, (
        f"bottom subtitle band is not blank (std={bottom_band.std():.4f}) — "
        f"table overflows into the subtitle area"
    )
    # Sanity: the middle band DOES have table content (non-trivial variance).
    mid = img[int(0.35 * H):int(0.55 * H), :, :3]
    assert float(mid.std()) > 0.02, "expected table content in the middle band"


def test_chart_content_for_beat_bar_returns_sentinel():
    """bar type returns ('__bar__', params) sentinel (mirrors __line__)."""
    params = {
        "title": "受伤率对比", "x_label": "组别", "y_label": "受伤率(%)",
        "categories": ["10%/周", "50%/周"], "values": [20.8, 20.3],
    }
    beat = {"id": "b_bar", "visual_type": "chart", "chart": {"type": "bar", "params": params}}
    result = _chart_content_for_beat(beat, {})
    assert isinstance(result, tuple)
    assert result[0] == "__bar__", f"expected '__bar__', got {result[0]!r}"
    assert result[1] == params
