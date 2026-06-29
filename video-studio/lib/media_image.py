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


# Unified style prefix for every generated still. The prefix is HALF of the
# series look (the other half is the color grade); both live in lib.grade so
# they're locked together at the column level (POC5). Sourced from the locked
# SERIES_LOOK so the pictures are generated toward the same look the grade locks.
from .grade import style_prefix as _series_style_prefix

STYLE_PREFIX = _series_style_prefix()


# Cap download size to defend against malicious/huge remote responses.
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _ext_for_magic(head: bytes) -> str | None:
    """Real image extension from leading magic bytes, or None if unrecognised.
    MiniMax returns JPEG bytes even when we ask to save as .png — we honour the
    real type instead of letting the on-disk filename lie about the format."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:4] in (b"GIF8",):
        return ".gif"
    return None


def _download(url: str, out_path: str, timeout: int = 120) -> str:
    """Download `url`, save it under a path whose extension matches the REAL
    content type (sniffed from magic bytes — the remote often sends JPEG for a
    .png request), and return that actual path. Streams in chunks with a size
    cap so we can abort early on oversize responses.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "video-studio/0.0.1"})
    written = 0
    dest = out_path
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            first = resp.read(64 * 1024)
            ext = _ext_for_magic(first[:16])
            if ext:
                stem, cur = os.path.splitext(out_path)
                if cur.lower() != ext:
                    dest = stem + ext
            with open(dest, "wb") as fh:
                chunk = first
                while chunk:
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        fh.close()
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                        raise RuntimeError(
                            f"download from {url} exceeded {_MAX_DOWNLOAD_BYTES} bytes"
                        )
                    fh.write(chunk)
                    chunk = resp.read(64 * 1024)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"download HTTP {e.code} from {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"download connection error from {url}: {e}") from e
    return dest


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
    _download(url, out_path)  # saved under its real extension; ref is used via url
    return url


def gen_still(prompt: str, character_ref_url: str | None, out_path: str) -> str:
    """Generate a 16:9 still. With a character_ref_url, locks the subject via
    subject_reference (人物镜). With None, generates a plain environment/detail
    shot (空镜 — track, pace chart, running shoes, watch…) so the video isn't
    always a talking-head.

    Args:
        prompt: Scene description. The unified STYLE_PREFIX is auto-prepended.
        character_ref_url: Character ref URL, or None for a character-less shot.
        out_path: Local file path to write the downloaded PNG to.

    Returns:
        The ACTUAL local path written — its extension matches the real content
        type (e.g. `.jpg` when the remote returns JPEG for a `.png` request), so
        callers must use this return value, not the path they passed in.

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
    }
    if character_ref_url:
        payload["subject_reference"] = [
            {"type": "character", "image_file": character_ref_url}
        ]
    url = _request_image_url(payload)

    _log(f"[media_image] downloading still to {out_path}")
    return _download(url, out_path)


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
