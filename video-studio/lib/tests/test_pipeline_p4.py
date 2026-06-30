"""Phase-4 pipeline tests: 分镜 step + make_storyboard_dispatch closure.

TDD red phase: make_storyboard_dispatch and the 分镜 pipeline step don't exist yet.
Tests collect but fail. Green after Task 3-impl.

Pinned contracts (per Phase-4 plan Task 3-tests):
- pipeline has exactly 15 steps after 分镜 insertion (currently 14 → red)
- 分镜 step: kind=agent, agent=director, retry=1, artifact=shotlist_art,
  gate=check_artifact, appears immediately after script
- make_storyboard_dispatch four states:
  (a)  fixture pass-through: beats carry visual_type, no sb, no SPIKE_STORYBOARD_JSON
       → zero live dispatch, ctx["shotlist_art"] set, beats unchanged
  (a2) SPIKE_STORYBOARD_JSON set: len(sb)!=len(beats) → RuntimeError guard
  (b)  live: beats lack visual_type → director called, N>cap downgraded to cap,
       ctx["beats"][i]["visual_type"] back-propagated, ctx["sb"] set
  (c)  bad JSON: no artifact set → runner retry=1 → halt(failed_step="分镜");
       first-bad-then-good → done
- prepare_render two-agent routing: digest-writer→script, director→storyboard,
  unknown→raises (regression net for 阻塞#1)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _shenjing_step():
    return {
        "name": "分镜",
        "kind": "agent",
        "agent": "director",
        "inputs": ["script", "config"],
        "artifact": "shotlist_art",
        "gate": [{"fn": "check_artifact"}],
        "retry": 1,
    }


def _make_cfg(cap: int = 2):
    cfg = MagicMock()
    cfg.budget.video_shots_per_episode = cap
    return cfg


def _beats_with_visual_type(n: int = 3):
    """Fixture-mode beats: each has visual_type (mirrors beats.long.fixture.json shape)."""
    return [
        {
            "id": f"b{i:02d}", "beat_id": f"b{i:02d}",
            "text": f"text segment {i}", "anchor": f"anchor {i}",
            "visual_type": "still",
            "image_prompt": f"image prompt {i}",
            "video_prompt": f"video prompt {i}",
            "shots": [],
        }
        for i in range(n)
    ]


def _beats_without_visual_type(n: int = 3):
    """Live/digest-mode beats: no visual_type (digest-writer schema)."""
    return [
        {"beat_id": f"b{i:02d}", "text": f"text {i}", "anchor": f"anchor {i}"}
        for i in range(n)
    ]


def _valid_shotlist(n: int, n_video: int = 0) -> list[dict]:
    """Valid director-format shotlist with n_video video entries."""
    items = []
    for i in range(n):
        vt = "video" if i < n_video else "still"
        item: dict = {
            "visual_type": vt,
            "shots": [{"prompt": f"shot {i}", "shot_type": "全景",
                        "with_character": False, "pace": "normal"}],
            "keywords": [],
        }
        if vt == "video":
            # Descending motion_value so budget keeps the first ones
            item["motion_value"] = round(0.9 - i * 0.1, 1)
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Pipeline step count: must be 15 after 分镜 insertion
# ---------------------------------------------------------------------------

def test_pipeline_step_count_15():
    """After 分镜 insertion, load_pipeline() must return exactly 15 steps.

    Pre-impl: returns 14 → FAILS (red). Green after Task 3-impl.
    """
    from lib.pipeline import load_pipeline
    steps = load_pipeline()
    assert len(steps) == 15, (
        f"expected 15 steps (分镜 inserted after script), got {len(steps)}"
    )


def test_shenjing_step_present():
    """Pipeline must contain a '分镜' step after 3-impl."""
    from lib.pipeline import load_pipeline
    names = [s["name"] for s in load_pipeline()]
    assert "分镜" in names, f"'分镜' step missing. Steps: {names}"


def test_shenjing_step_shape():
    """分镜 step shape: kind=agent, agent=director, retry=1,
    artifact=shotlist_art, gate=[{fn: check_artifact}]."""
    from lib.pipeline import load_pipeline
    steps = load_pipeline()
    names = [s["name"] for s in steps]
    assert "分镜" in names, f"'分镜' missing. Steps: {names}"
    step = steps[names.index("分镜")]
    assert step["kind"] == "agent", f"kind={step['kind']!r}"
    assert step["agent"] == "director", f"agent={step['agent']!r}"
    assert step.get("retry") == 1, f"retry={step.get('retry')!r}"
    assert step["artifact"] == "shotlist_art", f"artifact={step['artifact']!r}"
    assert step["gate"] is not None, "gate must not be None"
    gate_fns = [g["fn"] for g in step["gate"]]
    assert "check_artifact" in gate_fns, f"gate fns: {gate_fns}"


def test_shenjing_immediately_after_script():
    """分镜 must appear immediately after script in the pipeline."""
    from lib.pipeline import load_pipeline
    steps = load_pipeline()
    names = [s["name"] for s in steps]
    assert "script" in names, "script step missing from pipeline"
    assert "分镜" in names, "分镜 step missing from pipeline"
    idx_script = names.index("script")
    idx_shenjing = names.index("分镜")
    assert idx_shenjing == idx_script + 1, (
        f"分镜 at index {idx_shenjing}, script at {idx_script}; "
        f"expected 分镜 == script+1"
    )


# ---------------------------------------------------------------------------
# State (a): fixture pass-through — beats carry visual_type, no sb, no env
# ---------------------------------------------------------------------------

def test_storyboard_dispatch_fixture_passthrough_zero_live_calls(tmp_path, monkeypatch):
    """Fixture mode (beats carry visual_type, no sb, no SPIKE_STORYBOARD_JSON)
    → zero live dispatch calls.

    This is the regression net for 阻塞#1: the DRY render (evals/video_render_e2e.py)
    runs with fixture beats that already have visual_type but no ctx['sb']. If the
    pass-through signal is ctx.get('sb') instead of beats-carry-visual_type, this
    test goes red on 3-impl (wrong signal), not just pre-impl.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    call_log: list[str] = []

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        call_log.append(agent)
        out = Path(scratch_dir) / expected
        out.write_text("[]", encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=2)
    beats = _beats_with_visual_type(3)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}  # no ctx["sb"]

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert len(call_log) == 0, (
        f"expected ZERO live dispatches in fixture mode, got {call_log}"
    )


def test_storyboard_dispatch_fixture_passthrough_sets_shotlist_art(tmp_path, monkeypatch):
    """Fixture pass-through must set ctx['shotlist_art'] so check_artifact passes."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    cfg = _make_cfg()
    beats = _beats_with_visual_type(3)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=lambda *a, **kw: {"ok": False},
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert "shotlist_art" in ctx, "ctx['shotlist_art'] not set by fixture pass-through"
    assert ctx["shotlist_art"], "ctx['shotlist_art'] must be a non-empty path"
    assert Path(ctx["shotlist_art"]).exists(), (
        f"shotlist_art file does not exist: {ctx['shotlist_art']}"
    )


def test_storyboard_dispatch_fixture_passthrough_preserves_visual_type(tmp_path, monkeypatch):
    """Fixture pass-through must preserve existing beats visual_type unchanged."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    cfg = _make_cfg()
    beats = _beats_with_visual_type(3)
    original_vt = [b["visual_type"] for b in beats]

    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=lambda *a, **kw: {"ok": False},
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    for i, (b, vt) in enumerate(zip(ctx["beats"], original_vt)):
        assert b["visual_type"] == vt, (
            f"beat[{i}] visual_type changed: was {vt!r}, now {b['visual_type']!r}"
        )


def test_storyboard_dispatch_fixture_passthrough_sb_remains_none(tmp_path, monkeypatch):
    """Fixture pass-through must not set ctx['sb'] (render uses beat-level prompts)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    cfg = _make_cfg()
    beats = _beats_with_visual_type(3)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=lambda *a, **kw: {"ok": False},
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert ctx.get("sb") is None, (
        f"fixture pass-through must not overwrite ctx['sb'], got {ctx.get('sb')!r}"
    )


def test_storyboard_dispatch_wrong_agent_raises(tmp_path, monkeypatch):
    """dispatch(agent != 'director') must raise (not silently route elsewhere)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    dispatch = make_storyboard_dispatch(
        _make_cfg(), dispatch_persona_fn=lambda *a, **k: None,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    with pytest.raises(Exception):
        dispatch("digest-writer", _shenjing_step(), {}, tmp_path)


# ---------------------------------------------------------------------------
# State (a2): SPIKE_STORYBOARD_JSON reuse — length guard
# ---------------------------------------------------------------------------

def test_storyboard_dispatch_spike_reuse_length_mismatch_raises(tmp_path, monkeypatch):
    """SPIKE_STORYBOARD_JSON with wrong entry count → RuntimeError (guard in closure).
    Beat count=3, sb entries=2 → mismatch → RuntimeError.
    """
    from lib.wiring import make_storyboard_dispatch

    sb_data = [
        {"shots": [{"prompt": "p"}], "keywords": []},
        {"shots": [{"prompt": "q"}], "keywords": []},
    ]
    sb_path = tmp_path / "storyboard.json"
    sb_path.write_text(json.dumps(sb_data), encoding="utf-8")
    monkeypatch.setenv("SPIKE_STORYBOARD_JSON", str(sb_path))

    cfg = _make_cfg()
    beats = _beats_with_visual_type(3)  # 3 beats, 2 sb entries → mismatch
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=lambda *a, **k: {"ok": False},
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    with pytest.raises(RuntimeError):
        dispatch("director", _shenjing_step(), ctx, tmp_path)


def test_storyboard_dispatch_spike_reuse_matching_count_zero_dispatch(tmp_path, monkeypatch):
    """SPIKE_STORYBOARD_JSON with matching beat count → sb loaded, zero live dispatch,
    ctx['shotlist_art'] set.
    """
    from lib.wiring import make_storyboard_dispatch

    n = 3
    sb_data = [
        {"shots": [{"prompt": f"p{i}", "shot_type": "全景",
                    "with_character": False, "pace": "normal"}], "keywords": []}
        for i in range(n)
    ]
    sb_path = tmp_path / "storyboard.json"
    sb_path.write_text(json.dumps(sb_data), encoding="utf-8")
    monkeypatch.setenv("SPIKE_STORYBOARD_JSON", str(sb_path))

    call_log: list[str] = []

    def fake_persona(agent, *a, **kw):
        call_log.append(agent)
        return {"ok": False}

    cfg = _make_cfg()
    beats = _beats_with_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert len(call_log) == 0, f"expected zero live dispatches, got {call_log}"
    assert "shotlist_art" in ctx, "shotlist_art not set by SPIKE_STORYBOARD_JSON reuse"


# ---------------------------------------------------------------------------
# State (b): live + budget downgrade — beats lack visual_type
# ---------------------------------------------------------------------------

def test_storyboard_dispatch_live_director_called(tmp_path, monkeypatch):
    """Live mode (beats lack visual_type) → director dispatch_persona_fn called."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    n = 3
    shotlist_data = _valid_shotlist(n, n_video=0)

    call_log: list[str] = []

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        call_log.append(agent)
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=2)
    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert call_log == ["director"], (
        f"expected one 'director' dispatch, got {call_log}"
    )


def test_storyboard_dispatch_live_budget_downgrade_count(tmp_path, monkeypatch):
    """Live mode: N>cap videos → downgraded to cap; video count in ctx['beats']==cap."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    cap = 2
    n = 4
    n_video = 3  # 3 wanted, cap=2 → 1 downgraded

    # motion_values: beat 0=0.9, 1=0.8, 2=0.7 (video); beat 3=still
    shotlist_data = _valid_shotlist(n, n_video=n_video)

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=cap)
    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    video_count = sum(
        1 for b in ctx["beats"] if b.get("visual_type") == "video"
    )
    assert video_count == cap, (
        f"expected {cap} videos after budget downgrade (N={n_video} → cap={cap}), "
        f"got {video_count}"
    )


def test_storyboard_dispatch_live_visual_type_backpropagated(tmp_path, monkeypatch):
    """After live dispatch, ctx['beats'][i]['visual_type'] must be set (back-propagated)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    n = 3
    shotlist_data = _valid_shotlist(n, n_video=1)  # first beat is video

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=2)
    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    for i, b in enumerate(ctx["beats"]):
        assert "visual_type" in b, f"beat[{i}] missing visual_type after dispatch"
        assert b["visual_type"] in ("still", "video", "chart", "multi"), (
            f"beat[{i}] invalid visual_type: {b['visual_type']!r}"
        )


def test_storyboard_dispatch_live_sets_sb(tmp_path, monkeypatch):
    """After live dispatch, ctx['sb'] must be set with shots list (len==n_beats)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    n = 3
    shotlist_data = _valid_shotlist(n, n_video=0)

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=2)
    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert "sb" in ctx and ctx["sb"] is not None, "ctx['sb'] not set after live dispatch"
    assert len(ctx["sb"]) == n, (
        f"ctx['sb'] length {len(ctx['sb'])} != n_beats={n}"
    )


def test_storyboard_dispatch_live_sets_shotlist_art(tmp_path, monkeypatch):
    """After live dispatch, ctx['shotlist_art'] must be set (check_artifact gate)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    n = 3
    shotlist_data = _valid_shotlist(n)

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg(cap=2)
    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=fake_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert "shotlist_art" in ctx, "ctx['shotlist_art'] not set after live dispatch"
    assert ctx["shotlist_art"], "ctx['shotlist_art'] must be non-empty"


# ---------------------------------------------------------------------------
# State (c): bad JSON → no artifact → retry=1 → halt
# ---------------------------------------------------------------------------

def test_storyboard_dispatch_bad_json_no_artifact_set(tmp_path, monkeypatch):
    """Bad JSON from director → dispatch does NOT set ctx['shotlist_art'].
    (Caller's gate will then fail → runner retries; double miss → halt.)
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.wiring import make_storyboard_dispatch

    def bad_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text("this is not valid JSON [[[{{", encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = _make_cfg()
    beats = _beats_without_visual_type(3)
    ep = {"shotlist": tmp_path / "shotlist.json"}
    ctx: dict = {"beats": beats, "ep": ep}

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=bad_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    dispatch("director", _shenjing_step(), ctx, tmp_path)

    assert "shotlist_art" not in ctx, (
        "bad JSON must NOT set ctx['shotlist_art'] "
        "(gate must fail so runner retries)"
    )


def test_shenjing_bad_json_both_attempts_halt(tmp_path, monkeypatch):
    """Bad JSON on both dispatch attempts → halt with failed_step='分镜'."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.runner import run_pipeline
    from lib.episode import check_artifact
    from lib.wiring import make_storyboard_dispatch

    def bad_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text("INVALID{{{{", encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    beats = _beats_without_visual_type(3)
    ep = {"shotlist": tmp_path / "shotlist.json"}

    def seed_ctx(step, ctx, scratch_dir):
        ctx["beats"] = [dict(b) for b in beats]
        ctx["ep"] = ep

    extract_step = {
        "name": "extract", "kind": "code", "agent": None,
        "inputs": [], "artifact": None, "gate": None,
    }
    cfg = _make_cfg()
    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=bad_persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    res = run_pipeline(
        object(),
        pipeline=[extract_step, _shenjing_step()],
        step_fns={"extract": seed_ctx},
        dispatch=dispatch,
        gates={"check_artifact": check_artifact},
        scratch_dir=scratch,
    )
    assert res["status"] == "halted", (
        f"expected 'halted', got {res['status']!r}"
    )
    assert res["failed_step"] == "分镜", (
        f"expected failed_step='分镜', got {res['failed_step']!r}"
    )


def test_shenjing_bad_then_good_passes(tmp_path, monkeypatch):
    """First dispatch bad JSON, retry dispatch produces valid shotlist → status=done."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    from lib.runner import run_pipeline
    from lib.episode import check_artifact
    from lib.wiring import make_storyboard_dispatch

    n = 3
    shotlist_data = _valid_shotlist(n)

    state = {"calls": 0}

    def persona(agent, prompt, scratch_dir, expected, **kw):
        state["calls"] += 1
        out = Path(scratch_dir) / expected
        if state["calls"] == 1:
            out.write_text("BAD_JSON{{{{", encoding="utf-8")
        else:
            out.write_text(json.dumps(shotlist_data), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    beats = _beats_without_visual_type(n)
    ep = {"shotlist": tmp_path / "shotlist.json"}

    def seed_ctx(step, ctx, scratch_dir):
        ctx["beats"] = [dict(b) for b in beats]
        ctx["ep"] = ep

    extract_step = {
        "name": "extract", "kind": "code", "agent": None,
        "inputs": [], "artifact": None, "gate": None,
    }
    cfg = _make_cfg(cap=2)
    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=persona,
        plugin_root=PLUGIN_ROOT, log=lambda m: None,
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    res = run_pipeline(
        object(),
        pipeline=[extract_step, _shenjing_step()],
        step_fns={"extract": seed_ctx},
        dispatch=dispatch,
        gates={"check_artifact": check_artifact},
        scratch_dir=scratch,
    )
    assert res["status"] == "done", (
        f"expected 'done' after retry success, got {res}"
    )
    assert state["calls"] == 2, (
        f"expected 2 dispatch calls (1 bad + 1 good), got {state['calls']}"
    )


# ---------------------------------------------------------------------------
# prepare_render two-agent routing — 阻塞#1 配套
# ---------------------------------------------------------------------------

def test_prepare_render_routes_digest_writer_to_script_closure(tmp_path, monkeypatch):
    """prepare_render dispatch routes 'digest-writer' → script closure.
    Fixture mode (beats in ctx) → pass-through → ctx['script_ledger'] set.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    import lib.episode as ep_mod
    from lib.wiring import prepare_render

    cfg = MagicMock()
    cfg.ffmpeg.ffmpeg_bin = "ffmpeg"
    cfg.ffmpeg.ffprobe_bin = "ffprobe"
    cfg.media.provider = "minimax"
    cfg.output_dir = str(tmp_path / "output")
    cfg.budget.video_shots_per_episode = 2

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps({
        "beats": _beats_with_visual_type(3),
        "character_ref": {"prompt": "runner"},
        "cards": {},
    }), encoding="utf-8")

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    monkeypatch.setattr(ep_mod, "make_scratch", lambda *a, **kw: scratch_dir)

    bundle = prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq="001", slug="test",
        dry=True, log=lambda m: None,
        dispatch_persona_fn=lambda *a, **kw: {"ok": False},
    )
    dispatch = bundle["dispatch"]

    beats = _beats_with_visual_type(3)
    ep = {"shotlist": tmp_path / "ep_shotlist.json",
          "script": tmp_path / "script.md"}
    ctx = {"beats": beats, "ep": ep}
    dispatch("digest-writer", {"name": "script"}, ctx, scratch_dir)

    assert "script_ledger" in ctx, (
        "digest-writer dispatch must set ctx['script_ledger'] via script closure"
    )


def test_prepare_render_routes_director_to_storyboard_closure(tmp_path, monkeypatch):
    """prepare_render dispatch routes 'director' → storyboard closure.

    Fixture mode (beats carry visual_type) → pass-through → ctx['shotlist_art'] set.
    Pre-impl: dispatch raises 'unexpected agent step: director' → TEST FAILS (RED).
    Green after Task 3-impl wires the router.
    """
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    import lib.episode as ep_mod
    from lib.wiring import prepare_render

    cfg = MagicMock()
    cfg.ffmpeg.ffmpeg_bin = "ffmpeg"
    cfg.ffmpeg.ffprobe_bin = "ffprobe"
    cfg.media.provider = "minimax"
    cfg.output_dir = str(tmp_path / "output")
    cfg.budget.video_shots_per_episode = 2

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps({
        "beats": _beats_with_visual_type(3),
        "character_ref": {"prompt": "runner"},
        "cards": {},
    }), encoding="utf-8")

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    monkeypatch.setattr(ep_mod, "make_scratch", lambda *a, **kw: scratch_dir)

    bundle = prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq="001", slug="test",
        dry=True, log=lambda m: None,
        dispatch_persona_fn=lambda *a, **kw: {"ok": False},
    )
    dispatch = bundle["dispatch"]

    # Director in fixture mode (beats carry visual_type) → pass-through
    beats = _beats_with_visual_type(3)
    ep = {"shotlist": tmp_path / "ep_shotlist2.json"}
    ctx = {"beats": beats, "ep": ep}
    # Pre-impl: raises RuntimeError('unexpected agent step: director') → RED
    dispatch("director", _shenjing_step(), ctx, scratch_dir)
    assert "shotlist_art" in ctx, (
        "director dispatch must set ctx['shotlist_art'] via storyboard closure"
    )


def test_prepare_render_unknown_agent_raises(tmp_path, monkeypatch):
    """prepare_render dispatch raises for unknown agent names."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    import lib.episode as ep_mod
    from lib.wiring import prepare_render

    cfg = MagicMock()
    cfg.ffmpeg.ffmpeg_bin = "ffmpeg"
    cfg.ffmpeg.ffprobe_bin = "ffprobe"
    cfg.media.provider = "minimax"
    cfg.output_dir = str(tmp_path / "output")
    cfg.budget.video_shots_per_episode = 2

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps({
        "beats": _beats_with_visual_type(3),
        "character_ref": {"prompt": "runner"},
        "cards": {},
    }), encoding="utf-8")

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    monkeypatch.setattr(ep_mod, "make_scratch", lambda *a, **kw: scratch_dir)

    bundle = prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq="001", slug="test",
        dry=True, log=lambda m: None,
        dispatch_persona_fn=lambda *a, **kw: {"ok": False},
    )
    dispatch = bundle["dispatch"]

    with pytest.raises(Exception):
        dispatch("unknown-agent", {"name": "x"}, {}, scratch_dir)
