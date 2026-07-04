"""lib/pipeline.py — Phase-2 step-table + validation.

Pure data module: no execution logic, no imports from the execution layer.
The runner (lib/runner.py) resolves `fn` names and dispatches agents;
this module stores only names and metadata.
"""
from __future__ import annotations

import copy
from typing import Any


# ---------------------------------------------------------------------------
# Known gate function names (validation set; runner maps these to callables)
# ---------------------------------------------------------------------------
_KNOWN_GATE_FNS: set[str] = {
    "check_artifact",
}

# ---------------------------------------------------------------------------
# Phase-2 pipeline step table
#
# Step shape:
#   name     : str          — unique step identifier
#   kind     : 'code'|'agent' — execution kind
#   agent    : str|None     — agent identifier (required when kind='agent')
#   inputs   : list[str]    — ctx keys consumed by this step
#   artifact : str|None     — scratch/episode path key produced (or None)
#   gate     : list[dict]|None — post-step gate checks; each dict has 'fn'
#
# Ordering guarantees (enforced by list position):
#   config → cursor-read → scratch → extract → tts
#   → gen-still → gen-video → chart → subtitle
#   → assemble → provenance → publish → cleanup
# ---------------------------------------------------------------------------
_PIPELINE: list[dict[str, Any]] = [
    # 0 — load config; no prior context required
    {
        "name": "config",
        "kind": "code",
        "agent": None,
        "inputs": [],
        "artifact": None,
        "gate": None,
    },
    # 1 — read cursor / syllabus; writes cursor_marker so check_artifact can gate it
    {
        "name": "cursor-read",
        "kind": "code",
        "agent": None,
        "inputs": ["config"],
        "artifact": "cursor_marker",
        "gate": [{"fn": "check_artifact"}],
    },
    # 2 — create episode + scratch directories
    {
        "name": "scratch",
        "kind": "code",
        "agent": None,
        "inputs": ["config"],
        "artifact": "scratch_dir",
        "gate": [{"fn": "check_artifact"}],
    },
    # 3 — extract source text (pdftotext -layout per-page) OR load fixture beats
    {
        "name": "extract",
        "kind": "code",
        "agent": None,
        "inputs": ["cursor-read", "config"],
        "artifact": None,
        "gate": None,
    },
    # 3a2 — avoid-memo (anti-homogenization, B5): pure-code step that reads the
    # cross-episode covered-ground store and writes the avoidance memo into
    # ctx['avoid_memo'] BEFORE the writer runs, so make_script_dispatch can hand
    # the writer "这些类比/例子/钩子最近用滥了，避开". Non-gating (artifact=None,
    # gate=[] is falsy → runner skips gating); step_avoid_memo is fail-soft
    # (store error → memo='').
    {
        "name": "avoid-memo",
        "kind": "code",
        "agent": None,
        "inputs": ["config"],
        "artifact": None,
        "gate": [],
    },
    # 3b — script: digest-writer → beats, gated by the 忠实门 (verify_anchors)
    # inside the dispatch closure. retry=1: a faithfulness miss re-dispatches the
    # writer once (runner re-runs THIS step on gate miss); double-miss → halt.
    # Fixture/reuse mode (beats already in ctx) → pass-through (no live dispatch).
    {
        "name": "script",
        "kind": "agent",
        "agent": "digest-writer",
        "inputs": ["extract", "config"],
        "artifact": "script_ledger",
        "gate": [{"fn": "check_artifact"}],
        "retry": 1,
    },
    # 3b2 — distill: covered-ground 蒸馏器 (anti-homogenization). Runs on the
    # just-finalized台本 (script gate passed) to extract this episode's
    # signature apparatus (类比/例子/钩子) into covered.json for the next
    # episode's avoidance memo. Non-gating (artifact=None, gate=[]; gate=[] is
    # falsy → runner skips gating, mirroring query-writer): a distiller failure
    # must NEVER halt an already-rendered episode — the dispatch closure wraps
    # its whole body in try/except (MR-5, runner does not wrap steps).
    {
        "name": "distill",
        "kind": "agent",
        "agent": "covered-ground-distiller",
        "inputs": ["script", "config"],
        "artifact": None,
        "gate": [],
    },
    # 3b3 — covered-update (anti-homogenization, B6): pure-code station that
    # folds the just-distilled covered.json anchors into the cross-episode
    # store (<output_dir>/covered-ground.yaml), so the NEXT episode's avoid-memo
    # sees them. Non-gating (artifact=None, gate=[]); update_covered_ground is
    # fully fail-soft (whole body try/except → stderr) — a store write failure
    # must never halt an already-rendered episode.
    {
        "name": "covered-update",
        "kind": "code",
        "agent": None,
        "inputs": ["distill", "config"],
        "artifact": None,
        "gate": [],
    },
    # 3c — 分镜: director → shotlist.json, gated by check_artifact.
    # retry=1: bad JSON → no artifact → gate fail → re-dispatch director once;
    # double miss → halt (failed_step="分镜"). Budget gate (video ≤ cap) lives
    # inside the dispatch closure (needs ctx + cfg, stock gate fn can't see them).
    # Fixture/env-reuse mode → pass-through (zero live dispatch).
    {
        "name": "分镜",
        "kind": "agent",
        "agent": "director",
        "inputs": ["script", "config"],
        "artifact": "shotlist_art",
        "gate": [{"fn": "check_artifact"}],
        "retry": 1,
    },
    # 4 — text-to-speech for all beats
    {
        "name": "tts",
        "kind": "code",
        "agent": None,
        "inputs": ["extract", "config", "scratch_dir"],
        "artifact": "audio_dir",
        "gate": [{"fn": "check_artifact"}],
    },
    # 4b — Phase-1 shot-timeline: pure-code step that turns each beat's real
    # (post-tts) clip_len + director shots into N distinct content windows
    # (lib/shotplan.plan_windows). No agent dispatch, no gate (gate=[] is
    # falsy → runner.py's `if not step.get("gate"): break` skips gating).
    {
        "name": "镜头计划",
        "kind": "code",
        "agent": None,
        "inputs": ["tts", "分镜", "config"],
        "artifact": None,
        "gate": [],
    },
    # 4c — Phase-3 content-matched scene query per window (D-007). Non-gating
    # (artifact=None, gate=[]; MR#1): a dispatch failure never halts the
    # runner — the closure swallows it and windows keep their interim
    # prompt (no-regression fallback). Fixture render evals short-circuit
    # (MR#2) with zero live claude -p.
    {
        "name": "query-writer",
        "kind": "agent",
        "agent": "query-writer",
        "inputs": ["镜头计划", "config"],
        "artifact": None,
        "gate": [],
    },
    # 5 — generate still images for beats (image provider: minimax or doubao)
    {
        "name": "gen-still",
        "kind": "code",
        "agent": None,
        "inputs": ["tts", "config", "scratch_dir"],
        "artifact": "stills_dir",
        "gate": [{"fn": "check_artifact"}],
    },
    # 6 — generate S2V video clips for character shots
    {
        "name": "gen-video",
        "kind": "code",
        "agent": None,
        "inputs": ["gen-still", "config", "scratch_dir"],
        "artifact": "clips_dir",
        "gate": [{"fn": "check_artifact"}],
    },
    # 7 — render chart clips (code-rendered; no AI provider)
    {
        "name": "chart",
        "kind": "code",
        "agent": None,
        "inputs": ["tts", "config", "scratch_dir"],
        "artifact": "charts_dir",
        "gate": [{"fn": "check_artifact"}],
    },
    # 8 — burn subtitles SRT
    {
        "name": "subtitle",
        "kind": "code",
        "agent": None,
        "inputs": ["gen-still", "gen-video", "chart", "config", "scratch_dir"],
        "artifact": "subtitles_srt",
        "gate": [{"fn": "check_artifact"}],
    },
    # 9 — assemble final video (xfade + mux + BGM ducking)
    {
        "name": "assemble",
        "kind": "code",
        "agent": None,
        "inputs": ["subtitle", "tts", "config", "scratch_dir"],
        "artifact": "final_mp4",
        "gate": [{"fn": "check_artifact"}],
    },
    # 10 — write provenance record (placeholder; Phase 4+)
    {
        "name": "provenance",
        "kind": "code",
        "agent": None,
        "inputs": ["assemble", "config"],
        "artifact": None,
        "gate": None,
    },
    # 11 — publish to output location (placeholder; Phase 7+)
    {
        "name": "publish",
        "kind": "code",
        "agent": None,
        "inputs": ["provenance", "config"],
        "artifact": None,
        "gate": None,
    },
    # 12 — clean up scratch on success
    {
        "name": "cleanup",
        "kind": "code",
        "agent": None,
        "inputs": ["publish"],
        "artifact": None,
        "gate": None,
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pipeline() -> list[dict[str, Any]]:
    """Return a fresh, independent copy of the Phase-2 pipeline step list.

    Each call returns a new list; mutating one result does not affect others.
    The returned list is purely serialisable data — no callables embedded.
    """
    return copy.deepcopy(_PIPELINE)


_REQUIRED_STEP_FIELDS = frozenset({"name", "kind", "agent", "inputs", "artifact", "gate"})
_VALID_KINDS = frozenset({"code", "agent"})


def validate_pipeline(steps: list[dict[str, Any]]) -> None:
    """Validate a pipeline step list.  Raises ValueError on the first violation.

    Checks (fail-closed):
    - `steps` is a list.
    - Each step is a dict with all required fields.
    - `kind` is 'code' or 'agent'.
    - kind='agent' steps have a non-empty `agent` string.
    - `gate`, when present, is a list of dicts each with a non-empty `fn` string.
    """
    if not isinstance(steps, list):
        raise ValueError(
            f"validate_pipeline expects a list of step dicts, "
            f"got {type(steps).__name__!r}"
        )

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(
                f"steps[{idx}] must be a dict, got {type(step).__name__!r}"
            )

        # Required fields
        for field in _REQUIRED_STEP_FIELDS:
            if field not in step:
                raise ValueError(
                    f"steps[{idx}] ({step.get('name')!r}) missing required field: "
                    f"'name'" if field == "name" else
                    f"steps[{idx}] ({step.get('name')!r}) missing required field: "
                    f"{field!r}"
                )

        name = step.get("name", f"<index {idx}>")

        # kind validation
        kind = step["kind"]
        if kind not in _VALID_KINDS:
            raise ValueError(
                f"step {name!r} kind must be 'code' or 'agent', got {kind!r}"
            )

        # agent validation
        agent = step["agent"]
        if kind == "agent":
            if not isinstance(agent, str) or not agent:
                raise ValueError(
                    f"step {name!r} kind='agent' requires a non-empty string "
                    f"'agent' field, got {agent!r}"
                )

        # gate validation
        gate = step["gate"]
        if gate is not None:
            if not isinstance(gate, list):
                raise ValueError(
                    f"step {name!r} gate must be a list or None, "
                    f"got {type(gate).__name__!r}"
                )
            for j, gate_item in enumerate(gate):
                if not isinstance(gate_item, dict):
                    raise ValueError(
                        f"step {name!r} gate[{j}] must be a dict, "
                        f"got {type(gate_item).__name__!r}"
                    )
                if "fn" not in gate_item:
                    raise ValueError(
                        f"step {name!r} gate[{j}] missing required field 'fn'"
                    )
                fn_val = gate_item["fn"]
                if not isinstance(fn_val, str) or not fn_val:
                    raise ValueError(
                        f"step {name!r} gate[{j}].fn must be a non-empty string, "
                        f"got {fn_val!r}"
                    )
