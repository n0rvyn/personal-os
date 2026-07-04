"""E2E test for the offline pool (Phase 6 acceptance spine).

Proves `lib.offline_pool.PoolIndex` + `lib.media_source.OfflinePoolSource`
actually reuse on-disk assets ACROSS episodes (fresh instance, same pool_dir)
— the bug class where in-memory tests pass but the disk round-trip is broken.

Generation is STUBBED (hash-color PNG, $0) but ffmpeg and the on-disk pool
are REAL: no mocking of run_ff, PoolIndex, or OfflinePoolSource.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from shutil import which

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.media_source import OfflinePoolSource  # noqa: E402

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")


def _have_ffmpeg() -> bool:
    return which(FFMPEG_BIN) is not None or Path(os.path.expanduser(FFMPEG_BIN)).exists()


pytestmark = pytest.mark.skipif(not _have_ffmpeg(), reason="FFMPEG_BIN not available")


def _run_ff(argv, label, timeout=120):
    ffmpeg = os.path.expanduser(FFMPEG_BIN)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + list(argv)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg [{label}] exit={proc.returncode}\n{proc.stderr}")


class StubGenerate:
    """Stands in for GenerateSource: writes a REAL decodable PNG, counts calls."""

    def __init__(self):
        self.calls = 0

    def fetch(self, prompt, ref, out):
        self.calls += 1
        h = abs(hash(prompt)) % 0xFFFFFF
        color = f"0x{h:06X}"
        _run_ff(
            ["-f", "lavfi", "-i", f"color=c={color}:s=320x180", "-frames:v", "1", out],
            "stub-gen",
        )
        return out


def _win(query, prompt):
    return {"query": query, "prompt": prompt, "with_character": False}


def test_episode1_all_miss_then_episode2_reuses_from_disk(tmp_path):
    pool_dir = tmp_path / "_pool" / "简笔日漫"

    # --- Episode 1: three distinct-query windows, all pool misses. ---
    gen1 = StubGenerate()
    src1 = OfflinePoolSource(pool_dir, "简笔日漫", gen1)

    windows = [
        _win("runner on mountain trail", "跑者在山路上奔跑"),
        _win("runner sprinting on track", "跑者在跑道上冲刺"),
        _win("runner drinking water station", "跑者在补给站喝水"),
    ]

    ep1_clips = []
    for i, win in enumerate(windows):
        out = str(tmp_path / f"ep1_clip{i}.mp4")
        result = src1.fetch_clip(win, 3.0, out, _run_ff)
        assert result == out
        ep1_clips.append(out)

    assert gen1.calls == 3, "all three episode-1 windows must MISS and generate"

    for clip in ep1_clips:
        p = Path(clip)
        assert p.exists() and p.stat().st_size > 0

    pool_pngs = sorted(pool_dir.glob("pool_*.png"))
    assert len(pool_pngs) == 3, f"expected 3 pooled assets on disk, got {pool_pngs}"

    index_path = pool_dir / "index.json"
    assert index_path.exists()
    import json

    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(index_data) == 3

    # --- Episode 2: NEW instance, SAME pool_dir, fresh StubGenerate. ---
    gen2 = StubGenerate()
    src2 = OfflinePoolSource(pool_dir, "简笔日漫", gen2)

    ep2_clips = []
    for i, win in enumerate(windows):
        out = str(tmp_path / f"ep2_clip{i}.mp4")
        result = src2.fetch_clip(win, 3.0, out, _run_ff)
        assert result == out
        ep2_clips.append(out)

    # Acceptance #1 (跨集复用): episode 2 must reuse episode 1's on-disk pool
    # assets for the same queries — zero new generation calls.
    assert gen2.calls == 0, "episode 2 must reuse ep1's on-disk pool, not regenerate"

    for clip in ep2_clips:
        p = Path(clip)
        assert p.exists() and p.stat().st_size > 0

    # The on-disk pool must not have grown (no new assets added on pure hits).
    pool_pngs_after = sorted(pool_dir.glob("pool_*.png"))
    assert len(pool_pngs_after) == 3


def test_same_episode_no_duplicate_asset(tmp_path):
    pool_dir = tmp_path / "_pool" / "简笔日漫"

    # Seed the pool with exactly ONE asset via a PRIOR episode's miss (a
    # throwaway instance — its own MISS-generation immediately consumes the
    # asset into ITS `_used`, so seeding must not be the same instance under
    # test, exactly mirroring the real cross-episode `set_style` rebuild).
    seed_gen = StubGenerate()
    seed_src = OfflinePoolSource(pool_dir, "简笔日漫", seed_gen)

    seed_win = _win("runner on mountain trail", "跑者在山路上奔跑")
    seed_out = str(tmp_path / "seed_clip.mp4")
    result = seed_src.fetch_clip(seed_win, 3.0, seed_out, _run_ff)
    assert result == seed_out
    assert seed_gen.calls == 1

    pool_pngs = sorted(pool_dir.glob("pool_*.png"))
    assert len(pool_pngs) == 1

    # The episode UNDER TEST: a fresh instance (fresh `_used`), same pool_dir.
    gen = StubGenerate()
    src = OfflinePoolSource(pool_dir, "简笔日漫", gen)

    # Two windows in the SAME episode (same src instance) that both
    # most-overlap the SAME single pooled asset (share tokens with the seed
    # query "runner on mountain trail").
    win_a = _win("runner on mountain trail path", "跑者沿山路小径奔跑")
    win_b = _win("runner on mountain trail again", "跑者再次在山路上奔跑")

    out_a = str(tmp_path / "clip_a.mp4")
    result_a = src.fetch_clip(win_a, 3.0, out_a, _run_ff)
    assert result_a == out_a

    # First reuse: the only overlapping pooled asset gets used, no new
    # generation call.
    assert gen.calls == 0, "first same-episode overlap must reuse the seeded pool asset"
    assert len(src._used) == 1

    out_b = str(tmp_path / "clip_b.mp4")
    result_b = src.fetch_clip(win_b, 3.0, out_b, _run_ff)
    assert result_b == out_b

    # Acceptance #2 (同集不重复): the only overlapping asset is already in
    # `_used` from win_a, so win_b must MISS -> generate a NEW asset rather
    # than reusing the same one twice in one episode.
    assert gen.calls == 1, "same-episode second overlap on an already-used asset must generate anew"
    assert len(src._used) == 2, "the two windows must resolve to two distinct pool assets"

    pool_pngs_final = sorted(pool_dir.glob("pool_*.png"))
    assert len(pool_pngs_final) == 2, "pool must now have 2 assets (seed + the new miss-generated one)"

    for clip in (out_a, out_b):
        p = Path(clip)
        assert p.exists() and p.stat().st_size > 0
