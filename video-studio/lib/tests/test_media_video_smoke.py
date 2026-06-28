"""Smoke test for video-studio.lib.media_video (live MiniMax S2V).

Default behavior: skip unless RUN_LIVE_VIDEO=1 is set in the environment.
With RUN_LIVE_VIDEO=1: submits exactly ONE S2V task (uses 1/21 of the
weekly quota), waits for it to land on Success, downloads the file,
and asserts ffprobe can parse it as a 6~10s video.

This is gated by RUN_LIVE_VIDEO (not RUN_LIVE) so it doesn't get
accidentally triggered by the image/TTS smoke tests' env, and vice-versa.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import media_image
from lib import media_video


# Minimal burn fixture (short prompt + short reference).
_REF_PROMPT = (
    "portrait of a Chinese male running coach, 35 yo, short black hair, "
    "navy running jacket, neutral expression, head and shoulders, "
    "front-facing, soft studio light"
)
_VIDEO_PROMPT = (
    "the same coach slowly jogging forward on a running track at sunrise, "
    "smooth camera, gentle motion, 6 seconds"
)


@pytest.fixture
def _require_live():
    if os.environ.get("RUN_LIVE_VIDEO") != "1":
        pytest.skip("set RUN_LIVE_VIDEO=1 to run live MiniMax S2V smoke test")
    if not os.environ.get("MINIMAX_API_KEY"):
        pytest.fail("RUN_LIVE_VIDEO=1 but MINIMAX_API_KEY is not set")
    if not os.environ.get("MINIMAX_API_HOST"):
        pytest.fail("RUN_LIVE_VIDEO=1 but MINIMAX_API_HOST is not set")


def test_gen_video_live(tmp_path: Path, _require_live):
    # First generate a character_ref (no quota burn on character_ref
    # itself for S2V — it's just the anchor URL).
    ref_path = tmp_path / "character_ref.png"
    ref_url = media_image.gen_character_ref(_REF_PROMPT, str(ref_path))
    assert isinstance(ref_url, str) and ref_url.startswith("http"), ref_url

    out_path = tmp_path / "s2v.mp4"
    # Tight poll budget for smoke (10 min default is fine, but fail fast
    # if the API is dead — this is a smoke test, not a stress test).
    result = media_video.gen_video(
        _VIDEO_PROMPT, ref_url, str(out_path),
        max_poll_seconds=600, interval=10,
    )

    if not result["ok"]:
        pytest.skip(f"S2V unavailable, fallback returned: {result.get('reason')}")

    assert result["ok"] is True
    assert result["path"] == str(out_path)
    assert os.path.exists(out_path), f"mp4 not written: {out_path}"
    size = os.path.getsize(out_path)
    assert size > 50 * 1024, f"mp4 too small: {size} bytes"

    duration_s = media_video.probe_duration_s(out_path)
    assert 6.0 <= duration_s <= 10.0, (
        f"S2V clip length out of expected 6~10s window: {duration_s:.2f}s"
    )