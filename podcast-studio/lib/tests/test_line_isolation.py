"""Task 6 (P1): the "two lines don't fight" structural guard (DP-A3).

The opinion line (morning/evening) and the paper line must not import each
other's line-specific modules — so optimizing one line later can never touch
the other. The opinion-side assertion is active now; the paper-side activates
in P2 when lib/paperline/* exists.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent

# Opinion-line (+ shared-engine) modules that must NOT reach into the paper line.
_OPINION_MODULES = [
    "runner",
    "pipeline",
    "lines",
    "episode",
    "stance",
    "coveredground",
    "magnitude",
    "bible",
    "throughline",
    "dedup",
    "structlint",
    "scorecard",
    "factcheck",
]

# Paper-line module namespaces (land in P2+).
_PAPER_PREFIXES = ("lib.pipeline_papers", "lib.paperline")


def _imported_modules(module_basename: str) -> set[str]:
    src = (_LIB / f"{module_basename}.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_opinion_line_does_not_import_paper_line():
    """No opinion/shared module may import a paper-line module (DP-A3)."""
    offenders = {}
    for mod in _OPINION_MODULES:
        path = _LIB / f"{mod}.py"
        if not path.exists():
            continue
        bad = [
            imp
            for imp in _imported_modules(mod)
            if any(imp == p or imp.startswith(p + ".") for p in _PAPER_PREFIXES)
        ]
        if bad:
            offenders[mod] = bad
    assert not offenders, f"opinion modules import paper-line modules: {offenders}"


@pytest.mark.skip(reason="paper line lands in P2; activate when lib/paperline/* exists")
def test_paper_line_does_not_import_opinion_line():
    """P2: assert lib/pipeline_papers.py + lib/paperline/* do NOT import the
    opinion-line-specific modules (stance / coveredground / magnitude / bible)."""
    raise NotImplementedError("activate in P2")
