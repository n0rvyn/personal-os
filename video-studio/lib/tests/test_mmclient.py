"""Tests for video-studio.lib.mmclient (env reading, redaction, base_resp gate)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from lib import mmclient


# ---------- env helpers ----------


def test_redact_basic():
    assert mmclient._redact("sk-cp-123456789") == "sk-cp-***"


def test_redact_short():
    assert mmclient._redact("abc") == "***"


def test_host_missing_raises_runtimeerror_with_field_name(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_HOST", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_HOST"):
        mmclient._host()


def test_key_missing_raises_runtimeerror_with_field_name(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MINIMAX_API_KEY"):
        mmclient._key()


def test_host_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_HOST", "api.example.com/")
    assert mmclient._host() == "api.example.com"


def test_host_strips_https_prefix(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_HOST", "https://api.example.com")
    assert mmclient._host() == "api.example.com"


def test_host_strips_both(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_HOST", "https://api.example.com/")
    assert mmclient._host() == "api.example.com"


# ---------- post_json: no real network ----------


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_env(monkeypatch, host="api.test", key="sk-cp-abc123"):
    monkeypatch.setenv("MINIMAX_API_HOST", host)
    monkeypatch.setenv("MINIMAX_API_KEY", key)


def test_post_json_requires_env(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_HOST", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        mmclient.post_json("/v1/x", {})


def test_post_json_happy_path(monkeypatch):
    _patch_env(monkeypatch)
    captured = {}

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["method"] = req.method
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data.decode("utf-8")
        return _FakeResp(json.dumps({"base_resp": {"status_code": 0}, "data": {"ok": True}}).encode())

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)

    out = mmclient.post_json("/v1/test", {"hello": "world"})
    assert out == {"base_resp": {"status_code": 0}, "data": {"ok": True}}
    assert captured["url"] == "https://api.test/v1/test"
    assert captured["method"] == "POST"
    # urllib normalizes header capitalization
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer sk-cp-abc123"
    assert headers["content-type"] == "application/json"
    assert json.loads(captured["body"]) == {"hello": "world"}


def test_post_json_base_resp_nonzero_raises(monkeypatch):
    _patch_env(monkeypatch)

    def fake_urlopen(req, timeout):  # noqa: ARG001
        return _FakeResp(json.dumps({
            "base_resp": {"status_code": 1001, "status_msg": "bad arg"},
        }).encode())

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="MiniMax /v1/test failed: 1001 bad arg"):
        mmclient.post_json("/v1/test", {})


def test_post_json_missing_base_resp_is_ok(monkeypatch):
    """If response has no base_resp, accept it (some endpoints may omit)."""
    _patch_env(monkeypatch)

    def fake_urlopen(req, timeout):  # noqa: ARG001
        return _FakeResp(json.dumps({"hello": "world"}).encode())

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)
    out = mmclient.post_json("/v1/test", {})
    assert out == {"hello": "world"}


def test_post_json_http_error_raises(monkeypatch):
    _patch_env(monkeypatch)

    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, io_bytes(b"boom"))

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="MiniMax HTTP 500"):
        mmclient.post_json("/v1/test", {})


def test_post_json_url_error_raises(monkeypatch):
    _patch_env(monkeypatch)

    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="connection error"):
        mmclient.post_json("/v1/test", {})


def test_post_json_non_json_raises(monkeypatch):
    _patch_env(monkeypatch)

    def fake_urlopen(req, timeout):  # noqa: ARG001
        return _FakeResp(b"<html>not json</html>")

    monkeypatch.setattr(mmclient.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="non-JSON"):
        mmclient.post_json("/v1/test", {})


# helper to build a fake HTTPError body with .read()/.close()
class io_bytes:
    def __init__(self, b: bytes):
        self._b = b

    def read(self) -> bytes:
        return self._b

    def close(self) -> None:
        pass