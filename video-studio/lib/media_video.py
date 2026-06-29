"""Video helper for video-studio.

Wraps MiniMax Hailuo S2V (subject-reference video generation) as an
async 3-step pipeline:

  1. `submit(prompt, character_ref_url, model)` -> task_id
  2. `poll(task_id, max_poll_seconds, interval)`  -> poll until Success / Fail / timeout
  3. `fetch(file_id, out_path)`                   -> download via 9h signed URL

`gen_video` orchestrates all three. On any failure along the way (HTTP
error, base_resp != 0, status == Fail, timeout, missing file_id, fetch
HTTP error, no download_url, oversize/undersize file, ffprobe failure)
it returns ``{"ok": False, "fallback": "still", "reason": ...}`` so the
spike driver (Task 8) can swap in a still+KenBurns instead of aborting
the entire pipeline ([D-015] S2V unstable → fall back to still).

All API calls go through `lib.mmclient` (Bearer auth, env-driven,
base_resp check, key-redacted logs). Downloaded files are written to a
fixed local path; we never trust the remote filename.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import mmclient


# Default model: S2V-01 — MiniMax's dedicated Subject-Reference-Video model.
# (The Hailuo T2V/I2V models, e.g. MiniMax-Hailuo-2.3, reject subject_reference
# with "2013 invalid params ... does not support Subject-Reference-Video mode" —
# verified live against api.minimaxi.com on 2026-06-29.)
DEFAULT_MODEL = "S2V-01"

# Default total poll budget. Spike uses 600s (10 min) per task; Stage 1
# will make this configurable.
DEFAULT_MAX_POLL_SECONDS = 600

# Default poll interval (seconds between GET /query calls).
DEFAULT_POLL_INTERVAL = 10

# Cap download size to defend against malicious/huge remote responses.
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB (videos are bigger than images)

# Minimum acceptable downloaded file size. Anything smaller is treated
# as a truncated/corrupt response and triggers fallback.
_MIN_DOWNLOAD_BYTES = 50 * 1024  # 50 KB

# Accepted "task is still running" status strings. "Preparing" + "Processing"
# observed live for S2V-01; the rest kept for forward-compat with other models.
_PROCESSING_STATES = {"Preparing", "Queue", "Queueing", "Processing", "Pending", "Running"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Step 1: submit
# ---------------------------------------------------------------------------


def submit(
    prompt: str,
    character_ref_url: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """Submit an S2V video generation task and return its task_id.

    Args:
        prompt: Scene/action description (the character performing some
            motion; the video is anchored to the character_ref URL).
        character_ref_url: Remote URL of the character reference image
            (the return value of `media_image.gen_character_ref`).
        model: Hailuo model id; default ``MiniMax-Hailuo-2.3``.

    Returns:
        The task_id string to pass into `poll`.

    Raises:
        RuntimeError: on HTTP error, base_resp != 0, missing task_id, or
            missing data object. (The spike driver catches this and
            treats it as fallback.)
    """
    # S2V-01 subject_reference takes an "image" ARRAY (NOT image-01's
    # "image_file" scalar). duration/resolution per the S2V-01 docs.
    payload = {
        "model": model,
        "prompt": prompt,
        "subject_reference": [
            {"type": "character", "image": [character_ref_url]}
        ],
        "duration": 6,
        "resolution": "1080P",
    }
    resp = mmclient.post_json("/v1/video_generation", payload, timeout=120)

    # CREATE response is flat: {"task_id": "...", "base_resp": {...}} — task_id
    # is TOP-LEVEL, NOT under a `data` wrapper. Verified live 2026-06-29.
    task_id = resp.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError("MiniMax video_generation: missing 'task_id'")
    return task_id


# ---------------------------------------------------------------------------
# Step 2: poll
# ---------------------------------------------------------------------------


def _query_task(task_id: str) -> dict:
    """GET the current status of `task_id`. Returns the parsed response dict.

    The QUERY response is flat: {"task_id", "status", "file_id",
    "video_width", "video_height", "base_resp"} — status/file_id are
    TOP-LEVEL, NOT under a `data` wrapper. Verified live 2026-06-29.

    Raises RuntimeError on HTTP / base_resp errors.
    """
    resp = mmclient.get_json(
        f"/v1/query/video_generation?task_id={task_id}", timeout=60
    )
    if not isinstance(resp, dict):
        raise RuntimeError("MiniMax query/video_generation: non-dict response")
    return resp


def poll(
    task_id: str,
    max_poll_seconds: int = DEFAULT_MAX_POLL_SECONDS,
    interval: int = DEFAULT_POLL_INTERVAL,
    sleep_fn=time.sleep,
) -> dict:
    """Poll `task_id` until Success / Fail / timeout.

    Args:
        task_id: The task_id returned by `submit`.
        max_poll_seconds: Total budget; once exceeded, the function
            returns a fallback dict (does not raise).
        interval: Seconds between polls.
        sleep_fn: Sleep injection point (override in tests).

    Returns:
        On Success: ``{"ok": True, "file_id": str, "task_id": str}``
        On any failure: ``{"ok": False, "fallback": "still", "reason": str, "task_id": str}``

    Note:
        Failures (HTTP / base_resp != 0) raised by `_query_task` are
        caught and converted to a fallback dict here — we never raise
        out of poll, because the spike driver treats any non-ok as a
        signal to fall back to a still.
    """
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > max_poll_seconds:
            return {
                "ok": False,
                "fallback": "still",
                "reason": f"poll timeout after {elapsed:.0f}s "
                          f"(>{max_poll_seconds}s budget)",
                "task_id": task_id,
            }

        try:
            data = _query_task(task_id)
        except RuntimeError as e:
            return {
                "ok": False,
                "fallback": "still",
                "reason": f"poll query failed: {e}",
                "task_id": task_id,
            }

        status = data.get("status")
        if not isinstance(status, str) or not status:
            return {
                "ok": False,
                "fallback": "still",
                "reason": f"poll missing 'status' in response: {data!r}",
                "task_id": task_id,
            }

        if status in _PROCESSING_STATES:
            sleep_fn(interval)
            continue

        if status == "Fail":
            reason = data.get("reason") or data.get("status_msg") or "task reported Fail"
            return {
                "ok": False,
                "fallback": "still",
                "reason": f"task Fail: {reason}",
                "task_id": task_id,
            }

        if status == "Success":
            file_id = data.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                return {
                    "ok": False,
                    "fallback": "still",
                    "reason": "task Success but response missing 'file_id'",
                    "task_id": task_id,
                }
            return {"ok": True, "file_id": file_id, "task_id": task_id}

        # Unknown status — be conservative and treat as processing. If
        # we never see Success/Fail this will eventually time out.
        _log(f"[media_video] unknown status {status!r}, continuing poll")
        sleep_fn(interval)


# ---------------------------------------------------------------------------
# Step 3: fetch
# ---------------------------------------------------------------------------


def fetch(file_id: str, out_path: str, timeout: int = 300) -> str:
    """Fetch the rendered video for `file_id` and download it locally.

    Args:
        file_id: The file_id returned by `poll` on Success.
        out_path: Local file path to write the downloaded mp4 to.
        timeout: HTTP timeout for the download step (seconds).

    Returns:
        The local file path (== out_path).

    Raises:
        RuntimeError: on HTTP error, missing download_url, or download
            failure (timeout, oversize, network error). The spike driver
            catches these and treats them as fallback.
    """
    resp = mmclient.get_json(
        f"/v1/files/retrieve?file_id={file_id}", timeout=60
    )
    # RETRIEVE response: {"file": {"download_url": "...", ...}, "base_resp": ...}
    # download_url is under `file`, NOT `data`. Verified live 2026-06-29.
    file_obj = resp.get("file")
    if not isinstance(file_obj, dict):
        raise RuntimeError("MiniMax files/retrieve: missing 'file' object")
    download_url = file_obj.get("download_url")
    if not isinstance(download_url, str) or not download_url:
        raise RuntimeError("MiniMax files/retrieve: missing 'download_url'")

    _log(f"[media_video] downloading file_id={file_id} to {out_path}")
    _download(download_url, out_path, timeout=timeout)
    return out_path


def _download(url: str, out_path: str, timeout: int = 300) -> None:
    """Download `url` to `out_path` with timeout and size cap.

    Streams in chunks so we can abort early on oversize responses.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "video-studio/0.0.1"})
    written = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(out_path, "wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        fh.close()
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                        raise RuntimeError(
                            f"download from {url} exceeded {_MAX_DOWNLOAD_BYTES} bytes"
                        )
                    fh.write(chunk)
    except urllib.error.HTTPError as e:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError(f"download HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError(f"download connection error from {url}: {e}") from e


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def probe_duration_s(path: str) -> float:
    """Return the duration of an mp4 in seconds via ffprobe.

    Raises RuntimeError if ffprobe is missing or the file is unreadable.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe not found on PATH") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed for {path}: {e.stderr.strip()}") from e

    raw = out.stdout.strip()
    if not raw:
        raise RuntimeError(f"ffprobe returned empty duration for {path}")
    return float(raw)


def gen_video(
    prompt: str,
    character_ref_url: str,
    out_path: str,
    model: str = DEFAULT_MODEL,
    max_poll_seconds: int = DEFAULT_MAX_POLL_SECONDS,
    interval: int = DEFAULT_POLL_INTERVAL,
    sleep_fn=time.sleep,
) -> dict:
    """Orchestrate submit -> poll -> fetch for one S2V shot.

    Args:
        prompt: Scene/action description for the character.
        character_ref_url: Remote URL of the character reference image.
        out_path: Local file path to write the downloaded mp4 to.
        model: Hailuo model id.
        max_poll_seconds: Total poll budget.
        interval: Seconds between polls.
        sleep_fn: Sleep injection point (override in tests).

    Returns:
        On Success: ``{"ok": True, "path": out_path, "task_id": ..., "file_id": ...}``
        On any failure: ``{"ok": False, "fallback": "still", "reason": ...,
                           "task_id": ... | None}``

    Fallback is triggered by ANY of:
      - submit HTTP error or base_resp != 0 (caught via RuntimeError)
      - poll timeout
      - task status Fail
      - Success but no file_id
      - fetch HTTP error / no download_url
      - downloaded file < _MIN_DOWNLOAD_BYTES
      - ffprobe cannot parse the downloaded file
    """
    try:
        task_id = submit(prompt, character_ref_url, model=model)
    except RuntimeError as e:
        return {
            "ok": False,
            "fallback": "still",
            "reason": f"submit failed: {e}",
            "task_id": None,
        }

    poll_result = poll(
        task_id,
        max_poll_seconds=max_poll_seconds,
        interval=interval,
        sleep_fn=sleep_fn,
    )
    if not poll_result.get("ok"):
        return {
            "ok": False,
            "fallback": "still",
            "reason": poll_result.get("reason", "poll did not return ok"),
            "task_id": task_id,
        }

    file_id = poll_result["file_id"]
    try:
        fetch(file_id, out_path)
    except RuntimeError as e:
        return {
            "ok": False,
            "fallback": "still",
            "reason": f"fetch failed: {e}",
            "task_id": task_id,
            "file_id": file_id,
        }

    # Verify download is non-trivially sized.
    try:
        size = os.path.getsize(out_path)
    except OSError as e:
        return {
            "ok": False,
            "fallback": "still",
            "reason": f"download size unreadable: {e}",
            "task_id": task_id,
            "file_id": file_id,
        }
    if size < _MIN_DOWNLOAD_BYTES:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {
            "ok": False,
            "fallback": "still",
            "reason": f"download too small: {size} bytes "
                      f"(<{_MIN_DOWNLOAD_BYTES} byte minimum)",
            "task_id": task_id,
            "file_id": file_id,
        }

    # Verify ffprobe can parse it (defends against partial/corrupt mp4).
    try:
        probe_duration_s(out_path)
    except RuntimeError as e:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return {
            "ok": False,
            "fallback": "still",
            "reason": f"ffprobe cannot parse download: {e}",
            "task_id": task_id,
            "file_id": file_id,
        }

    return {"ok": True, "path": out_path, "task_id": task_id, "file_id": file_id}