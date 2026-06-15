"""podcast-studio covered-ground store — 跨期记忆 (`covered-ground.yaml`).

Per-run distillation of the apparatus (signature anchors / analogies /
frameworks) the host reached for, persisted across episodes so the next
episode can render an "avoidance memo" — used at `_assemble_briefs` to
strip recycle-prone prose out of davinci's writing brief.

Design:

- Single YAML file under `output_dir`: `<output_dir>/covered-ground.yaml`.
  Same realpath-guard discipline as `lib.bible.bible_path`: a `..`-style
  traversal / symlink escape raises ValueError.
- Atomic overwrite (clone of `lib.bible.write_bible`): temp file in
  `output_dir` + `os.replace`, temp removed on error (no orphan).
- Store schema:
    {
      "anchors": {
        "<anchor>": {
          "first_used": "YYYY-MM-DD",
          "last_used":  "YYYY-MM-DD",
          "count":      <int>,
          "episodes":   [{"date": "YYYY-MM-DD", "show": "<morning|evening>"}, ...]
        }
      }
    }
- Staleness predicates (per dev-guide Phase 2 / DP-001=A):
    (a) `count_in_window >= 3` over a 14-day window
    (b) `distinct_episodes_in_last_3 >= 2` over the latest 3 episode dates
  Either true → "hot". `render_memo` lists hot anchors with avoidance
  semantics; cool anchors are silent.
- Reskin detection: `update_store` consults `similarity_fn(anchor, key)`
  for every existing key. A score >= `_RESKIN_THRESHOLD` folds the new
  anchor INTO the existing key (count+1, last_used updated, episodes
  appended) — so "苏伊士运河 1956" merges with "1956苏伊士". A score below
  threshold → new entry.
- Temperature shield: `update_store` only ever stores `apparatus`-shaped
  inputs (callers feed the distiller's anchors list — not bet free text).
  `render_memo` outputs are confined to apparatus avoidance; they NEVER
  advise against opinions / bets.

The store is NOT a stance card: it is a refreshed cumulative projection
(overwrite, not append-only).
"""
from __future__ import annotations

import datetime as _dt
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

import yaml


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Fixed filename under `output_dir` (mirrors `lib.bible._BIBLE_FILENAME`).
_STORE_FILENAME = "covered-ground.yaml"

# Reskin detection threshold. Same value lives in `lib.embed`; the
# covered-ground store uses it as a guard against trivial paraphrases
# inflating the anchor dictionary. A candidate anchor whose
# `similarity_fn(candidate, existing_key)` >= this for ANY existing key
# folds INTO that key.
_RESKIN_THRESHOLD = 0.93

# Default staleness window (per dev-guide Phase 2 / magnitude judge).
_WINDOW_DAYS = 14

# "Last 3 episodes" window for the recency predicate.
_RECENCY_WINDOW = 3

# Recency lookback: the most recent episode in a recency-hot anchor must
# fall within this many days of `today`. Guards against stale-but-diverse
# entries (e.g. "used twice over 6 months ago") triggering the recency
# predicate when the count predicate already windows to `window_days`.
# 1 day = "yesterday or today" — the anchor must have been used very
# recently for the recency rule to fire.
_RECENCY_LOOKBACK_DAYS = 1

# Episode dedup key: ("date", "show") — two updates of the same anchor
# with the same episode must not double-count.
_EP_KEY_DATE = "date"
_EP_KEY_SHOW = "show"


# ---------------------------------------------------------------------------
# Path resolution + realpath guard (mirror lib.bible.bible_path)
# ---------------------------------------------------------------------------

def store_path(output_dir: str | os.PathLike) -> Path:
    """Return the canonical store path: `<output_dir>/covered-ground.yaml`.

    Realpath-asserted: the resolved path must stay inside the resolved
    parent of the input. Catches two escape modes:
      (a) `..`-style traversal in the string (resolved parent is
          higher than the input's parent).
      (b) a symlink whose target lives outside the input's directory
          (the user passes `tmp_path/link` where `link → ../sibling`).
    Does NOT create the file or any directories.
    """
    out_dir_raw = str(output_dir)
    out_dir_resolved = os.path.realpath(out_dir_raw)
    # Check (a) + (b): the resolved path must live inside the resolved
    # parent directory of the input. If the input itself is a symlink to
    # somewhere else, `out_dir_resolved` won't be a child of
    # `parent_resolved` (the symlink target is a sibling, not a child).
    parent_resolved = os.path.realpath(os.path.dirname(out_dir_raw))
    if (
        not out_dir_resolved.startswith(parent_resolved + os.sep)
        and out_dir_resolved != parent_resolved
    ):
        raise ValueError(
            f"store_path escapes output_dir: {out_dir_raw} "
            f"(realpath: {out_dir_resolved})"
        )

    out_dir = Path(out_dir_resolved)
    candidate = out_dir / _STORE_FILENAME
    real = os.path.realpath(str(candidate))
    if not real.startswith(str(out_dir) + os.sep) and real != str(out_dir):
        raise ValueError(
            f"store_path escapes output_dir: {candidate} (realpath: {real})"
        )
    return candidate


# ---------------------------------------------------------------------------
# Load / write (clone of lib.bible.write_bible atomic discipline)
# ---------------------------------------------------------------------------

def load_store(output_dir: str | os.PathLike) -> dict[str, Any]:
    """Return the store at `<output_dir>/covered-ground.yaml`.

    - Missing or empty file → `{"anchors": {}}` (no raise).
    - YAML parse error → `{"anchors": {}}` (fail-soft — the threat model
      mandates "store yaml 解析失败 → 当作空 store"; a corrupt file
      should not halt the run, just this run's memo is empty).
    - Unexpected top-level shape → coerced to `{"anchors": {}}`.
    """
    p = store_path(output_dir)
    if not p.exists():
        return {"anchors": {}}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {"anchors": {}}
    if not raw.strip():
        return {"anchors": {}}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {"anchors": {}}
    if not isinstance(parsed, dict):
        return {"anchors": {}}
    anchors = parsed.get("anchors")
    if not isinstance(anchors, dict):
        return {"anchors": {}}
    return {"anchors": anchors}


def write_store(output_dir: str | os.PathLike, store: dict[str, Any]) -> Path:
    """Atomic overwrite of the store YAML.

    Temp file is created in `output_dir` + `os.replace`. On any error,
    the temp file is removed (no orphan — mirrors `lib.bible.write_bible`).
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    if not out_dir.exists():
        raise FileNotFoundError(f"output_dir does not exist: {out_dir}")
    if not out_dir.is_dir():
        raise NotADirectoryError(f"output_dir is not a directory: {out_dir}")

    target = store_path(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(out_dir),
    )
    tmp_p = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                store,
                f,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(str(tmp_p), str(target))
    except Exception:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise

    return target


# ---------------------------------------------------------------------------
# Staleness predicate
# ---------------------------------------------------------------------------

def _parse_episode_date(ep: Any) -> Optional[_dt.date]:
    """Best-effort: pull a `date` field off an episode dict and parse it
    as ISO. Returns None on any failure (defensive — the distiller may
    emit shapes we don't strictly expect)."""
    if not isinstance(ep, dict):
        return None
    d = ep.get(_EP_KEY_DATE)
    if not isinstance(d, str):
        return None
    try:
        return _dt.date.fromisoformat(d)
    except ValueError:
        return None


def _episodes_in_window(
    episodes: Iterable[Any],
    today: _dt.date,
    window_days: int,
) -> list[_dt.date]:
    """Distinct ISO-parseable episode dates strictly <= today and within
    `window_days` of today. Future-dated episodes are ignored."""
    out: set[_dt.date] = set()
    for ep in episodes:
        d = _parse_episode_date(ep)
        if d is None:
            continue
        if d > today:
            continue
        if (today - d).days > window_days:
            continue
        out.add(d)
    return sorted(out)


def _distinct_episode_dates(episodes: Iterable[Any]) -> list[_dt.date]:
    """All distinct ISO-parseable episode dates, sorted ascending."""
    out: set[_dt.date] = set()
    for ep in episodes:
        d = _parse_episode_date(ep)
        if d is not None:
            out.add(d)
    return sorted(out)


def is_stale(
    entry: dict[str, Any],
    today: str,
    *,
    window_days: int = _WINDOW_DAYS,
) -> bool:
    """Staleness predicate (DP-001=A).

    Either of the following makes an anchor "hot" (over-used):

    (a) `count_in_window >= 3` — the anchor was used 3+ times in the
        last `window_days` days (default 14).
    (b) `distinct_episodes_in_last_3 >= 2` — the anchor appeared on 2+
        of the most recent 3 distinct episode dates.

    The "last 3" recency slice is taken over the anchor's own episodes
    (per-anchor purity — the predicate answers "is THIS anchor hot?",
    not "is anything in the store hot?"). The store-level render_memo
    iterates entries and applies this predicate per-entry.

    The recency rule additionally requires the most recent episode to
    be within `_RECENCY_LOOKBACK_DAYS` of `today` — a guard against
    stale-but-diverse entries (e.g. "used twice over 6 months ago")
    triggering the recency predicate. The count predicate already
    windows to `window_days`; the recency predicate is the "frequent
    AND recent" guard.
    """
    try:
        td = _dt.date.fromisoformat(today)
    except (ValueError, TypeError):
        return False

    if not isinstance(entry, dict):
        return False
    episodes = entry.get("episodes")
    if not isinstance(episodes, list):
        return False

    # (a) Count predicate — count of distinct dates inside the window.
    in_window = _episodes_in_window(episodes, td, window_days)
    if len(in_window) >= 3:
        return True

    # (b) Recency predicate — distinct dates in the last 3 episodes
    #     must all be within the window AND the most recent episode
    #     must be within the recency lookback. The count predicate
    #     already windows to `window_days`; the recency predicate is
    #     the "frequent AND recent" guard.
    distinct = _distinct_episode_dates(episodes)
    last_three = distinct[-_RECENCY_WINDOW:]
    if (
        len(last_three) >= 2
        and len(set(last_three)) >= 2
        and all(
            (td - d).days <= window_days
            for d in last_three
        )
        and last_three[-1] >= td - _dt.timedelta(days=_RECENCY_LOOKBACK_DAYS)
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# update_store: ingest a fresh list of anchors (post-publish distillation)
# ---------------------------------------------------------------------------

def _default_similarity(a: str, b: str) -> float:
    """The real similarity is `lib.embed.similarity` (vector + n-gram
    fallback). Importing `lib.embed` at module load would force a
    subprocess / platform probe before the store is ever touched; defer
    to a lazy import inside the function. This is also the seam the
    test suite uses to inject a fake similarity.

    Threads `plugin_root` (computed from this file's location —
    `<plugin_root>/lib/coveredground.py`) so `embed.similarity` can
    resolve the compiled `tools/embed` binary (or the `.swift` source as
    a fallback). WITHOUT this, `embed.similarity` runs with
    `plugin_root=None`, `_resolve_swift_bin` returns None, and every
    comparison silently degrades to n-gram — the design-specified
    NLContextualEmbedding path would be dead in the real pipeline.
    """
    try:
        from lib import embed  # type: ignore
    except Exception:
        # Last-resort: if lib.embed can't even import, no reskin merge
        # (the candidate is treated as a fresh anchor — safe default;
        # never reference `embed` here, it is unbound on import failure).
        return 0.0
    plugin_root = Path(__file__).resolve().parent.parent
    return embed.similarity(a, b, plugin_root=plugin_root)


def _episode_signature(ep: Any) -> Optional[tuple[str, str]]:
    """Tuple key for dedup: (date, show). Both must be non-empty strings."""
    if not isinstance(ep, dict):
        return None
    d = ep.get(_EP_KEY_DATE)
    s = ep.get(_EP_KEY_SHOW)
    if not isinstance(d, str) or not isinstance(s, str):
        return None
    if not d or not s:
        return None
    return (d, s)


def update_store(
    store: dict[str, Any],
    anchors: Iterable[str],
    date: str,
    episode: dict[str, Any],
    *,
    similarity_fn: Optional[Callable[[str, str], float]] = None,
) -> None:
    """Fold a fresh batch of apparatus anchors into `store` in-place.

    For each `anchor` in `anchors`:
      - If a `similarity_fn` is provided and finds an existing key whose
        score >= `_RESKIN_THRESHOLD`, the anchor is folded into that key
        (count+1, last_used updated, episode appended if not already
        present).
      - Otherwise a new entry is created with
        `{first_used: date, last_used: date, count: 1, episodes: [episode]}`.

    Same-episode dedup: the first update with a given (date, show) tuple
    counts and records the episode; subsequent updates with the same
    tuple do NOT double-count.

    The store is mutated in place; no return value.
    """
    if not isinstance(store, dict):
        raise TypeError("store must be a dict")
    bucket = store.get("anchors")
    if not isinstance(bucket, dict):
        bucket = {}
        store["anchors"] = bucket

    sim: Callable[[str, str], float] = similarity_fn or _default_similarity

    for raw in anchors:
        anchor = (raw or "").strip()
        if not anchor:
            continue

        # Exact-match short-circuit: an anchor that IS an existing key
        # always folds into itself (count+1, last_used updated, episode
        # appended if not already present). The reskin detector would
        # never fire here — `similarity_fn(anchor, anchor)` may be 0.0
        # for literal n-gram, but the semantic intent is "same anchor".
        if anchor in bucket:
            entry = bucket[anchor]
            if not isinstance(entry, dict):
                entry = {}
                bucket[anchor] = entry
            ep_sig = _episode_signature(episode)
            existing_sigs = {
                _episode_signature(e) for e in entry.get("episodes", [])
                if _episode_signature(e) is not None
            }
            eps = entry.get("episodes")
            if not isinstance(eps, list):
                eps = []
                entry["episodes"] = eps
            if ep_sig is not None and ep_sig not in existing_sigs:
                eps.append(episode)
                entry["count"] = int(entry.get("count", 0)) + 1
            elif ep_sig is None:
                # Defensive: episode lacked a parseable signature — count
                # it anyway (the distiller is the source of truth; we'd
                # rather over-record than silently drop).
                eps.append(episode)
                entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_used"] = date
            entry.setdefault("first_used", date)
            continue

        # Reskin detection against existing keys.
        folded_key: Optional[str] = None
        folded_score = 0.0
        for key in list(bucket.keys()):
            if not isinstance(key, str):
                continue
            try:
                score = float(sim(anchor, key))
            except Exception:
                score = 0.0
            if score >= _RESKIN_THRESHOLD and score > folded_score:
                folded_key = key
                folded_score = score

        if folded_key is not None:
            entry = bucket[folded_key]
            if not isinstance(entry, dict):
                entry = {}
                bucket[folded_key] = entry
            ep_sig = _episode_signature(episode)
            existing_sigs = {
                _episode_signature(e) for e in entry.get("episodes", [])
                if _episode_signature(e) is not None
            }
            eps = entry.get("episodes")
            if not isinstance(eps, list):
                eps = []
                entry["episodes"] = eps
            if ep_sig is not None and ep_sig not in existing_sigs:
                eps.append(episode)
                entry["count"] = int(entry.get("count", 0)) + 1
            elif ep_sig is None:
                eps.append(episode)
                entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_used"] = date
            entry.setdefault("first_used", date)
            continue

        # New entry.
        bucket[anchor] = {
            "first_used": date,
            "last_used": date,
            "count": 1,
            "episodes": [episode],
        }


# ---------------------------------------------------------------------------
# render_memo: project the store into a human-readable avoidance brief
# ---------------------------------------------------------------------------

def render_memo(store: dict[str, Any], today: str) -> str:
    """Render the avoidance memo: lists every hot anchor (per `is_stale`)
    with a single short line each, plus a one-line preamble.

    Temperature shield: the memo only ever names apparatus anchors and
    asks the writer to "avoid / 换说法" — it NEVER advises against
    opinions, takes, or bets. The distiller is the only legitimate
    input source for these anchors; subjective body text is not fed
    here (per the design plan).

    Returns "" when no anchors are hot. The caller (`_assemble_briefs`)
    decides what to do with an empty memo (the brief still includes an
    "(无 covered-ground 避让约束)" placeholder, so the writer always
    sees a structured cue, even when there's nothing to avoid).
    """
    if not isinstance(store, dict):
        return ""
    bucket = store.get("anchors")
    if not isinstance(bucket, dict) or not bucket:
        return ""

    hot: list[tuple[str, dict[str, Any]]] = []
    for key, entry in bucket.items():
        if not isinstance(entry, dict):
            continue
        if is_stale(entry, today):
            hot.append((key, entry))

    if not hot:
        return ""

    # Sort: highest count first, then most-recent last_used. Both fields
    # are best-effort — missing/garbage values don't break the sort.
    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        _, entry = item
        try:
            count = int(entry.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        last = entry.get("last_used") or ""
        return (-count, last)

    hot.sort(key=_sort_key)

    lines: list[str] = []
    lines.append("近期反复用过的招牌锚 / 类比 — 本期能避开就避开，要用请换新的说法：")
    for key, entry in hot:
        try:
            count = int(entry.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        last = entry.get("last_used") or "?"
        lines.append(f"- {key}（累计 {count} 次，最近 {last}）")
    lines.append("以上锚若本期不需要，可直接换新类比。")
    return "\n".join(lines)
