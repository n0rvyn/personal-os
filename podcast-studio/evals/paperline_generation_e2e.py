#!/usr/bin/env python3
"""Paper-line GENERATION e2e (P3 Task 8) — deterministic.

Proves the two load-bearing P3 acceptance points WITHOUT a live persona proxy
(the live committee/finalize dispatch is slow + proxy-gated; P2 already proved
live collection, and the generation LOGIC is unit-proven):

  Part A — the 忠实门 BLOCKS (the dev-guide's key acceptance): run the engine's
           gate `executors.check_faithfulness(verdict_path, ctx)` over a scratch
           staged with the real ledger + a faithful / exaggerated / dropped-
           limitation finalize body. Assert faithful PASSES, the other two are
           FLAGGED. This is the gate AS THE RUNNER CALLS IT (via _call_gate).

  Part B — the GENERATION chain runs through `run_pipeline("papers")`: a fake
           dispatch stages each agent station's artifact (committee drafts ≥ the
           floor, scores, a faithful finalize body, an empty judge verdict). The
           engine walks config→…→ledger-verify→committee→digest-score→digest-
           select→finalize→忠实门 and the faithful body clears the gate. Asserts
           every station ran via the engine (pays the generation-wiring debt).

Run: python3 evals/paperline_generation_e2e.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

STAGED_LEDGER = PLUGIN_ROOT / ".claude" / "p2-samples" / "paper-ledger.json"
FULLTEXT = PLUGIN_ROOT / ".claude" / "p2-samples" / "arxiv-2606.19341-pdftotext.txt"
FIXTURES = PLUGIN_ROOT / "lib" / "tests" / "fixtures" / "faithfulness"


def _stage_common(scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "paper-ledger.json").write_text(
        STAGED_LEDGER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (scratch / "fulltext.txt").write_text(
        FULLTEXT.read_text(encoding="utf-8"), encoding="utf-8"
    )


def part_a_faithfulness_blocks(tmp: Path) -> bool:
    """The 忠实门 gate (as the runner dispatches it) must PASS faithful, FLAG the
    exaggerated + dropped-limitation bodies."""
    from lib.paperline.executors import check_faithfulness

    print("\n[e2e] Part A — 忠实门 blocks (engine gate check_faithfulness):")
    ok_all = True
    for name, expect_ok in (("faithful", True), ("exaggerated", False), ("dropped_limitation", False)):
        scratch = tmp / f"a-{name}"
        _stage_common(scratch)
        body = (FIXTURES / f"{name}-draft.md").read_text(encoding="utf-8")
        (scratch / "finalize-result.json").write_text(
            json.dumps({"title": name, "body": body}, ensure_ascii=False), encoding="utf-8"
        )
        # Agent verdict: 溯源+夸大 are the code floor (empty claims). Coverage is
        # AGENT-ASSESSED (SF-2) — the dropped-limitation draft is caught because the
        # judge reports the drops; faithful/exaggerated report no drops (exaggerated
        # is still blocked by the code 夸大 floor).
        agent = {"claims": [], "faithful": True}
        if name == "dropped_limitation":
            agent = {"faithful": False, "dropped_limitations": [{"index": 0}, {"index": 1}, {"index": 2}]}
        verdict_path = scratch / "faithfulness-verdict.json"
        verdict_path.write_text(json.dumps(agent, ensure_ascii=False), encoding="utf-8")
        ctx = {"scratch_dir": scratch}
        out = check_faithfulness(verdict_path, ctx)
        hit = out["ok"] is expect_ok
        ok_all &= hit
        print(f"  {'PASS' if hit else 'FAIL'}  {name:18} ok={out['ok']} (expected {expect_ok}) — {out['reason']}")
    return ok_all


def part_b_generation_chain(tmp: Path, *, finalize_body: str = "faithful",
                            expect_ok: bool = True, label: str = "faithful") -> bool:
    """The generation chain runs through run_pipeline with a fake dispatch that
    stages each agent station's artifact.

    `finalize_body="faithful"` → the 忠实门 clears, status=ok (happy path).
    `finalize_body="exaggerated"` → the 忠实门 FLAGS, retry re-dispatches finalize
    (same body), 2nd flag HALTs (status != ok, no publish) — the engine-level
    negative control (SF-1): proves the gate blocks THROUGH run_pipeline, not just
    when called directly (Part A)."""
    from unittest.mock import MagicMock

    from lib.runner import run_pipeline
    from lib.paperline import discovery as _disc, fetch as _ft

    print("\n[e2e] Part B — generation chain through run_pipeline('papers'):")
    scratch = tmp / "b-chain"
    _stage_common(scratch)
    # Deterministic: skip load_config (pass a config) + stub the collection
    # stations' network so the chain reaches the generation half offline.
    config = MagicMock()
    config.papers.categories = ("cs.CL",)
    config.papers.max_candidates = 15
    # Give vault.output_dir a REAL path so the runner's output-subdir resolution
    # (Path(str(config.vault.output_dir))) doesn't stringify a MagicMock into a
    # junk "<MagicMock ...>" directory in the repo.
    (tmp / "b-out").mkdir(parents=True, exist_ok=True)
    config.vault.output_dir = str(tmp / "b-out")
    _FEED = (
        b"<feed xmlns='http://www.w3.org/2005/Atom' xmlns:arxiv='http://arxiv.org/schemas/atom'>"
        b"<entry><id>http://arxiv.org/abs/2606.19341v1</id><title>t</title><summary>s</summary>"
        b"<published>2026-06-14T00:00:00Z</published><arxiv:primary_category term='cs.CL'/>"
        b"<category term='cs.CL'/><link rel='related' type='application/pdf' href='http://arxiv.org/pdf/2606.19341v1'/>"
        b"</entry></feed>"
    )
    _disc._https_get = lambda url, *, timeout=30: _FEED
    _ft.fetch_fulltext = lambda arxiv_id, **kw: {"method": "html", "text": FULLTEXT.read_text(encoding="utf-8"), "source_url": "stub"}
    # discovery/curator/fetch/ledger-write produce the collection artifacts; we
    # stage them so the chain reaches the generation half deterministically.
    (scratch / "candidates.json").write_text(
        json.dumps([{"arxiv_id": "2606.19341v1", "title": "t", "summary": "s",
                     "published": "2026", "primary_category": "cs.CL",
                     "categories": ["cs.CL"], "pdf_url": "x"}]), encoding="utf-8"
    )
    (scratch / "chosen-arxiv-id.json").write_text(json.dumps({"arxiv_id": "2606.19341v1"}), encoding="utf-8")
    faithful_body = (FIXTURES / "faithful-draft.md").read_text(encoding="utf-8")
    final_body = (FIXTURES / f"{finalize_body}-draft.md").read_text(encoding="utf-8")
    floor_pad = faithful_body + ("\n\n" + "（讲解延展，把术语翻成大白话，保持忠实。）" * 200)
    inner_ledger = json.loads(STAGED_LEDGER.read_text(encoding="utf-8"))["ledger"]

    dispatched: list[str] = []

    def fake_dispatch(agent, user_prompt, scratch_dir, artifact_name, **kw):
        """Produce the artifact each agent station would — into the RUNNER's own
        scratch (`scratch_dir`), which is a fresh subdir, NOT the staging dir."""
        dispatched.append(agent)
        sd = Path(scratch_dir)
        if agent == "curator":
            (sd / artifact_name).write_text(
                json.dumps({"arxiv_id": "2606.19341v1", "rationale": "stub"}), encoding="utf-8")
        elif agent == "ledger-writer":
            # ledger-write produces the INNER 4-section ledger (problem/method/
            # key_results/limitations) — what ledger-verify + 忠实门 read.
            (sd / artifact_name).write_text(
                json.dumps(inner_ledger, ensure_ascii=False), encoding="utf-8")
        elif agent == "digest-writer":
            (sd / artifact_name).write_text(floor_pad, encoding="utf-8")
        elif agent == "digest-scorer":
            (sd / artifact_name).write_text(json.dumps({"candidates": [
                {"candidate_id": "稿-A", "scores": {"准确": 5, "清晰": 5, "框架还原": 5, "可读": 5, "total": 20}},
                {"candidate_id": "稿-B", "scores": {"准确": 3, "清晰": 3, "框架还原": 3, "可读": 3, "total": 12}},
                {"candidate_id": "稿-C", "scores": {"准确": 4, "清晰": 4, "框架还原": 4, "可读": 4, "total": 16}},
            ]}, ensure_ascii=False), encoding="utf-8")
        elif agent == "finalizer":
            (sd / artifact_name).write_text(
                json.dumps({"title": "OmniAgent 科普解读", "body": final_body}, ensure_ascii=False),
                encoding="utf-8")
        elif agent == "faithfulness-judge":
            (sd / artifact_name).write_text(json.dumps({"claims": [], "faithful": True}), encoding="utf-8")
        else:
            (sd / artifact_name).write_text("{}", encoding="utf-8")
        return {"ok": True}

    result = run_pipeline(
        "papers", date="2026-06-18", no_tts=True,
        dispatch=fake_dispatch, scratch_dir=scratch, plugin_root=PLUGIN_ROOT,
        config=config,
    )
    status = result.get("status") if isinstance(result, dict) else result
    failed_step = result.get("failed_step") if isinstance(result, dict) else None
    gen = ("digest-writer", "digest-scorer", "finalizer", "faithfulness-judge")
    gen_dispatched = sorted({a for a in dispatched if a in gen})
    print(f"  [{label}] run_pipeline status = {status}" + (f" (failed_step={failed_step})" if failed_step else ""))
    print(f"  [{label}] generation agents dispatched = {gen_dispatched}")
    # Happy path: status==ok proves the engine walked EVERY station + 忠实门 cleared.
    # Block path: an exaggerated body → 忠实门 flags → retry re-dispatches finalize →
    # 2nd flag HALTs (status!='ok', failed_step='faithfulness', no .md).
    status_matches = (status == "ok") == expect_ok
    halt_correct = expect_ok or (failed_step == "faithfulness")  # block must die AT 忠实门
    ok = status_matches and halt_correct and "finalizer" in dispatched and "faithfulness-judge" in dispatched
    desc = "cleared 忠实门 (happy path)" if expect_ok else "HALTED at 忠实门 (engine-level block + retry-then-stop)"
    print(f"  [{label}] {'PASS' if ok else 'FAIL'}  generation chain through run_pipeline — {desc}")
    return ok


def main() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="paperline-gen-e2e-"))
    a = part_a_faithfulness_blocks(tmp)
    b = part_b_generation_chain(tmp, finalize_body="faithful", expect_ok=True, label="faithful")
    print("\n[e2e] Part C — engine-level block (exaggerated body through run_pipeline):")
    c = part_b_generation_chain(tmp, finalize_body="exaggerated", expect_ok=False, label="exaggerated")
    print(f"\n[e2e] DONE  Part A (忠实门 gate blocks)={'PASS' if a else 'FAIL'}  "
          f"Part B (happy chain)={'PASS' if b else 'FAIL'}  "
          f"Part C (engine-level block + retry-then-stop)={'PASS' if c else 'FAIL'}")
    return 0 if (a and b and c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
