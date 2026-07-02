"""Doubao (Volcengine Ark) media provider — drop-in alternative to the MiniMax
helpers (media_image + media_video). Same function signatures and return
contracts so spike_run.py can switch providers with MEDIA_PROVIDER=doubao.

- Images: Seedream 4.5 via POST /images/generations — SYNCHRONOUS, returns
  data[].url. Character consistency via image-to-image references (`image`).
- Video: Seedance 1.5 pro via POST /contents/generations/tasks — async task,
  poll GET /contents/generations/tasks/{id}, result at content.video_url.

Auth: ARK_API_KEY (Bearer). The `model` field takes an Endpoint ID
(ep-…) from PIC_ENDPOINT / VIDEO_ENDPOINT, which the Ark console binds to the
concrete model + config. Verified live against ark.cn-beijing 2026-06-29.

Downloads reuse media_image._download so files land under their REAL extension.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

from .media_image import _download as _download_image  # 25MB cap + real-ext sniff
from .media_video import _download as _download_video  # 200MB cap (videos are big)

_BASE = os.environ.get("ARK_BASE", "https://ark.cn-beijing.volces.com/api/v3")

# Seedream 4.5 requires >= 3,686,400 px. 16:9 still and 1:1 ref both clear it;
# the pipeline scales down to 1080p, so a larger source just means more detail.
_STILL_SIZE = "2560x1440"   # 16:9, 3.69M px
_REF_SIZE = "2048x2048"     # 1:1, 4.19M px

_IMG_RETRIES = 4
_VIDEO_MAX_POLL_SECONDS = 600
_VIDEO_POLL_INTERVAL = 10
_VIDEO_PROCESSING = {"queued", "running", "pending", "preparing", "processing"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _key() -> str:
    k = os.environ.get("ARK_API_KEY")
    if not k:
        raise RuntimeError("ARK_API_KEY not set (needed for MEDIA_PROVIDER=doubao)")
    return k


def _post(path: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        _BASE + path, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        method="POST")
    _log(f"[doubao] POST {path} model={payload.get('model')}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ark POST {path} HTTP {e.code}: {e.read().decode()[:300]}") from e


def _get(path: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        _BASE + path, headers={"Authorization": f"Bearer {_key()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ark GET {path} HTTP {e.code}: {e.read().decode()[:300]}") from e


def _pic_ep() -> str:
    ep = os.environ.get("PIC_ENDPOINT")
    if not ep:
        raise RuntimeError("PIC_ENDPOINT not set (Seedream endpoint id)")
    return ep


def _vid_ep() -> str:
    ep = os.environ.get("VIDEO_ENDPOINT")
    if not ep:
        raise RuntimeError("VIDEO_ENDPOINT not set (Seedance endpoint id)")
    return ep


class ContentModerationError(RuntimeError):
    """Doubao/Ark flagged the generated image as sensitive (permanent 4xx). A
    sanitized retry already failed. Callers should fall back (not crash the run)."""


# Safety prefix for the ONE sanitized retry after a content-moderation flag.
_SAFE_PREFIX = "健康阳光、正能量、安全内容、适合所有年龄，画面干净、无文字，"


def _is_moderation(err: str) -> bool:
    e = err.lower()
    return ("sensitivecontent" in e or "sensitive information" in e
            or "outputimagesensitive" in e)


def _gen_image_url(prompt: str, size: str, ref_url: str | None,
                   _sanitize_attempt: bool = False) -> str:
    """POST /images/generations and return data[0].url, retrying transients.

    Content-moderation false-positives (OutputImageSensitiveContentDetected) get
    ONE sanitized retry (safety prefix + drop the ref, which can itself trip the
    filter). If that also flags, raise ContentModerationError so the caller can
    fall back for that shot instead of crashing the whole episode."""
    payload: dict = {
        "model": _pic_ep(), "prompt": prompt, "size": size,
        "response_format": "url", "watermark": False,
    }
    if ref_url:
        payload["image"] = [ref_url]   # image-to-image reference (character lock)
    last = "unknown"
    for attempt in range(_IMG_RETRIES):
        try:
            resp = _post("/images/generations", payload)
            data = resp.get("data")
            if isinstance(data, list) and data and isinstance(data[0].get("url"), str):
                return data[0]["url"]
            last = f"no data[].url (resp keys={list(resp.keys())})"
        except RuntimeError as e:
            last = str(e)
            # Don't waste retries on a PERMANENT error (4xx, e.g. bad params) —
            # only transient ones (5xx / timeout / 429 / empty data) are worth a
            # retry. 4xx won't fix itself; re-sending burns time, not quota.
            if " HTTP 4" in last and " HTTP 429" not in last:
                if _is_moderation(last) and not _sanitize_attempt:
                    _log("[doubao] 内容审核拦截 — 用净化 prompt 重试一次（去 ref）")
                    return _gen_image_url(_SAFE_PREFIX + prompt, size, None,
                                          _sanitize_attempt=True)
                if _is_moderation(last):
                    raise ContentModerationError(
                        f"content moderation (sanitized retry 仍被拦): {last}")
                raise RuntimeError(f"Seedream permanent error (no retry): {last}")
        if attempt < _IMG_RETRIES - 1:
            _log(f"[doubao] image transient: {last} — retry {attempt + 1}/{_IMG_RETRIES}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Seedream image failed after {_IMG_RETRIES} attempts: {last}")


def gen_character_ref(prompt: str, out_path: str) -> str:
    """Generate a 1:1 character reference (Seedream) and download it. Returns the
    remote URL (used as the image-to-image reference for later stills)."""
    url = _gen_image_url(prompt, _REF_SIZE, None)
    _log(f"[doubao] downloading character_ref to {out_path}")
    _download_image(url, out_path)
    return url


def gen_still(prompt: str, character_ref_url: str | None, out_path: str) -> str:
    """Generate a 16:9 still (Seedream). With character_ref_url, locks the
    subject via image-to-image. Returns the ACTUAL saved path (real extension).
    Style prefix is prepended by MediaSeams (single prepend point).
    """
    full_prompt = prompt
    url = _gen_image_url(full_prompt, _STILL_SIZE, character_ref_url)
    _log(f"[doubao] downloading still to {out_path}")
    return _download_image(url, out_path)


def gen_video(prompt: str, character_ref_url: str, out_path: str,
              max_poll_seconds: int = _VIDEO_MAX_POLL_SECONDS,
              interval: int = _VIDEO_POLL_INTERVAL) -> dict:
    """Seedance 1.5 pro: create task -> poll -> download. Same return contract as
    media_video.gen_video: {ok:True, path, task_id} or
    {ok:False, fallback:"still", reason, task_id}."""
    content = [{"type": "text", "text": prompt}]
    if character_ref_url:
        # role=first_frame anchors the subject; the model animates FROM the ref.
        content.append({"type": "image_url", "role": "first_frame",
                        "image_url": {"url": character_ref_url}})
    payload = {
        "model": _vid_ep(), "content": content,
        "ratio": "16:9", "resolution": "720p", "duration": 5,
    }
    try:
        created = _post("/contents/generations/tasks", payload)
    except RuntimeError as e:
        return {"ok": False, "fallback": "still", "reason": f"submit failed: {e}", "task_id": None}
    task_id = created.get("id") or created.get("task_id")
    if not task_id:
        return {"ok": False, "fallback": "still",
                "reason": f"no task id in response: {list(created.keys())}", "task_id": None}

    start = time.monotonic()
    while True:
        if time.monotonic() - start > max_poll_seconds:
            return {"ok": False, "fallback": "still",
                    "reason": f"poll timeout >{max_poll_seconds}s", "task_id": task_id}
        try:
            data = _get(f"/contents/generations/tasks/{task_id}")
        except RuntimeError as e:
            return {"ok": False, "fallback": "still", "reason": f"poll failed: {e}", "task_id": task_id}
        status = (data.get("status") or "").lower()
        if status in _VIDEO_PROCESSING:
            time.sleep(interval)
            continue
        if status == "succeeded":
            video_url = (data.get("content") or {}).get("video_url")
            if not video_url:
                return {"ok": False, "fallback": "still",
                        "reason": f"succeeded but no content.video_url: {data.get('content')}",
                        "task_id": task_id}
            try:
                _download_video(video_url, out_path, timeout=300)
            except RuntimeError as e:
                return {"ok": False, "fallback": "still", "reason": f"fetch failed: {e}", "task_id": task_id}
            usage = data.get("usage") or {}
            _log(f"[doubao] S2V ok task={task_id} tokens={usage.get('total_tokens')}")
            return {"ok": True, "path": out_path, "task_id": task_id,
                    "total_tokens": usage.get("total_tokens")}
        # failed / expired / cancelled / unknown
        reason = (data.get("error") or {}).get("message") or status or "unknown"
        return {"ok": False, "fallback": "still", "reason": f"task {status}: {reason}", "task_id": task_id}
