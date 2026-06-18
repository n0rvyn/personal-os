"""Line registry — the per-line bundle the line-agnostic engine consumes.

Phase 1 (paper-digest dev-guide) extracts the runner's hard-coded line bindings
into a `LineBundle` injected per "line". A LINE groups one or more shows that
share a topology/gate-map/executor-map/editorial/floor; the engine looks bindings
up via `get_line(show)` instead of importing them directly.

- **Opinion line** (morning + evening): two shows, one topology. Its bundle
  REFERENCES the existing objects (load_pipeline, runner._default_gate_map,
  episode.floor_chars_for_show, the references/{show}.md loader) so morning/evening
  behavior is byte-identical to pre-refactor. (DP-A1, crystal D-004.)
- **Paper line**: registered in a later phase (P2+); not present here.

All cross-module imports inside the bundle callables are LAZY (deferred to call
time) to break the import cycle: `lib.runner` imports `get_line` from this module,
and this module's opinion bundle delegates back to `lib.runner._default_gate_map`
/ `lib.runner._opinion_executor_map`. Lazy imports keep both modules importable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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
    - `floor_fn(show)` → the per-show min-chars length floor.
    """

    line_id: str
    topology: Callable[[str], list]
    gate_map: Callable[[], dict]
    executor_map: Callable[[], dict]
    editorial_loader: Callable[[str, Any], str]
    agent_dir: str
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
    floor_fn=_opinion_floor,
)


# ---------------------------------------------------------------------------
# Registry: show → line. Paper line is added in P2+.
# ---------------------------------------------------------------------------
_LINE_REGISTRY: dict[str, LineBundle] = {
    "morning": OPINION_LINE,
    "evening": OPINION_LINE,
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
