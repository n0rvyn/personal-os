"""Task 4-tests: A-layer reuse-on-exist for make_script_dispatch /
make_storyboard_dispatch.

Priority ladder (per design):
    SPIKE env hook  >  fixture pass-through  >  durable-on-disk reuse  >  live dispatch

These tests use a two-pass ("prime" then "reuse") pattern: pass 1 goes
through the live dispatch path (fake persona) and — once Task 4-impl lands —
persists a durable artifact + matching `.stamp`; pass 2, with nothing
upstream changed, must reuse the durable artifact and NOT call the fake
persona again. Changing an upstream input (fulltext / beats content) between
passes must force pass 2 back to live. `fresh=True` must force live even
when nothing changed.

FAILS before Task 4-impl:
  - `make_script_dispatch`/`make_storyboard_dispatch` don't accept `fresh=`
    (TypeError) and don't write a `.stamp` after live success (so a "reuse"
    pass always re-dispatches — the fake-persona call counts never
    plateau).
PASSES after Task 4-impl.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.config import HostConfig

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Script dispatch (digest-writer / beats.json) helpers
# ---------------------------------------------------------------------------

def _script_cfg():
    cfg = MagicMock()
    cfg.host = HostConfig()  # empty: no fixed-host line, keeps prompts simple
    return cfg


def _grounded_beats(n=2):
    """Beats whose anchor is a verbatim substring of its own text — trivially
    grounded by verify_anchors (no numbers, full word coverage)."""
    return [
        {"id": f"b{i:02d}", "beat_id": f"b{i:02d}",
         "text": f"跑步训练要点第{i}条说明文字。", "anchor": f"跑步训练要点第{i}条说明文字"}
        for i in range(n)
    ]


def _fulltext_for(beats):
    return "教材原文。" + "".join(b["anchor"] for b in beats) + "。全文结束。"


def _make_script_persona(beats, calls):
    """Fake dispatch_persona_fn for make_script_dispatch: writes `beats` as
    the digest artifact and records a call each invocation."""
    def fake(agent, prompt, scratch_dir, expected, **kw):
        calls.append(1)
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": beats}, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}
    return fake


def _script_ep(tmp_path):
    ep_dir = tmp_path / "ep"
    ep_dir.mkdir(parents=True, exist_ok=True)
    return {
        "beats": ep_dir / "beats.json",
        "script": ep_dir / "script.md",
        "shotlist": ep_dir / "shotlist.json",
    }


def _run_script_dispatch(cfg, calls, fulltext, beats, ep, scratch_dir, *, fresh=False):
    from lib.wiring import make_script_dispatch

    dispatch = make_script_dispatch(
        cfg, dispatch_persona_fn=_make_script_persona(beats, calls),
        plugin_root=_PLUGIN_ROOT, log=lambda m: None, fresh=fresh,
    )
    ctx = {"fulltext": fulltext, "ep": ep}
    scratch_dir.mkdir(parents=True, exist_ok=True)
    dispatch("digest-writer", {"name": "script"}, ctx, scratch_dir)
    return ctx


# ---------------------------------------------------------------------------
# Script dispatch: durable reuse
# ---------------------------------------------------------------------------

def test_script_dispatch_reuses_durable_beats_on_second_pass(tmp_path):
    """Pass 1 (live) writes beats.json + stamp. Pass 2 (nothing changed) must
    NOT call the persona again, must load ctx['beats'] from disk, and must
    still populate ctx['script_ledger'] (so the downstream gate passes)."""
    calls: list[int] = []
    beats = _grounded_beats(2)
    fulltext = _fulltext_for(beats)
    ep = _script_ep(tmp_path)

    ctx1 = _run_script_dispatch(
        _script_cfg(), calls, fulltext, beats, ep, tmp_path / "scratch1",
    )
    assert len(calls) == 1, "pass 1 must dispatch the persona (live, nothing durable yet)"
    assert ctx1["beats"], "pass 1 must populate ctx['beats'] after a grounded live dispatch"
    assert Path(ep["beats"]).exists(), "pass 1 must persist beats.json durably"

    ctx2 = _run_script_dispatch(
        _script_cfg(), calls, fulltext, beats, ep, tmp_path / "scratch2",
    )
    assert len(calls) == 1, (
        "pass 2 (nothing upstream changed) must reuse the durable beats.json "
        "and NOT re-dispatch the persona"
    )
    assert ctx2["beats"], "pass 2 must load ctx['beats'] from the durable artifact"
    assert ctx2.get("script_ledger"), "pass 2 must still populate ctx['script_ledger']"
    with open(ctx2["script_ledger"], encoding="utf-8") as f:
        ledger = json.load(f)
    assert ledger.get("beats"), "reused script_ledger must carry the loaded beats"


def test_script_dispatch_stamp_mismatch_forces_live(tmp_path):
    """Pass 2 with a CHANGED fulltext (upstream input) must re-dispatch the
    persona (stamp mismatch → auto-invalidate, no --fresh needed)."""
    calls: list[int] = []
    beats = _grounded_beats(2)
    fulltext = _fulltext_for(beats)
    ep = _script_ep(tmp_path)

    _run_script_dispatch(_script_cfg(), calls, fulltext, beats, ep, tmp_path / "scratch1")
    assert len(calls) == 1

    changed_fulltext = fulltext + "新增了一段教材原文内容。"
    _run_script_dispatch(
        _script_cfg(), calls, changed_fulltext, beats, ep, tmp_path / "scratch2",
    )
    assert len(calls) == 2, (
        "changed fulltext must invalidate the stamp and force a live re-dispatch"
    )


def test_script_dispatch_fresh_forces_live_even_when_stamp_matches(tmp_path):
    """`fresh=True` on pass 2 must force live dispatch even though nothing
    upstream changed (stamp would otherwise match)."""
    calls: list[int] = []
    beats = _grounded_beats(2)
    fulltext = _fulltext_for(beats)
    ep = _script_ep(tmp_path)

    _run_script_dispatch(_script_cfg(), calls, fulltext, beats, ep, tmp_path / "scratch1")
    assert len(calls) == 1

    _run_script_dispatch(
        _script_cfg(), calls, fulltext, beats, ep, tmp_path / "scratch2", fresh=True,
    )
    assert len(calls) == 2, "fresh=True must bypass durable reuse even on a stamp match"


# ---------------------------------------------------------------------------
# Storyboard dispatch (director / shotlist.json) helpers
# ---------------------------------------------------------------------------

def _storyboard_cfg():
    cfg = MagicMock()
    cfg.budget.video_shots_per_episode = 2
    cfg.budget.video_motion_threshold = 0.6
    cfg.budget.video_floor = 0  # avoid the floor promoting a still→video surprise
    cfg.host = HostConfig()
    return cfg


def _live_beats(n=2):
    """Beats WITHOUT visual_type — the storyboard fixture pass-through check
    (`all("visual_type" in b for b in beats)`) must be False, so these route
    past fixture pass-through toward durable-reuse / live."""
    return [{"id": f"b{i:02d}", "text": f"narration {i}"} for i in range(n)]


def _director_shotlist(n):
    return [
        {
            "visual_type": "still",
            "shots": [{"prompt": f"shot {i}", "shot_type": "全景",
                       "with_character": False, "pace": "normal"}],
            "keywords": [],
        }
        for i in range(n)
    ]


def _make_director_persona(shotlist, calls):
    def fake(agent, prompt, scratch_dir, expected, **kw):
        calls.append(1)
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(shotlist, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}
    return fake


def _storyboard_ep(tmp_path):
    ep_dir = tmp_path / "ep"
    ep_dir.mkdir(parents=True, exist_ok=True)
    return {"shotlist": ep_dir / "shotlist.json"}


def _run_storyboard_dispatch(cfg, calls, beats, shotlist, ep, scratch_dir, *, fresh=False):
    from lib.wiring import make_storyboard_dispatch

    dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=_make_director_persona(shotlist, calls),
        plugin_root=_PLUGIN_ROOT, log=lambda m: None, fresh=fresh,
    )
    ctx = {"beats": [dict(b) for b in beats], "ep": ep}
    scratch_dir.mkdir(parents=True, exist_ok=True)
    dispatch("director", {"name": "分镜"}, ctx, scratch_dir)
    return ctx


# ---------------------------------------------------------------------------
# Storyboard dispatch: durable reuse
# ---------------------------------------------------------------------------

def test_storyboard_dispatch_reuses_durable_shotlist_on_second_pass(tmp_path, monkeypatch):
    """Pass 1 (live) writes shotlist.json + stamp. Pass 2 (nothing changed)
    must NOT call the persona again, must load ctx['sb'] with
    len(sb) == len(beats), and must populate ctx['shotlist_art']."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    calls: list[int] = []
    beats = _live_beats(2)
    shotlist = _director_shotlist(2)
    ep = _storyboard_ep(tmp_path)

    ctx1 = _run_storyboard_dispatch(
        _storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch1",
    )
    assert len(calls) == 1, "pass 1 must dispatch the director (live, nothing durable yet)"
    assert Path(ep["shotlist"]).exists(), "pass 1 must persist shotlist.json durably"
    assert ctx1.get("shotlist_art")

    ctx2 = _run_storyboard_dispatch(
        _storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch2",
    )
    assert len(calls) == 1, (
        "pass 2 (nothing upstream changed) must reuse the durable shotlist.json "
        "and NOT re-dispatch the director"
    )
    assert ctx2.get("sb") is not None, "pass 2 must load ctx['sb'] from the durable artifact"
    assert len(ctx2["sb"]) == len(beats), "reused sb length must match beats length"
    assert ctx2.get("shotlist_art"), "pass 2 must still populate ctx['shotlist_art']"


def test_storyboard_dispatch_stamp_mismatch_forces_live(tmp_path, monkeypatch):
    """Pass 2 with CHANGED beats content (upstream input) must re-dispatch
    the director (stamp mismatch → auto-invalidate)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    calls: list[int] = []
    beats = _live_beats(2)
    shotlist = _director_shotlist(2)
    ep = _storyboard_ep(tmp_path)

    _run_storyboard_dispatch(_storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch1")
    assert len(calls) == 1

    changed_beats = _live_beats(2)
    changed_beats[0]["text"] = "narration 0 changed by the director's script edit"
    _run_storyboard_dispatch(
        _storyboard_cfg(), calls, changed_beats, shotlist, ep, tmp_path / "scratch2",
    )
    assert len(calls) == 2, (
        "changed beats content must invalidate the shotlist stamp and force "
        "a live re-dispatch"
    )


def test_storyboard_dispatch_fresh_forces_live_even_when_stamp_matches(tmp_path, monkeypatch):
    """`fresh=True` on pass 2 must force live dispatch even though nothing
    upstream changed (stamp would otherwise match)."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    calls: list[int] = []
    beats = _live_beats(2)
    shotlist = _director_shotlist(2)
    ep = _storyboard_ep(tmp_path)

    _run_storyboard_dispatch(_storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch1")
    assert len(calls) == 1

    _run_storyboard_dispatch(
        _storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch2", fresh=True,
    )
    assert len(calls) == 2, "fresh=True must bypass durable reuse even on a stamp match"


def test_storyboard_dispatch_spike_env_beats_durable_reuse(tmp_path, monkeypatch):
    """SPIKE_STORYBOARD_JSON must win even when a matching durable shotlist +
    stamp is ALSO present (priority: SPIKE > fixture > durable > live)."""
    calls: list[int] = []
    beats = _live_beats(2)
    shotlist = _director_shotlist(2)
    ep = _storyboard_ep(tmp_path)

    # Prime durable reuse state (pass 1, no SPIKE set).
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    _run_storyboard_dispatch(_storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "scratch1")
    assert len(calls) == 1
    assert Path(ep["shotlist"]).exists()

    # Now set SPIKE_STORYBOARD_JSON to a DIFFERENT shotlist and confirm it wins
    # over the durable reuse branch (durable is present + would otherwise match).
    spike_shotlist = _director_shotlist(2)
    spike_shotlist[0]["shots"][0]["prompt"] = "SPIKE OVERRIDE PROMPT"
    spike_path = tmp_path / "spike_shotlist.json"
    spike_path.write_text(json.dumps(spike_shotlist, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SPIKE_STORYBOARD_JSON", str(spike_path))

    from lib.wiring import make_storyboard_dispatch
    dispatch = make_storyboard_dispatch(
        _storyboard_cfg(), dispatch_persona_fn=_make_director_persona(shotlist, calls),
        plugin_root=_PLUGIN_ROOT, log=lambda m: None,
    )
    ctx = {"beats": [dict(b) for b in beats], "ep": ep}
    (tmp_path / "scratch2").mkdir(parents=True, exist_ok=True)
    dispatch("director", {"name": "分镜"}, ctx, tmp_path / "scratch2")

    assert len(calls) == 1, "SPIKE branch must not call the (durable/live) persona at all"
    with open(ep["shotlist"], encoding="utf-8") as f:
        written = json.load(f)
    assert written[0]["shots"][0]["prompt"] == "SPIKE OVERRIDE PROMPT", (
        "SPIKE_STORYBOARD_JSON must win over the durable-reuse branch even "
        "though a matching durable shotlist.json is present"
    )


# ---------------------------------------------------------------------------
# DOC-MODE RESUME regression coverage (the gap that let MF#1/MF#2 ship green):
# the durable/SPIKE reuse branches must reproduce the same ctx side-effects the
# LIVE branch performs, or a resumed doc-mode run (beats WITHOUT visual_type)
# crashes in step_shotplan / silently degrades its cards.
# ---------------------------------------------------------------------------

def _mixed_shotlist():
    """A director shotlist mixing still + video + chart (video carries a
    video_prompt, chart carries a chart spec), for asserting the reuse branches
    back-propagate ALL of visual_type / video_prompt / chart onto beats."""
    return [
        {"visual_type": "still",
         "shots": [{"prompt": "shot 0", "shot_type": "全景", "with_character": False, "pace": "normal"}],
         "keywords": []},
        {"visual_type": "video", "video_prompt": "running demo shot",
         "shots": [{"prompt": "shot 1", "shot_type": "中景", "with_character": True, "pace": "normal"}],
         "keywords": []},
        {"visual_type": "chart", "chart": {"type": "line", "params": {"title": "配速曲线"}},
         "shots": [{"prompt": "shot 2", "shot_type": "", "with_character": False, "pace": "normal"}],
         "keywords": []},
    ]


def test_storyboard_durable_reuse_backprops_visual_fields_onto_beats(tmp_path, monkeypatch):
    """MF#1: durable shotlist reuse must back-propagate visual_type (+ video_prompt)
    onto ctx['beats'] exactly like the live path. Without it, a resumed doc-mode
    run leaves beats with no visual_type and step_shotplan raises KeyError
    'visual_type' — the primary 崩溃续跑 path. Beats here have NO visual_type
    (doc-mode), so this drives the durable-reuse branch, not fixture pass-through."""
    monkeypatch.delenv("SPIKE_STORYBOARD_JSON", raising=False)
    calls: list[int] = []
    beats = _live_beats(3)                # doc-mode beats: no visual_type
    shotlist = _mixed_shotlist()
    ep = _storyboard_ep(tmp_path)

    _run_storyboard_dispatch(_storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "s1")
    assert len(calls) == 1
    # What the live pass actually persisted (post parse/budget) is the contract.
    persisted = json.loads(Path(ep["shotlist"]).read_text(encoding="utf-8"))
    expected_vt = [item["visual_type"] for item in persisted]

    ctx2 = _run_storyboard_dispatch(_storyboard_cfg(), calls, beats, shotlist, ep, tmp_path / "s2")
    assert len(calls) == 1, "pass 2 must reuse the durable shotlist (no re-dispatch)"
    # THE regression: every resumed beat must carry visual_type back from reuse.
    assert all("visual_type" in b for b in ctx2["beats"]), (
        "MF#1: durable reuse must back-prop visual_type onto ctx['beats'] "
        "(else step_shotplan raises KeyError 'visual_type' on doc-mode resume)"
    )
    assert [b["visual_type"] for b in ctx2["beats"]] == expected_vt
    # video/chart beats must also carry their extra fields (step_gen_video reads
    # video_prompt; step_chart reads chart) — all back-propped by reuse.
    for b, item in zip(ctx2["beats"], persisted):
        if item["visual_type"] == "video":
            assert b.get("video_prompt"), "reused video beat must carry video_prompt"
        if item.get("chart"):
            assert b.get("chart"), "reused chart beat must carry the director chart spec"


def test_storyboard_spike_reuse_backprops_visual_type_onto_beats(tmp_path, monkeypatch):
    """MF#1 sibling: the SPIKE_STORYBOARD_JSON branch must back-prop too — a
    doc-mode SPIKE reuse otherwise leaves beats without visual_type."""
    calls: list[int] = []
    beats = _live_beats(3)                # no visual_type
    spike_sb = _mixed_shotlist()
    spike_path = tmp_path / "spike.json"
    spike_path.write_text(json.dumps(spike_sb, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SPIKE_STORYBOARD_JSON", str(spike_path))
    ep = _storyboard_ep(tmp_path)

    from lib.wiring import make_storyboard_dispatch
    dispatch = make_storyboard_dispatch(
        _storyboard_cfg(), dispatch_persona_fn=_make_director_persona(spike_sb, calls),
        plugin_root=_PLUGIN_ROOT, log=lambda m: None,
    )
    ctx = {"beats": [dict(b) for b in beats], "ep": ep}
    (tmp_path / "s").mkdir(parents=True, exist_ok=True)
    dispatch("director", {"name": "分镜"}, ctx, tmp_path / "s")

    assert len(calls) == 0, "SPIKE branch must not dispatch the director"
    assert [b["visual_type"] for b in ctx["beats"]] == ["still", "video", "chart"], (
        "MF#1: SPIKE reuse must back-prop visual_type onto beats"
    )
    assert ctx["beats"][1].get("video_prompt"), "SPIKE-reused video beat must carry video_prompt"
    assert ctx["beats"][2].get("chart"), "SPIKE-reused chart beat must carry the chart spec"


def test_script_durable_reuse_restores_authored_card_fields(tmp_path):
    """MF#2: the live script path sets ctx['title_subtitle'] / ['end_card_bullets']
    from the scratch digest JSON; those must survive a durable resume (persisted
    into beats.json) or the resumed title/end cards silently revert to defaults."""
    from lib.wiring import make_script_dispatch

    calls: list[int] = []
    beats = _grounded_beats(2)
    fulltext = _fulltext_for(beats)
    ep = _script_ep(tmp_path)
    subtitle = "三个要点，训练不再凭感觉"
    bullets = [["01", "乳酸阈"], ["02", "配速"]]

    def _persona_with_cards(agent, prompt, scratch_dir, expected, **kw):
        calls.append(1)
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps(
            {"beats": beats, "title_subtitle": subtitle, "end_card_bullets": bullets},
            ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    def _run(scratch_name):
        dispatch = make_script_dispatch(
            _script_cfg(), dispatch_persona_fn=_persona_with_cards,
            plugin_root=_PLUGIN_ROOT, log=lambda m: None,
        )
        ctx = {"fulltext": fulltext, "ep": ep}
        sd = tmp_path / scratch_name
        sd.mkdir(parents=True, exist_ok=True)
        dispatch("digest-writer", {"name": "script"}, ctx, sd)
        return ctx

    ctx1 = _run("s1")
    assert len(calls) == 1
    assert ctx1.get("title_subtitle") == subtitle
    assert ctx1.get("end_card_bullets") == bullets

    ctx2 = _run("s2")
    assert len(calls) == 1, "pass 2 must reuse durable beats.json (no re-dispatch)"
    assert ctx2.get("title_subtitle") == subtitle, (
        "MF#2: durable reuse must restore the authored title_subtitle"
    )
    assert ctx2.get("end_card_bullets") == bullets, (
        "MF#2: durable reuse must restore the authored end_card_bullets"
    )
