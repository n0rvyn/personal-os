"""MiniMax HTTP base client.

Reads MINIMAX_API_HOST and MINIMAX_API_KEY from env, exposes a single
post_json helper that POSTs JSON, parses the response, checks base_resp.status_code,
and returns the parsed dict. All log output redacts the API key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _host() -> str:
    """Read MINIMAX_API_HOST from env. Strips trailing slash and any 'https://' duplicate prefix."""
    raw = os.environ.get("MINIMAX_API_HOST")
    if not raw:
        raise RuntimeError("MINIMAX_API_HOST not set")
    h = raw.strip()
    if h.endswith("/"):
        h = h[:-1]
    # Strip accidental https:// duplication.
    if h.startswith("https://"):
        h = h[len("https://"):]
    if h.startswith("http://"):
        h = h[len("http://"):]
    return h


def _key() -> str:
    """Read MINIMAX_API_KEY from env. Required for all MiniMax calls."""
    k = os.environ.get("MINIMAX_API_KEY")
    if not k:
        raise RuntimeError("MINIMAX_API_KEY not set")
    return k


def _redact(k: str) -> str:
    """Return first 6 chars + '***' for safe logging."""
    if len(k) <= 6:
        return "***"
    return k[:6] + "***"


def _log(msg: str) -> None:
    """Print a single log line to stderr; caller is responsible for any key redaction."""
    print(msg, file=sys.stderr)


def post_json(path: str, payload: dict, timeout: int = 120) -> dict:
    """POST JSON to https://{MINIMAX_API_HOST}{path} with Bearer auth.

    Args:
        path: URL path beginning with '/', e.g. '/v1/image_generation'.
        payload: JSON-serializable dict.
        timeout: request timeout in seconds.

    Returns:
        Parsed response dict on success.

    Raises:
        RuntimeError: if env vars missing, HTTP non-2xx, or base_resp.status_code != 0.
    """
    host = _host()
    key = _key()

    url = f"https://{host}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    _log(f"[mmclient] POST {path} key={_redact(key)}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"MiniMax HTTP {e.code} at {path}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"MiniMax connection error at {path}: {e}") from e

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"MiniMax {path} returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"MiniMax {path} returned non-object JSON: {type(data).__name__}")

    base_resp = data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code", 0)
        status_msg = base_resp.get("status_msg", "")
        if status_code != 0:
            raise RuntimeError(f"MiniMax {path} failed: {status_code} {status_msg}")

    return data


def get_json(path: str, timeout: int = 120) -> dict:
    """GET JSON from https://{MINIMAX_API_HOST}{path} with Bearer auth.

    Args:
        path: URL path beginning with '/', e.g. '/v1/query/video_generation?task_id=...'.
        timeout: request timeout in seconds.

    Returns:
        Parsed response dict on success.

    Raises:
        RuntimeError: if env vars missing, HTTP non-2xx, or base_resp.status_code != 0.
    """
    host = _host()
    key = _key()

    url = f"https://{host}{path}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )

    _log(f"[mmclient] GET {path} key={_redact(key)}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"MiniMax HTTP {e.code} at {path}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"MiniMax connection error at {path}: {e}") from e

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"MiniMax {path} returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"MiniMax {path} returned non-object JSON: {type(data).__name__}")

    base_resp = data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code", 0)
        status_msg = base_resp.get("status_msg", "")
        if status_code != 0:
            raise RuntimeError(f"MiniMax {path} failed: {status_code} {status_msg}")

    return data