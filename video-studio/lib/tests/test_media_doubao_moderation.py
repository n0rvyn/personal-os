"""R2 fix #12: Doubao content-moderation → one sanitized retry, else typed error."""
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import pytest
import lib.media_doubao as md


_MOD_ERR = ("ark POST /images/generations HTTP 400: "
            '{"error":{"code":"OutputImageSensitiveContentDetected"}}')


def test_moderation_sanitized_retry_succeeds(monkeypatch):
    monkeypatch.setattr(md, "_pic_ep", lambda: "ep-test")
    calls = {"n": 0}

    def fake_post(path, payload, timeout=120):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(_MOD_ERR)          # first call flagged
        return {"data": [{"url": "http://ok/sanitized.jpg"}]}  # sanitized retry passes

    monkeypatch.setattr(md, "_post", fake_post)
    url = md._gen_image_url("原始 prompt", md._STILL_SIZE, "http://ref")
    assert url == "http://ok/sanitized.jpg"
    assert calls["n"] == 2, "must retry exactly once after a moderation flag"


def test_moderation_persists_raises_content_moderation_error(monkeypatch):
    monkeypatch.setattr(md, "_pic_ep", lambda: "ep-test")

    def always_flag(path, payload, timeout=120):
        raise RuntimeError(_MOD_ERR)

    monkeypatch.setattr(md, "_post", always_flag)
    with pytest.raises(md.ContentModerationError):
        md._gen_image_url("原始 prompt", md._STILL_SIZE, "http://ref")


def test_non_moderation_4xx_still_plain_runtime_error(monkeypatch):
    monkeypatch.setattr(md, "_pic_ep", lambda: "ep-test")

    def bad_params(path, payload, timeout=120):
        raise RuntimeError("ark POST /images/generations HTTP 400: bad param")

    monkeypatch.setattr(md, "_post", bad_params)
    with pytest.raises(RuntimeError) as exc:
        md._gen_image_url("p", md._STILL_SIZE, None)
    assert not isinstance(exc.value, md.ContentModerationError)
