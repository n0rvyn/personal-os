"""Unit tests for lib.media_cache (test-FAIL-first contract — written before
lib/media_cache.py exists; collection must fail at import time).

Covers (Task 1-tests):
  - cache_key(): pure function, reproducible; endpoint/style_prefix/ref_url/
    render_dims all fold into the key; ref_url never leaks raw base64 into
    the key (it's hashed).
  - CachingMediaSeams: style_prefix write-through (MR-3) to `inner`; miss ->
    gen -> store; hit -> copy (inner not re-invoked); `fresh=True` bypasses
    the cache; return-value contract restored verbatim for all 4 seams
    (path / dict / int / url).
  - Atomic write + truncation guard: a zero-byte cache blob is treated as a
    miss (never "shipped").
  - hits/misses counters.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import media_cache  # noqa: E402
from lib.media_cache import CachingMediaSeams, cache_key  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeSeam:
    """Records calls; writes an identifiable file to out_path; returns the
    real per-seam contract value. `style_prefix`/`provider`/`voice_id`/`dry`
    are plain instance attributes (mirrors real MediaSeams)."""

    def __init__(self, provider="doubao", style_prefix="", voice_id=""):
        self.provider = provider
        self.style_prefix = style_prefix
        self.voice_id = voice_id
        self.dry = False
        self.calls = {"gen_still": [], "gen_video": [], "synth": [], "gen_character_ref": []}
        # Records the final prompt actually handed to the provider (i.e. after
        # any style_prefix prepend the seam itself performs) — proves the
        # wrapper doesn't strip style before delegating.
        self.last_final_prompt = {}

    def _final(self, prompt):
        p = f"{self.style_prefix}, {prompt}" if self.style_prefix else prompt
        return p

    def gen_still(self, prompt, ref_url, out_path):
        final = self._final(prompt)
        self.last_final_prompt["gen_still"] = final
        self.calls["gen_still"].append((prompt, ref_url, out_path))
        dst = os.path.splitext(out_path)[0] + ".jpg"
        Path(dst).write_bytes(f"still:{final}".encode("utf-8"))
        return dst

    def gen_video(self, prompt, ref_url, out_path):
        final = self._final(prompt)
        self.last_final_prompt["gen_video"] = final
        self.calls["gen_video"].append((prompt, ref_url, out_path))
        Path(out_path).write_bytes(f"video:{final}".encode("utf-8"))
        return {"ok": True, "path": out_path, "task_id": "fake"}

    def synth(self, text, out_path):
        self.calls["synth"].append((text, out_path))
        Path(out_path).write_bytes(f"audio:{text}".encode("utf-8"))
        return 5000

    def gen_character_ref(self, prompt, out_path):
        final = self._final(prompt)
        self.last_final_prompt["gen_character_ref"] = final
        self.calls["gen_character_ref"].append((prompt, out_path))
        Path(out_path).write_bytes(f"ref:{final}".encode("utf-8"))
        return "http://fake/ref"


# ---------------------------------------------------------------------------
# cache_key — pure function
# ---------------------------------------------------------------------------

def _base_kwargs(**overrides):
    kw = dict(
        provider="doubao",
        endpoint="ep-v1",
        style_prefix="写实电影",
        prompt="一个跑步者在清晨的公园",
        ref_url="",
        extra={"w": 1920, "h": 1080},
    )
    kw.update(overrides)
    return kw


def test_cache_key_reproducible():
    k1 = cache_key("still", **_base_kwargs())
    k2 = cache_key("still", **_base_kwargs())
    assert k1 == k2
    assert isinstance(k1, str) and len(k1) >= 32


def test_cache_key_endpoint_changes_key():
    k1 = cache_key("still", **_base_kwargs(endpoint="ep-v1"))
    k2 = cache_key("still", **_base_kwargs(endpoint="ep-v2"))
    assert k1 != k2

    k3 = cache_key("video", **_base_kwargs(endpoint="ep-v1"))
    k4 = cache_key("video", **_base_kwargs(endpoint="ep-v2"))
    assert k3 != k4


def test_cache_key_style_prefix_changes_key():
    k1 = cache_key("still", **_base_kwargs(style_prefix="写实电影"))
    k2 = cache_key("still", **_base_kwargs(style_prefix="简笔日漫"))
    assert k1 != k2


def test_cache_key_ref_url_changes_key_and_is_hashed():
    k1 = cache_key("still", **_base_kwargs(ref_url=""))
    k2 = cache_key("still", **_base_kwargs(ref_url="http://example.com/ref.png"))
    assert k1 != k2

    long_b64 = "data:image/png;base64," + ("A" * 5000)
    k3 = cache_key("still", **_base_kwargs(ref_url=long_b64))
    assert k3 != k1
    # The raw base64 payload must never appear verbatim in the key (it's hashed).
    assert "AAAA" not in k3
    assert len(k3) < len(long_b64)


def test_cache_key_render_dims_changes_key():
    k1 = cache_key("still", **_base_kwargs(extra={"w": 1920, "h": 1080}))
    k2 = cache_key("still", **_base_kwargs(extra={"w": 1280, "h": 720}))
    assert k1 != k2

    k3 = cache_key("video", **_base_kwargs(extra={"w": 1920, "h": 1080}))
    k4 = cache_key("video", **_base_kwargs(extra={"w": 1280, "h": 720}))
    assert k3 != k4


def test_cache_key_synth_is_function_of_provider_voice_text():
    k1 = cache_key(
        "synth", provider="minimax", endpoint="", style_prefix="",
        prompt="今天讲一讲配速", ref_url="", extra={"voice_id": "v1"},
    )
    k2 = cache_key(
        "synth", provider="minimax", endpoint="", style_prefix="",
        prompt="今天讲一讲配速换了", ref_url="", extra={"voice_id": "v1"},
    )
    assert k1 != k2  # text changed

    k3 = cache_key(
        "synth", provider="minimax", endpoint="", style_prefix="",
        prompt="今天讲一讲配速", ref_url="", extra={"voice_id": "v2"},
    )
    assert k1 != k3  # voice_id changed


# ---------------------------------------------------------------------------
# CachingMediaSeams — style_prefix write-through (MR-3)
# ---------------------------------------------------------------------------

def test_style_prefix_write_through_to_inner(tmp_path):
    inner = FakeSeam(style_prefix="")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    wrapper.style_prefix = "写实电影,"
    assert inner.style_prefix == "写实电影,"
    assert wrapper.style_prefix == "写实电影,"


def test_style_prefix_affects_gen_still_cache_key(tmp_path):
    cache_dir = tmp_path / "cache"

    inner_a = FakeSeam(style_prefix="")
    wrapper_a = CachingMediaSeams(inner_a, cache_dir, render_dims=(1920, 1080))
    wrapper_a.style_prefix = "写实电影"
    out_a = tmp_path / "a.jpg"
    wrapper_a.gen_still("一个跑步者", None, str(out_a))

    inner_b = FakeSeam(style_prefix="")
    wrapper_b = CachingMediaSeams(inner_b, cache_dir, render_dims=(1920, 1080))
    wrapper_b.style_prefix = "简笔日漫"
    out_b = tmp_path / "b.jpg"
    wrapper_b.gen_still("一个跑步者", None, str(out_b))

    # Different style_prefix -> different cache entries -> both actually generated.
    assert len(inner_a.calls["gen_still"]) == 1
    assert len(inner_b.calls["gen_still"]) == 1
    assert inner_a.last_final_prompt["gen_still"] != inner_b.last_final_prompt["gen_still"]
    assert "写实电影" in inner_a.last_final_prompt["gen_still"]
    assert "简笔日漫" in inner_b.last_final_prompt["gen_still"]


# ---------------------------------------------------------------------------
# CachingMediaSeams — miss -> gen -> store, hit -> copy, fresh bypass
# ---------------------------------------------------------------------------

def test_gen_still_miss_then_hit(tmp_path):
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    out1 = tmp_path / "shot1.jpg"
    r1 = wrapper.gen_still("跑步者清晨", None, str(out1))
    assert len(inner.calls["gen_still"]) == 1
    assert Path(r1).exists()
    content1 = Path(r1).read_bytes()

    out2 = tmp_path / "shot2.jpg"
    r2 = wrapper.gen_still("跑步者清晨", None, str(out2))
    # Still only 1 inner call — second was served from cache.
    assert len(inner.calls["gen_still"]) == 1
    assert Path(r2).exists()
    assert Path(r2).read_bytes() == content1
    assert wrapper.hits == 1
    assert wrapper.misses == 1


def test_gen_still_fresh_bypasses_cache(tmp_path):
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080), fresh=True)

    out1 = tmp_path / "shot1.jpg"
    wrapper.gen_still("跑步者清晨", None, str(out1))
    out2 = tmp_path / "shot2.jpg"
    wrapper.gen_still("跑步者清晨", None, str(out2))

    assert len(inner.calls["gen_still"]) == 2  # fresh -> always regenerate
    assert wrapper.misses == 2
    assert wrapper.hits == 0


def test_fresh_still_writes_back_to_cache(tmp_path):
    cache_dir = tmp_path / "cache"

    inner1 = FakeSeam(style_prefix="写实电影")
    wrapper1 = CachingMediaSeams(inner1, cache_dir, render_dims=(1920, 1080), fresh=True)
    wrapper1.gen_still("跑步者清晨", None, str(tmp_path / "shot1.jpg"))

    # Now query without fresh — should hit what fresh wrote back.
    inner2 = FakeSeam(style_prefix="写实电影")
    wrapper2 = CachingMediaSeams(inner2, cache_dir, render_dims=(1920, 1080), fresh=False)
    wrapper2.gen_still("跑步者清晨", None, str(tmp_path / "shot2.jpg"))
    assert len(inner2.calls["gen_still"]) == 0
    assert wrapper2.hits == 1


# ---------------------------------------------------------------------------
# CachingMediaSeams — return-value contract restored per seam
# ---------------------------------------------------------------------------

def test_gen_video_return_contract_restored_on_hit(tmp_path):
    inner = FakeSeam()
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    out1 = str(tmp_path / "clip1.mp4")
    r1 = wrapper.gen_video("镜头描述", "http://ref", out1)
    assert r1 == {"ok": True, "path": out1, "task_id": "fake"}

    out2 = str(tmp_path / "clip2.mp4")
    r2 = wrapper.gen_video("镜头描述", "http://ref", out2)
    assert len(inner.calls["gen_video"]) == 1  # second was a hit
    assert isinstance(r2, dict)
    assert r2["path"] == out2  # path rewritten to this call's out_path
    assert r2["ok"] is True
    assert Path(out2).exists()
    assert Path(out2).read_bytes() == Path(out1).read_bytes()


def test_synth_return_contract_restored_on_hit(tmp_path):
    inner = FakeSeam(voice_id="voice-1")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    out1 = str(tmp_path / "a1.mp3")
    r1 = wrapper.synth("今天讲一讲配速", out1)
    assert r1 == 5000

    out2 = str(tmp_path / "a2.mp3")
    r2 = wrapper.synth("今天讲一讲配速", out2)
    assert len(inner.calls["synth"]) == 1
    assert r2 == 5000
    assert Path(out2).exists()
    assert Path(out2).read_bytes() == Path(out1).read_bytes()


def test_gen_character_ref_return_contract_restored_on_hit(tmp_path):
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    out1 = str(tmp_path / "ref1.png")
    r1 = wrapper.gen_character_ref("主讲人物", out1)
    assert r1 == "http://fake/ref"

    out2 = str(tmp_path / "ref2.png")
    r2 = wrapper.gen_character_ref("主讲人物", out2)
    assert len(inner.calls["gen_character_ref"]) == 1
    assert r2 == "http://fake/ref"
    assert Path(out2).exists()
    assert Path(out2).read_bytes() == Path(out1).read_bytes()


def test_gen_still_hit_returns_path_with_correct_extension(tmp_path):
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))

    out1 = str(tmp_path / "shotA.png")  # requested .png, seam actually writes .jpg
    r1 = wrapper.gen_still("跑步者", None, out1)
    assert r1.endswith(".jpg")

    out2 = str(tmp_path / "shotB.png")
    r2 = wrapper.gen_still("跑步者", None, out2)
    assert len(inner.calls["gen_still"]) == 1
    assert r2.endswith(".jpg")
    assert Path(r2).exists()


# ---------------------------------------------------------------------------
# Atomic write + truncation guard
# ---------------------------------------------------------------------------

def test_zero_byte_cache_blob_is_treated_as_miss(tmp_path):
    cache_dir = tmp_path / "cache"
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, cache_dir, render_dims=(1920, 1080))

    out1 = tmp_path / "shot1.jpg"
    wrapper.gen_still("跑步者清晨", None, str(out1))
    assert len(inner.calls["gen_still"]) == 1

    # Corrupt every cache blob for this key: truncate to 0 bytes.
    blobs = [p for p in cache_dir.glob("*") if p.is_file() and not p.name.endswith(".meta.json")]
    assert blobs, "expected at least one cached blob after first gen_still call"
    for b in blobs:
        b.write_bytes(b"")

    out2 = tmp_path / "shot2.jpg"
    wrapper.gen_still("跑步者清晨", None, str(out2))
    # Truncated blob must be treated as a miss -> inner called again.
    assert len(inner.calls["gen_still"]) == 2
    assert Path(out2).stat().st_size > 0


def test_atomic_write_uses_tmp_then_replace(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, cache_dir, render_dims=(1920, 1080))

    real_replace = os.replace

    def boom(*a, **kw):
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr(media_cache.os, "replace", boom)

    out1 = tmp_path / "shot1.jpg"
    with pytest.raises(OSError):
        wrapper.gen_still("跑步者清晨", None, str(out1))

    monkeypatch.setattr(media_cache.os, "replace", real_replace)

    # No half-written *real* (non-.tmp) blob should have been left behind.
    leftover_real = [
        p for p in cache_dir.glob("*")
        if p.is_file() and not p.name.endswith(".tmp") and not p.name.endswith(".meta.json")
    ]
    assert leftover_real == []


# ---------------------------------------------------------------------------
# hits/misses counters
# ---------------------------------------------------------------------------

def test_hits_and_misses_counters(tmp_path):
    inner = FakeSeam(style_prefix="写实电影")
    wrapper = CachingMediaSeams(inner, tmp_path / "cache", render_dims=(1920, 1080))
    assert wrapper.hits == 0
    assert wrapper.misses == 0

    wrapper.gen_still("A", None, str(tmp_path / "a1.jpg"))
    assert wrapper.misses == 1
    assert wrapper.hits == 0

    wrapper.gen_still("A", None, str(tmp_path / "a2.jpg"))
    assert wrapper.misses == 1
    assert wrapper.hits == 1

    wrapper.gen_still("B", None, str(tmp_path / "b1.jpg"))
    assert wrapper.misses == 2
    assert wrapper.hits == 1
