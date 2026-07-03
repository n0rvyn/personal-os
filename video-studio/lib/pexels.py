"""Pexels video search/download client (Phase 4, D-006).

Thin HTTP wrapper around the Pexels video-search API — used only by
`PexelsSource` (`lib/media_source.py`) to source real-life b-roll for the
"写实" (realistic) style. `search`/`download` take an injectable `fetcher`
so callers (and tests) never hit the network directly.

API shape (curl-verified, not from docs — see plan
`docs/06-plans/2026-07-03-video-studio-p4-pexels-plan.md`):
`GET https://api.pexels.com/videos/search?query=<q>&per_page=N&orientation=landscape`
with header `Authorization: <key>` (+ `User-Agent`, avoids 403). Response:
`{videos:[{id, duration, url(slug), video_files:[{quality, width, height,
link, file_type}]}]}`.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

_SEARCH_URL = "https://api.pexels.com/videos/search"
_USER_AGENT = "video-studio/0.0.1"


class PexelsError(Exception):
    """Raised for configuration errors (e.g. missing API key)."""


def _default_json_fetcher(url: str, headers: dict, _attempts: int = 3) -> dict:
    # Pexels intermittently IncompleteReads even small JSON responses; retry.
    req = urllib.request.Request(url, headers=headers)
    last_exc: Exception | None = None
    for _ in range(_attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except http.client.IncompleteRead as exc:
            last_exc = exc
            continue
    raise last_exc if last_exc is not None else PexelsError(f"search failed: {url}")


def _default_bytes_fetcher(url: str, _attempts: int = 3) -> bytes:
    # Large Pexels video streams IncompleteRead intermittently (server closes
    # early / Content-Length mismatch). Retry the whole fetch a few times; if it
    # still fails the caller's transient guard falls back to generation.
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    last_exc: Exception | None = None
    for _ in range(_attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except http.client.IncompleteRead as exc:
            last_exc = exc
            continue
    raise last_exc if last_exc is not None else PexelsError(f"download failed: {url}")


def search(query: str, api_key: str, per_page: int = 15, fetcher=_default_json_fetcher) -> list:
    """Search Pexels videos; returns the raw `videos` list (candidate dicts).

    Raises `PexelsError` when `api_key` is empty (fail-fast, not silent).
    Propagates `urllib.error.URLError`/`HTTPError` for transient HTTP
    failures — callers (`PexelsSource.fetch_clip`) catch those and fall back
    to generation (D-003), per MR#5.
    """
    if not api_key:
        raise PexelsError("Pexels API key missing — set PEXELS_API_KEY")

    params = urllib.parse.urlencode({
        "query": query,
        "per_page": per_page,
        "orientation": "landscape",
    })
    url = f"{_SEARCH_URL}?{params}"
    headers = {
        "Authorization": api_key,
        "User-Agent": _USER_AGENT,
    }
    data = fetcher(url, headers)
    return data.get("videos", [])


def best_video_file(video: dict, min_width: int = 1280) -> dict | None:
    """Pick the highest-quality `video_files` entry with width >= min_width."""
    candidates = [vf for vf in video.get("video_files", []) if vf.get("width", 0) >= min_width]
    if not candidates:
        return None
    return max(candidates, key=lambda vf: vf.get("width", 0))


def download(link: str, out_path: str, fetcher=_default_bytes_fetcher) -> None:
    """Download `link` to `out_path` (raw bytes write, no transcoding)."""
    data = fetcher(link)
    with open(out_path, "wb") as f:
        f.write(data)
