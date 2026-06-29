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
    # Saved under its REAL extension (e.g. .jpg when the remote sends JPEG), so
    # match by stem, not the .png we passed in.
    refs = list(tmp_path.glob("character_ref.*"))
    assert refs and refs[0].stat().st_size > 10 * 1024, "character_ref not written"

    # gen_still returns the ACTUAL path written (real extension) — use it.
    out = media_image.gen_still(_STILL_PROMPT, ref_url, str(still_path))
    out_p = Path(out)
    assert out_p.exists(), f"still not written at returned path {out}"
    assert out_p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}, out
    assert out_p.stat().st_size > 10 * 1024, (
        f"still too small: {out_p.stat().st_size} bytes"
    )

    # Aspect ratio: 16:9 with ±2px tolerance on the height after width is fixed.
    w, h = media_image.probe_dimensions(out)
    expected_h = round(w * 9 / 16)
    assert abs(h - expected_h) <= 2, f"expected 16:9 (±2px), got {w}x{h} (h≈{expected_h})"


# --- non-live unit tests for the real-extension fix ---

def test_ext_for_magic_detects_real_type():
    assert media_image._ext_for_magic(b"\xff\xd8\xff\xe0\x00\x10JFIF") == ".jpg"
    assert media_image._ext_for_magic(b"\x89PNG\r\n\x1a\n\x00\x00") == ".png"
    assert media_image._ext_for_magic(b"RIFF\x24\x00\x00\x00WEBPVP8 ") == ".webp"
    assert media_image._ext_for_magic(b"GIF89a\x01\x00") == ".gif"
    assert media_image._ext_for_magic(b"not-an-image") is None


def test_download_saves_jpeg_under_jpg_name(tmp_path: Path, monkeypatch):
    """JPEG bytes requested as .png must land at .jpg, and _download returns it."""
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 4096

    class _FakeResp:
        def __init__(self, data):
            self._d, self._i = data, 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            chunk = self._d[self._i:self._i + n]
            self._i += n
            return chunk

    monkeypatch.setattr(media_image.urllib.request, "urlopen",
                        lambda req, timeout=0: _FakeResp(jpeg))
    dest = media_image._download("http://x/y.png", str(tmp_path / "img.png"))
    assert dest.endswith(".jpg") and os.path.exists(dest)
    assert not (tmp_path / "img.png").exists()  # the lying .png name is not used
