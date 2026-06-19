#!/usr/bin/env python3
"""Paper-line FULL e2e (P4) — real `claude -p --model sonnet`, no-TTS.

Drives `run_pipeline('papers', no_tts=True)` on the REAL production dispatch
path (claude -p → localhost:9090 proxy → MiniMax M3) through the COMPLETE P4
topology: …finalize → 忠实门 → broadcast-script → (tts skipped, no_tts) →
paper-log-write → publish → cleanup. Proves the model produces gate-passing
artifacts end-to-end and a real `.md` lands in the PAPER line's own episodes
dir (output_dir/papers/episodes), with the paper recorded in paper-log.yaml.

Staged (NOT the unproven P4 surface):
  - `discovery` → the single cached 19341 candidate (so curator-live picks it
    deterministically; chosen-id stays consistent with the staged fulltext).
  - `fetch` → the cached 85KB fulltext (flaky arXiv network).
  - `ledger-writer` → the cached VERIFIED ledger (preserve-run3). The live
    sonnet/MiniMax ledger-writer's verbatim-anchor flakiness is a KNOWN
    collection-side finding (it mutates "Notably, on LVBench…"→"On LVBench…" /
    `10$\times$`→`10×`, failing the D-008 `ledger-verify` recompute — which is
    that gate doing its job, not a P4 bug). Staging it isolates the P4 PUBLISH
    half, the genuinely unproven surface.

    `LIVE_LEDGER` env (opt-in): when truthy ("1"/"true"/"yes"), do NOT stage
    `ledger-writer` — let it run LIVE on --model sonnet too. Used to verify
    the P3 anchor-grounding relaxation (`docs/06-plans/2026-06-19-paperline-
    anchor-grounding-plan.md`): live writer's faithful paraphrase now passes
    `ledger-verify`, so the full pipeline goes green end-to-end with real
    sonnet writing the ledger. Default behavior (env unset/false) unchanged.

EVERY OTHER persona runs LIVE on --model sonnet: curator, committee×3,
digest-scorer, finalizer, faithfulness-judge, AND the new P4 broadcaster.
`cleanup_scratch` is no-op'd so the artifacts survive for inspection.

Env (hard constraints, see docs/06-plans/2026-06-19-p4-handoff.md §5):
  PODCAST_STUDIO_CONFIG  — sandbox config whose output_dir is OUTSIDE .claude/
                           (claude -p refuses Write under .claude/) and carries
                           a papers.* section.
  no_proxy='*' NO_PROXY='*'  — direct arXiv (irrelevant here since net is
                           staged, but kept for parity / any live fetch).

Run:
  no_proxy='*' NO_PROXY='*' \
    PODCAST_STUDIO_CONFIG=~/.personal-os/scratch/p3-live-sandbox/config-p3-live.yaml \
    python3 evals/paperline_full_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Cached REAL arXiv 2606.19341 (staged in place of the flaky network front only).
_CANDIDATES_CACHE = (
    PLUGIN_ROOT / ".claude/p3-e2e-scratch/.scratch-2026-06-18-papers-180241/candidates.json"
)
_FULLTEXT_CACHE = PLUGIN_ROOT / ".claude/p2-samples/arxiv-2606.19341-pdftotext.txt"
# Cached VERIFIED ledger (its anchors pass verify_anchors against the cached
# fulltext — confirmed offline: check_ledger_verify ok=True). Staged in place
# of the LIVE ledger-writer, whose verbatim-anchor flakiness is a known
# collection-side finding (not the P4 publish surface this e2e proves).
_LEDGER_CACHE = PLUGIN_ROOT / ".claude/p3-live-sandbox/preserve-run3/paper-ledger.json"
_TARGET_ID = "2606.19341v1"


def _stage_network_and_model() -> None:
    """Stage ONLY discovery+fetch (cached real 19341) + inject --model sonnet +
    no-op cleanup_scratch + a per-dispatch progress log line."""
    import lib.runner as _runner
    import lib.paperline.discovery as _disc
    import lib.paperline.fetch as _fetch
    import lib.dispatch as _disp

    all_cands = json.loads(_CANDIDATES_CACHE.read_text(encoding="utf-8"))
    single = [c for c in all_cands if c.get("arxiv_id") == _TARGET_ID]
    if not single:
        raise SystemExit(f"cached candidates missing {_TARGET_ID}")
    fulltext = _FULLTEXT_CACHE.read_text(encoding="utf-8")
    cached_ledger = _LEDGER_CACHE.read_text(encoding="utf-8")

    def _staged_candidates(categories, *a, **k):  # noqa: ANN001
        return list(single)

    def _staged_fulltext(arxiv_id, *a, **k):  # noqa: ANN001
        return {"method": "pdf", "text": fulltext,
                "source_url": f"https://arxiv.org/pdf/{_TARGET_ID} (STAGED cached real; requested={arxiv_id})"}

    _disc.fetch_candidates = _staged_candidates
    _fetch.fetch_fulltext = _staged_fulltext
    _runner.cleanup_scratch = lambda *a, **k: None  # preserve scratch for inspection

    _orig = _disp.dispatch_persona

    def _sonnet_dispatch(agent_name, *a, **k):  # noqa: ANN002
        t0 = time.time()
        # Stage ledger-writer (known verbatim-anchor flakiness — isolates the
        # P4 publish half) UNLESS LIVE_LEDGER is set, in which case it runs
        # LIVE on --model sonnet (post-anchor-grounding relaxation it should
        # pass). a = (user_prompt, scratch_dir, expected_artifact).
        live_ledger = bool(os.environ.get("LIVE_LEDGER"))
        if agent_name == "ledger-writer" and not live_ledger:
            out = Path(a[1]) / a[2]
            out.write_text(cached_ledger, encoding="utf-8")
            print(f"[dispatch] ~~ ledger-writer STAGED (cached verified ledger) -> {out.name}", flush=True)
            return {"ok": True, "reason": "STAGED cached verified ledger", "artifact_path": str(out)}
        k["model"] = "sonnet"
        tag = "LIVE" if (agent_name == "ledger-writer" and live_ledger) else ""
        print(f"[dispatch] -> {agent_name}{(' '+tag) if tag else ''} (artifact={a[2] if len(a) > 2 else k.get('expected_artifact','?')})", flush=True)
        res = _orig(agent_name, *a, **k)
        print(f"[dispatch] <- {agent_name} ok={res.get('ok')} dt={time.time()-t0:.0f}s "
              f"reason={str(res.get('reason'))[:160]}", flush=True)
        return res

    _disp.dispatch_persona = _sonnet_dispatch
    live_ledger = bool(os.environ.get("LIVE_LEDGER"))
    print(f"[drive] staged discovery+fetch ONLY (single cached real 2606.19341); "
          f"cleanup no-op; ledger-writer "
          f"{'LIVE on --model sonnet (LIVE_LEDGER=1)' if live_ledger else 'STAGED (cached verified ledger)'}; "
          f"curator+committee×3+scorer+finalizer+judge+broadcaster "
          f"ALL LIVE on --model sonnet (→9090 proxy).", flush=True)


def main() -> int:
    from lib.config import load_config
    from lib.runner import run_pipeline

    cfg = load_config()
    out_root = Path(str(cfg.vault.output_dir))
    papers_eps = Path(str(getattr(cfg.vault, "papers_episodes_dir")))
    papers_state = Path(str(getattr(cfg.vault, "papers_state_dir")))
    print(f"[drive] output_dir={out_root}", flush=True)
    print(f"[drive] papers_episodes={papers_eps}", flush=True)

    _stage_network_and_model()
    t0 = time.time()
    try:
        res = run_pipeline("papers", no_tts=True)
    except Exception as e:  # noqa: BLE001
        print(f"[drive] CRASH: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return 2
    dt = time.time() - t0
    print(f"\n[drive] DONE in {dt:.0f}s :: status={res.get('status')} "
          f"steps_run={res.get('steps_run')} failed_step={res.get('failed_step')} "
          f"reason={res.get('reason')}", flush=True)

    # --- verify P4 publish outputs ---
    eps = sorted(papers_eps.glob(f"{res.get('date')}-*.md")) if papers_eps.exists() else []
    print(f"[drive] papers episodes .md: {[p.name for p in eps]}", flush=True)
    log_path = papers_state / "paper-log.yaml"
    logged = False
    if log_path.exists():
        try:
            from lib.paperline.paperlog import is_covered, load_paperlog
            logged = is_covered(load_paperlog(str(papers_state)), _TARGET_ID)
        except Exception as e:  # noqa: BLE001
            print(f"[drive] paper-log read error: {e}", flush=True)
    print(f"[drive] paper-log has {_TARGET_ID}: {logged}", flush=True)
    if eps:
        chars = len(eps[0].read_text(encoding="utf-8"))
        print(f"[drive] published body chars: {chars}", flush=True)

    ok = res.get("status") == "ok" and bool(eps) and logged
    print(f"[drive] GREEN={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
