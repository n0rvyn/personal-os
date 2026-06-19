"""Line registry — the per-line bundle the line-agnostic engine consumes.

Phase 1 (paper-digest dev-guide) extracts the runner's hard-coded line bindings
into a `LineBundle` injected per "line". A LINE groups one or more shows that
share a topology/gate-map/executor-map/editorial/floor; the engine looks bindings
up via `get_line(show)` instead of importing them directly.

- **Opinion line** (morning + evening): two shows, one topology. Its bundle
  REFERENCES the existing objects (load_pipeline, runner._default_gate_map,
  episode.floor_chars_for_show, the references/{show}.md loader) so morning/evening
  behavior is byte-identical to pre-refactor. (DP-A1, crystal D-004.)
- **Paper line** (papers): Phase 2 collection skeleton (config → scratch →
  discovery → curator → fetch → ledger-write → ledger-verify). Its bundle
  REFERENCES the new `lib.pipeline_papers.load_papers_pipeline` and points
  `agent_dir` at `agents/papers/` (the paper personas Task 5 creates). The
  generation/publish stations land in P3/P4 — not declared-but-dead here.

All cross-module imports inside the bundle callables are LAZY (deferred to call
time) to break the import cycle: `lib.runner` imports `get_line` from this module,
and this module's opinion bundle delegates back to `lib.runner._default_gate_map`
/ `lib.runner._opinion_executor_map`. Lazy imports keep both modules importable.
The paper bundle's lazy `from lib.pipeline_papers import …` inside its
callables mirrors the opinion bundle's lazy pattern — the registry IS the
one legitimate cross-line bridge (test_line_isolation.py:18 must-fix #2).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Per-line whitelists the runner threads into dispatch_persona (P3). Opinion
# imports the leaf dispatch module directly (no cycle: dispatch is a leaf,
# lines is a registry, dispatch does not import lines). Paper single-sources
# from lib.pipeline_papers (the registry bridge — runner does NOT import
# paperline; pipeline_papers is the one legitimate cross-line import).
from lib.dispatch import AGENT_WHITELIST
from lib.pipeline_papers import PAPER_AGENT_WHITELIST  # noqa: E402, F401


@dataclass(frozen=True)
class LineBundle:
    """The per-line binding bundle (D-004 shape + floor_fn).

    - `topology(show)` → the ordered step list for the line.
    - `gate_map()` → the gate-name → gate-fn dict for the line.
    - `executor_map()` → the station-name → executor-callable dict for the line
      (lazy; wired into the runner in the executor-dispatch task). Each value
      encapsulates the FULL dispatch behavior of its station (ctx side-effects +
      return shaping), not just a helper call.
    - `editorial_loader(show, plugin_root)` → the per-show editorial text.
    - `agent_dir` → the directory dispatch_persona reads `<name>.md` from.
    - `whitelist` → the per-line agent whitelist the runner threads into
      `dispatch_persona` (P3 line-aware dispatch). Defaults are byte-faithful:
      OPINION_LINE carries the opinion `AGENT_WHITELIST`, PAPER_LINE carries
      `PAPER_AGENT_WHITELIST` from `lib.pipeline_papers` (single-sourced).
    - `floor_fn(show)` → the per-show min-chars length floor.
    """

    line_id: str
    topology: Callable[[str], list]
    gate_map: Callable[[], dict]
    executor_map: Callable[[], dict]
    editorial_loader: Callable[[str, Any], str]
    agent_dir: str
    whitelist: "frozenset[str]"
    floor_fn: Callable[[str], int]


# ---------------------------------------------------------------------------
# Opinion line (morning / evening) — references existing objects, lazy-bound.
# ---------------------------------------------------------------------------
def _opinion_topology(show: str) -> list:
    from lib.pipeline import load_pipeline  # lazy: no cycle (pipeline ⊄ lines)

    return load_pipeline(show)


def _opinion_gate_map() -> dict:
    from lib.runner import _default_gate_map  # lazy: breaks runner↔lines cycle

    return _default_gate_map()


def _opinion_executor_map() -> dict:
    # Wired in the executor-dispatch task: returns the runner's
    # station-name → executor-callable map (each encapsulating its full
    # dispatch block). Lazy so importing lib.lines never imports lib.runner.
    from lib.runner import _opinion_executor_map as _impl  # lazy

    return _impl()


def _opinion_editorial_loader(show: str, plugin_root: Any) -> str:
    """Read references/{show}.md; OSError → "" (byte-faithful to runner 1941-1947)."""
    try:
        return (
            Path(str(plugin_root)) / "skills" / "podcast" / "references" / f"{show}.md"
        ).read_text(encoding="utf-8")
    except OSError:
        return ""


def _opinion_floor(show: str) -> int:
    from lib.episode import floor_chars_for_show  # lazy

    return floor_chars_for_show(show)


OPINION_LINE = LineBundle(
    line_id="opinion",
    topology=_opinion_topology,
    gate_map=_opinion_gate_map,
    executor_map=_opinion_executor_map,
    editorial_loader=_opinion_editorial_loader,
    agent_dir="agents",
    whitelist=AGENT_WHITELIST,
    floor_fn=_opinion_floor,
)


# ---------------------------------------------------------------------------
# Paper line (papers) — Phase 2 collection skeleton (Task 6-impl).
# ---------------------------------------------------------------------------
def _paper_topology(show: str) -> list:
    from lib.pipeline_papers import load_papers_pipeline  # lazy: registry bridge

    return load_papers_pipeline(show)


def _paper_gate_map() -> dict:
    """Paper-line gate map — delegates to `lib.paperline.executors`.

    The paper-line gate map carries `check_ledger_verify` (the
    `ledger-verify` station's `gate[0].fn` per `lib.pipeline_papers`).
    The implementation lives in `lib.paperline.executors` (P3 Task 2-impl)
    — `lib.lines` is the registry, NOT the executor surface, so it lazy-
    delegates to keep the import graph clean (the registry bridge may
    not import executor implementations at import time).
    """
    from lib.paperline.executors import paper_gate_map  # lazy: registry bridge

    return paper_gate_map()


def _paper_executor_map() -> dict:
    """Paper-line executor map — delegates to `lib.paperline.executors`.

    Each value is a `(ctx) -> Any` callable wired in
    `lib.paperline.executors.paper_executor_map()`. Lazy delegate so
    importing `lib.lines` does not import the executor module (the
    executor module imports `lib.paperline.*` siblings + lazy runner /
    config helpers — would create a needless import chain at registry
    load time).
    """
    from lib.paperline.executors import paper_executor_map  # lazy: registry bridge

    return paper_executor_map()


def _paper_editorial_loader(show: str, plugin_root: Any) -> str:
    """Paper-line editorial loader.

    Reads `skills/podcast/references/papers.md` (the 4-段 editorial:
    问题→方法→结果→意义+局限 + 讲解者 register + committee 差异
    discipline). Mirrors the opinion line's OSError→"" behavior — a
    missing file yields empty string, never a raise (the runner's
    editorial path is fail-soft for the loader itself).
    """
    try:
        return (
            Path(str(plugin_root)) / "skills" / "podcast" / "references" / "papers.md"
        ).read_text(encoding="utf-8")
    except OSError:
        return ""


def _paper_floor(show: str) -> int:
    """Paper-line floor — the finalize-body min-chars gate.

    Returns 4500 (≈13 min @ ~350 non-ws chars/min, calibrated off the
    opinion 6500 ≈18 min ratio for a focused single-paper digest). The
    floor is enforced ONCE, on the finalize body (`check_min_chars` with
    `json_field="body"`, step 11 of `lib.pipeline_papers`) — the published
    deliverable, not the throwaway committee drafts (committee gates
    existence only; a too-short discarded draft must not halt the run).
    A too-short body re-derives the finalizer up to its retry cap, then
    halts. Opinion floor stays in `_opinion_floor` →
    `lib.episode.floor_chars_for_show` (byte-identical for morning/evening).
    """
    return 4500


PAPER_LINE = LineBundle(
    line_id="paper",
    topology=_paper_topology,
    gate_map=_paper_gate_map,
    executor_map=_paper_executor_map,
    editorial_loader=_paper_editorial_loader,
    agent_dir="agents/papers",
    whitelist=PAPER_AGENT_WHITELIST,
    floor_fn=_paper_floor,
)


# ---------------------------------------------------------------------------
# Registry: show → line. Paper line is registered here (Task 6-impl);
# engine's `get_line("papers")` now resolves to PAPER_LINE.
# ---------------------------------------------------------------------------
_LINE_REGISTRY: dict[str, LineBundle] = {
    "morning": OPINION_LINE,
    "evening": OPINION_LINE,
    "papers": PAPER_LINE,
}


def get_line(show: str) -> LineBundle:
    """Resolve a show to its line bundle. Fail-closed on an unregistered show."""
    try:
        return _LINE_REGISTRY[show]
    except KeyError:
        raise ValueError(
            f"unknown line for show {show!r}; "
            f"registered shows: {sorted(_LINE_REGISTRY)}"
        ) from None
