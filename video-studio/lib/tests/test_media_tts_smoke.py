"""Smoke test for video-studio.lib.media_tts.

Default behavior: skip unless RUN_LIVE=1 is set in the environment.
With RUN_LIVE=1: synthesizes one short Chinese narration, asserts the
mp3 file exists, is non-trivially sized, and that ffprobe's measured
duration matches the API-returned `audio_length_ms` within 400 ms.

Consumes MiniMax speech-02-hd quota — keep usage minimal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib import media_tts


# Short, unambiguous fixture (minimal quota burn).
_SHORT_TEXT = "今天聊跑步配速。"

# Tolerance for the duration sanity check (ms).
_DUR_TOLERANCE_MS = 400


@pytest.fixture
def _require_live():
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("set RUN_LIVE=1 to run live MiniMax speech-02-hd smoke test")
    if not os.environ.get("MINIMAX_API_KEY"):
        pytest.fail("RUN_LIVE=1 but MINIMAX_API_KEY is not set")
    if not os.environ.get("MINIMAX_API_HOST"):
        pytest.fail("RUN_LIVE=1 but MINIMAX_API_HOST is not set")


def _probe_duration_ms(mp3_path: str) -> int:
    """Return the duration of an audio file in milliseconds via ffprobe.

    Raises RuntimeError on ffprobe failure or unparseable output.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                mp3_path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"ffprobe failed for {mp3_path}: {e.stderr.strip()}"
        ) from e

    raw = out.stdout.strip()
    if not raw:
        raise RuntimeError(f"ffprobe returned empty duration for {mp3_path}")
    seconds = float(raw)
    return int(round(seconds * 1000))


def test_synth_returns_audio_length_and_writes_mp3(tmp_path: Path, _require_live):
    out_path = str(tmp_path / "narration.mp3")

    audio_length_ms = media_tts.synth(_SHORT_TEXT, out_path)
    assert isinstance(audio_length_ms, int) and audio_length_ms > 0, audio_length_ms

    assert os.path.exists(out_path), f"mp3 not written: {out_path}"
    size = os.path.getsize(out_path)
    assert size > 1024, f"mp3 too small: {size} bytes"

    measured_ms = _probe_duration_ms(out_path)
    delta = abs(measured_ms - audio_length_ms)
    assert delta < _DUR_TOLERANCE_MS, (
        f"duration mismatch: api={audio_length_ms}ms "
        f"ffprobe={measured_ms}ms delta={delta}ms (tol={_DUR_TOLERANCE_MS}ms)"
    )
