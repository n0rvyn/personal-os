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

from lib.media_chart import render_pace_table


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
