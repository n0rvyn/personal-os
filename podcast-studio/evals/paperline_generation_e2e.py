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
                            expect_ok: bool = True, label: str = "faithful",
                            short_slice: str | None = None, short_finalize: bool = False,
                            expect_failed_step: str | None = None,
                            expect_finalize_dispatches: int | None = None,
                            out_root_override: "Path | None" = None) -> bool:
    """The generation chain runs through run_pipeline with a fake dispatch that
    stages each agent station's artifact.

    `finalize_body="faithful"` → the 忠实门 clears, status=ok (happy path).
    `finalize_body="exaggerated"` → the 忠实门 FLAGS, retry re-dispatches finalize
    (same body), 2nd flag HALTs (status != ok, no publish) — the engine-level
    negative control (SF-1): proves the gate blocks THROUGH run_pipeline, not just
    when called directly (Part A).

    Length-floor placement knobs (过长度门 moved committee → finalize body):
    - `short_slice="B"` → committee slice B is written BELOW the floor. Under the
      OLD per-slice committee floor this halted the whole run; under the new
      existence-only committee gate it MUST pass (the short draft is discarded at
      digest-select, never airs). Pair with expect_ok=True.
    - `short_finalize=True` → the finalize BODY is written below the floor →
      exercises the finalize-body floor + retry=3 (halts AT finalize, never
      reaching 忠实门). Pair with expect_failed_step="finalize",
      expect_finalize_dispatches=4 (1 initial + 3 retries).
    `expect_failed_step` / `expect_finalize_dispatches`: optional extra assertions
    on the halt site + the finalize re-dispatch count."""
    from unittest.mock import MagicMock

    from lib.runner import run_pipeline
    from lib.paperline import discovery as _disc, fetch as _ft

    print("\n[e2e] Part B — generation chain through run_pipeline('papers'):")
    # Label-keyed dirs so each scenario is isolated: Part B (faithful) PUBLISHES
    # a 2026-06-18 episode, and the same-day guard (DP-404=A) would then halt any
    # later scenario sharing the same papers episodes dir + date. Per-label dirs
    # keep the guard's real behavior without cross-contaminating the scenarios.
    scratch = tmp / f"b-chain-{label}"
    _stage_common(scratch)
    # Deterministic: skip load_config (pass a config) + stub the collection
    # stations' network so the chain reaches the generation half offline.
    config = MagicMock()
    config.papers.categories = ("cs.CL",)
    config.papers.max_candidates = 15
    # Give vault.output_dir a REAL path so the runner's output-subdir resolution
    # (Path(str(config.vault.output_dir))) doesn't stringify a MagicMock into a
    # junk "<MagicMock ...>" directory in the repo.
    out_root = out_root_override if out_root_override is not None else tmp / f"b-out-{label}"
    out_root.mkdir(parents=True, exist_ok=True)
    config.vault.output_dir = str(out_root)
    # P4: the publish-half code stations (same-day-guard / paper-log-read /
    # paper-log-write / publish) resolve the PAPER subdirs via
    # `_paper_subdir` → config.vault.papers_<kind>_dir. Set them to REAL tmp
    # paths (else MagicMock stringifies them into junk "<MagicMock ...>" dirs).
    papers_episodes = out_root / "papers" / "episodes"
    papers_state = out_root / "papers" / "state"
    papers_reports = out_root / "papers" / "reports"
    for _d in (papers_episodes, papers_state, papers_reports):
        _d.mkdir(parents=True, exist_ok=True)
    config.vault.papers_episodes_dir = str(papers_episodes)
    config.vault.papers_state_dir = str(papers_state)
    config.vault.papers_reports_dir = str(papers_reports)
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
    # Neutral filler: no absolute-strength phrases + no factual claims, so it
    # clears the length floor WITHOUT touching any 忠实门 verdict (a faithful
    # body stays faithful; an exaggerated body keeps its fixture's 夸大 phrases).
    _pad = "\n\n" + "（讲解延展，把术语翻成大白话，保持忠实。）" * 200
    floor_pad = faithful_body + _pad
    # The finalize body is now the floored deliverable (step 11 carries
    # check_min_chars json_field="body"); pad it past 4500 so Part B clears the
    # floor and Part C reaches 忠实门 (the short raw fixtures would otherwise
    # halt at finalize, never reaching the gate this e2e exercises).
    final_body_padded = final_body + _pad
    inner_ledger = json.loads(STAGED_LEDGER.read_text(encoding="utf-8"))["ledger"]

    dispatched: list[str] = []

    def fake_dispatch(agent, user_prompt, scratch_dir, artifact_name, **kw):
        """Produce the artifact each agent station would — into the RUNNER's own
        scratch (`scratch_dir`), which is a fresh subdir, NOT the staging dir."""
        dispatched.append(agent)
        sd = Path(scratch_dir)
        if agent == "curator":
            # Emit concepts (as curator.md now does) so the non-empty concepts
            # path is exercised end-to-end: curator → chosen-arxiv-id.json →
            # paper-log-write → paper-log.yaml carries them (review should-fix #2).
            (sd / artifact_name).write_text(
                json.dumps({"arxiv_id": "2606.19341v1", "rationale": "stub",
                            "concepts": ["视频理解", "agent 循环"]}, ensure_ascii=False),
                encoding="utf-8")
        elif agent == "ledger-writer":
            # ledger-write produces the INNER 4-section ledger (problem/method/
            # key_results/limitations) — what ledger-verify + 忠实门 read.
            (sd / artifact_name).write_text(
                json.dumps(inner_ledger, ensure_ascii=False), encoding="utf-8")
        elif agent == "digest-writer":
            # short_slice: write ONE committee slice below the floor. The OLD
            # per-slice committee floor halted at committee:<slice>; the new
            # existence-only gate must let it through (discarded at select).
            if short_slice and artifact_name.endswith(f"draft-{short_slice}.md"):
                (sd / artifact_name).write_text("x" * 100, encoding="utf-8")  # <4500, >50 stub bypass
            else:
                (sd / artifact_name).write_text(floor_pad, encoding="utf-8")
        elif agent == "digest-scorer":
            (sd / artifact_name).write_text(json.dumps({"candidates": [
                {"candidate_id": "稿-A", "scores": {"准确": 5, "清晰": 5, "框架还原": 5, "可读": 5, "total": 20}},
                {"candidate_id": "稿-B", "scores": {"准确": 3, "清晰": 3, "框架还原": 3, "可读": 3, "total": 12}},
                {"candidate_id": "稿-C", "scores": {"准确": 4, "清晰": 4, "框架还原": 4, "可读": 4, "total": 16}},
            ]}, ensure_ascii=False), encoding="utf-8")
        elif agent == "finalizer":
            # short_finalize: write the unpadded (<4500) body to exercise the
            # finalize-body floor + retry=3 (halts at finalize, never 忠实门).
            body = final_body if short_finalize else final_body_padded
            (sd / artifact_name).write_text(
                json.dumps({"title": "OmniAgent 科普解读", "body": body}, ensure_ascii=False),
                encoding="utf-8")
        elif agent == "faithfulness-judge":
            (sd / artifact_name).write_text(json.dumps({"claims": [], "faithful": True}), encoding="utf-8")
        elif agent == "broadcaster":
            # P4: 口播改写 → spoken script (plain text). no_tts skips the jay
            # TTS step downstream, so content is nominal — just non-empty.
            (sd / artifact_name).write_text("各位听众，今天我们聊一篇论文……", encoding="utf-8")
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
    checks = {"status": status_matches}
    if expect_failed_step is not None:
        checks["failed_step"] = (failed_step == expect_failed_step)
    if expect_finalize_dispatches is not None:
        checks["finalize_dispatches"] = (dispatched.count("finalizer") == expect_finalize_dispatches)
    if expect_ok:
        # Happy path must walk the whole chain through the 忠实门 AND the P4
        # publish half: the .md lands in the PAPER episodes dir and the paper is
        # recorded in paper-log (DP-601=B: log written BEFORE publish).
        checks["full_chain"] = ("finalizer" in dispatched and "faithfulness-judge" in dispatched)
        published = list(papers_episodes.glob("2026-06-18-*.md"))
        checks["published_md_in_papers_dir"] = bool(published)
        try:
            from lib.paperline.paperlog import is_covered, load_paperlog
            _log = load_paperlog(str(papers_state))
            checks["paper_logged"] = is_covered(_log, "2606.19341v1")
            # Concepts path e2e (review should-fix #2): the curator-emitted
            # concepts must reach the paper-log entry (non-empty).
            _entry = next((e for e in _log if e.get("arxiv_id") == "2606.19341v1"), None)
            checks["paper_concepts_recorded"] = bool(_entry and _entry.get("concepts"))
        except Exception:
            checks["paper_logged"] = False
            checks["paper_concepts_recorded"] = False
    ok = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    print(f"  [{label}] {'PASS' if ok else 'FAIL'}  run_pipeline "
          f"(status={status}, failed_step={failed_step}, finalizer×{dispatched.count('finalizer')})"
          + ("" if ok else f" — failed checks: {failed}"))
    return ok


def main() -> int:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="paperline-gen-e2e-"))
    a = part_a_faithfulness_blocks(tmp)
    b = part_b_generation_chain(tmp, finalize_body="faithful", expect_ok=True, label="faithful")
    print("\n[e2e] Part C — engine-level block (exaggerated body through run_pipeline):")
    c = part_b_generation_chain(tmp, finalize_body="exaggerated", expect_ok=False,
                                label="exaggerated", expect_failed_step="faithfulness")
    print("\n[e2e] Part D — a discarded short committee draft must NOT halt the run:")
    d = part_b_generation_chain(tmp, short_slice="B", expect_ok=True, label="short-B-discarded")
    print("\n[e2e] Part E — a too-short finalize BODY floors + retries (3) then halts AT finalize:")
    e = part_b_generation_chain(tmp, short_finalize=True, expect_ok=False, label="short-finalize",
                                expect_failed_step="finalize", expect_finalize_dispatches=4)
    # Part F — two runs into the SAME paper output root: run 1 publishes today's
    # episode; run 2 (same date) must fail-fast at the same-day guard (DP-404=A,
    # one episode per line per day). Proves the continuity guard at run_pipeline
    # level, not just unit (the GOAL's "同日护栏").
    print("\n[e2e] Part F — same-day re-run fail-fast (two run_pipeline calls, shared output root):")
    shared = tmp / "f-shared-out"
    f1 = part_b_generation_chain(tmp, finalize_body="faithful", expect_ok=True,
                                 label="run1-publish", out_root_override=shared)
    f2 = part_b_generation_chain(tmp, finalize_body="faithful", expect_ok=False,
                                 label="run2-sameday", out_root_override=shared,
                                 expect_failed_step="same-day-guard")
    f = f1 and f2
    print(f"\n[e2e] DONE  Part A (忠实门 gate blocks)={'PASS' if a else 'FAIL'}  "
          f"Part B (happy chain)={'PASS' if b else 'FAIL'}  "
          f"Part C (engine-level block + retry-then-stop)={'PASS' if c else 'FAIL'}  "
          f"Part D (short discarded draft → no halt)={'PASS' if d else 'FAIL'}  "
          f"Part E (short body → finalize floor + retry×3 → halt)={'PASS' if e else 'FAIL'}  "
          f"Part F (same-day re-run fail-fast)={'PASS' if f else 'FAIL'}")
    return 0 if (a and b and c and d and e and f) else 1


if __name__ == "__main__":
    raise SystemExit(main())
