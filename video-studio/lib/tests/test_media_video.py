"""Tests for video-studio.lib.media_video (mock-based, no network).

Covers the six fallback branches required by the spike plan:
  (a) Processing×N → Success normal path -> ok=True
  (b) Poll timeout -> ok=False, fallback=still
  (c) Task status Fail -> ok=False, fallback=still
  (d) Downloaded file too small -> ok=False, fallback=still
  (e) Success but missing file_id -> ok=False, fallback=still
  (f) fetch download HTTP error -> ok=False, fallback=still

All HTTP calls (submit/poll-query/fetch-retrieve) are mocked via
monkeypatch on `lib.media_video.mmclient`. The download step is
mocked via `lib.media_video._download` to avoid real network IO while
still exercising the size and ffprobe gates.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lib import media_video


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMmclientError(RuntimeError):
    """Marker so we can distinguish mmclient-raised errors from ours."""


def _patch_submit(monkeypatch, *, fail_with: Exception | None = None, task_id: str = "t"):
    """Replace submit() with either a fake success or a raising stub."""
    if fail_with is not None:
        def _raise(*args, **kwargs):  # noqa: ARG001
            raise fail_with
        monkeypatch.setattr(media_video, "submit", _raise)
    else:
        def _ok(*args, **kwargs):  # noqa: ARG001
            return task_id
        monkeypatch.setattr(media_video, "submit", _ok)


def _patch_poll(monkeypatch, *, result: dict, sleep_calls=None):
    """Replace poll() with a fake. sleep_calls is a list that receives
    the requested interval values (for verification)."""
    def _fake_poll(task_id, **kwargs):  # noqa: ARG001
        return dict(result)
    monkeypatch.setattr(media_video, "poll", _fake_poll)


def _patch_fetch(
    monkeypatch,
    *,
    fail_with: Exception | None = None,
    write_bytes: int | None = None,
    out_path_capture: dict | None = None,
):
    """Replace fetch(). Either raises, or writes `write_bytes` of
    sentinel data to out_path and returns it."""
    if fail_with is not None:
        def _raise(file_id, out_path, timeout=300):  # noqa: ARG001
            raise fail_with
        monkeypatch.setattr(media_video, "fetch", _raise)
    else:
        def _write(file_id, out_path, timeout=300):  # noqa: ARG001
            if out_path_capture is not None:
                out_path_capture["path"] = out_path
            with open(out_path, "wb") as fh:
                fh.write(b"\x00" * (write_bytes or 0))
            return out_path
        monkeypatch.setattr(media_video, "fetch", _write)


def _patch_probe(monkeypatch, *, fail_with: Exception | None = None, duration_s: float = 8.0):
    """Replace probe_duration_s()."""
    if fail_with is not None:
        def _raise(path):  # noqa: ARG001
            raise fail_with
        monkeypatch.setattr(media_video, "probe_duration_s", _raise)
    else:
        def _ok(path):  # noqa: ARG001
            return duration_s
        monkeypatch.setattr(media_video, "probe_duration_s", _ok)


# ---------------------------------------------------------------------------
# (a) Processing×N → Success normal path
# ---------------------------------------------------------------------------


def test_a_processing_then_success_happy_path(monkeypatch, tmp_path: Path):
    """Poll loops through Processing states and lands on Success → ok=True."""
    poll_states = [
        {"ok": False, "fallback": "still", "reason": "x", "task_id": "t"},  # would be processing
        {"ok": False, "fallback": "still", "reason": "x", "task_id": "t"},  # would be processing
        {"ok": True, "file_id": "file-xyz", "task_id": "t"},
    ]

    # Replace poll itself directly — the loop is tested in (b/c/d); here
    # we exercise the orchestration: submit succeeds, poll returns
    # Success, fetch writes a valid file, ffprobe parses → ok=True.
    _patch_submit(monkeypatch, task_id="t")
    _patch_poll(monkeypatch, result=poll_states[2])
    out_path = tmp_path / "out.mp4"
    _patch_fetch(monkeypatch, write_bytes=200 * 1024, out_path_capture={})
    _patch_probe(monkeypatch, duration_s=8.0)

    captured = {}
    result = media_video.gen_video(
        "p", "http://ref", str(out_path),
        sleep_fn=lambda s: captured.setdefault("sleeps", []).append(s),
    )

    assert result["ok"] is True
    assert result["path"] == str(out_path)
    assert result["task_id"] == "t"
    assert result["file_id"] == "file-xyz"
    assert out_path.exists()
    assert out_path.stat().st_size == 200 * 1024


def test_a_poll_processing_state_loop(monkeypatch):
    """Drive the real poll() loop with fake mmclient responses to verify
    the Processing→Success transition (loop body, no orchestration)."""
    queue = [
        {"status": "Processing", "task_id": "t"},
        {"status": "Processing", "task_id": "t"},
        {"status": "Success", "task_id": "t", "file_id": "f-1"},
    ]

    def fake_query(task_id):  # noqa: ARG001
        return queue.pop(0)

    monkeypatch.setattr(media_video, "_query_task", fake_query)

    sleeps = []
    result = media_video.poll(
        "t", max_poll_seconds=600, interval=10,
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert result == {"ok": True, "file_id": "f-1", "task_id": "t"}
    assert sleeps == [10, 10]  # one per Processing tick
    assert queue == []  # all states consumed


# ---------------------------------------------------------------------------
# (b) Poll timeout
# ---------------------------------------------------------------------------


def test_b_poll_timeout_returns_fallback(monkeypatch):
    """poll() that always sees Processing exceeds its budget → fallback."""

    def fake_query(task_id):  # noqa: ARG001
        return {"status": "Processing", "task_id": task_id}

    monkeypatch.setattr(media_video, "_query_task", fake_query)

    # Tiny budget + short max_poll via elapsed math (use monotonic).
    sleeps = []
    # Monkeypatch the start time so the very first check is already over budget.
    import time as _time
    orig_monotonic = _time.monotonic
    counter = {"t": 0.0}
    def fake_monotonic():
        counter["t"] += 1000.0  # each call advances 1000s
        return counter["t"]
    monkeypatch.setattr(_time, "monotonic", fake_monotonic)

    result = media_video.poll("t", max_poll_seconds=10, interval=5,
                              sleep_fn=lambda s: sleeps.append(s))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "timeout" in result["reason"].lower()
    assert result["task_id"] == "t"


def test_b_orchestration_timeout_returns_fallback(monkeypatch, tmp_path: Path):
    """gen_video() surfaces a poll-timeout fallback."""
    _patch_submit(monkeypatch)
    _patch_poll(monkeypatch, result={
        "ok": False, "fallback": "still",
        "reason": "poll timeout after 600s",
        "task_id": "t",
    })
    result = media_video.gen_video("p", "http://ref", str(tmp_path / "out.mp4"))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "timeout" in result["reason"].lower()
    assert result["task_id"] == "t"


# ---------------------------------------------------------------------------
# (c) Task status Fail
# ---------------------------------------------------------------------------


def test_c_task_status_fail_returns_fallback(monkeypatch):
    """poll() sees status=Fail → fallback (not raise)."""
    monkeypatch.setattr(
        media_video, "_query_task",
        lambda task_id: {"status": "Fail", "reason": "content policy"},
    )
    sleeps = []
    result = media_video.poll("t", sleep_fn=lambda s: sleeps.append(s))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "Fail" in result["reason"]
    assert "content policy" in result["reason"]
    assert sleeps == []  # did not sleep before failing


def test_c_orchestration_task_fail_returns_fallback(monkeypatch, tmp_path: Path):
    _patch_submit(monkeypatch)
    _patch_poll(monkeypatch, result={
        "ok": False, "fallback": "still",
        "reason": "task Fail: content policy violation",
        "task_id": "t",
    })
    result = media_video.gen_video("p", "http://ref", str(tmp_path / "out.mp4"))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "Fail" in result["reason"]
    assert result["task_id"] == "t"


# ---------------------------------------------------------------------------
# (d) Downloaded file too small
# ---------------------------------------------------------------------------


def test_d_download_too_small_returns_fallback(monkeypatch, tmp_path: Path):
    """fetch writes a tiny file (below 50KB minimum) → fallback."""
    _patch_submit(monkeypatch)
    _patch_poll(monkeypatch, result={"ok": True, "file_id": "f", "task_id": "t"})
    out_path = tmp_path / "out.mp4"
    # 1 KB << 50 KB minimum.
    _patch_fetch(monkeypatch, write_bytes=1024)
    _patch_probe(monkeypatch, duration_s=8.0)  # would pass ffprobe, but size check first

    result = media_video.gen_video("p", "http://ref", str(out_path))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "too small" in result["reason"].lower()
    assert result["task_id"] == "t"
    assert result["file_id"] == "f"
    # Tiny file should be cleaned up.
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# (e) Success but missing file_id
# ---------------------------------------------------------------------------


def test_e_success_missing_file_id_returns_fallback(monkeypatch):
    """poll() sees Success without file_id → fallback (not raise)."""
    monkeypatch.setattr(
        media_video, "_query_task",
        lambda task_id: {"status": "Success", "task_id": task_id},
    )
    sleeps = []
    result = media_video.poll("t", sleep_fn=lambda s: sleeps.append(s))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "file_id" in result["reason"]
    assert result["task_id"] == "t"


def test_e_orchestration_success_no_file_id(monkeypatch, tmp_path: Path):
    """This is the orchestration case where poll returns Success but no
    file_id — gen_video's own poll() gate catches it before fetch."""
    # Don't replace poll — let the real poll() run.
    monkeypatch.setattr(
        media_video, "_query_task",
        lambda task_id: {"status": "Success", "task_id": task_id},
    )
    _patch_submit(monkeypatch, task_id="t")

    result = media_video.gen_video("p", "http://ref", str(tmp_path / "out.mp4"))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "file_id" in result["reason"]
    assert result["task_id"] == "t"


# ---------------------------------------------------------------------------
# (f) fetch download HTTP error
# ---------------------------------------------------------------------------


def test_f_fetch_http_error_returns_fallback(monkeypatch, tmp_path: Path):
    """fetch() raises RuntimeError (simulated HTTP error) → fallback."""
    _patch_submit(monkeypatch)
    _patch_poll(monkeypatch, result={"ok": True, "file_id": "f", "task_id": "t"})
    out_path = tmp_path / "out.mp4"
    _patch_fetch(
        monkeypatch,
        fail_with=RuntimeError("download HTTP 502 from https://cdn/x.mp4"),
    )

    result = media_video.gen_video("p", "http://ref", str(out_path))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "fetch failed" in result["reason"].lower()
    assert "502" in result["reason"]
    assert result["task_id"] == "t"
    assert result["file_id"] == "f"
    # No file should have been written.
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Bonus: submit failure surface
# ---------------------------------------------------------------------------


def test_submit_runtime_error_returns_fallback(monkeypatch, tmp_path: Path):
    """If submit raises (e.g. HTTP error, base_resp != 0), gen_video
    surfaces it as fallback with task_id=None."""
    _patch_submit(monkeypatch, fail_with=RuntimeError("MiniMax HTTP 401 at /v1/video_generation: ..."))

    result = media_video.gen_video("p", "http://ref", str(tmp_path / "out.mp4"))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "submit failed" in result["reason"].lower()
    assert "401" in result["reason"]
    assert result["task_id"] is None


# ---------------------------------------------------------------------------
# Bonus: ffprobe parse failure on the downloaded file
# ---------------------------------------------------------------------------


def test_d_ffprobe_unparseable_returns_fallback(monkeypatch, tmp_path: Path):
    """fetch writes a sufficiently-large file but ffprobe rejects it."""
    _patch_submit(monkeypatch)
    _patch_poll(monkeypatch, result={"ok": True, "file_id": "f", "task_id": "t"})
    out_path = tmp_path / "out.mp4"
    _patch_fetch(monkeypatch, write_bytes=100 * 1024)  # above size minimum
    _patch_probe(monkeypatch, fail_with=RuntimeError("ffprobe failed: Invalid data"))

    result = media_video.gen_video("p", "http://ref", str(out_path))
    assert result["ok"] is False
    assert result["fallback"] == "still"
    assert "ffprobe" in result["reason"].lower()
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# submit() unit tests — exercise the real submit() with mocked mmclient
# ---------------------------------------------------------------------------


def test_submit_happy_path(monkeypatch):
    """submit() returns task_id from the data object."""
    monkeypatch.setattr(
        media_video.mmclient, "post_json",
        lambda path, payload, timeout=120: {
            "data": {"task_id": "task-zzz"},
            "base_resp": {"status_code": 0},
        },
    )
    task_id = media_video.submit("a prompt", "http://ref")
    assert task_id == "task-zzz"


def test_submit_missing_task_id_raises(monkeypatch):
    monkeypatch.setattr(
        media_video.mmclient, "post_json",
        lambda path, payload, timeout=120: {
            "data": {},
            "base_resp": {"status_code": 0},
        },
    )
    with pytest.raises(RuntimeError, match="task_id"):
        media_video.submit("p", "http://ref")


def test_submit_base_resp_nonzero_raises(monkeypatch):
    """mmclient.post_json raises on base_resp != 0; submit propagates."""
    def fake(path, payload, timeout=120):  # noqa: ARG001
        raise RuntimeError("MiniMax /v1/video_generation failed: 1004 invalid arg")
    monkeypatch.setattr(media_video.mmclient, "post_json", fake)

    with pytest.raises(RuntimeError, match="1004"):
        media_video.submit("p", "http://ref")


# ---------------------------------------------------------------------------
# fetch() unit tests — exercise the real fetch() with mocked mmclient
# ---------------------------------------------------------------------------


def test_fetch_happy_path(monkeypatch, tmp_path: Path):
    """fetch() calls retrieve, then downloads via _download."""
    monkeypatch.setattr(
        media_video.mmclient, "get_json",
        lambda path, timeout=60: {
            "data": {"download_url": "https://cdn.example/x.mp4"},
            "base_resp": {"status_code": 0},
        },
    )
    captured = {}
    def fake_download(url, out_path, timeout=300):
        captured["url"] = url
        captured["out"] = out_path
        with open(out_path, "wb") as fh:
            fh.write(b"\x00" * 1024)
    monkeypatch.setattr(media_video, "_download", fake_download)

    out = media_video.fetch("f-1", str(tmp_path / "out.mp4"))
    assert out == str(tmp_path / "out.mp4")
    assert captured["url"] == "https://cdn.example/x.mp4"
    assert (tmp_path / "out.mp4").exists()


def test_fetch_missing_download_url_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        media_video.mmclient, "get_json",
        lambda path, timeout=60: {
            "data": {},
            "base_resp": {"status_code": 0},
        },
    )
    with pytest.raises(RuntimeError, match="download_url"):
        media_video.fetch("f-1", str(tmp_path / "out.mp4"))


# ---------------------------------------------------------------------------
# _download() defensive gates (size cap + cleanup)
# ---------------------------------------------------------------------------


def test_download_size_cap_raises_and_cleans(monkeypatch, tmp_path: Path):
    """A response exceeding _MAX_DOWNLOAD_BYTES triggers cleanup + raise."""
    from lib import media_video as mv

    class _Resp:
        def __init__(self, total):
            self._remaining = total
            self.entered = False
        def __enter__(self):
            self.entered = True
            return self
        def __exit__(self, *args):
            return False
        def read(self, n):
            if self._remaining <= 0:
                return b""
            chunk = b"\x00" * min(n, self._remaining)
            self._remaining -= len(chunk)
            return chunk

    big = mv._MAX_DOWNLOAD_BYTES + 1024
    monkeypatch.setattr(mv.urllib.request, "urlopen", lambda req, timeout: _Resp(big))
    out_path = tmp_path / "big.mp4"

    with pytest.raises(RuntimeError, match="exceeded"):
        mv._download("https://x/y", str(out_path), timeout=30)
    assert not out_path.exists()


def test_download_http_error_raises_and_cleans(monkeypatch, tmp_path: Path):
    """An HTTPError during download triggers cleanup + raise."""
    from lib import media_video as mv
    import urllib.error

    class _Body:
        def read(self):
            return b"boom"
        def close(self):
            pass

    def fake(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, _Body())

    monkeypatch.setattr(mv.urllib.request, "urlopen", fake)
    out_path = tmp_path / "out.mp4"
    # Pre-create the file so we can verify cleanup.
    out_path.write_bytes(b"junk")

    with pytest.raises(RuntimeError, match="HTTP 502"):
        mv._download("https://x/y", str(out_path), timeout=30)
    assert not out_path.exists()