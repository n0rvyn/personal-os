"""Image helper for video-studio.

Wraps MiniMax image-01 to:
- Generate a series-level character reference portrait (1:1)
- Generate 16:9 stills with subject_reference locked to the character ref

All API calls go through `lib.mmclient.post_json` (Bearer auth, env-driven,
base_resp check, key-redacted logs). Downloaded files are written to a fixed
local path; we never trust the remote filename.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from . import mmclient


# image-01 occasionally returns base_resp=success but an empty image_urls
# (observed under rapid back-to-back calls in a long run). Retry a few times
# with backoff so one transient blank doesn't kill a 20-image pipeline.
_IMG_RETRIES = 4


def _request_image_url(payload: dict, timeout: int = 180, sleep_fn=time.sleep) -> str:
    """POST /v1/image_generation and return image_urls[0], retrying on a
    transient empty response or transient API error. Raises after _IMG_RETRIES."""
    last = "unknown"
    for attempt in range(_IMG_RETRIES):
        try:
            resp = mmclient.post_json("/v1/image_generation", payload, timeout=timeout)
            data = resp.get("data")
            urls = data.get("image_urls") if isinstance(data, dict) else None
            if isinstance(urls, list) and urls and isinstance(urls[0], str) and urls[0]:
                return urls[0]
            last = f"empty image_urls (base_resp={resp.get('base_resp')})"
        except RuntimeError as e:
            last = str(e)
        if attempt < _IMG_RETRIES - 1:
            _log(f"[media_image] image_generation transient: {last} "
                 f"— retry {attempt + 1}/{_IMG_RETRIES}")
            sleep_fn(2 * (attempt + 1))
    raise RuntimeError(
        f"MiniMax image_generation failed after {_IMG_RETRIES} attempts: {last}"
    )


# Unified style prefix for every generated still. Single style v1 per
# video-studio crystal: 风格 = 模块级 style 前缀（v1 全同一种，同模块内强一致）.
STYLE_PREFIX = (
    "cinematic, cool morning light, film grain, realistic, no text"
)


# Cap download size to defend against malicious/huge remote responses.
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _download(url: str, out_path: str, timeout: int = 120) -> None:
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
        raise RuntimeError(f"download HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"download connection error from {url}: {e}") from e


def gen_character_ref(prompt: str, out_path: str) -> str:
    """Generate a 1:1 character reference portrait and download it locally.

    Args:
        prompt: Subject description (e.g. "a Chinese male running coach, 35 yo,
            short hair, wearing a navy running jacket").
        out_path: Local file path to write the downloaded PNG to.

    Returns:
        The remote image URL (use it as `image_file` for `subject_reference`
        in subsequent calls).

    Raises:
        RuntimeError: on API failure, missing image_urls, or download failure.
    """
    payload = {
        "model": "image-01",
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "response_format": "url",
        "n": 1,
    }
    url = _request_image_url(payload)

    _log(f"[media_image] downloading character_ref to {out_path}")
    _download(url, out_path)
    return url


def gen_still(prompt: str, character_ref_url: str, out_path: str) -> str:
    """Generate a 16:9 still with subject_reference locked to character_ref_url.

    Args:
        prompt: Scene description (the character performing some action / in
            some setting). The unified STYLE_PREFIX is automatically prepended.
        character_ref_url: Remote URL of the character reference image (the
            return value of `gen_character_ref`).
        out_path: Local file path to write the downloaded PNG to.

    Returns:
        out_path (the local file path).

    Raises:
        RuntimeError: on API failure, missing image_urls, or download failure.
    """
    full_prompt = f"{STYLE_PREFIX}, {prompt}"
    payload = {
        "model": "image-01",
        "prompt": full_prompt,
        "aspect_ratio": "16:9",
        "response_format": "url",
        "n": 1,
        "subject_reference": [
            {"type": "character", "image_file": character_ref_url}
        ],
    }
    url = _request_image_url(payload)

    _log(f"[media_image] downloading still to {out_path}")
    _download(url, out_path)
    return out_path


# ---------------------------------------------------------------------------
# ffprobe-based dimension check (used by smoke test + future acceptance).
# ---------------------------------------------------------------------------


def probe_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of an image/video file via ffprobe.

    Raises RuntimeError if ffprobe is missing or the file is unreadable.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
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
        raise RuntimeError(
            f"ffprobe failed for {path}: {e.stderr.strip()}"
        ) from e

    raw = out.stdout.strip()
    if "x" not in raw:
        raise RuntimeError(f"ffprobe returned unexpected output: {raw!r}")
    w_s, h_s = raw.split("x", 1)
    return int(w_s), int(h_s)
