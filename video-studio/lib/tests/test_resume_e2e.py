"""Task 8: end-to-end resume/media-cache proof — counting fake seam over REAL
wiring (fixture mode, real libass ffmpeg).

Proves the design doc's UX-001/003/004/005/006/007/010 assertions by driving
`lib.wiring.prepare_render` -> `lib.runner.run_pipeline` exactly like the
production CLI (`python -m lib.runner`) does, with ONLY the paid MediaSeams
calls swapped for a counting fake (`CountingSeam`, monkeypatched over
`lib.wiring.MediaSeams`). No real network / no real provider is ever reached
(Non-goal, per plan; the paid LIVE verification is Task 7's manual step).

Deviation from the plan's literal Step 3 (documented, not silent): fixture
mode's storyboard dispatch ALWAYS regenerates `shotlist.json` fresh from
`ctx["beats"]` every run (fixture-pass-through branch, `wiring.py` ~1637) —
editing the persisted `episodes/{seq}-{slug}/shotlist.json` on disk between
runs is therefore a no-op in fixture mode (it gets clobbered by the storyboard
step before any gen-still step reads it). The actual per-shot prompt lives on
the BEAT itself (`beat["image_prompt"]`), read straight from the fixture file
each run. So UX-003 here edits one beat's `image_prompt` in a *copy* of the
fixture JSON (never the repo's own beats.fixture.json) between runs — this
changes exactly one gen_still cache key, proving the identical "N changed
prompts -> N regenerated" behavior the plan's step 3 is after.

UX-010 (MR-1, --episode suppresses cursor/status advance) is proven at the
`step_publish` unit — the real `build_step_fns(...)["publish"]` callable
(same helper pattern as `test_publish.py`) — rather than a full syllabus-mode
render: a full run would need real doc/PDF materials-snapshot extraction
machinery (Phase 6), which is orthogonal to Task 8's media-cache mandate and
already has its own coverage elsewhere. `step_publish` is real production
code; only `run_ff`/`probe_ms`/`seams` are irrelevant-to-publish stand-ins.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent.parent.parent  # video-studio/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib import wiring as wiring_mod
from lib import runner as runner_mod
from lib import pipeline as pipeline_mod
from lib import episode as episode_mod
from lib.config import load_config

SPIKE = HERE / ".spike"
_DEFAULT_FFMPEG = str(Path.home() / ".local" / "ffmpeg-libass" / "ffmpeg")
_DEFAULT_FFPROBE = str(Path.home() / ".local" / "ffmpeg-libass" / "ffprobe")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", _DEFAULT_FFMPEG)
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", _DEFAULT_FFPROBE)

pytestmark = pytest.mark.skipif(
    not Path(FFMPEG_BIN).exists(),
    reason=f"libass ffmpeg not found at {FFMPEG_BIN} (set FFMPEG_BIN to a libass build)",
)


# ---------------------------------------------------------------------------
# CountingSeam — a fake MediaSeams that copies cheap cached assets (like dry
# mode does) but is NOT flagged `dry` unless the caller explicitly asked for
# it — so `wrap_seams` wraps it in `CachingMediaSeams` (B-layer) exactly like
# a real (non-dry) MediaSeams would. Never touches the network.
# ---------------------------------------------------------------------------

class CountingSeam:
    def __init__(self, dry: bool, provider: str, log, voice_id: str = "", style_prefix: str = ""):
        self.dry = dry
        self.provider = provider
        self.log = log
        self.voice_id = voice_id
        self.style_prefix = style_prefix
        self.counts = {"gen_still": 0, "gen_video": 0, "synth": 0, "gen_character_ref": 0}

    def gen_character_ref(self, prompt: str, out_path: str) -> str:
        self.counts["gen_character_ref"] += 1
        shutil.copy(SPIKE / "character_ref.jpg", out_path)
        return "fake://character_ref"

    def gen_still(self, prompt: str, ref_url, out_path: str) -> str:
        self.counts["gen_still"] += 1
        dst = os.path.splitext(out_path)[0] + ".jpg"
        shutil.copy(SPIKE / "b00_shot0.jpg", dst)
        return dst

    def gen_video(self, prompt: str, ref_url, out_path: str) -> dict:
        self.counts["gen_video"] += 1
        shutil.copy(SPIKE / "b04.mp4", out_path)
        return {"ok": True, "path": out_path, "task_id": "fake"}

    def synth(self, text: str, out_path: str) -> int:
        self.counts["synth"] += 1
        shutil.copy(SPIKE / "b00.raw.mp3", out_path)
        return 5000


def _underlying_counts(seams) -> dict:
    """`seams` is either the CachingMediaSeams wrapper (`.inner` is the
    CountingSeam) or, when dry, the CountingSeam itself unwrapped."""
    inner = getattr(seams, "inner", seams)
    return inner.counts


# ---------------------------------------------------------------------------
# Fixture + config helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, output_dir: Path) -> Path:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "render:\n  width: 640\n  height: 360\n  fps: 24\n  subtitle_font: PingFang SC\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {FFMPEG_BIN}\n  ffprobe_bin: {FFPROBE_BIN}\n"
        f"output_dir: {output_dir}\n",
        encoding="utf-8",
    )
    return cfg_path


def _write_fixture(dst: Path, still_prompt: str = "跑步教练站在清晨的城市跑道上，微笑着开场讲解，中景，柔和晨光") -> None:
    fixture = {
        "character_ref": {"prompt": "一位三十岁中国男性跑步教练，短发，深色运动外套，亲和而专业，正脸半身特写，干净背景"},
        "cards": {
            "title": {
                "tag": "跑步知识 · EP.01", "title": "乳酸阈 · 跑姿 · 配速",
                "subtitle": "三个要点，训练不再凭感觉",
                "bg_prompt": "清晨田径场跑道，一位跑者迎着晨光在画面右侧奔跑的剪影，左侧大量留白晨雾，电影感，冷蓝调",
            },
            "end": {
                "tag": "本期 3 个要点",
                "bullets": [["01", "乳酸阈"], ["02", "跑姿"], ["03", "配速"]],
                "footer": "关注 · 科学跑步不靠感觉",
                "bg_prompt": "清晨空旷红色跑道笔直延伸向远方，晨雾弥漫，冷蓝调，大量留白，无人，电影感",
            },
        },
        "beats": [
            {
                "id": "beat0", "visual_type": "still",
                "text": "今天聊乳酸阈配速。它决定你能在多快的速度下稳定坚持。",
                "image_prompt": still_prompt,
            },
            {
                "id": "beat1", "visual_type": "video",
                "text": "先看跑姿。前脚掌落地，重心前移，步频保持在每分钟一百八十步。",
                "video_prompt": "跑步教练在跑道上慢跑示范标准跑姿，侧面跟拍，动作连贯自然",
                "image_prompt": "跑步教练在跑道上慢跑的侧面定格，前脚掌落地，动作清晰",
            },
            {
                "id": "beat2", "visual_type": "chart",
                "text": "再用VDOT定配速。不同水平对应不同的训练区间，照表执行就不会练错。",
                "chart_title": "VDOT 训练配速参考表",
                "chart_rows": [["轻松跑 E", "6:30 / 公里"], ["马拉松配速 M", "5:20 / 公里"]],
            },
        ],
    }
    dst.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")


def _raising_persona(*a, **kw):
    raise AssertionError(
        "dispatch_persona_fn must never be called — fixture-mode beats already "
        "carry text/visual_type, so script/storyboard/query-writer/distill are "
        "all fixture pass-through (zero live claude -p)."
    )


def _run_once(cfg, fixture_path: Path, *, seq: str, slug: str, fresh: bool = False,
              dry: bool = False, monkeypatch, keep_env: tuple = ()) -> dict:
    """One prepare_render -> run_pipeline pass with CountingSeam injected via
    monkeypatch (prepare_render resolves `MediaSeams` from wiring's module
    globals, so patching `wiring_mod.MediaSeams` is sufficient).

    `keep_env` lists SPIKE_* vars the caller has deliberately set and needs to
    survive into this run — without it the hermetic clear below would delete the
    very hook a test like test_adv2_spike_storyboard_json_wins is exercising.
    """
    monkeypatch.setattr(wiring_mod, "MediaSeams", CountingSeam)
    for var in ("SPIKE_REF_PNG", "SPIKE_REF_URL", "SPIKE_CARD_TITLE_BG",
                "SPIKE_CARD_END_BG", "SPIKE_S2V_CLIP", "SPIKE_STORYBOARD_JSON",
                "PEXELS_API_KEY"):
        if var in keep_env:
            continue
        monkeypatch.delenv(var, raising=False)

    pipeline = pipeline_mod.load_pipeline()
    pipeline_mod.validate_pipeline(pipeline)

    def log(msg: str) -> None:
        pass  # keep test output quiet; flip to print(msg) when debugging

    bundle = wiring_mod.prepare_render(
        cfg, fixture_path=fixture_path, seq=seq, slug=slug, dry=dry, log=log,
        dispatch_persona_fn=_raising_persona, fresh=fresh,
    )
    seams = bundle.pop("_seams")
    result = runner_mod.run_pipeline(cfg, pipeline=pipeline, **bundle)
    return {"result": result, "seams": seams}


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


@pytest.fixture()
def cfg(tmp_path: Path, output_dir: Path):
    return load_config(_write_config(tmp_path, output_dir))


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    p = tmp_path / "beats.e2e.json"
    _write_fixture(p)
    return p


def _cache_dir(cfg, seq: str, slug: str) -> Path:
    return Path(cfg.output_dir) / "episodes" / f"{seq}-{slug}" / ".cache"


def _final_mp4(cfg, seq: str, slug: str) -> Path:
    return episode_mod.episode_paths(cfg.output_dir, seq, slug)["final"]


# ---------------------------------------------------------------------------
# UX-001 / UX-006: same-episode re-run -> zero paid calls, final.mp4 produced,
# no live dispatch (fixture pass-through — pinned by _raising_persona above).
# ---------------------------------------------------------------------------

def test_ux001_ux006_rerun_same_episode_zero_paid_calls(cfg, fixture_path, monkeypatch):
    seq, slug = "001", "ux001"

    pass1 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass1["result"]["status"] == "done", pass1["result"]
    counts1 = _underlying_counts(pass1["seams"])
    assert sum(counts1.values()) > 0, "pass 1 (cold cache) must reach the fake provider"
    assert _final_mp4(cfg, seq, slug).exists()

    pass2 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass2["result"]["status"] == "done", pass2["result"]
    counts2 = _underlying_counts(pass2["seams"])
    assert counts2 == {"gen_still": 0, "gen_video": 0, "synth": 0, "gen_character_ref": 0}, (
        f"UX-001: re-running the same episode (no --fresh) must reach the fake "
        f"provider ZERO times on pass 2; got {counts2}"
    )
    assert _final_mp4(cfg, seq, slug).exists(), "UX-001: final.mp4 must still be produced on the resumed run"


# ---------------------------------------------------------------------------
# UX-003: editing ONE beat's image_prompt -> only that beat's gen_still
# regenerates; everything else stays cached.
# ---------------------------------------------------------------------------

def test_ux003_single_prompt_change_regenerates_only_that_shot(cfg, tmp_path, monkeypatch):
    seq, slug = "001", "ux003"
    fx = tmp_path / "beats.ux003.json"
    _write_fixture(fx)

    pass1 = _run_once(cfg, fx, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass1["result"]["status"] == "done"
    still_total = _underlying_counts(pass1["seams"])["gen_still"]
    assert still_total > 0

    # Edit ONLY beat0's image_prompt in the (copy of the) fixture file.
    _write_fixture(fx, still_prompt="全新的开场镜头描述，跑者站在山顶俯瞰城市，日出逆光剪影")

    pass2 = _run_once(cfg, fx, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass2["result"]["status"] == "done"
    counts2 = _underlying_counts(pass2["seams"])
    assert counts2["gen_still"] == 1, (
        f"UX-003: changing exactly one beat's image_prompt must regenerate "
        f"exactly that one still (of {still_total} total); got {counts2['gen_still']}"
    )
    assert counts2["gen_video"] == 0
    assert counts2["synth"] == 0


# ---------------------------------------------------------------------------
# UX-004: --fresh bypasses the cache entirely -> full regen, matching pass 1.
# ---------------------------------------------------------------------------

def test_ux004_fresh_forces_full_regen(cfg, fixture_path, monkeypatch):
    seq, slug = "001", "ux004"

    pass1 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    counts1 = _underlying_counts(pass1["seams"])

    # Sanity: an ordinary (non-fresh) re-run would hit cache (already proven by
    # UX-001); --fresh must instead force the SAME full-regen counts as pass 1.
    pass2 = _run_once(cfg, fixture_path, seq=seq, slug=slug, fresh=True, monkeypatch=monkeypatch)
    assert pass2["result"]["status"] == "done"
    counts2 = _underlying_counts(pass2["seams"])
    assert counts2 == counts1, (
        f"UX-004: --fresh must bypass the media cache entirely (full regen, "
        f"matching the cold-cache pass 1 counts {counts1}); got {counts2}"
    )


# ---------------------------------------------------------------------------
# UX-005: changing PIC_ENDPOINT invalidates every still cache key -> all
# stills regenerate even without --fresh (gen_video/synth untouched — their
# cache keys don't read PIC_ENDPOINT).
# ---------------------------------------------------------------------------

def test_ux005_endpoint_change_invalidates_all_stills(cfg, fixture_path, monkeypatch):
    seq, slug = "001", "ux005"

    pass1 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    still_total = _underlying_counts(pass1["seams"])["gen_still"]
    assert still_total > 0

    monkeypatch.setenv("PIC_ENDPOINT", "https://different-endpoint.example/v2")
    pass2 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass2["result"]["status"] == "done"
    counts2 = _underlying_counts(pass2["seams"])
    assert counts2["gen_still"] == still_total, (
        f"UX-005: PIC_ENDPOINT change must invalidate every still cache key "
        f"(expected {still_total} regenerated); got {counts2['gen_still']}"
    )
    assert counts2["gen_video"] == 0, "gen_video's cache key does not read PIC_ENDPOINT"
    assert counts2["synth"] == 0, "synth's cache key does not read PIC_ENDPOINT"


# ---------------------------------------------------------------------------
# UX-007: dry=True never touches the media cache (`.cache/` never created).
# ---------------------------------------------------------------------------

def test_ux007_dry_never_creates_cache_dir(cfg, fixture_path, monkeypatch):
    seq, slug = "001", "ux007"

    for _ in range(2):
        run = _run_once(cfg, fixture_path, seq=seq, slug=slug, dry=True, monkeypatch=monkeypatch)
        assert run["result"]["status"] == "done", run["result"]
        assert not getattr(run["seams"], "hits", None) and not getattr(run["seams"], "misses", None), (
            "dry seams must be the bare CountingSeam (no CachingMediaSeams wrapper counters)"
        )

    assert not _cache_dir(cfg, seq, slug).exists(), (
        "UX-007: dry mode must never create the episode's .cache/ directory "
        "(wrap_seams returns the inner seam unwrapped when dry=True)"
    )
    assert _final_mp4(cfg, seq, slug).exists()


# ---------------------------------------------------------------------------
# ADV-2: branch-order guard — SPIKE_STORYBOARD_JSON reuse wins over durable
# shotlist reuse, and fixture pass-through wins over durable shotlist reuse
# (both proven under the real wiring, not just unit-level).
# ---------------------------------------------------------------------------

def test_adv2_fixture_passthrough_beats_durable_shotlist_reuse(cfg, fixture_path, monkeypatch):
    """beats carry visual_type (fixture mode) -> storyboard ALWAYS takes the
    fixture-pass-through branch, even though pass 2 has a durable
    shotlist.json already sitting on disk from pass 1 (proving fixture >
    durable in the real reuse-priority ladder, wiring.py:1636-1652)."""
    seq, slug = "001", "adv2fixture"

    pass1 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass1["result"]["status"] == "done"
    shotlist_path = episode_mod.episode_paths(cfg.output_dir, seq, slug)["shotlist"]
    assert Path(shotlist_path).exists(), "pass 1 must have persisted a durable shotlist.json"
    mtime1 = Path(shotlist_path).stat().st_mtime_ns

    pass2 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass2["result"]["status"] == "done"
    mtime2 = Path(shotlist_path).stat().st_mtime_ns
    assert mtime2 >= mtime1, (
        "fixture pass-through must have REWRITTEN shotlist.json on pass 2 "
        "(it always regenerates from ctx['beats'], never reads the durable "
        "file back) — a stale mtime would mean durable-reuse silently won"
    )
    # And the B-layer media cache must STILL have hit on pass 2 (fixture mode
    # for the storyboard step is orthogonal to the B-layer cache for seams).
    counts2 = _underlying_counts(pass2["seams"])
    assert sum(counts2.values()) == 0


def test_adv2_spike_storyboard_json_wins_even_with_durable_shotlist(cfg, fixture_path, monkeypatch):
    """SPIKE_STORYBOARD_JSON > durable shotlist reuse: with the env hook set,
    the storyboard dispatch takes the SPIKE branch (wiring.py:1614-1634)
    regardless of whatever durable shotlist.json already exists on disk."""
    seq, slug = "001", "adv2spike"

    pass1 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch)
    assert pass1["result"]["status"] == "done"

    # A SPIKE storyboard reuse file distinct from the beats' own visual_type
    # (3 beats, still/multi/multi shape) — its mere presence must be consumed
    # by the SPIKE branch without raising, proving SPIKE-first ordering.
    spike_sb = [
        {"visual_type": "still", "shots": [{"prompt": "p0", "shot_type": "", "with_character": True, "pace": "normal"}], "keywords": []},
        {"visual_type": "video", "shots": [{"prompt": "p1", "shot_type": "", "with_character": True, "pace": "normal"}], "keywords": []},
        {"visual_type": "chart", "shots": [{"prompt": "p2", "shot_type": "", "with_character": True, "pace": "normal"}], "keywords": []},
    ]
    spike_path = fixture_path.parent / "spike_storyboard.json"
    spike_path.write_text(json.dumps(spike_sb, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SPIKE_STORYBOARD_JSON", str(spike_path))
    try:
        # keep_env: _run_once's hermetic SPIKE_* clear would otherwise delete the
        # hook we just set, so the SPIKE branch would never fire and this test
        # could never actually verify SPIKE-first ordering.
        pass2 = _run_once(cfg, fixture_path, seq=seq, slug=slug, monkeypatch=monkeypatch,
                          keep_env=("SPIKE_STORYBOARD_JSON",))
    finally:
        monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    assert pass2["result"]["status"] == "done", pass2["result"]
    shotlist_path = episode_mod.episode_paths(cfg.output_dir, seq, slug)["shotlist"]
    on_disk = json.loads(Path(shotlist_path).read_text(encoding="utf-8"))
    assert on_disk[0]["shots"][0]["prompt"] == "p0", (
        "SPIKE_STORYBOARD_JSON must win over durable shotlist reuse even "
        "though a durable shotlist.json already exists from pass 1"
    )


# ---------------------------------------------------------------------------
# UX-010 (MR-1): --episode <seq> (episode_target=True) never advances
# cursor/status; the SAME target without episode_target does advance.
# ---------------------------------------------------------------------------

SYLLABUS_YAML = textwrap.dedent("""\
    book: running-science
    character_ref: ""
    modules:
      - name: VDOT System
        style: educational
        episodes:
          - title: What is VDOT
            doc_refs: ["01-daniels-overview.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Understand the VDOT concept
            status: done
          - title: VDOT Pace Tables
            doc_refs: ["02-vdot-system.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Read and use VDOT pace tables
            status: locked
      - name: Training Zones
        style: educational
        episodes:
          - title: Seven Training Zones
            doc_refs: ["03-training-zones.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Understand the seven training zones
            status: locked
          - title: Easy Run Zone
            doc_refs: ["03-training-zones.md#easy"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Run in the easy zone
            status: locked
""")

CURSOR_YAML = textwrap.dedent("""\
    book: running-science
    module_idx: 0
    episode_idx: 1
""")


def _build_publish_fn(tmp_path: Path, cfg, *, seq: str, slug: str, selection: Any,
                      episode_target: bool = False):
    from lib.wiring import build_step_fns

    step_fns = build_step_fns(
        cfg,
        seams=CountingSeam(dry=True, provider="doubao", log=lambda m: None),
        fixture_path=tmp_path / "unused_fixture.json",
        seq=seq, slug=slug,
        run_ff=lambda *a, **kw: None,
        probe_ms=lambda p: 1000,
        log=lambda msg: None,
        selection=selection,
        episode_target=episode_target,
    )
    return step_fns["publish"]


def test_ux010_episode_target_suppresses_cursor_advance(tmp_path):
    from lib.syllabus import select_by_seq, select_next, load_syllabus, load_cursor

    syl_path = tmp_path / "syllabus.yaml"
    cur_path = tmp_path / "cursor.yaml"
    syl_path.write_text(SYLLABUS_YAML, encoding="utf-8")
    cur_path.write_text(CURSOR_YAML, encoding="utf-8")

    class _FakeCfg:
        pass
    fake_cfg = _FakeCfg()
    fake_cfg.syllabus_path = str(syl_path)
    fake_cfg.cursor_path = str(cur_path)

    # seq=003 ("Seven Training Zones", locked/not-done) — reachable via
    # select_by_seq regardless of cursor position (cursor currently sits at
    # module=0/ep=1, i.e. "before" seq=003 in traversal order).
    target_selection = select_by_seq(fake_cfg, "003")
    assert target_selection is not None
    assert target_selection.seq == "003"

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        "render:\n  width: 640\n  height: 360\n  fps: 24\n  subtitle_font: Arial\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        "ffmpeg:\n  ffmpeg_bin: /bin/true\n  ffprobe_bin: /bin/true\n"
        f"output_dir: {tmp_path}\n"
        f"syllabus_path: {syl_path}\n"
        f"cursor_path: {cur_path}\n",
        encoding="utf-8",
    )
    real_cfg = load_config(cfg_path)

    ep_dir = tmp_path / "episodes" / "003-seven-training-zones"
    ep_dir.mkdir(parents=True)
    final_mp4 = ep_dir / "final.mp4"
    final_mp4.write_bytes(b"x")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    syl_bytes_before = syl_path.read_bytes()
    cur_bytes_before = cur_path.read_bytes()

    # --- episode_target=True: cursor/status must NOT change (MR-1) ---
    step_publish_targeted = _build_publish_fn(
        tmp_path, real_cfg, seq="003", slug="seven-training-zones",
        selection=target_selection, episode_target=True,
    )
    step_publish_targeted({}, {"final_mp4": str(final_mp4)}, scratch)

    assert syl_path.read_bytes() == syl_bytes_before, (
        "UX-010: --episode (episode_target=True) must leave syllabus.yaml "
        "byte-for-byte unchanged"
    )
    assert cur_path.read_bytes() == cur_bytes_before, (
        "UX-010: --episode (episode_target=True) must leave cursor.yaml "
        "byte-for-byte unchanged"
    )

    # --- Contrast: same target, episode_target=False -> DOES advance/mark done ---
    step_publish_normal = _build_publish_fn(
        tmp_path, real_cfg, seq="003", slug="seven-training-zones",
        selection=target_selection, episode_target=False,
    )
    step_publish_normal({}, {"final_mp4": str(final_mp4)}, scratch)

    syl_after = load_syllabus(syl_path)
    assert syl_after.modules[1].episodes[0].status == "done", (
        "control: without episode_target, publish must mark the episode done "
        "(proving the suppression above was episode_target-specific, not a "
        "general breakage)"
    )
