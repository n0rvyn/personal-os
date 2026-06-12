"""podcast-studio episode helper.

Deterministic per-step glue for the /podcast pipeline:
- sanitize_title / episode_paths: filename naming + path-traversal safety
- check_artifact: per-step artifact-presence gate (presence + non-empty only)
- check_min_chars / floor_chars_for_show: per-show LENGTH floor — the coded
  gate that catches a too-short script. The 字数 target used to live ONLY in
  the persona prompt, so a too-short draft slipped the presence-only
  check_artifact gate and shipped (an evening run went out at ~1500 字). The
  floor is the PRODUCT minimum (~18 min), not the disaster line — a length
  miss re-dispatches with an EXPAND instruction (see SKILL.md retry contract),
  it does not just re-run identical inputs.
- select_draft: max-total scorer (ignores LLM-mislabeled `selected` flag)
- make_scratch / cleanup_scratch: per-run scratch dir lifecycle

Claude-driven skill SKILL.md calls these; the deterministic parts live here
so the gate isn't Claude self-discipline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


# Negated whitelist for sanitize_title: keep ASCII word chars + CJK Unified
# Ideographs + dot/space/hyphen; replace everything else with `-`.
# NOTE: a negated class is used (rather than `[\w一-鿿 .-]`) because Python
# 3.13 has buggy `re.sub` behavior with `\w` inside a non-ASCII char class
# — every ASCII char gets replaced too. Using explicit char ranges avoids
# the issue.
_TITLE_STRIP_RE = re.compile(r"[^a-zA-Z0-9_一-鿿 .-]")
_MULTI_DASH_RE = re.compile(r"-+")
_MULTI_SPACE_RE = re.compile(r" +")
_MULTI_DOT_RE = re.compile(r"\.+")
_TITLE_CAP = 60


def sanitize_title(title: str) -> str:
    """Return a safe filename slug for a title.

    - Replaces path separators, control chars, and anything outside the
      whitelist with `-`.
    - Collapses repeated separators / whitespace.
    - Strips leading/trailing whitespace and `-`.
    - Length-capped at ~60 chars.
    - Empty result if nothing kept (caller falls back to date-only name).
    """
    if not isinstance(title, str):
        return ""

    # First pass: replace anything outside the whitelist with `-`.
    kept = _TITLE_STRIP_RE.sub("-", title)

    # Collapse runs of `-`, spaces, and dots.
    kept = _MULTI_DASH_RE.sub("-", kept)
    kept = _MULTI_SPACE_RE.sub(" ", kept)
    kept = _MULTI_DOT_RE.sub(".", kept)

    # Strip leading/trailing whitespace, dashes, and dots.
    kept = kept.strip(" -.")

    # Length cap.
    if len(kept) > _TITLE_CAP:
        kept = kept[:_TITLE_CAP].rstrip(" -")

    return kept


def stance_path(
    output_dir: str | os.PathLike,
    date: str,
    show: str,
) -> Path:
    """Return the canonical stance-card path: `{output_dir}/{date}-{show}.stance.yaml`.

    Shared single source of truth — `lib/stance.py` imports this rather than
    duplicating the literal (MR-2: avoid two writers racing append-only
    refuse-overwrite on different path strings).
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    return out_dir / f"{date}-{show}.stance.yaml"


def episode_paths(
    output_dir: str | os.PathLike,
    date: str,
    title: str,
    show: str,
) -> dict[str, Path]:
    """Return the canonical paths for an episode.

    Returns a dict with keys: `script` (.md), `audio` (.mp3), `stance` (.yaml).

    Naming rule:
      - sanitized title is non-empty → `{date}-{title}`
      - else → `{date}-{show}` (date-only fallback)
    All returned paths are asserted to be inside `output_dir`.
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    if not out_dir.exists():
        raise FileNotFoundError(f"output_dir does not exist: {out_dir}")
    if not out_dir.is_dir():
        raise NotADirectoryError(f"output_dir is not a directory: {out_dir}")

    slug = sanitize_title(title)
    if not slug:
        # Date-only fallback uses the show name (e.g. "morning" / "evening").
        base = f"{date}-{show}"
    else:
        base = f"{date}-{slug}"

    paths = {
        "script": out_dir / f"{base}.md",
        "audio": out_dir / f"{base}.mp3",
        "stance": stance_path(out_dir, date, show),
    }

    # Path-traversal guard: all joined paths must stay inside output_dir.
    for key, p in paths.items():
        real = os.path.realpath(str(p))
        if not real.startswith(str(out_dir) + os.sep) and real != str(out_dir):
            raise ValueError(
                f"episode path escapes output_dir ({key}): {p}"
            )

    return paths


def check_artifact(path: str | os.PathLike) -> dict[str, Any]:
    """Return whether an artifact is present and non-empty.

    Returns: `{"ok": bool, "reason": str}`. `ok` iff file exists and
    size > 0. Zero-byte files count as missing.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "reason": f"missing: {p}"}
    if not p.is_file():
        return {"ok": False, "reason": f"not a file: {p}"}
    if p.stat().st_size == 0:
        return {"ok": False, "reason": f"empty: {p}"}
    return {"ok": True, "reason": f"present: {p}"}


def check_stance_card(
    output_dir: str | os.PathLike,
    date: str,
    show: str,
) -> dict[str, Any]:
    """Continuity gate: is the stance card for {output_dir, date, show}
    present AND a loadable stance card?

    Returns `{"ok": bool, "reason": str}` (same shape as check_artifact).
    `ok` iff the canonical `{date}-{show}.stance.yaml` exists, is non-empty,
    parses as YAML, and is a mapping with an `episode` block. A zero-byte or
    garbage file counts as missing — a half-written card must not pass as a
    continuity record. Fail-closed: any parse error returns ok=False, never
    an uncaught raise.

    The card CONTENT is authored by the pipeline's finalize hook (LLM); this
    gate only enforces PRESENCE so the write step cannot be silently skipped.
    Does a direct single-file parse rather than calling lib.stance.load_cards
    — load_cards scans the whole dir, and stance.py imports stance_path from
    this module, so importing lib.stance here would be circular.
    """
    path = stance_path(output_dir, date, show)
    basic = check_artifact(path)  # presence + non-empty
    if not basic["ok"]:
        return basic
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — fail-closed on any parse/read error
        return {"ok": False, "reason": f"unparseable stance card {path}: {e}"}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("episode"), dict):
        return {"ok": False, "reason": f"not a stance card (no episode block): {path}"}
    return {"ok": True, "reason": f"present: {path}"}


# Length gate ------------------------------------------------------------------

# Coded character floor per show — the PRODUCT minimum a draft / finalized body
# must clear: set at the real show-length floor (~18 min), BELOW the ~20 min
# prompt target so normal variance does not false-reject, but ABOVE the disaster
# line so a stunted episode (the 1500-字 disaster, and the ~14 min run that
# erodes through polish→finalize) is caught and re-dispatched to EXPAND rather
# than silently shipped. Both shows unified at floor 6500 / target ~7000 字.
#
# Measured TTS rate (n=1, 2026-06-11 evening sample: 2329 CJK / 2731 non-ws 字
# → mp3 7m28s): ≈310 CJK字/min ≈365 non-ws字/min (check_min_chars counts
# non-whitespace). So floor 6500 ≈18 min, target 7000 ≈20 min. Earlier
# 150–250 字/min figures were wrong guesses — RE-MEASURE if the TTS voice/speed
# setting changes. 6500 is calibrated tight (≈1.4 min headroom under target);
# if healthy episodes trip the gate often, lower toward ~6200. The target lives
# in the persona prompt; the FLOOR lives here so a short script can no longer
# pass the presence-only check_artifact gate.
_FLOOR_CHARS_BY_SHOW = {"morning": 6500, "evening": 6500}


def floor_chars_for_show(show: str) -> int:
    """Return the coded character floor for a show ('morning' / 'evening').

    Fail-closed: an unknown show raises ValueError rather than defaulting to a
    permissive 0 — a typo'd show name must not silently disable the floor.
    """
    try:
        return _FLOOR_CHARS_BY_SHOW[show]
    except (KeyError, TypeError):
        raise ValueError(
            f"unknown show {show!r}; expected one of "
            f"{sorted(_FLOOR_CHARS_BY_SHOW)}"
        )


def _count_script_chars(text: str) -> int:
    """Count non-whitespace characters — the '字数' proxy for the length gate.

    Whitespace and line breaks (incl. CJK full-width space U+3000, which
    str.isspace() recognizes) are excluded so markdown layout — blank lines
    between 段 — does not inflate the count. Markdown syntax chars (#, -, *) and
    the few English/number tokens in a Chinese script are a negligible fraction
    of a multi-thousand-char body and the floor sits well below target, so they
    need no special stripping; the gate is for catching a clearly-short script,
    not for billing-grade word counting.
    """
    return sum(1 for ch in text if not ch.isspace())


def check_min_chars(
    path: str | os.PathLike,
    min_chars: int,
    *,
    json_field: str | None = None,
) -> dict[str, Any]:
    """Length gate: is the artifact present AND at least `min_chars` 字 long?

    Returns `{"ok": bool, "reason": str}` — the SAME shape as check_artifact, so
    a miss rides the pipeline's existing ok=False re-dispatch loop (no new retry
    policy). Composes check_artifact first (presence + non-empty), then counts
    non-whitespace characters.

    - `json_field=None` (default): count the file's whole text. Used for the
      step-7 drafts (plain `.md`) and the step-15 published reader `.md`.
    - `json_field="body"`: parse the file as JSON and count that field's string
      value instead. Used to gate the step-12 finalize `body` (which lives
      inside `finalize-result.json`) BEFORE the expensive 口播稿 + TTS, without
      first writing the body to its own file. A non-dict JSON, a missing field,
      or a non-string field value fails closed.

    A present-but-short artifact fails naming the actual vs required count, so a
    too-short draft / body can no longer pass the structure gate silently.
    """
    basic = check_artifact(path)
    if not basic["ok"]:
        return basic
    raw = Path(path).read_text(encoding="utf-8", errors="replace")

    if json_field is None:
        text = raw
    else:
        try:
            obj = json.loads(raw)
        except Exception as e:  # noqa: BLE001 — fail-closed on any parse error
            return {"ok": False, "reason": f"unparseable JSON {Path(path)}: {e}"}
        val = obj.get(json_field) if isinstance(obj, dict) else None
        if not isinstance(val, str):
            return {
                "ok": False,
                "reason": (
                    f"JSON field {json_field!r} missing or not a string: "
                    f"{Path(path)}"
                ),
            }
        text = val

    n = _count_script_chars(text)
    where = f"{Path(path)}" + (f"[{json_field}]" if json_field else "")
    if n < min_chars:
        return {"ok": False, "reason": f"too short: {n} < {min_chars} 字 ({where})"}
    return {"ok": True, "reason": f"length ok: {n} ≥ {min_chars} 字 ({where})"}


def make_scratch(output_dir: str | os.PathLike, run_id: str) -> Path:
    """Create a per-run scratch directory under output_dir.

    Returns the scratch path. Caller is responsible for cleanup.
    """
    if not run_id or not isinstance(run_id, str):
        raise ValueError(f"run_id must be a non-empty string, got {run_id!r}")
    # Restrict run_id to a safe filename slug (re-use sanitize_title, which
    # also handles CJK + path-separators).
    safe_id = sanitize_title(run_id) or "run"
    scratch = Path(str(output_dir)) / f".scratch-{safe_id}"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def cleanup_scratch(scratch: str | os.PathLike) -> None:
    """Remove a scratch directory — best-effort.

    Safe to call from both success and error/finally paths. Tolerates a
    non-existent path (no-op). Recursive removal.

    Best-effort by contract: an OSError (e.g. a host/sandbox UID split where
    the scratch was created under a different uid than the one running cleanup,
    so the remove gets EPERM/"Operation not permitted") is logged to stderr and
    swallowed, NOT raised. The final artifacts (.md/.mp3/.yaml) already live at
    output_dir root by the time cleanup runs; a leftover scratch is harmless and
    the next run's make_scratch creates a fresh dir. Swallowing here keeps
    "cleanup is non-blocking" a coded contract, not a per-run Claude judgment.
    NOTE: the EPERM is a SYMPTOM of a uid split that could touch more than
    cleanup; this swallow does not fix that root, only stops it blocking the run.
    """
    p = Path(scratch)
    if not p.exists():
        return
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    except OSError as e:
        print(f"cleanup_scratch: best-effort skip, could not remove {p}: {e}",
              file=sys.stderr)


# Select_draft helpers ---------------------------------------------------------

# Order in which candidates are considered for tie-breaking when total AND
# 洞察 tie. The export ref (ref:666-667) pins the order 稿-A < 稿-B < 稿-C.
_CANDIDATE_ORDER = ("稿-A", "稿-B", "稿-C")


def _candidate_order_index(candidate_id: str) -> int:
    """Lower index = wins tie-break. Unknown ids sort after known ones
    (deterministic via insertion order of the candidates dict)."""
    try:
        return _CANDIDATE_ORDER.index(candidate_id)
    except ValueError:
        return len(_CANDIDATE_ORDER)


def select_draft(
    verdict: dict[str, Any],
    candidates: dict[str, str],
) -> tuple[str, str]:
    """Pick the winning draft from a 钱钟书 scoring verdict.

    Semantics (per export ref:666-667):
    - **Ignore any `selected` / `chosen` flag** — the scoring LLM can
      mislabel it. Winner is determined by `scores.total` alone.
    - Pick the candidate with the **max `scores.total`**.
    - Tiebreak: higher `洞察` score wins.
    - Tiebreak: candidate order `稿-A` < `稿-B` < `稿-C` wins.

    `candidates` maps `candidate_id` (e.g. "稿-A") to its associated value
    (e.g. a path). Returns `(chosen_id, chosen_value)`.

    Malformed / empty verdict → raises `ValueError` (never silently picks
    the first candidate).
    """
    if not isinstance(verdict, dict):
        raise ValueError(f"verdict must be a dict, got {type(verdict).__name__}")
    if not isinstance(candidates, dict) or not candidates:
        raise ValueError("candidates must be a non-empty dict")

    cands = verdict.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise ValueError("verdict.candidates must be a non-empty list")

    # Score the candidates that are present in the candidates mapping.
    scored: list[tuple[str, int, int, int, str]] = []
    seen_ids: set[str] = set()
    for entry in cands:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("candidate_id")
        if not isinstance(cid, str) or not cid:
            continue
        if cid not in candidates:
            # Skip verdict entries with no corresponding candidate file.
            continue
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        scores = entry.get("scores") or {}
        if not isinstance(scores, dict):
            raise ValueError(f"verdict candidate {cid!r} scores must be a dict")

        # total — accept either an explicit total or recompute from the 4 KPIs.
        total_raw = scores.get("total")
        if isinstance(total_raw, (int, float)):
            total = int(total_raw)
        else:
            total = sum(
                int(scores.get(k, 0))
                for k in ("洞察", "命名", "跨域", "思考问句")
            )

        insight = int(scores.get("洞察", 0))

        scored.append(
            (
                cid,
                total,
                insight,
                _candidate_order_index(cid),
                candidates[cid],
            )
        )

    if not scored:
        raise ValueError(
            "no candidates in verdict match the candidates mapping"
        )

    # Sort: higher total wins; tie → higher 洞察 wins; tie → lower order idx wins.
    # `max` picks the first element under the sort key tuple (which sorts
    # ascending), so we sort with a key that makes the best candidate first.
    scored.sort(
        key=lambda t: (-t[1], -t[2], t[3], _fallback_order(t[0], candidates))
    )
    chosen_id, _, _, _, chosen_value = scored[0]
    return chosen_id, chosen_value


def _fallback_order(candidate_id: str, candidates: dict[str, str]) -> int:
    """Stable fallback for unknown candidate_ids: use dict insertion order."""
    keys = list(candidates.keys())
    try:
        return keys.index(candidate_id)
    except ValueError:
        return len(keys)
