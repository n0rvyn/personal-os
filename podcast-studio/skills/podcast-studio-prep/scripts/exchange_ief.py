"""IEF reader for podcast-studio.

Phase 5 — Fleet-consumer note (this file's reason for existing):

podcast-studio is now a **fleet consumer** (舰队消费者) in the personal-os Insight
Exchange Format (IEF) network. It reads IEF markdown written by sibling
plugins (e.g. domain-intel, youtube-scout) from
`{cfg.exchange_dir}/<producer>/**/*.md`, parses frontmatter, normalizes
the candidate shape, and feeds it to the orchestrator's brief as a
deterministic field (`run_check` stays PURE — all filesystem IO lives
in the CLI `check` handler, not in this reader either).

What is read and what is NOT:

- This module reads a **DIRECTORY** injected via `lib/config.py`
  (`cfg.exchange_dir`, resolved from `~/.podcast-studio/config.yaml`).
  It does **NOT** `import` from pkos / personal-os / domain-intel. The
  exchange IEF files are plain markdown on disk; no runtime code
  dependency on the producer plugin is created.
- The personal-os self-containment red line ("podcast-studio must not
  depend on personal-os at runtime") is therefore **NOT broken** by
  this file. The red line is about CODE dependencies, not about a
  shared filesystem staging area that config wires up.
- The CLAUDE.md / README wording that names podcast-studio as a fleet
  consumer (and adjusts the "fully independent" phrasing) is **deferred
  to Phase 6** along with the rest of the doc rewording work, so this
  file's docstring is the in-code signpost until then.

This module parses the markdown ITSELF (split frontmatter + yaml.safe_load).
It is self-contained: it does not import across plugin boundaries, and the
IEF files it reads are plain markdown on disk rather than runtime API
contracts.

First-run note: `ief_source_log.jsonl` may not exist on day 1 →
`recent_source_ids` returns an empty set → no IEF is excluded. That is the
expected non-error state; the pipeline simply offers everything in the
14-day window. This advisory is folded into the plan's Decisions section
(verifier advisory accepted at plan approval time, 2026-06-12).
"""
import os
import re
from datetime import date, timedelta
from pathlib import Path

import yaml


# 9 required IEF frontmatter keys (per docs/ief-format.md) + the consumption flag.
_REQUIRED_IEF_KEYS = (
    "id", "source", "url", "title", "significance", "tags",
    "category", "domain", "date", "read",
)

# Producer directory names owned by podcast-studio itself. Files under
# {exchange_dir}/{_SELF_PRODUCER_DIRS}/* are podcast-studio's own exchange
# output (see _archive_episode / IEF producer side elsewhere) — reading
# them back would create a feedback loop. Skip unconditionally.
_SELF_PRODUCER_DIRS = ("podcast-prep",)


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Split `text` into (frontmatter_dict, body).

    Expects the canonical `---<newline>...<newline>---<newline>body` shape.
    On any deviation (no opening/closing fence, bad YAML) returns
    (None, "") and lets the caller treat it as malformed. Never raises.
    """
    # Leading whitespace before the first `---` is tolerated (some editors
    # prepend a blank line). Match the first fenced block only.
    m = re.match(r"\A\s*---\s*\n(.*?\n)---\s*\n?(.*)\Z", text, re.DOTALL)
    if not m:
        return None, ""
    fm_raw, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_raw)
    except yaml.YAMLError:
        return None, ""
    if not isinstance(fm, dict):
        return None, ""
    return fm, body


def _first_body_line(body: str, max_chars: int = 200) -> str:
    """Excerpt = first non-empty, non-header body line, trimmed to `max_chars`.

    Real IEF bodies start with `# {title}` (markdown H1) followed by a
    `**Problem:** ...` / `**Insight:** ...` template; the H1 is metadata
    that duplicates the `title` field, so it is not a useful excerpt. Skip
    markdown ATX headers (`# `, `## `, …) to land on the first prose line.
    """
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue  # skip markdown headers
        return s[:max_chars]
    return ""


def _coerce_significance(raw) -> tuple[int | None, bool]:
    """Return (int_value, ok). ok=False when raw is missing, non-numeric,
    or not an integer (e.g. a float string like '3.5' or a stray word)."""
    if raw is None:
        return None, False
    if isinstance(raw, bool):
        # bool is a subclass of int in Python — reject explicitly.
        return None, False
    if isinstance(raw, int):
        return raw, True
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, False
        try:
            # IntValue must be a real int — float strings like "3.5" fail
            # under strict int() parse.
            return int(s), True
        except ValueError:
            return None, False
    return None, False


def _coerce_date(raw) -> tuple[date | None, bool]:
    """Return (date_value, ok). ok=False when raw is missing or unparseable.

    PyYAML parses unquoted `date: 2026-06-07` as a datetime.date object
    (ISO 8601 native type). Accept both that and a string-shaped form so
    both quoting styles are tolerated.
    """
    if isinstance(raw, date):
        return raw, True
    if not isinstance(raw, str) or not raw.strip():
        return None, False
    try:
        return date.fromisoformat(raw.strip()), True
    except ValueError:
        return None, False


def parse_ief_file(path, exchange_dir: str | None = None) -> tuple[dict | None, dict]:
    """Parse one IEF markdown file.

    Returns (candidate, diagnostic):
      - candidate: normalized dict on success — {path, title, tags, created,
        domain, excerpt, id, source, significance, category, url, read};
        `path` is RELATIVE to `exchange_dir` (or as-given when exchange_dir
        is None).
      - diagnostic: {"status": "skipped", "reason": ..., "path": ...} when
        the file is unreadable, has no/bad frontmatter, or fails any
        required-field check.

    Fail-closed: never raises. A bad file is one diagnostic; other files
    in the same exchange dir are unaffected.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        return None, {"status": "skipped", "reason": f"unreadable: {e}", "path": str(p)}

    fm, body = _split_frontmatter(text)
    if fm is None:
        return None, {"status": "skipped", "reason": "no/bad frontmatter", "path": str(p)}

    # Required field check — return at the FIRST missing key for a clean
    # diagnostic message; consumers don't need the full list to act.
    for key in _REQUIRED_IEF_KEYS:
        if key not in fm or fm[key] is None:
            return None, {"status": "skipped", "reason": f"missing required field: {key}",
                          "path": str(p)}

    sig, sig_ok = _coerce_significance(fm.get("significance"))
    if not sig_ok:
        return None, {"status": "skipped", "reason": f"non-integer significance: {fm.get('significance')!r}",
                      "path": str(p)}

    d, date_ok = _coerce_date(fm.get("date"))
    if not date_ok:
        return None, {"status": "skipped", "reason": f"unparseable date: {fm.get('date')!r}",
                      "path": str(p)}

    tags = fm.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return None, {"status": "skipped", "reason": "tags is not a list of strings",
                      "path": str(p)}

    # Relative path: useful for the brief + the ief_source_log (D-4: dedup by id,
    # but path is a good display label and matches cross_domain candidate shape).
    try:
        rel = str(p.relative_to(exchange_dir)) if exchange_dir else str(p)
    except ValueError:
        rel = str(p)

    candidate = {
        "path": rel,
        "title": str(fm.get("title", "")),
        "tags": list(tags),
        "created": d.isoformat(),
        "domain": str(fm.get("domain", "")),
        "excerpt": _first_body_line(body),
        "id": str(fm.get("id", "")),
        "source": str(fm.get("source", "")),
        "significance": sig,
        "category": str(fm.get("category", "")),
        "url": str(fm.get("url", "")),
        "read": bool(fm.get("read", False)),
    }
    return candidate, {"status": "ok", "path": str(p)}


def load_ief_candidates(exchange_dir, today: str, window_days: int = 14,
                        exclude_ids=None, n: int | None = None
                        ) -> tuple[list, list]:
    """Scan `exchange_dir` recursively for IEF candidates.

    Window: [today - window_days, today - 1] — exclude-today (D-3).
    The IEF candidate set is path-invariant across multiple same-day check
    runs; today-inclusive would zero out check B/C after check A wrote to
    ief_source_log. We need all three checks to see the same pool so
    parallel-N perturbation produces stable briefs.

    Args:
      exchange_dir: directory to rglob `**/*.md` from. None or nonexistent
        → ([], []): no-op, fail-soft.
      today: ISO date string (the episode's `date`).
      window_days: 14 by default; <=0 yields an empty result.
      exclude_ids: iterable of IEF `id` strings to skip (cross-period dedup).
        None → no exclusion.
      n: optional cap after sort (significance desc, date desc).

    Returns:
      (candidates, diagnostics). Each candidate is a dict as produced by
      `parse_ief_file`. Diagnostics are the per-file skip records.
    """
    if not exchange_dir:
        return [], []
    root = Path(exchange_dir)
    if not root.exists() or not root.is_dir():
        return [], []

    try:
        today_d = date.fromisoformat(today)
    except ValueError:
        return [], []
    if window_days <= 0:
        return [], []

    # Exclude-today window: [cutoff, today - 1]
    cutoff = today_d - timedelta(days=window_days)
    upper_excl = today_d  # exclusive upper bound

    exclude_set: set = set(exclude_ids) if exclude_ids else set()

    candidates: list = []
    diagnostics: list = []
    for md_path in root.rglob("*.md"):
        # Skip self-producer dirs to avoid feedback loops.
        try:
            rel_parts = md_path.relative_to(root).parts
        except ValueError:
            rel_parts = (md_path.name,)
        if rel_parts and rel_parts[0] in _SELF_PRODUCER_DIRS:
            continue

        cand, diag = parse_ief_file(md_path, exchange_dir=str(root))
        if cand is None:
            diagnostics.append(diag)
            continue
        # Window filter: cutoff <= d < upper_excl (exclude today)
        d = date.fromisoformat(cand["created"])
        if not (cutoff <= d < upper_excl):
            continue
        # Id-based dedup (D-4): id is the stable key, not the file path.
        if exclude_set and cand["id"] in exclude_set:
            continue
        candidates.append(cand)

    # Sort: significance desc (more important first), date desc (newer first).
    # ISO YYYY-MM-DD strings sort lexicographically the same as chronologically,
    # so the 2-tuple key with reverse=True yields sig desc + date desc in a
    # single pass.
    candidates.sort(
        key=lambda c: (int(c.get("significance", 0)), c.get("created", "")),
        reverse=True,
    )

    if n is not None:
        candidates = candidates[:n]

    return candidates, diagnostics
