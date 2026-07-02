"""Tests for cross-episode character persistence (path→base64 reuse).

Fixes E2E acceptance point #3 (人物固定) + the character_ref path-vs-prompt
contract bug. resolve_character_ref_url decides, in priority order:
  1. SPIKE_REF_PNG + SPIKE_REF_URL env  → dev hook (highest)
  2. character_ref is an existing, readable IMAGE path → base64 data URI, no gen
  3. character_ref is a description / empty / unreadable path → gen_character_ref
Doubao/Ark accepts a base64 data URI as the image-to-image ref (verified live),
so reusing one stored png every episode gives a pixel-consistent face.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.wiring import resolve_character_ref_url

# A minimal valid 1x1 PNG (real image bytes; passes the image sniff).
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _RecordingGen:
    """Fake gen_character_ref; records whether/with-what it was called."""
    def __init__(self):
        self.called = False
        self.prompt = None

    def __call__(self, prompt, out_path):
        self.called = True
        self.prompt = prompt
        return "gen://generated-url"


def _clear_spike(monkeypatch):
    monkeypatch.delenv("SPIKE_REF_PNG", raising=False)
    monkeypatch.delenv("SPIKE_REF_URL", raising=False)


def test_existing_image_path_returns_base64_no_gen(tmp_path, monkeypatch):
    _clear_spike(monkeypatch)
    ref = tmp_path / "coach.png"
    ref.write_bytes(_PNG_1x1)
    cref_out = str(tmp_path / "ep_assets_character_ref.png")
    gen = _RecordingGen()

    url = resolve_character_ref_url(str(ref), cref_out, gen, log=lambda _m: None)

    assert url.startswith("data:image/"), f"expected base64 data URI, got {url[:30]!r}"
    assert not gen.called, "must NOT regenerate when a persistent image path exists"


def test_description_string_calls_gen(tmp_path, monkeypatch):
    _clear_spike(monkeypatch)
    desc = "一位动漫女性跑步教练，短马尾，深色运动衣"
    cref_out = str(tmp_path / "ref.png")
    gen = _RecordingGen()

    url = resolve_character_ref_url(desc, cref_out, gen, log=lambda _m: None)

    assert gen.called, "a description (non-path) must go through gen_character_ref"
    assert gen.prompt == desc
    assert url == "gen://generated-url"


def test_spike_hook_takes_priority(tmp_path, monkeypatch):
    spike_png = tmp_path / "spike.png"
    spike_png.write_bytes(_PNG_1x1)
    monkeypatch.setenv("SPIKE_REF_PNG", str(spike_png))
    monkeypatch.setenv("SPIKE_REF_URL", "https://spike.example/ref.jpg")
    # even with a valid character_ref path, SPIKE wins
    ref = tmp_path / "coach.png"
    ref.write_bytes(_PNG_1x1)
    cref_out = str(tmp_path / "ref_out.png")
    gen = _RecordingGen()

    url = resolve_character_ref_url(str(ref), cref_out, gen, log=lambda _m: None)

    assert url == "https://spike.example/ref.jpg"
    assert not gen.called


def test_corrupt_image_path_falls_back_to_gen_no_raise(tmp_path, monkeypatch):
    _clear_spike(monkeypatch)
    bad = tmp_path / "corrupt.png"
    bad.write_bytes(b"this is not a real image")   # exists but not decodable
    cref_out = str(tmp_path / "ref.png")
    gen = _RecordingGen()

    # must not raise; must fall back to generation
    url = resolve_character_ref_url(str(bad), cref_out, gen, log=lambda _m: None)

    assert gen.called, "unreadable/corrupt persistent image must fall back to gen"
    assert url == "gen://generated-url"


def test_empty_string_calls_gen_with_default(tmp_path, monkeypatch):
    _clear_spike(monkeypatch)
    cref_out = str(tmp_path / "ref.png")
    gen = _RecordingGen()
    url = resolve_character_ref_url("", cref_out, gen, log=lambda _m: None)
    assert gen.called
    assert url == "gen://generated-url"


def test_non_doubao_provider_falls_back_to_gen_not_base64(tmp_path, monkeypatch):
    """R2 fix #2: MiniMax's subject_reference wants a remote URL, not a data URI.
    For provider != doubao, an existing image path must NOT be base64'd — fall back
    to gen (real URL) instead of silently shipping a broken ref."""
    _clear_spike(monkeypatch)
    ref = tmp_path / "coach.png"
    ref.write_bytes(_PNG_1x1)
    cref_out = str(tmp_path / "out.png")
    gen = _RecordingGen()
    url = resolve_character_ref_url(str(ref), cref_out, gen, log=lambda _m: None,
                                    provider="minimax")
    assert gen.called, "non-doubao provider must fall back to gen, not base64"
    assert not url.startswith("data:"), f"must not return a base64 data URI, got {url[:20]!r}"


def test_doubao_provider_still_base64(tmp_path, monkeypatch):
    _clear_spike(monkeypatch)
    ref = tmp_path / "coach.png"
    ref.write_bytes(_PNG_1x1)
    gen = _RecordingGen()
    url = resolve_character_ref_url(str(ref), str(tmp_path / "o.png"), gen,
                                    log=lambda _m: None, provider="doubao")
    assert url.startswith("data:image/")
    assert not gen.called


def test_nonexistent_path_uses_default_prompt_not_raw_path(tmp_path, monkeypatch):
    """R2 fix #5: a character_ref that looks like a path but doesn't exist must NOT
    be fed to gen as a prompt (garbage) — fall back to the default prompt 主讲人物."""
    _clear_spike(monkeypatch)
    missing = str(tmp_path / "does_not_exist.png")
    gen = _RecordingGen()
    url = resolve_character_ref_url(missing, str(tmp_path / "o.png"), gen,
                                    log=lambda _m: None)
    assert gen.called
    assert gen.prompt == "主讲人物", f"must use default prompt, not the raw path; got {gen.prompt!r}"
    assert url == "gen://generated-url"


# minimal valid JPEG (SOI + APP0 JFIF + EOI) — passes the magic-byte sniff (0xFFD8FF)
_JPEG_MIN = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def test_jpeg_path_returns_base64_jpeg(tmp_path, monkeypatch):
    """The real running-class char ref is a .jpg — verify the JPEG sniff branch,
    not just PNG."""
    _clear_spike(monkeypatch)
    ref = tmp_path / "coach.jpg"
    ref.write_bytes(_JPEG_MIN)
    gen = _RecordingGen()
    url = resolve_character_ref_url(str(ref), str(tmp_path / "o.png"), gen,
                                    log=lambda _m: None, provider="doubao")
    assert url.startswith("data:image/jpeg;base64,"), f"expected jpeg data URI, got {url[:24]!r}"
    assert not gen.called
