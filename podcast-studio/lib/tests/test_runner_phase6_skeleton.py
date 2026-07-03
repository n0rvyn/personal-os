"""Phase 6 wiring: the chosen skeleton reaches the davinci draft prompt.

Pins:
- _assemble_briefs picks+records an evening skeleton into ctx + brief
- _build_draft_prompt injects the skeleton's structure
- morning gets NO skeleton (out of scope — 四段 event-centric)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def test_assemble_picks_evening_skeleton_and_records(tmp_path):
    from lib.runner import _assemble_briefs
    from lib.skeleton import load_history
    scratch = tmp_path / "scratch"
    state = tmp_path / "state"
    scratch.mkdir()
    state.mkdir()
    ctx = {"scratch_dir": scratch, "state_dir": str(state),
           "date": "2026-06-30", "show": "evening"}
    _assemble_briefs(ctx)
    assert ctx["skeleton"].get("id")  # a template was chosen
    # recorded to history
    hist = load_history(str(state))
    assert any(h["date"] == "2026-06-30" and h["show"] == "evening" for h in hist)
    # brief carries it
    brief = json.loads((scratch / "writing-brief-A.json").read_text(encoding="utf-8"))
    assert brief["skeleton"]["id"] == ctx["skeleton"]["id"]


def test_morning_gets_no_skeleton(tmp_path):
    from lib.runner import _assemble_briefs
    scratch = tmp_path / "scratch"
    state = tmp_path / "state"
    scratch.mkdir()
    state.mkdir()
    ctx = {"scratch_dir": scratch, "state_dir": str(state),
           "date": "2026-06-30", "show": "morning"}
    _assemble_briefs(ctx)
    assert ctx["skeleton"] == {}


def test_draft_prompt_injects_skeleton(tmp_path):
    from lib.runner import _build_draft_prompt
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    bp = scratch / "writing-brief-A.json"
    bp.write_text(json.dumps({
        "candidate": "A", "airtime": "brief", "avoid_memo": "", "thesis_memo": "",
        "skeleton": {"id": "dual-track", "name": "双线对照体",
                     "structure": "① 并置两个画面；② 合流；③ 收尾。"},
        "continuity": {},
    }, ensure_ascii=False), encoding="utf-8")
    prompt = _build_draft_prompt("A", bp, scratch,
                                 {"editorial": "", "date": "2026-06-30", "show": "evening"})
    assert "双线对照体" in prompt
    assert "并置两个画面" in prompt
