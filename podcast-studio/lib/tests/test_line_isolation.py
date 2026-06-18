"""Task 6 (P1): the "two lines don't fight" structural guard (DP-A3).

The opinion line (morning/evening) and the paper line must not import each
other's line-specific modules — so optimizing one line later can never touch
the other. The opinion-side assertion is active now; the paper-side activates
in P2 when lib/paperline/* exists.
"""
from __future__ import annotations

import ast
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent

# Opinion-line modules that must NOT reach into the paper line. NOTE: "lines"
# is intentionally absent — it is the shared line REGISTRY (lib/lines.py), and
# PAPER_LINE callables there lazy-import lib.pipeline_papers the same way
# OPINION_LINE lazy-imports lib.pipeline. That lazy import IS the legitimate
# cross-line bridge, not a firewall violation. The firewall targets
# line-SPECIFIC logic modules; the registry is shared infrastructure.
_OPINION_MODULES = [
    "runner",
    "pipeline",
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

# The four opinion-ONLY modules the paper line must not reach into
# (factcheck / config / dispatch / episode / pipeline are deliberately shareable —
# P3 reuses factcheck as the faithfulness gate; the registry lives in lines.py).
# Both `lib.`-prefixed and bare forms are listed: this repo imports as
# `from lib.bible import …` (plugin root on sys.path via conftest), so matching
# must cover the prefixed form — matching only the bare names against
# `module.split(".")[0]` made this check VACUOUS (split("lib.bible")[0] == "lib").
_FORBIDDEN_IN_PAPER = {
    "stance", "coveredground", "magnitude", "bible",
    "lib.stance", "lib.coveredground", "lib.magnitude", "lib.bible",
}


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


def _iter_paper_modules() -> list[Path]:
    """Yield every Python file under the paper-line surface: the pipeline_papers
    topology module + every module under lib/paperline/ (init + siblings)."""
    files: list[Path] = [_LIB / "pipeline_papers.py"]
    paper_pkg = _LIB / "paperline"
    files.extend(sorted(paper_pkg.glob("*.py")))
    return [p for p in files if p.exists()]


def _check_paper_clean(path: Path) -> list[str]:
    """Return any forbidden opinion-line imports found in `path` (or []).

    Matches the FULL imported module name against each forbidden form with
    `imp == f or imp.startswith(f + ".")` (same matcher as the opinion-side
    direction). This catches `from lib.bible import …`, `import lib.stance`,
    bare `from stance import …`, and submodule forms (`lib.bible.x`) alike —
    unlike the prior `split(".")[0]` matcher, which collapsed every
    `lib.`-prefixed import to `"lib"` and never fired.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return [
        imp
        for imp in sorted(names)
        if any(imp == f or imp.startswith(f + ".") for f in _FORBIDDEN_IN_PAPER)
    ]


def test_paper_line_does_not_import_opinion_line():
    """P2: assert lib/pipeline_papers.py + lib/paperline/* do NOT import the
    four opinion-only modules (stance / coveredground / magnitude / bible)."""
    offenders: dict[str, list[str]] = {}
    for path in _iter_paper_modules():
        bad = _check_paper_clean(path)
        if bad:
            offenders[str(path.relative_to(_LIB))] = bad
    assert not offenders, f"paper-line modules import forbidden opinion modules: {offenders}"
