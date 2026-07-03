"""Unit tests for lib.media_source (test-FAIL-first contract — written before
lib/media_source.py exists; collection must fail at import time).

Covers:
  - GenerateSource.fetch / fetch_video wrap seams.gen_still / seams.gen_video,
    never miss (never return None).
  - MediaRouter routes library-first / generate-fallback (D-003):
      no library -> generate directly
      library hit (fetch returns a path) -> router returns it, does NOT call
        generate
      library miss (fetch returns None) -> router falls back to generate
  - gen_video routing: library has no fetch_video (or returns None) -> falls
    back to generate.fetch_video.
  - set_style records the router's per-episode style (D-005): generate side
    still relies on seams.style_prefix for style-awareness; set_style itself
    only needs to be observable on the router.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.media_source import GenerateSource, LibrarySource, MediaRouter  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeSeams:
    """Records calls; returns deterministic sentinel values."""

    def __init__(self):
        self.gen_still_calls = []
        self.gen_video_calls = []
        self.style_prefix = "写实电影风"

    def gen_still(self, prompt, ref_url, out_path):
        self.gen_still_calls.append((prompt, ref_url, out_path))
        return out_path  # "generated" path

    def gen_video(self, prompt, ref_url, out_path):
        self.gen_video_calls.append((prompt, ref_url, out_path))
        return {"ok": True, "path": out_path, "task_id": "fake-task"}


class FakeLibraryHit:
    """Library source stub that always hits."""

    def __init__(self, hit_path="stub://library-hit.jpg"):
        self.hit_path = hit_path
        self.fetch_calls = []

    def fetch(self, prompt, ref_url, out_path):
        self.fetch_calls.append((prompt, ref_url, out_path))
        return self.hit_path

    def fetch_video(self, prompt, ref_url, out_path):
        return None


class FakeLibraryMiss:
    """Library source stub that always misses (empty pool)."""

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, prompt, ref_url, out_path):
        self.fetch_calls.append((prompt, ref_url, out_path))
        return None

    def fetch_video(self, prompt, ref_url, out_path):
        return None


# ---------------------------------------------------------------------------
# GenerateSource
# ---------------------------------------------------------------------------

def test_generate_source_fetch_calls_seams_gen_still_and_returns_path():
    seams = FakeSeams()
    src = GenerateSource(seams)

    result = src.fetch("a prompt", "ref://x", "/tmp/out.jpg")

    assert result == "/tmp/out.jpg"
    assert seams.gen_still_calls == [("a prompt", "ref://x", "/tmp/out.jpg")]


def test_generate_source_fetch_never_misses():
    seams = FakeSeams()
    src = GenerateSource(seams)

    result = src.fetch("prompt", None, "/tmp/out.jpg")

    assert result is not None


def test_generate_source_fetch_video_calls_seams_gen_video_and_returns_dict():
    seams = FakeSeams()
    src = GenerateSource(seams)

    result = src.fetch_video("a prompt", "ref://x", "/tmp/out.mp4")

    assert result == {"ok": True, "path": "/tmp/out.mp4", "task_id": "fake-task"}
    assert seams.gen_video_calls == [("a prompt", "ref://x", "/tmp/out.mp4")]


# ---------------------------------------------------------------------------
# MediaRouter — gen_still routing
# ---------------------------------------------------------------------------

def test_router_no_library_calls_generate_directly():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    router = MediaRouter(generate=generate, library=None)

    result = router.gen_still("prompt", "ref", "/tmp/out.jpg")

    assert result == "/tmp/out.jpg"
    assert len(seams.gen_still_calls) == 1


def test_router_library_hit_returns_library_path_without_calling_generate():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    library = FakeLibraryHit(hit_path="/library/stock.jpg")
    router = MediaRouter(generate=generate, library=library)

    result = router.gen_still("prompt", "ref", "/tmp/out.jpg")

    assert result == "/library/stock.jpg"
    assert len(library.fetch_calls) == 1
    assert seams.gen_still_calls == [], "generate must NOT be called when library hits"


def test_router_library_miss_falls_back_to_generate():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    library = FakeLibraryMiss()
    router = MediaRouter(generate=generate, library=library)

    result = router.gen_still("prompt", "ref", "/tmp/out.jpg")

    assert result == "/tmp/out.jpg"
    assert len(library.fetch_calls) == 1, "library must be tried first"
    assert len(seams.gen_still_calls) == 1, "generate must be tried as fallback"


# ---------------------------------------------------------------------------
# MediaRouter — gen_video routing
# ---------------------------------------------------------------------------

def test_router_gen_video_no_library_calls_generate():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    router = MediaRouter(generate=generate, library=None)

    result = router.gen_video("prompt", "ref", "/tmp/out.mp4")

    assert result == {"ok": True, "path": "/tmp/out.mp4", "task_id": "fake-task"}
    assert len(seams.gen_video_calls) == 1


def test_router_gen_video_library_has_no_fetch_video_hit_falls_back_to_generate():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    library = FakeLibraryHit(hit_path="/library/stock.jpg")  # fetch_video -> None
    router = MediaRouter(generate=generate, library=library)

    result = router.gen_video("prompt", "ref", "/tmp/out.mp4")

    assert result == {"ok": True, "path": "/tmp/out.mp4", "task_id": "fake-task"}
    assert len(seams.gen_video_calls) == 1, "generate.fetch_video must be the fallback"


# ---------------------------------------------------------------------------
# set_style
# ---------------------------------------------------------------------------

def test_set_style_records_current_episode_style():
    seams = FakeSeams()
    generate = GenerateSource(seams)
    router = MediaRouter(generate=generate, library=None)

    router.set_style("简笔日漫")

    assert router.style == "简笔日漫"
    # Style-awareness for generation lives on seams.style_prefix (D-005) —
    # set_style on the router does not need to mutate seams itself, but
    # generate must still be reachable and unaffected by the router's style
    # bookkeeping.
    result = router.gen_still("prompt", "ref", "/tmp/out.jpg")
    assert result == "/tmp/out.jpg"
    assert seams.style_prefix == "写实电影风"


def test_library_source_stub_kind_hits_with_real_decodable_jpg(tmp_path):
    out_path = tmp_path / "out.jpg"
    src = LibrarySource(kind="stub", style="写实电影风")

    result = src.fetch("prompt", None, str(out_path))

    assert result is not None
    assert Path(result).exists(), "stub hit must copy a real, decodable file to disk"
    assert Path(result).stat().st_size > 1000, "stub asset must be a real jpg, not a sentinel"


def test_library_source_none_kind_always_misses(tmp_path):
    out_path = tmp_path / "out.jpg"
    src = LibrarySource(kind="none", style="写实电影风")

    result = src.fetch("prompt", None, str(out_path))

    assert result is None
