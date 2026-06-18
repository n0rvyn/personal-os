"""Tests for lib/paperline/executors.py — paper-line collection executor_map + gates.

Written before `lib/paperline/executors.py` exists and before
`lib.lines._paper_executor_map` / `_paper_gate_map` are wired to the real
implementations (the P2 plan left them as empty stubs). At this point:

  - `lib.paperline.executors` does not exist → `ModuleNotFoundError`.
  - `get_line("papers").executor_map()` returns `{}` (the P2 stub).
  - `get_line("papers").gate_map()` returns `{}` (the P2 stub).
  - No `check_ledger_verify` gate exists anywhere.

The pinned contracts (Task 2 plan + crystal D-008 + Threat Model §2):

  - Each code-station executor is a `(ctx) -> Any` callable. It writes its
    artifact under `ctx["scratch_dir"]`, populates the named ctx slot
    (e.g. `ctx["candidates"]`, `ctx["fulltext"]`, `ctx["ledger"]`), and
    returns the artifact path (or `None` for no-op code stations).

  - The two agent stations (curator / ledger-write) are NOT code-station
    executors — they go through `_run_agent_step` / the runner's dispatch
    chain (Task 1 line-aware dispatch). The test pins that the dispatch
    threading is correct: a paper agent step dispatched through the
    line-aware path resolves `agents/papers/<name>.md` and accepts only
    `PAPER_AGENT_WHITELIST` agents.

  - `check_ledger_verify(ledger_path, ctx) -> {"ok": bool, "reason": str}`
    composes `validate_ledger` THEN `verify_anchors`. A fabricated anchor
    flags (`ok=False`); a missing section flags. A clean, schema-valid
    ledger whose anchors are all verbatim substrings of the fulltext
    passes (`ok=True`).

  - `get_line("papers").executor_map()` returns a non-empty dict covering
    the collection code stations (config / scratch / discovery / fetch /
    ledger-verify).
  - `get_line("papers").gate_map()` carries `check_ledger_verify`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.executors import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

# The collection topology's code stations (mirror of pipeline_papers). The
# executor_map must cover ALL of these (a missing station would fall through
# to no-op at runtime — exactly the kind of silent failure the test pins).
EXPECTED_CODE_STATIONS = {
    "config",
    "scratch",
    "discovery",
    "fetch",
    "ledger-verify",
}

# The collection topology's agent stations — they go through the dispatch
# chain, not the executor_map. The test still asserts they DO NOT appear as
# executor keys (the executor_map is for code stations only).
EXPECTED_AGENT_STATIONS = {"curator", "ledger-write"}


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The `lib.paperline.executors` module must exist after Task 2-impl.

    Before Task 2-impl this raises `ModuleNotFoundError: No module named
    'lib.paperline.executors'` (the test-FAIL-first contract).
    """
    from lib.paperline import executors  # noqa: F401

    # The module must expose both map builders as public callables.
    assert hasattr(executors, "paper_executor_map"), (
        "executors.paper_executor_map is the public surface the bundle "
        "calls; it must be exposed at module level"
    )
    assert hasattr(executors, "paper_gate_map"), (
        "executors.paper_gate_map is the public surface the bundle calls; "
        "it must be exposed at module level"
    )
    assert callable(executors.paper_executor_map)
    assert callable(executors.paper_gate_map)


def test_executors_module_exposes_check_ledger_verify_gate():
    """The collection gate `check_ledger_verify` must live in
    `lib.paperline.executors` so the bundle can resolve it.

    This is the gate implementation, NOT the map entry — the gate takes
    a ledger artifact path + ctx and returns `{ok, reason}`. It composes
    `validate_ledger` (schema) then `verify_anchors` (anchor recompute).
    """
    from lib.paperline import executors

    assert hasattr(executors, "check_ledger_verify"), (
        "check_ledger_verify must live in executors.py so the bundle can "
        "resolve it via the gate map; do NOT put it in lib.paperline.ledger "
        "(that module owns schema+anchor primitives only)"
    )
    assert callable(executors.check_ledger_verify)


# ---------------------------------------------------------------------------
# paper_executor_map() shape
# ---------------------------------------------------------------------------

def test_paper_executor_map_wired_non_empty():
    """`paper_executor_map()` must return a non-empty dict covering the
    collection code stations. The P2 stub returned `{}` — this test pins
    that Task 2-impl fills it.
    """
    from lib.paperline.executors import paper_executor_map

    m = paper_executor_map()
    assert isinstance(m, dict), f"executor_map must be dict, got {type(m).__name__}"
    assert m, "executor_map must be non-empty after Task 2-impl"

    # Every collection code station must have an executor. Missing entries
    # would silently fall through to _run_code_step's `return None`.
    missing = EXPECTED_CODE_STATIONS - set(m.keys())
    assert not missing, (
        f"executor_map missing code stations: {sorted(missing)}; "
        f"present: {sorted(m.keys())}"
    )

    # Agent stations MUST NOT appear in the executor map — they go through
    # the dispatch chain, not _run_code_step.
    leaked = EXPECTED_AGENT_STATIONS & set(m.keys())
    assert not leaked, (
        f"agent stations leaked into executor_map: {sorted(leaked)}; "
        "agents go through the dispatch chain, not the code executor map"
    )


def test_paper_executor_map_each_value_is_callable():
    """Every entry in the executor_map must be callable (a `(ctx) -> Any`).
    A non-callable value would raise at dispatch time and mask as a generic
    gate failure; this test pins the signature up front."""
    from lib.paperline.executors import paper_executor_map

    m = paper_executor_map()
    for name, fn in m.items():
        assert callable(fn), (
            f"executor_map[{name!r}] must be callable (a (ctx)->Any), "
            f"got {type(fn).__name__}"
        )


# ---------------------------------------------------------------------------
# paper_gate_map() shape
# ---------------------------------------------------------------------------

def test_paper_gate_map_has_ledger_verify():
    """The paper gate map MUST carry `check_ledger_verify` — the gate the
    ledger-verify code station declares in its step dict. The P2 stub
    returned `{}`, so this test pins the fill.
    """
    from lib.paperline.executors import paper_gate_map

    g = paper_gate_map()
    assert isinstance(g, dict), f"gate_map must be dict, got {type(g).__name__}"
    assert "check_ledger_verify" in g, (
        f"gate_map must contain 'check_ledger_verify', got keys {sorted(g.keys())}"
    )
    assert callable(g["check_ledger_verify"]), (
        "gate_map['check_ledger_verify'] must be callable"
    )


# ---------------------------------------------------------------------------
# Bundle integration: get_line("papers") delegates to the executors module
# ---------------------------------------------------------------------------

def test_paper_bundle_executor_map_delegates_to_executors_module():
    """`get_line("papers").executor_map()` must return the same dict shape
    as `paper_executor_map()` — the bundle is the engine's surface, and
    the engine resolves the executor map through the bundle, not the
    executors module directly. Both callables must produce equivalent maps.
    """
    from lib.lines import get_line
    from lib.paperline.executors import paper_executor_map

    bundle_map = get_line("papers").executor_map()
    module_map = paper_executor_map()

    assert isinstance(bundle_map, dict)
    assert bundle_map == module_map, (
        f"bundle.executor_map() and paper_executor_map() must agree; "
        f"keys: bundle={sorted(bundle_map)}, module={sorted(module_map)}"
    )


def test_paper_bundle_gate_map_delegates_to_executors_module():
    """Same delegation contract for the gate map."""
    from lib.lines import get_line
    from lib.paperline.executors import paper_gate_map

    bundle_gates = get_line("papers").gate_map()
    module_gates = paper_gate_map()

    assert isinstance(bundle_gates, dict)
    assert bundle_gates == module_gates, (
        f"bundle.gate_map() and paper_gate_map() must agree; "
        f"keys: bundle={sorted(bundle_gates)}, module={sorted(module_gates)}"
    )


# ---------------------------------------------------------------------------
# Individual code-station executors: (ctx) -> Any callable contract
# ---------------------------------------------------------------------------

def test_executor_each_code_station_uses_ctx(tmp_path, monkeypatch):
    """Each code-station executor must:
      1. accept a `ctx` dict (with `show`, `scratch_dir`, `plugin_root`,
         `date`, and per-line config like `cfg.papers`).
      2. write its artifact under `ctx["scratch_dir"]` (or return None for
         the no-op config/scratch/ledger-verify stations — those either
         write their artifact as a side effect of ctx mutation, or write
         a report under scratch and return its path).
      3. return either None (no-op body) or a Path (the artifact the gate
         then validates).

    This test runs every executor with a populated ctx and asserts the
    return type — the shape that _execute_step feeds into `_run_gate`.
    """
    from lib.paperline.executors import paper_executor_map

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    ctx = {
        "show": "papers",
        "scratch_dir": scratch,
        "plugin_root": PLUGIN_ROOT,
        "date": "2026-06-18",
    }
    # Provide a minimal papers config so the `config` executor doesn't fail
    # on a missing `cfg.papers.categories` (it's the executor's job to read
    # from ctx, but tests need the shape to be present).
    ctx["cfg"] = _minimal_papers_config()

    # Offline-determinism: the discovery + fetch executors call the network by
    # default. Stub both (monkeypatch → auto-restore, no module-global leak) so
    # this executor-SHAPE test never touches live arXiv (it asserts return shape,
    # not real collection). Mirrors the test_runner threading-test fix.
    from lib.paperline import discovery as _discovery_mod
    from lib.paperline import fetch as _fetch_mod
    _FEED = (
        b"<feed xmlns='http://www.w3.org/2005/Atom' "
        b"xmlns:arxiv='http://arxiv.org/schemas/atom'>"
        b"<entry><id>http://arxiv.org/abs/2606.19341v1</id>"
        b"<title>Stub</title><summary>Stub</summary>"
        b"<published>2026-06-14T00:00:00Z</published>"
        b"<arxiv:primary_category term='cs.CL'/><category term='cs.CL'/>"
        b"<link rel='related' type='application/pdf' href='http://arxiv.org/pdf/2606.19341v1'/>"
        b"</entry></feed>"
    )
    monkeypatch.setattr(_discovery_mod, "_https_get", lambda url, *, timeout=30: _FEED)
    monkeypatch.setattr(
        _fetch_mod, "fetch_fulltext",
        lambda arxiv_id, **kw: {"method": "html", "text": "stub fulltext", "source_url": "stub"},
    )
    # Stage the prior-station artifacts the fetch / ledger-verify executors read
    # (curator/ledger-write are agent stations, absent from the executor map).
    (scratch / "chosen-arxiv-id.json").write_text(
        json.dumps({"arxiv_id": "2606.19341v1"}), encoding="utf-8"
    )
    _staged_ledger = PLUGIN_ROOT.parent / ".claude" / "p2-samples" / "paper-ledger.json"
    if _staged_ledger.exists():
        (scratch / "paper-ledger.json").write_text(
            _staged_ledger.read_text(encoding="utf-8"), encoding="utf-8"
        )

    m = paper_executor_map()
    for station_name, fn in m.items():
        result = fn(ctx)
        # The return shape is one of:
        #   - None (no-op body, gate will read artifact from ctx/scratch)
        #   - Path (the artifact the gate validates)
        #   - some non-Path truthy artifact the gate reads via ctx
        # A TypeError or non-None non-truthy return is a contract break.
        assert result is None or isinstance(result, Path), (
            f"executor {station_name!r} returned {type(result).__name__}: "
            f"{result!r}; expected None or Path"
        )


# ---------------------------------------------------------------------------
# check_ledger_verify gate: validate_ledger THEN verify_anchors
# ---------------------------------------------------------------------------

def test_check_ledger_verify_passes_real_ledger(tmp_path):
    """`check_ledger_verify` must PASS on the staged real ledger from
    `.claude/p2-samples/paper-ledger.json`. The staged ledger has
    `anchors_ok=true` per its `verdict` field — the gate composes
    `validate_ledger` (schema) + `verify_anchors` (recompute) and both
    must clear.
    """
    from lib.paperline.executors import check_ledger_verify

    staged = PLUGIN_ROOT.parent / ".claude" / "p2-samples" / "paper-ledger.json"
    if not staged.exists():
        pytest.skip(f"staged real ledger not found at {staged}")
    payload = json.loads(staged.read_text(encoding="utf-8"))
    ledger = payload["ledger"]
    fulltext = _fulltext_for_ledger(staged, payload)

    # Stage the ledger under scratch so the gate reads it from disk
    # (mirrors the runner: gate path is `ctx["scratch_dir"] / <artifact>`).
    ledger_path = tmp_path / "paper-ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ctx = {
        "show": "papers",
        "scratch_dir": tmp_path,
        "plugin_root": PLUGIN_ROOT,
        "date": "2026-06-18",
        "fulltext": fulltext,
    }

    result = check_ledger_verify(ledger_path, ctx)
    assert isinstance(result, dict), f"gate must return dict, got {type(result).__name__}"
    assert "ok" in result, f"gate result must carry 'ok', got {result!r}"
    assert result["ok"] is True, (
        f"gate must PASS on the staged real ledger, got {result!r}"
    )


def test_check_ledger_verify_flags_fabricated_anchor(tmp_path):
    """A ledger whose anchor is NOT a substring of the fulltext must
    flag (`ok=False`). This is the recompute half — never trust the
    ledger-writer's self-label.
    """
    from lib.paperline.executors import check_ledger_verify

    fulltext = "This paper introduces a method called OmniAgent for video understanding."
    ledger = {
        "problem": [
            {
                "text": "Long video understanding is computationally expensive.",
                # Anchor is FABRICATED — does not appear in fulltext.
                "anchor": "in our groundbreaking 2025 survey we discovered",
            },
        ],
        "method": [
            {
                "text": "Iterative observation-thought-action loop.",
                "anchor": "OmniAgent",
            },
        ],
        "key_results": [
            {
                "text": "Beats Qwen2.5-VL-72B on LVBench.",
                "anchor": "video understanding",
            },
        ],
        "limitations": [
            {
                "text": "Limited out-of-distribution evaluation.",
                "anchor": "video understanding",
            },
        ],
    }
    ledger_path = tmp_path / "paper-ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ctx = {
        "show": "papers",
        "scratch_dir": tmp_path,
        "plugin_root": PLUGIN_ROOT,
        "date": "2026-06-18",
        "fulltext": fulltext,
    }

    result = check_ledger_verify(ledger_path, ctx)
    assert isinstance(result, dict), f"gate must return dict, got {type(result).__name__}"
    assert result.get("ok") is False, (
        f"gate must FLAG a fabricated anchor (ok=False), got {result!r}"
    )
    # Reason must NAME the fabrication so a human can chase it down.
    reason = result.get("reason", "")
    assert reason, f"flagged result must carry a non-empty 'reason', got {result!r}"


def test_check_ledger_verify_flags_missing_section(tmp_path):
    """A ledger missing one of the four required sections must flag
    (`ok=False`). This is the schema half — `validate_ledger` raises
    `LedgerError` on a missing section; the gate translates that to
    `{ok: False, reason: ...}` (the runner's gate protocol).
    """
    from lib.paperline.executors import check_ledger_verify

    # Drop the `limitations` section.
    ledger = {
        "problem": [{"text": "Long video is expensive.", "anchor": "video"}],
        "method": [{"text": "Iterative loop.", "anchor": "loop"}],
        "key_results": [{"text": "Beats 72B baseline.", "anchor": "baseline"}],
        # NO `limitations` — schema violation.
    }
    ledger_path = tmp_path / "paper-ledger.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    ctx = {
        "show": "papers",
        "scratch_dir": tmp_path,
        "plugin_root": PLUGIN_ROOT,
        "date": "2026-06-18",
        "fulltext": "video loop baseline",
    }

    result = check_ledger_verify(ledger_path, ctx)
    assert isinstance(result, dict), f"gate must return dict, got {type(result).__name__}"
    assert result.get("ok") is False, (
        f"gate must FLAG a missing-section ledger (ok=False), got {result!r}"
    )
    # Reason must mention the missing section by name.
    reason = result.get("reason", "")
    assert "limitations" in reason or "missing" in reason.lower(), (
        f"reason must name the missing section, got {reason!r}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_papers_config() -> dict:
    """A minimal `cfg.papers` shape the `config` executor can read from
    ctx. The real config comes from `require_papers(cfg)`; for the executor
    unit tests we inject a stand-in so the executor doesn't crash on
    `ctx["cfg"].papers.categories` access.
    """
    return {
        "papers": {
            "categories": ("cs.CL",),
            "max_candidates": 60,
        },
    }


def _fulltext_for_ledger(staged_path: Path, payload: dict) -> str:
    """Load the fulltext corresponding to the staged ledger from the
    p2-samples sibling files. Falls back to a placeholder if neither
    sibling is present (the test is then skipped — see caller)."""
    samples_dir = staged_path.parent
    html_head = samples_dir / "arxiv-2606.19341-html-head.html"
    pdftotext_txt = samples_dir / "arxiv-2606.19341-pdftotext.txt"
    if pdftotext_txt.exists():
        return pdftotext_txt.read_text(encoding="utf-8")
    if html_head.exists():
        return html_head.read_text(encoding="utf-8")
    return ""