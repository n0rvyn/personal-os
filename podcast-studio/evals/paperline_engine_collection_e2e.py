"""Paper-line collection e2e through `run_pipeline` — P3 Task 3.

Engine-wire acceptance: the paper line's COLLECTION topology runs through
`lib.runner.run_pipeline("papers", no_tts=True, ...)`, dispatching paper
personas via the line-aware path, producing a verified ledger.

This closes the P2 `p2_engine_boundary` debt — first real `run_pipeline` of
the paper line. The opinion line's full pipeline already runs through this
engine; this e2e proves the paper line shares the engine with the opinion
line (DP-001 / crystal D-003) without an opinion-side regression.

Topological stations run, in order:
    config -> scratch -> discovery -> curator -> fetch -> ledger-write
        -> ledger-verify (gate `check_ledger_verify`)

Why `no_tts=True`: the paper line has NO TTS / publish stations (P3 stops at
generation; P4 adds publish). The flag is defensive — a future station that
assumes TTS would skip harmlessly under `no_tts`.

Determinism strategy: arXiv 503'd mid-P2 and a live run requires the proxy.
For deterministic iteration this harness has TWO modes:

  * `--inject-staged` (default off → ON by default for the acceptance run):
    pre-stage the real candidates + fulltext + ledger into the scratch dir,
    inject a fake `dispatch` that lands the curator's
    `chosen-arxiv-id.json` + the ledger-writer's `paper-ledger.json` from
    `.claude/p2-samples/paper-ledger.json`. This exercises the ENGINE
    wiring (topology + executor_map + gate_map + line-aware dispatch
    threading) without a network or proxy round-trip. The `ledger-verify`
    gate is the SOLE non-injected station; it runs real against the
    staged artifacts and is the load-bearing proof (Task 2 gate).

  * `--live-dispatch-smoke`: a one-shot `claude -p` for one paper
    persona (curator) to confirm the proxy is reachable. On proxy
    unreachability, mark `⚠️ DEFERRED — needs proxy` and continue. NEVER
    fake a dispatch verdict.

The harness asserts the run actually went through `run_pipeline` (not a
direct harness) — i.e. the runner call-site's return envelope
(`status: ok`, `failed_step: ledger-verify`, etc.) is observed.

Usage:
    python3 evals/paperline_engine_collection_e2e.py
    python3 evals/paperline_engine_collection_e2e.py --live-dispatch-smoke
    python3 evals/paperline_engine_collection_e2e.py --date 2026-06-18 \
        --scratch /tmp/eval-paperline
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# ---------- paths ----------
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Staged P2 samples (real arXiv 2606.19341 ledger + fulltext).
SAMPLES_DIR = PLUGIN_ROOT / ".claude" / "p2-samples"
STAGED_LEDGER_PATH = SAMPLES_DIR / "paper-ledger.json"
STAGED_FULLTEXT_PATH = SAMPLES_DIR / "arxiv-2606.19341-pdftotext.txt"
STAGED_ARXIV_ID = "2606.19341v1"

# Subprocess timeout for the live-dispatch smoke (one persona, no big fan-out).
_SMOKE_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Fake dispatch — lands staged artifacts so the engine wiring is exercised
# end-to-end without a network / proxy round-trip.
# ---------------------------------------------------------------------------

class _FakePaperDispatch:
    """Stand-in for `dispatch_persona` that writes pre-staged artifacts.

    The fake records every call (so the harness can print the dispatch
    order) and routes by agent_name + step_name to the right staged
    payload. The point is to exercise the line-aware dispatch THREADING
    through the runner — not to test the personas themselves (that's
    Task 6 / Phase 2's `paperline_collection_e2e.py`'s job).
    """

    def __init__(self, scratch_dir: Path, ledger_payload: dict, fulltext: str):
        self.scratch_dir = scratch_dir
        self.ledger_payload = ledger_payload
        self.fulltext = fulltext
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        agent_name: str,
        user_prompt: str,
        scratch_dir: Any,
        expected_artifact: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append({
            "agent": agent_name,
            "step": kwargs.get("step_name"),
            "expected_artifact": expected_artifact,
            "agent_dir": kwargs.get("agent_dir"),
            "whitelist": sorted(kwargs.get("whitelist") or []),
            "plugin_root": kwargs.get("plugin_root"),
        })

        artifact_path = Path(str(scratch_dir)) / expected_artifact
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        # Route by agent — write the staged artifact that the gate then
        # validates. `ledger_payload["ledger"]` is the inner 4-section
        # ledger the `check_ledger_verify` gate reads.
        if agent_name == "curator":
            chosen = {
                "arxiv_id": STAGED_ARXIV_ID,
                "rationale": "e2e injection: staged real ledger from .claude/p2-samples/",
            }
            artifact_path.write_text(
                json.dumps(chosen, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif agent_name == "ledger-writer":
            # The real ledger-writer persona wrote this staged artifact in
            # P2's `paperline_collection_e2e.py`; we land it verbatim.
            artifact_path.write_text(
                json.dumps(self.ledger_payload["ledger"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            # Unknown paper agent (none expected) — write empty so the
            # gate flags it (a real failure, not a silent pass).
            artifact_path.write_text("", encoding="utf-8")

        return {
            "ok": True,
            "reason": f"fake wrote {artifact_path}",
            "artifact_path": str(artifact_path),
        }


# ---------------------------------------------------------------------------
# Live-dispatch smoke (proxy-reachable half)
# ---------------------------------------------------------------------------

def _live_dispatch_smoke(persona: str, prompt: str) -> tuple[bool, str]:
    """One-shot `claude -p` for a paper persona — exercises the real
    line-aware dispatch binary path.

    Returns (ok, detail). On any failure, the harness records
    `⚠️ DEFERRED — needs proxy` and continues with the staged half
    (the staged half is the engine-wiring proof; the smoke is the
    "the binary path still works" spot-check).
    """
    agent_md = PLUGIN_ROOT / "agents" / "papers" / f"{persona}.md"
    if not agent_md.is_file():
        return False, f"persona .md not found: {agent_md}"
    system_prompt = agent_md.read_text(encoding="utf-8")
    argv = [
        "claude", "-p", prompt,
        "--append-system-prompt", system_prompt,
        "--allowedTools", "Read",
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {_SMOKE_TIMEOUT}s"
    except FileNotFoundError as exc:
        return False, f"claude binary not on PATH: {exc}"
    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "")[-200:]
        return False, f"exit={completed.returncode} stderr={stderr_tail!r}"
    return True, f"exit={completed.returncode} stdout_chars={len(completed.stdout or '')}"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Paper-line collection e2e through run_pipeline (P3 Task 3). "
            "Engine-wire acceptance — exercises the full collection topology "
            "(config→scratch→discovery→curator→fetch→ledger-write→ledger-verify) "
            "via the line-agnostic runner, with the ledger-verify gate running "
            "real on the staged ledger from P2 samples."
        ),
    )
    parser.add_argument(
        "--date",
        default="2026-06-18",
        help="ISO date for the run (default: 2026-06-18)",
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help="Scratch dir for the run (default: <output_dir>/.scratch-<date>-papers)",
    )
    parser.add_argument(
        "--live-dispatch-smoke",
        action="store_true",
        help=(
            "Spot-check the live `claude -p` binary path with one paper "
            "persona. Marks the live-dispatch half `⚠️ DEFERRED — needs proxy` "
            "on failure; the staged half (engine wiring + gate) still passes."
        ),
    )
    args = parser.parse_args(argv)

    print(f"[e2e] plugin_root = {PLUGIN_ROOT}")
    print(f"[e2e] date        = {args.date}")
    print(f"[e2e] staged      = {STAGED_LEDGER_PATH}")
    print(f"[e2e] fulltext    = {STAGED_FULLTEXT_PATH}")

    # --- 1. Load staged fixtures (real P2 ledger + fulltext) ---------------
    if not STAGED_LEDGER_PATH.is_file():
        print(
            f"[e2e] FAIL: staged ledger missing at {STAGED_LEDGER_PATH}; "
            "run `evals/paperline_collection_e2e.py` first to produce it",
            file=sys.stderr,
        )
        return 1
    if not STAGED_FULLTEXT_PATH.is_file():
        print(
            f"[e2e] FAIL: staged fulltext missing at {STAGED_FULLTEXT_PATH}",
            file=sys.stderr,
        )
        return 1
    ledger_payload = json.loads(STAGED_LEDGER_PATH.read_text(encoding="utf-8"))
    fulltext = STAGED_FULLTEXT_PATH.read_text(encoding="utf-8")
    print(
        f"[e2e]   arxiv_id   = {ledger_payload['ledger']['arxiv_id']}"
    )
    print(
        f"[e2e]   title      = {ledger_payload['ledger']['title'][:80]}"
    )

    # --- 2. Build the runner config stub (paper-line needs `papers.*`) ----
    # The runner reads `config.vault.output_dir` (fail-closed), and the
    # paper-line `config` code station reads `cfg.papers.categories` +
    # `cfg.papers.max_candidates`. Both must be set; we use a MagicMock
    # + real string slots, mirroring `lib/tests/test_runner.py`.
    output_dir = (args.scratch or (PLUGIN_ROOT / ".claude" / "p3-e2e-scratch")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir = args.scratch or (output_dir / f".scratch-{args.date}-papers")
    scratch_dir.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock()
    cfg.vault = MagicMock()
    cfg.vault.output_dir = str(output_dir)
    cfg.vault.episodes_dir = str(output_dir / "episodes")
    cfg.vault.state_dir = str(output_dir / "state")
    cfg.vault.reports_dir = str(output_dir / "reports")
    for d in (output_dir, output_dir / "episodes", output_dir / "state", output_dir / "reports"):
        d.mkdir(parents=True, exist_ok=True)
    cfg.vault.voice_corpus_dir = None
    # Paper-line config (read by `_config_executor` + `_discovery_executor`).
    cfg.papers = MagicMock()
    cfg.papers.categories = ("cs.CL",)
    cfg.papers.max_candidates = 15

    # --- 3. Fake dispatch + pre-staged discovery / fetch artifacts --------
    fake_dispatch = _FakePaperDispatch(
        scratch_dir=scratch_dir,
        ledger_payload=ledger_payload,
        fulltext=fulltext,
    )

    # Pre-stage `candidates.json` + `fulltext.txt` + `fulltext-meta.json` so
    # the `discovery` + `fetch` code stations don't need a live network.
    # This keeps the e2e deterministic (arXiv was 503'd mid-P2). The
    # `_discovery_executor` calls `fetch_candidates(...)` (which calls the
    # module-level `_https_get`); we monkey-patch those functions to
    # return staged bytes. Same shape as the existing
    # `test_paperline_executors.py` offline tests.
    from lib.paperline import discovery as _discovery_mod
    from lib.paperline import fetch as _fetch_mod

    staged_xml = (SAMPLES_DIR / "arxiv-api-cs.CL-sample.xml").read_text(
        encoding="utf-8"
    )

    def _staged_discovery_https_get(url: str, *, timeout: int = 30) -> bytes:
        return staged_xml.encode("utf-8")

    class _StagedResponse:
        """`_Response`-shaped stub: `.status` + `.read()` (bytes)."""

        def __init__(self, body: bytes, status: int = 200):
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def close(self) -> None:
            pass

    def _staged_fetch_https_get(url: str, *, timeout: int = 30) -> _StagedResponse:
        # The fetch module's HTML primary path checks
        # `_HTML_MARKERS = ("ltx_abstract", "ltx_title_document")` to detect
        # the arxiv HTML page; we don't have those, so we make the
        # HTML path's marker check return EMPTY (forcing the PDF fallback).
        # The PDF fallback calls `pdftotext` on bytes — we patch that too.
        return _StagedResponse(b"%PDF-1.4\n(staged pdf body)\n", status=200)

    def _staged_run_pdftotext(pdf_path: str) -> str:
        return fulltext

    original_discovery_https_get = _discovery_mod._https_get
    original_fetch_https_get = _fetch_mod._https_get
    original_run_pdftotext = _fetch_mod._run_pdftotext
    _discovery_mod._https_get = _staged_discovery_https_get  # type: ignore[attr-defined]
    _fetch_mod._https_get = _staged_fetch_https_get  # type: ignore[attr-defined]
    _fetch_mod._run_pdftotext = _staged_run_pdftotext  # type: ignore[attr-defined]

    # Pre-write the artifacts anyway so even if a code branch reads from
    # disk rather than the executor's return value, the gate sees them.
    (scratch_dir / "candidates.json").write_text(
        json.dumps(
            [
                {
                    "arxiv_id": STAGED_ARXIV_ID,
                    "title": ledger_payload["ledger"]["title"],
                    "summary": "(staged) candidate for e2e injection",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (scratch_dir / "fulltext.txt").write_text(fulltext, encoding="utf-8")
    (scratch_dir / "fulltext-meta.json").write_text(
        json.dumps(
            {
                "method": "staged",
                "source_url": "staged://p2-samples/arxiv-2606.19341-pdftotext.txt",
                "arxiv_id": STAGED_ARXIV_ID,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        return _drive_and_verify(
            args=args,
            cfg=cfg,
            scratch_dir=scratch_dir,
            fake_dispatch=fake_dispatch,
            ledger_payload=ledger_payload,
        )
    finally:
        # Restore patched module attrs so we don't leak into the process.
        _discovery_mod._https_get = original_discovery_https_get  # type: ignore[attr-defined]
        _fetch_mod._https_get = original_fetch_https_get  # type: ignore[attr-defined]
        _fetch_mod._run_pdftotext = original_run_pdftotext  # type: ignore[attr-defined]


def _drive_and_verify(
    *,
    args: argparse.Namespace,
    cfg: Any,
    scratch_dir: Path,
    fake_dispatch: _FakePaperDispatch,
    ledger_payload: dict,
) -> int:
    """Drive `run_pipeline`, verify engine wiring + gate verdict, return exit code."""
    print(f"\n[e2e] drive lib.runner.run_pipeline('papers', no_tts=True)")
    from lib.runner import run_pipeline

    result = run_pipeline(
        "papers",
        date=args.date,
        no_tts=True,
        dispatch=fake_dispatch,
        config=cfg,
        scratch_dir=scratch_dir,
        plugin_root=str(PLUGIN_ROOT),
    )
    print(f"[e2e]   status      = {result.get('status')}")
    print(f"[e2e]   steps_run   = {result.get('steps_run')}")
    if result.get("failed_step"):
        print(f"[e2e]   failed_step = {result['failed_step']}")
    if result.get("reason"):
        print(f"[e2e]   reason      = {result['reason']}")

    # --- 5. Engine-path proof: this harness DID go through run_pipeline --
    # The status envelope is the run_pipeline contract; a bespoke harness
    # would not produce this shape. Asserted here so a refactor that
    # accidentally swaps run_pipeline for a direct call still trips.
    assert "status" in result, (
        "run_pipeline did not return its status envelope — did the harness "
        "actually go through the engine?"
    )
    assert "steps_run" in result, "run_pipeline envelope missing 'steps_run'"
    # This is a COLLECTION e2e: it stages the collection half (config→…→
    # ledger-verify, steps 1-7) and proves the ledger-verify gate runs through
    # run_pipeline. The generation half (committee→…→faithfulness) is
    # intentionally NOT staged here (the fake writes empty for generation
    # agents — full-chain staging is paperline_generation_e2e.py's job). So the
    # run is EXPECTED to complete the collection half then halt at the first
    # un-staged generation station (committee). Asserting status=="ok" would
    # require staging the whole generation chain — out of this e2e's scope (and
    # was a stale assertion from P2, when the topology ended at ledger-verify).
    # We assert COLLECTION COMPLETION instead: ≥7 steps ran AND any halt is in
    # the generation half (proving ledger-verify, the last collection step, let
    # the run pass). A halt INSIDE the collection half is a real regression.
    _COLLECTION_STEPS = 7  # config,scratch,discovery,curator,fetch,ledger-write,ledger-verify
    _GENERATION_STATIONS = {"committee", "digest-score", "digest-select", "finalize", "faithfulness"}
    assert result["status"] in ("ok", "halted"), (
        f"unexpected run_pipeline status {result['status']!r}: {result!r}"
    )
    if result["status"] == "halted":
        _failed = (result.get("failed_step") or "").split(":")[0]
        assert _failed in _GENERATION_STATIONS, (
            f"collection e2e halted INSIDE the collection half at "
            f"{result.get('failed_step')!r} — the collection chain (through "
            f"ledger-verify) must complete; only the intentionally-unstaged "
            f"generation half may halt. {result!r}"
        )
    assert result.get("steps_run", 0) >= _COLLECTION_STEPS, (
        f"collection half incomplete: only {result.get('steps_run')} steps ran, "
        f"expected ≥{_COLLECTION_STEPS} (config→…→ledger-verify). {result!r}"
    )

    # --- 6. Station-order proof: the fake dispatch recorded the steps ----
    # The 2 agent stations in the topology are `curator` (step 4) and
    # `ledger-write` (step 6); both must have been dispatched in that
    # order. Discovery (step 3) + fetch (step 5) are code stations and
    # did not go through dispatch — they read the pre-staged artifacts.
    dispatched_agents = [c["agent"] for c in fake_dispatch.calls]
    print(f"[e2e]   dispatched  = {dispatched_agents}")
    assert "curator" in dispatched_agents, (
        "curator agent station was not dispatched — engine wiring missed it"
    )
    assert "ledger-writer" in dispatched_agents, (
        "ledger-writer agent station was not dispatched — engine wiring missed it"
    )
    # Order: curator comes before ledger-writer in the topology.
    assert dispatched_agents.index("curator") < dispatched_agents.index("ledger-writer"), (
        f"agent dispatch order wrong: {dispatched_agents}; "
        "curator must precede ledger-writer per topology"
    )

    # --- 7. Line-aware dispatch proof: agent_dir + whitelist threaded -----
    # Every paper-path dispatch must have received `agent_dir='agents/papers'`
    # + `whitelist={'curator','ledger-writer'}` (= PAPER_AGENT_WHITELIST).
    # This is the "looks wired, isn't" lesson from P2's vacuous-firewall
    # bug — verify the threading, not just the call site.
    # The threaded whitelist is the line bundle's PAPER_AGENT_WHITELIST — which
    # P3 EXPANDED to include the generation personas (digest-writer/-scorer/
    # finalizer/faithfulness-judge). Compare against the real source of truth,
    # not a hardcoded P2-era snapshot ({curator, ledger-writer}) — that stale
    # snapshot is what broke this check once P3 grew the whitelist.
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST
    for call in fake_dispatch.calls:
        assert call["agent_dir"] == "agents/papers", (
            f"dispatch {call['step']!r} got agent_dir={call['agent_dir']!r}; "
            "expected 'agents/papers' (paper line bundle)"
        )
        assert set(call["whitelist"]) == set(PAPER_AGENT_WHITELIST), (
            f"dispatch {call['step']!r} got whitelist={call['whitelist']!r}; "
            f"expected PAPER_AGENT_WHITELIST ({sorted(PAPER_AGENT_WHITELIST)})"
        )

    # --- 8. ledger-verify gate proof: real run on the staged ledger -------
    # The gate runs as part of `run_pipeline`; the collection-completion
    # assertion above proves the run passed ledger-verify. Now also confirm the
    # gate's report landed.
    report_path = scratch_dir / "ledger-verify-report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"[e2e]   ledger-verify ok = {report.get('ok')}")
        if not report.get("ok"):
            print(
                f"[e2e] FAIL ledger-verify gate: {report.get('reason')!r}",
                file=sys.stderr,
            )
            return 2
    else:
        # Gate runs INLINE on the artifact path (not by reading the report);
        # the report's absence is not a halt (the gate verdict is in the
        # status envelope). Log and continue.
        print(
            "[e2e]   (ledger-verify-report.json not landed; "
            "gate ran inline on paper-ledger.json — ok=True per envelope)"
        )

    # --- 9. Opinion-line regression shield (sanity check, optional) -------
    # Confirm the opinion line still resolves morning's bundle (the engine
    # is line-agnostic; if paper-line changes broke morning, this trips).
    from lib.lines import get_line
    opinion = get_line("morning")
    assert opinion.agent_dir == "agents", (
        f"opinion agent_dir regressed: {opinion.agent_dir!r}; "
        "paper-line changes must NOT touch the opinion bundle"
    )
    assert "davinci" in opinion.whitelist, (
        "opinion whitelist regressed (no davinci); paper-line changes "
        "must NOT touch the opinion bundle"
    )
    print("[e2e]   opinion line = morning, agents/, AGENT_WHITELIST (regression shield ok)")

    # --- 10. Live-dispatch smoke (optional, default OFF) -----------------
    if args.live_dispatch_smoke:
        print("\n[e2e] live-dispatch smoke (claude -p, one paper persona)")
        ok, detail = _live_dispatch_smoke(
            "curator",
            "Smoke test: return JSON `{\"arxiv_id\": \"2606.19341v1\", "
            "\"rationale\": \"smoke\"}` only.",
        )
        if ok:
            print(f"[e2e]   smoke ok ({detail})")
        else:
            print(f"[e2e]   ⚠️ DEFERRED — needs proxy: {detail}")

    print("\n[e2e] DONE  ran via run_pipeline; ledger-verify ok=True; "
          "paper line wired through the line-agnostic engine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())