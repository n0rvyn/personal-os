"""paper-log store — 论文线连续性 (D-013).

The paper line's own continuity store: an append-only YAML list of the papers
already covered, used by the curator for arXiv-id dedup (DP-403=A) and by the
same-day guard. One entry per covered paper: `{arxiv_id, title, date, concepts}`.
NO bets, NO opinion (unlike the opinion line's stance cards — D-013).

Physically isolated from the opinion line (D-015 firewall): this module imports
ONLY stdlib + yaml. It must NEVER import `lib.stance` / `lib.coveredground` /
`lib.magnitude` / `lib.bible` (opinion-only continuity) nor the shared engine
modules (`lib.runner` / `lib.pipeline` / `lib.episode` / `lib.dispatch`) — paper
continuity is a paper-line primitive (enforced by
`test_paperlog_does_not_import_opinion_line`).

Discipline (mirrors `lib.stance` where it applies):
  - **load fails CLOSED**: a corrupt log RAISES, never silently returns `[]` —
    a silent-empty log would let the next run's curator re-select a covered
    paper (the worst-case silent failure for the dedup 命脉, D-013). An empty /
    missing file legitimately returns `[]` (first run).
  - **append-only**: load → append → atomic write; the store is never
    overwritten with fewer entries.
  - **atomic write**: temp file in `state_dir` + `os.replace` (no torn write,
    no orphan temp) — mirrors `lib.stance.write_card`.
  - **input validation fails CLOSED**: schema + arxiv_id/date format checked
    before write; title/concepts sanitized (they feed the curator prompt, so
    newlines/control chars could break the YAML block or inject structure).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

# The paper-log file lives at `<state_dir>/paper-log.yaml` (the paper line's
# state/ subdir — DP-402=A: output_dir/papers/state/).
_PAPER_LOG_NAME = "paper-log.yaml"

# arxiv_id format — mirror of lib.paperline.discovery._ARXIV_ID_RE (inlined to
# keep paperlog import-isolated: discovery is a sibling, but inlining the small
# regex avoids dragging its dependency surface into the continuity primitive).
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

# date format — ISO YYYY-MM-DD.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Control characters (incl. newlines) stripped from free-text fields before
# they enter the YAML store / curator prompt.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_REQUIRED_FIELDS = ("arxiv_id", "title", "date", "concepts")


def _log_path(state_dir: str | os.PathLike) -> Path:
    return Path(str(state_dir)) / _PAPER_LOG_NAME


def _sanitize_text(s: str) -> str:
    """Replace newlines / control characters with a space (then collapse runs).

    paper-log free-text (title, concept strings) feeds the curator prompt and
    the YAML block — embedded newlines could break the block structure or
    inject prompt sections, so they are flattened to spaces.
    """
    return " ".join(_CONTROL_RE.sub(" ", s).split())


def load_paperlog(state_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Return the list of covered-paper entries from `<state_dir>/paper-log.yaml`.

    - Missing file → `[]` (first run — no log yet).
    - Empty / whitespace-only file → `[]` (mirrors `lib.stance.load_cards`'s
      empty-file skip — an empty store is legitimately no coverage).
    - Corrupt YAML / non-list payload → RAISE (fail-closed, D-013: a silent
      empty return would let the curator re-select a covered paper).
    """
    path = _log_path(state_dir)
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    # yaml.safe_load raises YAMLError on malformed content — propagate (fail-closed).
    loaded = yaml.safe_load(raw)
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise ValueError(
            f"paper-log at {path} is not a YAML list (got {type(loaded).__name__}); "
            "refusing to treat a malformed dedup store as empty"
        )
    return loaded


def _validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Validate + sanitize a paper-log entry; raise on any schema/format breach.

    Returns a NEW dict with sanitized free-text — the dedup 命脉 must not admit
    dirty data (Threat Model §Input validation)."""
    if not isinstance(entry, dict):
        raise ValueError(f"paper-log entry must be a dict, got {type(entry).__name__}")
    for field in _REQUIRED_FIELDS:
        if field not in entry:
            raise ValueError(
                f"paper-log entry missing required field {field!r}; "
                f"required: {_REQUIRED_FIELDS}"
            )

    arxiv_id = entry["arxiv_id"]
    if not isinstance(arxiv_id, str) or not _ARXIV_ID_RE.match(arxiv_id):
        raise ValueError(
            f"invalid arxiv_id {arxiv_id!r}: must match {_ARXIV_ID_RE.pattern}"
        )

    date = entry["date"]
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise ValueError(
            f"invalid date {date!r}: must match ISO {_DATE_RE.pattern}"
        )

    title = entry["title"]
    if not isinstance(title, str):
        raise ValueError(f"title must be a string, got {type(title).__name__}")

    concepts = entry["concepts"]
    if not isinstance(concepts, list) or not all(isinstance(c, str) for c in concepts):
        raise ValueError(
            f"concepts must be a list[str], got {type(concepts).__name__}"
        )

    return {
        "arxiv_id": arxiv_id,
        "title": _sanitize_text(title),
        "date": date,
        "concepts": [_sanitize_text(c) for c in concepts],
    }


def append_paper(state_dir: str | os.PathLike, entry: dict[str, Any]) -> None:
    """Append a validated+sanitized entry to the paper-log (append-only, atomic).

    Validates the entry fail-closed (schema + arxiv_id/date format), sanitizes
    free-text, then loads the existing log, appends, and writes back via a temp
    file + `os.replace` (no torn write, no orphan temp). Raises on any validation
    breach WITHOUT touching the store.
    """
    clean = _validate_entry(entry)  # raises before any IO on a bad entry

    state = Path(str(state_dir))
    state.mkdir(parents=True, exist_ok=True)
    existing = load_paperlog(state_dir)  # fail-closed read (corrupt → raise)
    existing.append(clean)

    # Atomic write: temp file in the SAME dir (so os.replace is atomic on one
    # filesystem) + os.replace onto the target. mkstemp creates a uniquely-named
    # file; after os.replace the temp name no longer exists (no orphan).
    fd, tmp_name = tempfile.mkstemp(
        dir=str(state), prefix=".paper-log.yaml.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(existing, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp_name, str(_log_path(state_dir)))
    except BaseException:
        # On any failure, remove the temp so no orphan lingers, then re-raise.
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def is_covered(paperlog: list[dict[str, Any]], arxiv_id: str) -> bool:
    """True iff `arxiv_id` exactly matches some entry's `arxiv_id` (DP-403=A).

    Exact string match — a versioned id (`2606.19341v2`) and its bare id
    (`2606.19341`) are DISTINCT keys (covering both is two intentional episodes).
    Concept-level near-dedup is the curator persona's job, not this hard gate.
    """
    return any(
        isinstance(e, dict) and e.get("arxiv_id") == arxiv_id for e in paperlog
    )
