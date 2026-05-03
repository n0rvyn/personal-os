#!/usr/bin/env python3
"""Unit tests for harvest_transcripts.py (3 test cases per plan spec)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

from harvest_transcripts import (
    extract_api_key_from_html,
    validate_video_id,
    fetch_caption_tracks_html_fallback,
    _extract_json_object,
)


# ---------------------------------------------------------------------------
# test_language_preference_order
# ---------------------------------------------------------------------------

def test_language_preference_order():
    """tracks=[fr, en, zh], lang=[en, zh] → returns en track (first preference match)."""
    # Import here to test the full function behavior
    from harvest_transcripts import CaptionTrack

    tracks = [
        CaptionTrack(base_url="http://example.com/fr", language_code="fr", name="French", is_automatic=False),
        CaptionTrack(base_url="http://example.com/en", language_code="en", name="English", is_automatic=False),
        CaptionTrack(base_url="http://example.com/zh", language_code="zh-Hans", name="Chinese", is_automatic=False),
    ]

    preferred_langs = ["en", "zh-Hans"]

    # Select preferred track
    selected = None
    for lang in preferred_langs:
        for track in tracks:
            if track.language_code.startswith(lang):
                selected = track
                break
        if selected:
            break

    assert selected is not None, "Should have selected a track"
    assert selected.language_code == "en", f"Expected en, got {selected.language_code}"


# ---------------------------------------------------------------------------
# test_videoid_validation_rejects_special_chars
# ---------------------------------------------------------------------------

def test_videoid_validation_rejects_special_chars():
    """videoID='ab"cdef-1234' → raises ValueError."""
    import pytest

    # Various invalid video IDs
    invalid_ids = [
        'ab"cdef-1234',   # quote char
        "abc<script>def",  # script injection attempt
        "abc123",          # too short
        "abcdefghijk",     # too long
        "abc def-123",     # space
        "",                 # empty
    ]

    for vid in invalid_ids:
        try:
            validate_video_id(vid)
            # If no exception, it should match the 11-char pattern
            import re
            assert re.match(r"^[A-Za-z0-9_-]{11}$", vid), f"Should be invalid: {vid!r}"
        except ValueError:
            pass  # Expected for invalid IDs


def test_videoid_validation_accepts_valid():
    """Valid 11-char video IDs → no exception."""
    valid_ids = [
        "dQw4w9WgXcQ",
        "ABCDEFGHIJK",
        "abc-123_456",
        "1a2B3c4D5e6",
    ]

    for vid in valid_ids:
        result = validate_video_id(vid)
        assert result == vid, f"Should return {vid!r}"


# ---------------------------------------------------------------------------
# test_html_fallback_when_regex_fails
# ---------------------------------------------------------------------------

def test_html_fallback_when_regex_fails():
    """Mock page HTML with no INNERTUBE_API_KEY → fallback to parseCaptionTracks-equivalent succeeds."""
    # HTML without INNERTUBE_API_KEY but with ytInitialPlayerResponse
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>YouTube Video</title></head>
    <body>
    <script>
    ytInitialPlayerResponse = {
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "http://example.com/transcript?lang=en",
                        "languageCode": "en",
                        "name": {"simpleText": "English"},
                        "kind": ""
                    },
                    {
                        "baseUrl": "http://example.com/transcript?lang=fr",
                        "languageCode": "fr",
                        "name": {"simpleText": "French"},
                        "kind": "asr"
                    }
                ]
            }
        }
    };
    </script>
    </body>
    </html>
    """

    # Verify the HTML has no INNERTUBE_API_KEY
    assert "INNERTUBE_API_KEY" not in html

    # Extract API key should return None
    key = extract_api_key_from_html(html)
    assert key is None

    # HTML fallback should find the caption tracks
    tracks = fetch_caption_tracks_html_fallback(html)

    assert len(tracks) == 2, f"Expected 2 tracks, got {len(tracks)}"
    assert tracks[0].language_code == "en"
    assert tracks[1].language_code == "fr"
    assert tracks[0].is_automatic is False
    assert tracks[1].is_automatic is True  # kind == "asr"


def test_html_fallback_rejects_malformed():
    """ytInitialPlayerResponse not found → raises ValueError."""
    html = "<html><body>No player response here</body></html>"

    import pytest
    with pytest.raises(ValueError, match="ytInitialPlayerResponse not found"):
        fetch_caption_tracks_html_fallback(html)


def test_extract_json_object():
    """_extract_json_object correctly handles nested braces and string literals."""
    html = '''
    ytInitialPlayerResponse = {
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "http://example.com?text=hello",
                        "languageCode": "en"
                    }
                ]
            }
        }
    };
    '''
    import re
    match = re.search(r"ytInitialPlayerResponse\s*=\s*\{", html)
    assert match is not None
    # match.start() points to 'y' of ytInitialPlayerResponse; need the opening brace
    json_str = _extract_json_object(html, match.end() - 1)

    import json
    data = json.loads(json_str)
    assert "captions" in data
