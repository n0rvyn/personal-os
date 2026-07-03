"""Phase 4: thesis_memo pre-draft steering (lib/runner.py).

Pins:
- _assemble_briefs renders the thesis_memo from the thesis-ledger and writes it
  into every writing-brief + ctx (peer to the covered-ground avoid_memo)
- _build_draft_prompt surfaces the thesis_memo with continuation/change-spine
  wording; empty ledger → explicit "(无 thesis-ledger 续写约束)" placeholder
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _hot_ledger_store():
    return {"theses": {
        "判断力是最后护城河": {
            "first_used": "2026-06-24", "last_used": "2026-06-30", "count": 2,
            "statements": ["判断力是最后护城河"],
            "episodes": [
                {"date": "2026-06-24", "show": "evening", "advanced": False, "bet_ids": []},
                {"date": "2026-06-30", "show": "morning", "advanced": False, "bet_ids": []},
            ],
        },
    }}


def test_assemble_briefs_writes_thesis_memo(tmp_path):
    from lib.runner import _assemble_briefs
    from lib.thesis_ledger import write_store
    scratch = tmp_path / "scratch"
    state = tmp_path / "state"
    scratch.mkdir()
    state.mkdir()
    write_store(str(state), _hot_ledger_store())
    ctx = {
        "scratch_dir": scratch,
        "state_dir": str(state),
        "date": "2026-06-30",
        "show": "morning",
    }
    _assemble_briefs(ctx)
    assert "判断力是最后护城河" in ctx["thesis_memo"]
    brief = json.loads((scratch / "writing-brief-A.json").read_text(encoding="utf-8"))
    assert "判断力是最后护城河" in brief["thesis_memo"]


def test_draft_prompt_surfaces_thesis_memo(tmp_path):
    from lib.runner import _build_draft_prompt
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    brief_path = scratch / "writing-brief-A.json"
    brief_path.write_text(json.dumps({
        "candidate": "A", "magnitude": "light", "airtime": "brief",
        "avoid_memo": "", "thesis_memo": "近期讲过、但没往前推进的论点 …\n- 判断力是最后护城河（已用 2 次）",
        "continuity": {},
    }, ensure_ascii=False), encoding="utf-8")
    prompt = _build_draft_prompt("A", brief_path, scratch, {"editorial": "", "date": "2026-06-30", "show": "morning"})
    assert "判断力是最后护城河" in prompt
    assert "换题" in prompt or "续写" in prompt


def test_draft_prompt_placeholder_when_no_thesis_memo(tmp_path):
    from lib.runner import _build_draft_prompt
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    bp = scratch / "writing-brief-A.json"
    bp.write_text(json.dumps({"candidate": "A", "airtime": "brief", "avoid_memo": "",
                              "thesis_memo": "", "continuity": {}}, ensure_ascii=False), encoding="utf-8")
    prompt = _build_draft_prompt("A", bp, scratch, {"editorial": "", "date": "2026-06-30", "show": "morning"})
    assert "无 thesis-ledger 续写约束" in prompt
