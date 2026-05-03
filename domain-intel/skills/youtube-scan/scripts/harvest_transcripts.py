#!/usr/bin/env python3
"""Harvest transcripts from YouTube videos using Innertube API + HTML fallback.

Implements DP-001 Option A (mirror Lumina pattern):
  1. GET the watch page; regex-extract INNERTUBE_API_KEY
  2. If key found: POST to Innertube API (Android client) for caption tracks
  3. If key NOT found: fallback to HTML parsing of ytInitialPlayerResponse JSON
  4. Prefer user language list; fallback to ASR auto-captions

Ports lumina-backend/internal/collector/youtube_transcript.go:97-330.
No runtime dependency on Lumina.

Usage:
    python3 harvest_transcripts.py --input /tmp/candidates.json --output /tmp/transcripts.json
    python3 harvest_transcripts.py --video-id dQw4w9WgXcQ --lang en,zh-Hans

Output:
    stdout: JSON mapping video_id to {text, lang, segments} or {error: "..."}
    stderr: diagnostic messages
    Exit codes: 0 = success (even if some videos have no transcript)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

INNERTUBE_CLIENT_BODY = {
    "context": {
        "client": {
            "clientName": "ANDROID",
            "clientVersion": "20.10.38",
        }
    },
}

DEFAULT_LANGS = ["en", "zh-Hans", "zh", "zh-TW", "ja", "ko"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CaptionTrack:
    """A single caption track from YouTube."""
    base_url: str
    language_code: str
    name: str
    is_automatic: bool = False  # True if Kind == "asr" (auto-generated)


@dataclass
class Transcript:
    """A parsed transcript for a video."""
    video_id: str
    language: str
    text: str
    segments: list[dict] = field(default_factory=list)
    is_automatic: bool = False


# ---------------------------------------------------------------------------
# API key extraction (DP-001 Option A — Path 1)
# ---------------------------------------------------------------------------

def extract_api_key_from_html(html: str) -> Optional[str]:
    r"""Extract INNERTUBE_API_KEY from watch page HTML via regex.

    Pattern: "INNERTUBE_API_KEY":\s*"([a-zA-Z0-9_-]+)"
    """
    match = re.search(r'"INNERTUBE_API_KEY":\s*"([a-zA-Z0-9_-]+)"', html)
    if match:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# VideoID validation (S3 advisory — prevent injection before HTTP body use)
# ---------------------------------------------------------------------------

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def validate_video_id(video_id: str) -> str:
    """Validate and return video_id. Raises ValueError if invalid."""
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError(f"Invalid video_id: {video_id!r} — must match ^[A-Za-z0-9_-]{{11}}$")
    return video_id


# ---------------------------------------------------------------------------
# Caption track extraction — Innertube API (DP-001 Option A — Path 1)
# ---------------------------------------------------------------------------

def fetch_caption_tracks_innertube(
    video_id: str,
    api_key: str,
    timeout: float = 10.0,
) -> list[CaptionTrack]:
    """Fetch caption tracks via Innertube API (Android client)."""
    url = f"https://www.youtube.com/youtubei/v1/player?key={api_key}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "com.google.android.youtube/20.10.38",
    }
    payload = {**INNERTUBE_CLIENT_BODY, "videoId": video_id}

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    renderer = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    raw_tracks = renderer.get("captionTracks", [])

    tracks = []
    for raw in raw_tracks:
        base_url = raw.get("baseUrl", "")
        # Remove fmt=srv3 restriction appended to some URLs
        if "&fmt=srv3" in base_url:
            base_url = base_url.replace("&fmt=srv3", "", 1)
        name_text = ""
        name_obj = raw.get("name", {})
        if isinstance(name_obj, dict):
            name_text = name_obj.get("simpleText", "")
        tracks.append(CaptionTrack(
            base_url=base_url,
            language_code=raw.get("languageCode", ""),
            name=name_text,
            is_automatic=(raw.get("kind") == "asr"),
        ))

    return tracks


# ---------------------------------------------------------------------------
# Caption track extraction — HTML fallback (DP-001 Option A — Path 2)
# ---------------------------------------------------------------------------

def _extract_json_object(html: str, start: int) -> str:
    """Extract a balanced JSON object starting at position `start` using brace counting."""
    depth = 0
    i = start
    started = False
    while i < len(html):
        ch = html[i]
        if ch == "{" and not started:
            started = True
            depth = 1
            i += 1
            continue
        elif not started:
            i += 1
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start:i+1]
        elif ch in ('"', "'"):
            # Skip string literals to avoid counting braces inside strings
            q = ch
            i += 1
            while i < len(html):
                if html[i] == "\\":
                    i += 2
                    continue
                if html[i] == q:
                    break
                i += 1
        i += 1

    raise ValueError(f"Could not find closing brace for JSON starting at {start}")


def fetch_caption_tracks_html_fallback(
    html: str,
    timeout: float = 10.0,
) -> list[CaptionTrack]:
    """Parse caption tracks directly from ytInitialPlayerResponse JSON in HTML.

    This is the resilience path when Innertube API key extraction fails.
    No API key needed — uses HTML-baked JSON directly.
    """
    # Find ytInitialPlayerResponse JSON start
    match = re.search(r"ytInitialPlayerResponse\s*=\s*\{", html)
    if not match:
        raise ValueError("ytInitialPlayerResponse not found in HTML")

    start = match.end() - 1  # position of the '{' character
    json_str = _extract_json_object(html, start)
    data = json.loads(json_str)

    renderer = data.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    raw_tracks = renderer.get("captionTracks", [])

    tracks = []
    for raw in raw_tracks:
        # HTML fallback: base_url from HTML-baked JSON is directly usable (no API restriction)
        tracks.append(CaptionTrack(
            base_url=raw.get("baseUrl", ""),
            language_code=raw.get("languageCode", ""),
            name=raw.get("name", {}).get("simpleText", ""),
            is_automatic=(raw.get("kind") == "asr"),
        ))

    return tracks


# ---------------------------------------------------------------------------
# Transcript XML fetching + parsing
# ---------------------------------------------------------------------------

def fetch_transcript_xml(base_url: str, timeout: float = 10.0) -> list[dict]:
    """Fetch timedtext XML and parse into [{start, duration, text}] segments."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    resp = requests.get(base_url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    body = resp.content
    if not body:
        raise ValueError("Empty response from timedtext API (may require browser session)")

    # Parse XML
    root = ET.fromstring(body)
    segments = []
    for text_el in root.findall("text"):
        start = float(text_el.get("start", 0))
        dur = float(text_el.get("dur", 0))
        text = (text_el.text or "").strip()
        if text:
            segments.append({"start": start, "duration": dur, "text": text})

    return segments


# ---------------------------------------------------------------------------
# Full transcript fetch (DP-001 Option A — both paths)
# ---------------------------------------------------------------------------

def fetch_transcript(
    video_id: str,
    preferred_langs: Optional[list[str]] = None,
    timeout: float = 10.0,
    min_duration_minutes: float = 0,
) -> dict:
    """Fetch transcript for a video, trying Innertube API then HTML fallback.

    Args:
        video_id: 11-char YouTube video ID
        preferred_langs: list of language codes in priority order
        timeout: seconds per HTTP request
        min_duration_minutes: skip if transcript is shorter than this

    Returns:
        {"video_id": ..., "text": ..., "lang": ..., "segments": [...], "is_automatic": bool}
        or {"video_id": ..., "error": "..."}
    """
    video_id = validate_video_id(video_id)
    preferred_langs = preferred_langs or DEFAULT_LANGS

    # Step 1: GET the watch page for API key extraction
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(watch_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"video_id": video_id, "error": f"watch_page_fetch_failed: {e}"}

    html = resp.text

    # Step 2: Try Innertube API (Path 1)
    api_key = extract_api_key_from_html(html)
    caption_tracks: list[CaptionTrack] = []

    if api_key:
        try:
            caption_tracks = fetch_caption_tracks_innertube(video_id, api_key, timeout)
        except Exception as e:
            print(f"[{video_id}] Innertube API failed: {e} — falling back to HTML", file=sys.stderr)
            api_key = None  # Force fallback

    # Step 3: HTML fallback if Innertube failed or key not found (Path 2)
    if not api_key or not caption_tracks:
        try:
            caption_tracks = fetch_caption_tracks_html_fallback(html, timeout)
        except Exception as e:
            return {"video_id": video_id, "error": f"html_fallback_failed: {e}"}

    if not caption_tracks:
        return {"video_id": video_id, "error": "no_captions_available"}

    # Step 4: Select preferred language track
    selected_track: Optional[CaptionTrack] = None
    for lang in preferred_langs:
        for track in caption_tracks:
            if track.language_code.startswith(lang):
                selected_track = track
                break
        if selected_track:
            break

    # Step 5: Fall back to first available track (prefer manual over ASR)
    if not selected_track:
        manual = [t for t in caption_tracks if not t.is_automatic]
        asr = [t for t in caption_tracks if t.is_automatic]
        if manual:
            selected_track = manual[0]
        elif caption_tracks:
            selected_track = caption_tracks[0]

    if not selected_track:
        return {"video_id": video_id, "error": "no_captions_available"}

    # Step 6: Fetch and parse transcript XML
    try:
        segments = fetch_transcript_xml(selected_track.base_url, timeout)
    except Exception as e:
        return {"video_id": video_id, "error": f"transcript_xml_fetch_failed: {e}"}

    if not segments:
        return {"video_id": video_id, "error": "empty_transcript"}

    # Step 7: Duration filter
    if min_duration_minutes > 0:
        total_seconds = segments[-1]["start"] + segments[-1]["duration"]
        if total_seconds < min_duration_minutes * 60:
            return {"video_id": video_id, "error": f"transcript_too_short: {total_seconds:.0f}s < {min_duration_minutes}min"}

    # Step 8: Build full text
    full_text = " ".join(seg["text"] for seg in segments)

    return {
        "video_id": video_id,
        "text": full_text,
        "lang": selected_track.language_code,
        "is_automatic": selected_track.is_automatic,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def harvest_candidates(
    input_path: str,
    output_path: Optional[str] = None,
    preferred_langs: Optional[list[str]] = None,
    min_duration_minutes: float = 0,
) -> dict[str, dict]:
    """Load candidate video list and harvest transcripts for each.

    Input JSON: list of {video_id, ...} objects (from discover_videos.py output)
    Output: dict mapping video_id -> {text, lang, segments} or {error}
    """
    preferred_langs = preferred_langs or DEFAULT_LANGS

    with open(input_path, encoding="utf-8") as fh:
        candidates = json.load(fh)

    if not isinstance(candidates, list):
        raise ValueError(f"Expected list of candidates, got {type(candidates)}")

    results: dict[str, dict] = {}
    for i, cand in enumerate(candidates, 1):
        video_id = cand.get("video_id", "")
        if not video_id:
            print(f"[{i}/{len(candidates)}] Skipping candidate without video_id", file=sys.stderr)
            continue

        try:
            video_id = validate_video_id(video_id)
        except ValueError as e:
            print(f"[{i}/{len(candidates)}] {e}", file=sys.stderr)
            results[video_id] = {"video_id": video_id, "error": f"invalid_video_id: {e}"}
            continue

        print(f"[{i}/{len(candidates)}] Fetching transcript for {video_id}: {cand.get('title', '')}", file=sys.stderr)
        result = fetch_transcript(video_id, preferred_langs=preferred_langs, min_duration_minutes=min_duration_minutes)
        results[video_id] = result

        if "error" in result:
            print(f"  [{video_id}] Error: {result['error']}", file=sys.stderr)
        else:
            word_count = len(result["text"].split())
            print(f"  [{video_id}] OK — {len(result['segments'])} segments, {word_count} words, lang={result['lang']}", file=sys.stderr)

    success = sum(1 for r in results.values() if "error" not in r)
    print(f"Done: {success}/{len(results)} transcripts fetched", file=sys.stderr)

    if output_path:
        Path(output_path).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote results to {output_path}", file=sys.stderr)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Harvest YouTube transcripts via Innertube API + HTML fallback"
    )
    parser.add_argument(
        "--input",
        help="JSON file with list of {video_id, ...} candidates (from discover_videos.py)",
    )
    parser.add_argument(
        "--output",
        help="Output JSON file for results (default: stdout)",
    )
    parser.add_argument(
        "--video-id",
        help="Fetch a single video by ID (skip --input)",
    )
    parser.add_argument(
        "--lang",
        default="en,zh-Hans",
        help="Comma-separated preferred language codes (default: en,zh-Hans)",
    )
    parser.add_argument(
        "--min-duration-minutes",
        type=float,
        default=0,
        help="Minimum transcript duration in minutes (default: 0 = no filter)",
    )
    args = parser.parse_args()

    preferred_langs = [l.strip() for l in args.lang.split(",") if l.strip()]

    if args.video_id:
        result = fetch_transcript(
            args.video_id,
            preferred_langs=preferred_langs,
            min_duration_minutes=args.min_duration_minutes,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not args.input:
        parser.error("Either --input or --video-id is required")

    results = harvest_candidates(
        args.input,
        output_path=args.output,
        preferred_langs=preferred_langs,
        min_duration_minutes=args.min_duration_minutes,
    )

    if not args.output:
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
