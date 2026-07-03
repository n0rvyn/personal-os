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

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import media_source  # noqa: E402
from lib import pexels  # noqa: E402
from lib.media_source import GenerateSource, LibrarySource, MediaRouter, PexelsSource  # noqa: E402


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


# ---------------------------------------------------------------------------
# PexelsSource — Phase 4 (D-006/D-007/D-008), FAIL-first until Task 1-impl
# ---------------------------------------------------------------------------

_CAND_LONG = {
    "id": 8459963,
    "duration": 9,
    "url": "https://www.pexels.com/video/runner-on-track-8459963/",
    "video_files": [
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/long.mp4"},
    ],
}
_CAND_SHORT = {
    "id": 111,
    "duration": 3,
    "url": "https://www.pexels.com/video/runner-on-track2-111/",
    "video_files": [
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/short.mp4"},
    ],
}
_CAND_BANNED = {
    "id": 222,
    "duration": 9,
    "url": "https://www.pexels.com/video/coding-laptop-office-222/",
    "video_files": [
        {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/banned.mp4"},
    ],
}


class FakeLedger:
    """Records seen/record calls; mirrors lib.visual_ledger.VisualLedger's id API."""

    def __init__(self, pre_seen=()):
        self._ids = set(pre_seen)

    def seen_id(self, vid) -> bool:
        return vid in self._ids

    def record_id(self, vid) -> None:
        self._ids.add(vid)


class FakeRunFf:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, label=None):
        self.calls.append((cmd, label))


def test_pexels_source_can_be_instantiated_without_typeerror():
    # MR#2: PexelsSource must implement the abstract `fetch` method, else
    # MediaRouter/from_config construction (which relies on instantiating
    # every configured source) raises TypeError at runtime.
    src = PexelsSource(api_key="sk-test")
    assert src is not None


def test_pexels_source_fetch_returns_none_not_a_still_asset(tmp_path):
    # MR#2: Pexels only serves clips, never still assets.
    src = PexelsSource(api_key="sk-test")
    result = src.fetch("a prompt", None, str(tmp_path / "out.jpg"))
    assert result is None


def test_fetch_clip_duration_guard_picks_the_longer_candidate_not_the_short_one(monkeypatch):
    # D-008 THE trap: never pick a candidate shorter than the window duration
    # (that would force a loop/hold to fill the beat).
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_SHORT, _CAND_LONG])
    monkeypatch.setattr(pexels, "download", lambda link, out_path: Path(out_path).write_bytes(b"x"))

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}

    out_path = "/tmp/pexels_test_out.mp4"
    result = src.fetch_clip(window, 6.0, out_path, run_ff, grade=None, ledger=None)

    assert result == out_path
    assert len(run_ff.calls) == 1
    cmd, _label = run_ff.calls[0]
    assert "https://pexels.com/long.mp4".replace("https://pexels.com", "") not in " ".join(cmd)
    # The dur=3 short candidate must never be chosen/downloaded.
    assert "short" not in " ".join(cmd)


def test_fetch_clip_all_candidates_too_short_returns_none(monkeypatch):
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_SHORT])
    called = {"download": False}

    def fake_download(link, out_path):
        called["download"] = True

    monkeypatch.setattr(pexels, "download", fake_download)

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out2.mp4", run_ff, grade=None, ledger=None)

    assert result is None
    assert called["download"] is False
    assert run_ff.calls == []


def test_fetch_clip_rerank_excludes_banned_slug(monkeypatch):
    # L2: coding/office slug must be filtered out by rerank_candidates (Phase 3)
    # even though its duration would otherwise qualify.
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_BANNED, _CAND_LONG])
    monkeypatch.setattr(pexels, "download", lambda link, out_path: Path(out_path).write_bytes(b"x"))

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out3.mp4", run_ff, grade=None, ledger=None)

    assert result is not None
    cmd, _label = run_ff.calls[0]
    assert "banned" not in " ".join(cmd)


def test_fetch_clip_dedup_skips_seen_id_selects_next_unused(monkeypatch):
    # DP-P4-002: dedup by Pexels id — a candidate already recorded in the
    # ledger must be skipped in favor of the next qualifying candidate.
    cand_long_2 = {
        "id": 333,
        "duration": 10,
        "url": "https://www.pexels.com/video/runner-on-track3-333/",
        "video_files": [
            {"quality": "hd", "width": 1920, "height": 1080, "link": "https://pexels.com/long2.mp4"},
        ],
    }
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_LONG, cand_long_2])
    monkeypatch.setattr(pexels, "download", lambda link, out_path: Path(out_path).write_bytes(b"x"))

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}
    ledger = FakeLedger(pre_seen={8459963})  # _CAND_LONG's id already used

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out4.mp4", run_ff, grade=None, ledger=ledger)

    assert result is not None
    cmd, _label = run_ff.calls[0]
    assert "long2" in " ".join(cmd)


def test_fetch_clip_download_uses_video_clip_cmd_without_src_dur(monkeypatch, tmp_path):
    # MR#1: fetch_clip must call ffcmd.video_clip_cmd WITHOUT src_dur, so
    # actual_dur == dur_s exactly (not min(src_dur, WINDOW_MAX)).
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_LONG])

    downloaded = {}

    def fake_download(link, out_path):
        downloaded["link"] = link
        downloaded["out_path"] = out_path
        Path(out_path).write_bytes(b"x")

    monkeypatch.setattr(pexels, "download", fake_download)

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    out_path = str(tmp_path / "beat_sub0.mp4")
    window = {"query": "runner on track"}

    result = src.fetch_clip(window, 6.0, out_path, run_ff, grade=None, ledger=None)

    assert result == out_path
    assert len(run_ff.calls) == 1
    cmd, _label = run_ff.calls[0]
    assert "-loop" not in cmd, "sub-clip download must not be treated as a still (no -loop)"
    assert "-t" in cmd
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "6.0", "actual_dur must equal dur_s exactly (MR#1: no src_dur passed)"
    assert downloaded["link"] == "https://pexels.com/long.mp4"


def test_fetch_clip_no_query_returns_none_and_never_calls_search(monkeypatch):
    # MR#6: a window with only "prompt" (no "query") must NOT search using
    # the abstract generation prompt — that is exactly the D-007 office/code
    # drift the query-writer step was built to avoid.
    search_called = {"yes": False}

    def fake_search(q, key, per_page=15):
        search_called["yes"] = True
        return []

    monkeypatch.setattr(pexels, "search", fake_search)

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"prompt": "a scene about pacing strategy"}

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out5.mp4", run_ff, grade=None, ledger=None)

    assert result is None
    assert search_called["yes"] is False


def test_fetch_clip_transient_http_error_returns_none_not_raise(monkeypatch):
    # MR#5: transient HTTP failures (5xx/timeout/403) must fall back to
    # generation, not crash a paid render.
    import urllib.error

    def fake_search(q, key, per_page=15):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(pexels, "search", fake_search)

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out6.mp4", run_ff, grade=None, ledger=None)

    assert result is None
    assert run_ff.calls == []


def test_fetch_clip_no_key_raises_pexels_error_fail_fast():
    # MR#5: missing key is a configuration error, not a transient failure —
    # must raise (fail-fast), distinct from the transient-HTTP None path.
    src = PexelsSource(api_key="")
    run_ff = FakeRunFf()
    window = {"query": "runner on track"}

    with pytest.raises(pexels.PexelsError):
        src.fetch_clip(window, 6.0, "/tmp/pexels_test_out7.mp4", run_ff, grade=None, ledger=None)


def test_fetch_clip_records_ledger_id_on_hit(monkeypatch):
    # MR#3: a chosen candidate's id must be recorded in the ledger so a later
    # window in the same episode (or a later episode) doesn't reuse it.
    monkeypatch.setattr(pexels, "search", lambda q, key, per_page=15: [_CAND_LONG])
    monkeypatch.setattr(pexels, "download", lambda link, out_path: Path(out_path).write_bytes(b"x"))

    src = PexelsSource(api_key="sk-test")
    run_ff = FakeRunFf()
    ledger = FakeLedger()
    window = {"query": "runner on track"}

    assert ledger.seen_id(8459963) is False

    result = src.fetch_clip(window, 6.0, "/tmp/pexels_test_out8.mp4", run_ff, grade=None, ledger=ledger)

    assert result is not None
    assert ledger.seen_id(8459963) is True


# ---------------------------------------------------------------------------
# Task 3: LibrarySource.from_config(cfg, style) with kind "pexels" -> PexelsSource
# ---------------------------------------------------------------------------


class _FakeCfgPexels:
    library = {"写实电影": "pexels"}


def test_from_config_pexels_kind_returns_pexels_source(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "sk-env-test")
    src = LibrarySource.from_config(_FakeCfgPexels(), "写实电影")
    assert isinstance(src, PexelsSource)


def test_from_config_pexels_kind_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "sk-env-test-2")
    src = LibrarySource.from_config(_FakeCfgPexels(), "写实电影")
    assert src.api_key == "sk-env-test-2"


def test_from_config_pexels_kind_no_key_constructs_without_raising(monkeypatch):
    # Construction must succeed even without a key (fail-fast happens at
    # fetch_clip time, not construction — MR#5).
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    src = LibrarySource.from_config(_FakeCfgPexels(), "写实电影")
    assert isinstance(src, PexelsSource)
    assert src.api_key == ""
