"""Smoke test for video-studio.lib.media_image.

Default behavior: skip unless RUN_LIVE=1 is set in the environment.
With RUN_LIVE=1: generates a character reference and one still, then uses
ffprobe to assert the still's aspect ratio is 16:9 (±2px tolerance).

Consumes MiniMax image-01 quota — keep usage minimal.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import media_image


# Common short, unambiguous fixture (minimal quota burn).
_REF_PROMPT = (
    "portrait of a Chinese male running coach, 35 yo, short black hair, "
    "navy running jacket, neutral expression, head and shoulders, "
    "front-facing, soft studio light"
)
_STILL_PROMPT = (
    "the same coach standing on a running track at sunrise, holding a "
    "clipboard, motion blur in the background, wide shot"
)


@pytest.fixture
def _require_live():
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("set RUN_LIVE=1 to run live MiniMax image-01 smoke test")
    # Sanity: env must be set so post_json can succeed.
    if not os.environ.get("MINIMAX_API_KEY"):
        pytest.fail("RUN_LIVE=1 but MINIMAX_API_KEY is not set")
    if not os.environ.get("MINIMAX_API_HOST"):
        pytest.fail("RUN_LIVE=1 but MINIMAX_API_HOST is not set")


def test_gen_character_ref_and_still(tmp_path: Path, _require_live):
    ref_path = tmp_path / "character_ref.png"
    still_path = tmp_path / "still.png"

    ref_url = media_image.gen_character_ref(_REF_PROMPT, str(ref_path))
    assert isinstance(ref_url, str) and ref_url.startswith("http"), ref_url
    assert ref_path.exists(), "character_ref.png not written"
    assert ref_path.stat().st_size > 10 * 1024, (
        f"character_ref.png too small: {ref_path.stat().st_size} bytes"
    )

    out = media_image.gen_still(_STILL_PROMPT, ref_url, str(still_path))
    assert out == str(still_path)
    assert still_path.exists(), "still.png not written"
    assert still_path.stat().st_size > 10 * 1024, (
        f"still.png too small: {still_path.stat().st_size} bytes"
    )

    # Aspect ratio: 16:9 with ±2px tolerance on the height after width is fixed.
    w, h = media_image.probe_dimensions(str(still_path))
    expected_h = round(w * 9 / 16)
    assert abs(h - expected_h) <= 2, f"expected 16:9 (±2px), got {w}x{h} (h≈{expected_h})"
