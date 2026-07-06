"""Phase-3 pipeline tests: the `script` agent step's 忠实门 retry/halt semantics
and the make_script_dispatch closure (fixture pass-through / PDF dispatch+gate).

The runner re-runs the SAME step on a gate miss (runner.py retry loop), so the
faithfulness gate lives INSIDE the script dispatch closure: grounded → write the
`script_ledger` artifact (gate passes); flagged → no artifact (gate fails →
retry; double-miss → halt, failed_step="script").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

LAYOUT_FT = "71.0  2:36.2  5:20.0/5:45.7\n乳酸阈 T 配速 vdot\n"


def _script_step():
    return {
        "name": "script", "kind": "agent", "agent": "digest-writer",
        "inputs": [], "artifact": "script_ledger",
        "gate": [{"fn": "check_artifact"}], "retry": 1,
    }


# ---------------- runner retry/halt with the script step ----------------

def test_pipeline_has_single_script_agent_step_with_retry():
    from lib.pipeline import load_pipeline, validate_pipeline
    p = load_pipeline()
    validate_pipeline(p)
    scripts = [s for s in p if s["name"] == "script"]
    assert len(scripts) == 1
    s = scripts[0]
    assert s["kind"] == "agent" and s["agent"] == "digest-writer"
    assert s.get("retry") == 1
    # ordered after extract, before tts
    names = [x["name"] for x in p]
    assert names.index("extract") < names.index("script") < names.index("tts")


def test_script_passes_after_retry(tmp_path):
    """First dispatch misses (no artifact), retry grounds → run completes."""
    from lib.runner import run_pipeline
    from lib.episode import check_artifact

    state = {"n": 0}

    def dispatch(agent, step, ctx, scratch_dir):
        state["n"] += 1
        if state["n"] >= 2:  # grounded on the retry
            art = Path(scratch_dir) / "script_ledger.json"
            art.write_text("{}", encoding="utf-8")
            ctx["script_ledger"] = str(art)
        return {"ok": True}

    scratch = tmp_path / "s"; scratch.mkdir()
    res = run_pipeline(object(), pipeline=[_script_step()], step_fns={},
                       dispatch=dispatch, gates={"check_artifact": check_artifact},
                       scratch_dir=scratch)
    assert res["status"] == "done"
    assert state["n"] == 2  # exactly one retry


def test_script_double_miss_halts_at_script(tmp_path):
    """Fabricated on both attempts → halt, failed_step='script'."""
    from lib.runner import run_pipeline
    from lib.episode import check_artifact

    def dispatch(agent, step, ctx, scratch_dir):
        return {"ok": True}  # never writes the artifact

    scratch = tmp_path / "s"; scratch.mkdir()
    res = run_pipeline(object(), pipeline=[_script_step()], step_fns={},
                       dispatch=dispatch, gates={"check_artifact": check_artifact},
                       scratch_dir=scratch)
    assert res["status"] == "halted"
    assert res["failed_step"] == "script"


# ---------------- make_script_dispatch closure (the 忠实门 host) ----------------

def test_fixture_passthrough_writes_artifact(tmp_path):
    from lib.wiring import make_script_dispatch
    d = make_script_dispatch(object())
    ctx = {"beats": [{"beat_id": "b1", "text": "t", "anchor": "a"}]}
    d("digest-writer", _script_step(), ctx, tmp_path)
    assert "script_ledger" in ctx
    assert Path(ctx["script_ledger"]).exists()


def test_pdf_mode_grounded_writes_artifact_and_script_md(tmp_path):
    from lib.wiring import make_script_dispatch

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b1", "text": "乳酸阈配速", "anchor": "乳酸阈 T 配速 5:20"}
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    ep = {"script": tmp_path / "script.md", "beats": tmp_path / "beats.json"}
    ctx = {"fulltext": LAYOUT_FT, "ep": ep}
    d = make_script_dispatch(object(), dispatch_persona_fn=fake_persona)
    d("digest-writer", _script_step(), ctx, tmp_path)
    assert "script_ledger" in ctx          # 忠实门 passed
    assert ctx["beats"][0]["beat_id"] == "b1"
    assert ep["script"].exists()           # script.md deliverable written


def test_pdf_mode_fabricated_flags_no_artifact(tmp_path):
    from lib.wiring import make_script_dispatch

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b1", "text": "编造", "anchor": "配速 5:25"}  # 5:25 not in source
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    ctx = {"fulltext": LAYOUT_FT}
    d = make_script_dispatch(object(), dispatch_persona_fn=fake_persona)
    d("digest-writer", _script_step(), ctx, tmp_path)
    assert "script_ledger" not in ctx          # gate would fail → retry
    assert ctx["_faithfulness_flagged"][0]["beat_id"] == "b1"


def test_pdf_mode_malformed_json_is_miss_not_crash(tmp_path):
    """Malformed digest JSON (e.g. unescaped inner quote) → no artifact (retry),
    never a pipeline crash."""
    from lib.wiring import make_script_dispatch

    def bad_json_persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text('{"beats": [{"text": "叫"工具"的东西"}]}', encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    ctx = {"fulltext": LAYOUT_FT}
    d = make_script_dispatch(object(), dispatch_persona_fn=bad_json_persona)
    d("digest-writer", _script_step(), ctx, tmp_path)  # must not raise
    assert "script_ledger" not in ctx  # treated as a miss → would retry


def test_pdf_mode_empty_or_unsourced_beats_is_miss_not_pass(tmp_path):
    """G-1: validate_ledger must be wired — empty beats / missing / empty anchor
    must NOT clear the 忠实门 (else an unsourced script passes the gate)."""
    from lib.wiring import make_script_dispatch

    for bad in (
        {"beats": []},                                          # empty
        {"beats": [{"beat_id": "b1", "text": "t"}]},            # no anchor
        {"beats": [{"beat_id": "b1", "text": "t", "anchor": ""}]},  # empty anchor
    ):
        def persona(agent, prompt, scratch_dir, expected, _b=bad, **kw):
            out = Path(scratch_dir) / expected
            out.write_text(json.dumps(_b), encoding="utf-8")
            return {"ok": True, "artifact_path": str(out)}

        ctx = {"fulltext": LAYOUT_FT}
        d = make_script_dispatch(object(), dispatch_persona_fn=persona)
        d("digest-writer", _script_step(), ctx, tmp_path)
        assert "script_ledger" not in ctx, f"unsourced beats cleared the gate: {bad}"


def test_pdf_mode_retry_then_ground(tmp_path):
    """Stateful persona: fabricated → flagged → faithful → grounded (full run)."""
    from lib.runner import run_pipeline
    from lib.episode import check_artifact
    from lib.wiring import make_script_dispatch

    state = {"n": 0}

    def fake_persona(agent, prompt, scratch_dir, expected, **kw):
        state["n"] += 1
        out = Path(scratch_dir) / expected
        anchor = "配速 5:25" if state["n"] == 1 else "乳酸阈 T 配速 5:20"
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b1", "text": "t", "anchor": anchor}
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    # extract-stub step seeds ctx['fulltext'] (PDF mode), then the script step.
    def seed_fulltext(step, ctx, scratch_dir):
        ctx["fulltext"] = LAYOUT_FT

    extract_step = {"name": "extract", "kind": "code", "agent": None,
                    "inputs": [], "artifact": None, "gate": None}
    d = make_script_dispatch(object(), dispatch_persona_fn=fake_persona)
    scratch = tmp_path / "s"; scratch.mkdir()
    res = run_pipeline(object(), pipeline=[extract_step, _script_step()],
                       step_fns={"extract": seed_fulltext}, dispatch=d,
                       gates={"check_artifact": check_artifact}, scratch_dir=scratch)
    assert res["status"] == "done"
    assert state["n"] == 2  # one retry then grounded


# ---------------- R2: host persona injected into the digest-writer prompt ----------------

def test_host_persona_injected_into_prompt(tmp_path):
    """When cfg.host.name is set, the dispatched prompt tells the writer to open
    with a greeting-as-hook + sign off in the given tone."""
    from types import SimpleNamespace
    from lib.wiring import make_script_dispatch
    from lib.config import HostConfig

    captured = {}

    def capturing_persona(agent, prompt, scratch_dir, expected, **kw):
        captured["prompt"] = prompt
        out = Path(scratch_dir) / expected
        # return a grounded beat so the flow completes cleanly (not the focus here)
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b0", "text": "示例", "anchor": "配速 5:25"}
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = SimpleNamespace(host=HostConfig(name="卞旸", channel="跑者学堂", tone="亲切"))
    ctx = {"fulltext": LAYOUT_FT}
    d = make_script_dispatch(cfg, dispatch_persona_fn=capturing_persona)
    d("digest-writer", _script_step(), ctx, tmp_path)

    p = captured["prompt"]
    assert "卞旸" in p and "跑者学堂" in p and "亲切" in p
    assert "问候" in p and "签名" in p


def test_no_host_no_greeting_directive(tmp_path):
    """No host configured → prompt carries no greeting/signoff directive (cold open)."""
    from types import SimpleNamespace
    from lib.wiring import make_script_dispatch
    from lib.config import HostConfig

    captured = {}

    def capturing_persona(agent, prompt, scratch_dir, expected, **kw):
        captured["prompt"] = prompt
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b0", "text": "示例", "anchor": "配速 5:25"}
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = SimpleNamespace(host=HostConfig())  # empty host
    ctx = {"fulltext": LAYOUT_FT}
    d = make_script_dispatch(cfg, dispatch_persona_fn=capturing_persona)
    d("digest-writer", _script_step(), ctx, tmp_path)
    assert "本期主播" not in captured["prompt"]
