"""Unit tests for lib.pexels (Phase 4 — FAIL-first, written before lib/pexels.py
exists; collection must fail at import time until Task 1-impl lands).

Covers (plan Task 1-tests):
  - search() builds the correct URL (orientation=landscape) + headers
    (Authorization + User-Agent), and parses the real Pexels response shape
    (`{videos:[{id,duration,url,video_files:[{quality,width,height,link}]}]}`,
    per the curl-verified structure in the plan) into a candidate list.
  - no api_key -> PexelsError (not silent).
  - best_video_file() picks the highest-quality link >= min_width.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import pexels  # noqa: E402

# Real-shaped fixture (curl-verified in the plan): id 8459963, dur=9s, 1920x1080,
# slug "female-athlete-warming-up-on-a-running-track".
_REAL_VIDEO = {
    "id": 8459963,
    "duration": 9,
    "url": "https://www.pexels.com/video/female-athlete-warming-up-on-a-running-track-8459963/",
    "video_files": [
        {"quality": "sd", "width": 640, "height": 360, "link": "https://pexels.com/sd.mp4", "file_type": "video/mp4"},
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/hd.mp4", "file_type": "video/mp4"},
        {"quality": "hd", "width": 1280, "height": 720, "link": "https://pexels.com/hd720.mp4", "file_type": "video/mp4"},
    ],
}


def _fake_response(videos):
    return {"videos": videos}


def test_search_builds_correct_url_and_headers():
    captured = {}

    def fake_fetcher(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return _fake_response([_REAL_VIDEO])

    result = pexels.search("runner on track", "sk-test-key", per_page=15, fetcher=fake_fetcher)

    assert "query=" in captured["url"]
    assert "runner" in captured["url"]
    assert "orientation=landscape" in captured["url"]
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "sk-test-key"
    assert "user-agent" in headers
    assert result == [_REAL_VIDEO]


def test_search_parses_candidates_with_id_duration_url_video_files():
    def fake_fetcher(url, headers):  # noqa: ARG001
        return _fake_response([_REAL_VIDEO])

    result = pexels.search("runner on track", "sk-test-key", fetcher=fake_fetcher)

    assert len(result) == 1
    cand = result[0]
    assert cand["id"] == 8459963
    assert cand["duration"] == 9
    assert "video_files" in cand


def test_search_no_key_raises_pexels_error():
    def fake_fetcher(url, headers):  # noqa: ARG001
        raise AssertionError("must not call HTTP when no key")

    with pytest.raises(pexels.PexelsError):
        pexels.search("runner on track", "", fetcher=fake_fetcher)


def test_best_video_file_picks_highest_quality_over_min_width():
    vf = pexels.best_video_file(_REAL_VIDEO, min_width=1280)

    assert vf is not None
    assert vf["width"] >= 1280
    # The highest-width candidate meeting the floor should win (1920 over 1280).
    assert vf["link"] == "https://pexels.com/hd.mp4"


def test_best_video_file_none_when_no_candidate_meets_min_width():
    video = {"video_files": [{"quality": "sd", "width": 640, "height": 360, "link": "x"}]}

    vf = pexels.best_video_file(video, min_width=1280)

    assert vf is None


def test_download_writes_fetched_bytes_to_out_path(tmp_path):
    out_path = tmp_path / "clip.mp4"

    def fake_fetcher(url):  # noqa: ARG001
        return b"fake-mp4-bytes"

    pexels.download("https://pexels.com/hd.mp4", str(out_path), fetcher=fake_fetcher)

    assert out_path.exists()
    assert out_path.read_bytes() == b"fake-mp4-bytes"
