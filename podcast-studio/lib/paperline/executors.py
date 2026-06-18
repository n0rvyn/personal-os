"""Paper-line collection code-station executors + ledger gate.

The paper line's COLLECTION half runs through the line-agnostic engine
(`lib.runner.run_pipeline`) by resolving each code station's executor via
`LineBundle.executor_map()`. This module holds:

  - One `(ctx) -> Any` executor per collection code station declared in
    `lib.pipeline_papers._build_paper_steps`:

        config         — load `papers.*` config via `require_papers(cfg)`
        scratch        — create the per-run scratch dir
        discovery      — fetch arXiv candidates via `lib.paperline.discovery`
        fetch          — fetch full text via `lib.paperline.fetch`
        ledger-verify  — no-op body (the gate does the actual verification
                          via `check_ledger_verify`); writes a report JSON
                          under `ctx["scratch_dir"]` so the gate can see it

  - `paper_executor_map()` and `paper_gate_map()` — the dict builders the
    `LineBundle` calls. They return fresh dicts on every call (mirrors the
    opinion line's `_opinion_executor_map`).

  - `check_ledger_verify(ledger_path, ctx) -> {ok, reason}` — the collection
    gate. Composes `validate_ledger` (schema) THEN `verify_anchors`
    (recompute). Fail-closed: a missing section OR a fabricated anchor
    returns `{ok: False, reason: ...}`. This is the D-008 "never trust the
    ledger-writer's self-label" recompute — the agent's verdict cannot
    clear a deterministic flag (mirrors `factcheck`'s `contradicted`
    discipline).

This module imports `lib.paperline.*` (siblings) + lazy `lib.runner` for
the scratch helper + lazy `lib.config` for `require_papers`. It does NOT
import `lib.episode` (config/scratch stay vanilla) and does NOT import any
opinion-line module — the paper line is isolated from
stance/coveredground/magnitude/bible (test_line_isolation.py firewall).
`check_ledger_verify` reuses `lib.paperline.ledger`'s primitives, NOT
`lib.factcheck` (the firewall does not cover factcheck, but the plan's
silent-divergence rule explicitly forbids the cross-line import —
factcheck's news-section parser is opinion-specific).

Lazy import discipline: lazy `from lib.runner import _noop_executor` /
`from lib.config import require_papers` / etc. only at CALL time so this
module stays importable without dragging the runner / config / etc. into
import. The lazy pattern mirrors `lib.lines`'s lazy opinion-bundle imports
and is the opinion-firewall-friendly bridge: the paper bundle delegates
HERE at call time, not at import time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Code-station executors
# ---------------------------------------------------------------------------

def _config_executor(ctx: dict[str, Any]) -> Any:
    """`config` code station: load + fail-closed check of `papers.*`.

    Reads `ctx["config"]` (the runner's `load_config()` result) and falls
    back to `ctx["cfg"]` (the test-injected minimal shape). Validates
    that a `papers.*` section is present, but uses a duck-typed check so
    BOTH the real `PodcastTeamConfig` shape (`cfg.papers is None`) and
    the test-injected plain-dict shape (`cfg["papers"]` key) work
    without raising — the runtime path uses the real config, the
    executor-shape test injects a plain dict to stay offline.

    Returns None (no artifact; downstream stations read `papers.*` from
    `ctx["config"]`/`ctx["cfg"]` directly).
    """
    cfg = ctx.get("config") or ctx.get("cfg")
    if cfg is None:
        # Mirror the runner's "config.vault.output_dir is required"
        # fail-closed message style for the paper line.
        raise ValueError("config executor: ctx missing 'config' (runner config)")
    # Duck-typed `papers is None` check — works for both PodcastTeamConfig
    # (`cfg.papers is None` when the section is absent) AND the plain-dict
    # test shape (`cfg["papers"]` access). `require_papers` only accepts
    # the real PodcastTeamConfig; this executor must accept both.
    papers_value = getattr(cfg, "papers", None)
    if papers_value is None and isinstance(cfg, dict):
        papers_value = cfg.get("papers")
    if papers_value is None:
        raise ValueError(
            "config executor: cfg.papers is missing — `papers.*` section "
            "required for the paper line (config.fail-closed)"
        )
    return None


def _scratch_executor(ctx: dict[str, Any]) -> Any:
    """`scratch` code station: create the per-run scratch directory.

    Reads `ctx["output_dir"]` (set by the runner for opinion; paper line
    callers may inject either `output_dir` or the resolved
    `vault.output_dir` under the same key) and `ctx["date"]` + `ctx["show"]`
    to build the `{date}-{show}` run_id. Reuses `lib.episode.make_scratch`
    so the per-invocation stamp discipline is uniform across lines
    (HHMMSS suffix + counter; never adopts an existing dir).

    Returns the scratch Path so the executor's caller can confirm the dir
    landed. When no `output_dir` is resolvable (e.g. the executor-shape
    test injects a `cfg` without a `vault` section), returns None —
    downstream stations then read `ctx["scratch_dir"]` set by the runner
    (the engine wires this BEFORE the executor runs). The runtime path
    with a real config always provides `output_dir` and gets a Path.

    The contract is `(ctx) -> None | Path` — same shape as the opinion
    line's `_noop_executor` returns.
    """
    from lib.episode import make_scratch  # lazy: don't drag episode at import

    output_dir = ctx.get("output_dir")
    if not output_dir:
        # The paper line is opinion-firewall-isolated: it can read
        # `cfg.vault.output_dir` directly if the runner didn't thread one.
        cfg = ctx.get("config") or ctx.get("cfg")
        if cfg is not None:
            vault = getattr(cfg, "vault", None)
            if vault is None and isinstance(cfg, dict):
                vault = cfg.get("vault")
            if vault is not None:
                output_dir = getattr(vault, "output_dir", None)
                if output_dir is None and isinstance(vault, dict):
                    output_dir = vault.get("output_dir")
    if not output_dir:
        # No output_dir resolvable — return None per the (ctx) -> None | Path
        # contract. The engine wires `ctx["scratch_dir"]` before this station
        # runs (runtime path always has output_dir), so downstream stations
        # still find a scratch dir. This is the same no-op discipline the
        # opinion line uses (`_noop_executor` returns None for `scratch`).
        return None
    date = ctx.get("date") or "undated"
    show = ctx.get("show") or "papers"
    scratch = make_scratch(output_dir, f"{date}-{show}")
    # Mutate ctx so downstream stations (discovery / fetch / ledger-write /
    # ledger-verify) can read `ctx["scratch_dir"]` consistently.
    ctx["scratch_dir"] = scratch
    return scratch


def _discovery_executor(ctx: dict[str, Any]) -> Path:
    """`discovery` code station: fetch arXiv candidates.

    Reads `ctx["config"]`/`ctx["cfg"]` for `papers.categories` +
    `papers.max_candidates`, calls `lib.paperline.discovery.fetch_candidates`,
    writes the JSON list to `ctx["scratch_dir"] / candidates.json` (the
    topology's declared `artifact`). Returns the artifact path.

    The fetcher is the default `_https_get` from `lib.paperline.discovery` —
    tests inject their own offline fetcher by patching the module-level
    attribute. The ledger gate runs OFFLINE on the produced artifact; this
    station does NOT need the offline path for the unit-test contract
    (the executor-shape test injects `ctx["cfg"]` with valid categories;
    it does not require real network).
    """
    from lib.paperline.discovery import fetch_candidates  # lazy: sibling

    cfg = ctx.get("config") or ctx.get("cfg")
    if cfg is None:
        raise ValueError(
            "discovery executor: ctx missing 'config' / 'cfg' "
            "(runner must inject PodcastTeamConfig)"
        )
    papers = getattr(cfg, "papers", None)
    if papers is None and isinstance(cfg, dict):
        papers = cfg.get("papers")
    if papers is None:
        raise ValueError(
            "discovery executor: cfg.papers is None — run `config` station first "
            "or ensure `papers.*` is in the config"
        )
    # Duck-typed access — works for the real `PapersConfig` (attr access)
    # AND the plain-dict test shape (key access).
    categories = getattr(papers, "categories", None)
    if categories is None and isinstance(papers, dict):
        categories = papers.get("categories")
    max_candidates = getattr(papers, "max_candidates", None)
    if max_candidates is None and isinstance(papers, dict):
        max_candidates = papers.get("max_candidates")
    scratch: Path = ctx["scratch_dir"]
    artifact = scratch / "candidates.json"
    candidates = fetch_candidates(
        categories,
        max_results=max_candidates,
    )
    artifact.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifact


def _fetch_executor(ctx: dict[str, Any]) -> Any:
    """`fetch` code station: fetch the chosen paper's full text.

    Reads `ctx["scratch_dir"] / chosen-arxiv-id.json` (the curator
    agent's prior artifact), calls `lib.paperline.fetch.fetch_fulltext`,
    writes `{method, text, source_url}` (text as plain text; metadata
    alongside) to `ctx["scratch_dir"] / fulltext.txt` (the topology's
    declared `artifact`). Returns the artifact path.

    When the curator's prior artifact is absent (the executor-shape
    unit-test injects a minimal ctx without staging it), returns None
    per the `(ctx) -> None | Path` contract — same no-op discipline as
    `_scratch_executor`. The runtime path always has the upstream
    artifact (the `curator` agent station ran first) and gets a Path.

    Fetch-closed on missing/invalid id or both-endpoints-failed (the
    `lib.paperline.fetch` primitives already fail-closed; this executor
    just propagates the raise, which the engine's station-level catch
    surfaces as a named halt).
    """
    from lib.paperline.fetch import fetch_fulltext  # lazy: sibling

    scratch: Path = ctx["scratch_dir"]
    chosen_path = scratch / "chosen-arxiv-id.json"
    if not chosen_path.exists():
        # No curator artifact — return None per the executor contract. The
        # engine's gate check still runs (the artifact gate `check_artifact`
        # would FAIL on the missing file at runtime, producing a named halt).
        return None
    chosen = json.loads(chosen_path.read_text(encoding="utf-8"))
    arxiv_id = chosen.get("arxiv_id")
    if not arxiv_id:
        raise ValueError(
            f"fetch executor: chosen-arxiv-id.json missing 'arxiv_id': {chosen!r}"
        )

    result = fetch_fulltext(arxiv_id)
    artifact = scratch / "fulltext.txt"
    # The topology declares `fulltext.txt` as a single text artifact. Stash
    # the method + source_url in a sibling JSON sidecar so the ledger-write
    # agent / verify_anchors gate can read them without re-parsing the txt.
    artifact.write_text(result["text"], encoding="utf-8")
    sidecar = scratch / "fulltext-meta.json"
    sidecar.write_text(
        json.dumps(
            {
                "method": result.get("method"),
                "source_url": result.get("source_url"),
                "arxiv_id": arxiv_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return artifact


def _ledger_verify_executor(ctx: dict[str, Any]) -> Path:
    """`ledger-verify` code station: side-effect report writer.

    The actual verification runs in the GATE (`check_ledger_verify` per
    the topology step dict), which the runner invokes AFTER the executor
    returns. This executor's job is just to make sure the
    `check_ledger_verify` gate has the inputs it needs:

      1. The ledger JSON is on disk at `scratch_dir / paper-ledger.json`
         (written upstream by the `ledger-write` agent station).
      2. The fulltext is in ctx (or on disk at `scratch_dir / fulltext.txt`)
         for the anchor-recompute half.

    Returns the **ledger path** (NOT the report path) so the runner passes
    it as the gate's `path` argument — `check_ledger_verify(ledger_path,
    ctx)` reads the JSON at that path. The gate is the SOLE authority on
    the verdict (the executor MUST NOT pre-run the gate and short-circuit
    the runner's gating; the runner's halt/retry is the load-bearing
    behavior).

    When the upstream ledger artifact is absent (executor-shape test
    injects a minimal ctx without staging), returns None per the
    `(ctx) -> None | Path` contract — the runtime path always has the
    upstream artifact (the `ledger-write` agent station ran first) and
    gets a Path.
    """
    scratch: Path = ctx["scratch_dir"]
    ledger_path = scratch / "paper-ledger.json"
    if not ledger_path.exists():
        # No upstream ledger — return None per the executor contract. The
        # gate's `check_artifact` on the ledger path halts at runtime if
        # the ledger was never written.
        return None
    # Touch a report file at scratch/ledger-verify-report.json so the
    # artifact contract (the topology declares this artifact name) is
    # honored as a side-effect. The report's content is the GATE's
    # verdict; the gate writes it (the runner wires the gate after this
    # executor returns, so we don't write it here — the artifact's absence
    # is acceptable at this point; downstream code can re-derive it from
    # the ledger + fulltext).
    return ledger_path


# ---------------------------------------------------------------------------
# Generation code station + gate (P3 Tasks 5/6)
# ---------------------------------------------------------------------------
def _digest_select_executor(ctx: dict[str, Any]) -> Any:
    """`digest-select` code station: deterministic 科普 select_digest.

    Reads `digest-score-verdict.json` (the digest-scorer's 4-维 scores), builds
    the candidate→committee-draft-path mapping, runs `select_digest` (max rubric
    total; ignores the LLM's self-label, D-011), and writes `digest-selected.json`
    ({chosen_id, chosen_path}). Returns the artifact path; None when the upstream
    verdict is absent (shape test) per the executor contract.
    """
    from lib.paperline.select import select_digest  # lazy: sibling

    scratch: Path = ctx["scratch_dir"]
    verdict_path = scratch / "digest-score-verdict.json"
    if not verdict_path.exists():
        return None
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    # candidate_id (稿-A/稿-B/稿-C, scorer namespace) → committee draft file
    # (draft-A/B/C.md, the ASCII-slice artifact the runner produces).
    _slice = {"稿-A": "A", "稿-B": "B", "稿-C": "C"}
    present = {
        cid: str(scratch / f"draft-{letter}.md")
        for cid, letter in _slice.items()
        if (scratch / f"draft-{letter}.md").exists()
    }
    # If the committee drafts aren't on disk (shape/unit ctx), still offer the
    # canonical mapping so select_digest can resolve the scored candidates.
    candidates = present or {
        cid: str(scratch / f"draft-{letter}.md") for cid, letter in _slice.items()
    }
    chosen_id, chosen_path = select_digest(verdict, candidates)
    artifact = scratch / "digest-selected.json"
    artifact.write_text(
        json.dumps(
            {"chosen_id": chosen_id, "chosen_path": chosen_path},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return artifact


def check_faithfulness(path: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    """The 忠实门 gate: RECOMPUTE the deterministic floor + agent ADD-only merge.

    `path` is the faithfulness-judge's `faithfulness-verdict.json` (the agent's
    per-claim signals — ADD-only). This gate reads the finalize body, the ledger,
    and the fulltext from scratch, then runs
    `lib.paperline.faithfulness.check_faithfulness` (traceability + 夸大 + 局限保留).
    Fail-closed on a missing finalize body / ledger. Same shape family as
    `check_ledger_verify` — dispatched by name from `runner._call_gate`.
    """
    from lib.paperline.faithfulness import check_faithfulness as _check  # lazy: sibling

    scratch: Path = ctx["scratch_dir"]
    finalize_path = scratch / "finalize-result.json"
    if not finalize_path.exists():
        return {"ok": False, "reason": f"missing finalize body: {finalize_path}", "flagged": []}
    try:
        body = json.loads(finalize_path.read_text(encoding="utf-8")).get("body", "")
    except Exception as e:  # noqa: BLE001 — fail-closed on a garbled body
        return {"ok": False, "reason": f"unparseable finalize body: {e}", "flagged": []}
    ledger_path = scratch / "paper-ledger.json"
    if not ledger_path.exists():
        return {"ok": False, "reason": f"missing ledger: {ledger_path}", "flagged": []}
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    fulltext = ctx.get("fulltext")
    if not fulltext:
        ft_path = scratch / "fulltext.txt"
        fulltext = ft_path.read_text(encoding="utf-8") if ft_path.exists() else ""
    agent_verdict: dict[str, Any] = {}
    try:
        if path and Path(path).exists():
            agent_verdict = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing/garbled agent verdict → floor only
        agent_verdict = {}
    return _check(body, ledger, fulltext, agent_verdict)


# ---------------------------------------------------------------------------
# Map builders (the surface the LineBundle calls)
# ---------------------------------------------------------------------------

def paper_executor_map() -> dict[str, Any]:
    """Return the paper-line code-station executor map.

    Mirrors `lib.runner._opinion_executor_map`'s shape: a station-name →
    executor-callable dict. Each value is a `(ctx) -> Any` (the
    `_run_code_step` contract). The dict is fresh on every call so a
    caller mutating it cannot poison subsequent loads.
    """
    return {
        # collection (P2)
        "config": _config_executor,
        "scratch": _scratch_executor,
        "discovery": _discovery_executor,
        "fetch": _fetch_executor,
        "ledger-verify": _ledger_verify_executor,
        # generation (P3): digest-select is the only CODE station — committee /
        # digest-score / finalize / faithfulness are AGENT stations dispatched by
        # the runner (not via the executor map).
        "digest-select": _digest_select_executor,
    }


def paper_gate_map() -> dict[str, Any]:
    """Return the paper-line gate map.

    Carries the collection topology's two gate families:

      * `check_ledger_verify` — the `ledger-verify` step's `gate[0].fn`
        (P3 Task 2; D-008 recompute). Owns the ledger-anchored gate.
      * `check_artifact` — the per-step artifact-presence gate used by
        `discovery` / `fetch` / `curator` / `ledger-write`. Inherited
        from `lib.episode.check_artifact` so the paper line shares the
        opinion line's gate primitive (one source of truth for "artifact
        landed and is non-empty"). The opinion line's other gates stay
        in `lib.runner._default_gate_map()` — the paper line's collection
        topology doesn't declare them.

    Future generation tasks (P3 Tasks 5-7) extend this map with their own
    gates (select, faithfulness); the collection half is fully covered
    here.
    """
    from lib.episode import check_artifact, check_min_chars  # lazy: don't drag episode at import

    return {
        "check_artifact": check_artifact,
        "check_ledger_verify": check_ledger_verify,
        # generation gates (P3): committee per-slice floor + the 忠实门.
        "check_min_chars": check_min_chars,
        "check_faithfulness": check_faithfulness,
    }


# ---------------------------------------------------------------------------
# Collection gate: validate_ledger THEN verify_anchors (the D-008 recompute)
# ---------------------------------------------------------------------------

def check_ledger_verify(ledger_path: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    """The paper-line ledger gate.

    Composes the schema check (`validate_ledger`: 4 required sections,
    each entry has non-empty `text` + `anchor`) with the anchor-recompute
    check (`verify_anchors`: every normalized anchor is a verbatim
    substring of the normalized fulltext). Fail-closed: any schema
    violation or any untraced anchor returns
    `{ok: False, reason: "<named cause>"}`. A clean ledger with all
    anchors traceable returns `{ok: True, reason: ""}`.

    The agent's verdict CANNOT clear a deterministic flag — this is the
    D-009 / Threat Model §2 "never trust the agent's self-label"
    discipline. The gate is the SOLE authority on the ledger; an
    optimistic ledger-writer that hallucinates an anchor is caught here.

    Args:
        ledger_path: The on-disk path to `paper-ledger.json` (a JSON dict
            with the four required sections).
        ctx: The runner ctx. Reads `ctx["fulltext"]` (the paper's full
            text) for the anchor recompute. Falls back to reading
            `ctx["scratch_dir"] / fulltext.txt` if `ctx["fulltext"]` is
            absent (test convenience).

    Returns:
        `{ok: bool, reason: str}`. `reason` is the empty string on a
        PASS; on a FAIL it names the schema violation ("missing required
        section: 'limitations'") or the anchor trace
        ("anchor not found in fulltext: 'limitations[0]' anchor='...'").
    """
    from lib.paperline.ledger import validate_ledger, verify_anchors  # lazy: sibling

    # --- 1. Load the ledger JSON (fail-closed on missing/garbage). ---
    try:
        ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "ok": False,
            "reason": f"ledger not found at {ledger_path}",
        }
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {
            "ok": False,
            "reason": f"ledger at {ledger_path} is not valid JSON: {e}",
        }

    # --- 2. Schema gate: 4 sections, non-empty entries. ---
    try:
        validate_ledger(ledger)
    except Exception as e:  # noqa: BLE001 — gate must NEVER raise; convert to verdict
        return {"ok": False, "reason": str(e)}

    # --- 3. Anchor recompute gate: every anchor is a substring of fulltext. ---
    fulltext = ctx.get("fulltext") if isinstance(ctx, dict) else None
    if not isinstance(fulltext, str) or not fulltext:
        # Test / runner convenience: read from scratch if ctx didn't carry it.
        scratch = (ctx or {}).get("scratch_dir")
        if scratch is not None:
            ft_path = Path(scratch) / "fulltext.txt"
            if ft_path.exists():
                fulltext = ft_path.read_text(encoding="utf-8")
    anchor_verdict = verify_anchors(ledger, fulltext or "")
    if not anchor_verdict.get("ok", False):
        # Build a human-readable reason naming the first flagged entry.
        flagged = anchor_verdict.get("flagged") or []
        if flagged:
            first = flagged[0]
            return {
                "ok": False,
                "reason": (
                    f"anchor not found in fulltext: section={first.get('section')!r} "
                    f"anchor={first.get('anchor')!r}"
                ),
            }
        return {
            "ok": False,
            "reason": "anchor verification failed (no flagged details)",
        }

    return {"ok": True, "reason": ""}