"""video-studio covered-ground store — 跨期记忆 (`covered-ground.yaml`).

Per-run distillation of the apparatus (signature anchors / analogies /
frameworks) the script reached for, persisted across episodes so the next
episode can render an "avoidance memo" — used at brief assembly to strip
recycle-prone prose out of the writing brief.

Design:

- Single YAML file under `output_dir`: `<output_dir>/covered-ground.yaml`.
  Same realpath-guard discipline as podcast-studio's `bible_path`: a
  `..`-style traversal / symlink escape raises ValueError.
- Atomic overwrite: temp file in `output_dir` + `os.replace`, temp
  removed on error (no orphan).
- Store schema:
    {
      "anchors": {
        "<anchor>": {
          "first_used": "YYYY-MM-DD",
          "last_used":  "YYYY-MM-DD",
          "count":      <int>,
          "episodes":   [{"date": "YYYY-MM-DD", "seq": <int>}, ...]
        }
      }
    }
- Staleness predicate (MR-1, SEQ-based recency):
    video-studio is a WEEKLY cadence — the old podcast calendar windows
    (count≥3 over 14 days, "most recent within 1 day") structurally never
    fire at ~7-day spacing, so the memo would be a silent no-op. Instead
    an anchor is "hot" iff ANY of its episodes has
    `seq > current_seq - recent_n` — i.e. it was used within the last
    `recent_n` episodes (default 20). `render_memo` lists hot anchors with
    avoidance semantics; cool anchors are silent.
- Reskin detection: `update_store` consults `similarity_fn(anchor, key)`
  for every existing key. A score >= `_RESKIN_THRESHOLD` folds the new
  anchor INTO the existing key (count+1, last_used updated, episodes
  appended) — so "苏伊士运河 1956" merges with "1956苏伊士". A score below
  threshold → new entry.
- Temperature shield: `update_store` only ever stores `apparatus`-shaped
  inputs (callers feed the distiller's anchors list — not opinion free
  text). `render_memo` outputs are confined to apparatus avoidance; they
  NEVER advise against opinions / takes.

The store is NOT a stance card: it is a refreshed cumulative projection
(overwrite, not append-only).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Fixed filename under `output_dir`.
_STORE_FILENAME = "covered-ground.yaml"

# Reskin detection threshold. Same value lives in `lib.embed`; the
# covered-ground store uses it as a guard against trivial paraphrases
# inflating the anchor dictionary. A candidate anchor whose
# `similarity_fn(candidate, existing_key)` >= this for ANY existing key
# folds INTO that key. SINGLE SOURCE — do not duplicate this constant.
_RESKIN_THRESHOLD = 0.93

# Recency window measured in EPISODES (seq), not calendar days (MR-1).
# An anchor is "hot" iff it was used within the last `_RECENT_EPISODES`
# episodes. Default 20 spans ~几个月 of weekly cadence. Workspace config
# can override via the `recent_n` kwarg on `is_stale` / callers.
_RECENT_EPISODES = 20

# Episode dedup key: ("date", "seq") — video-studio has no morning/evening
# split, so the per-episode global seq disambiguates. Two updates of the
# same anchor with the same episode must not double-count.
_EP_KEY_DATE = "date"
_EP_KEY_SEQ = "seq"


# ---------------------------------------------------------------------------
# Path resolution + realpath guard
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
# Load / write (atomic discipline)
# ---------------------------------------------------------------------------

def load_store(output_dir: str | os.PathLike) -> dict[str, Any]:
    """Return the store at `<output_dir>/covered-ground.yaml`.

    - Missing or empty file → `{"anchors": {}}` (no raise).
    - YAML parse error → `{"anchors": {}}` (fail-soft — a corrupt file
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
    the temp file is removed (no orphan).
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
# Staleness predicate (MR-1: SEQ-based recency, NOT calendar windows)
# ---------------------------------------------------------------------------

def _episode_seq(ep: Any) -> Optional[int]:
    """Pull the integer `seq` off an episode dict. Returns None on any
    failure (defensive — the distiller may emit shapes we don't strictly
    expect). Booleans are rejected (a stray `True` is not a seq)."""
    if not isinstance(ep, dict):
        return None
    s = ep.get(_EP_KEY_SEQ)
    if isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s
    return None


def is_stale(
    entry: dict[str, Any],
    current_seq: int,
    *,
    recent_n: int = _RECENT_EPISODES,
) -> bool:
    """Staleness predicate (MR-1, SEQ-based recency).

    An anchor is "hot" (over-used) iff ANY of its episodes was used within
    the last `recent_n` episodes — i.e. some episode has
    `seq > current_seq - recent_n`.

    This replaces the podcast-studio calendar windows (count≥3 over 14
    days + "most recent within 1 day"), which at video-studio's WEEKLY
    cadence would structurally NEVER fire (14 days ≈ 2 episodes → count
    never reaches 3; ~7-day spacing → "within 1 day" never true), making
    the avoidance memo a silent no-op. The seq window fires correctly at
    weekly cadence.

    Returns False on any malformed input (non-dict entry, non-int
    current_seq, missing/garbage episodes) — fail-soft.
    """
    if not isinstance(entry, dict):
        return False
    try:
        cur = int(current_seq)
    except (TypeError, ValueError):
        return False

    episodes = entry.get("episodes")
    if not isinstance(episodes, list):
        return False

    threshold = cur - recent_n
    for ep in episodes:
        s = _episode_seq(ep)
        if s is not None and s > threshold:
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


def _episode_signature(ep: Any) -> Optional[tuple[str, int]]:
    """Tuple key for dedup: (date, seq). date must be a non-empty string,
    seq must be an int (booleans rejected)."""
    if not isinstance(ep, dict):
        return None
    d = ep.get(_EP_KEY_DATE)
    s = ep.get(_EP_KEY_SEQ)
    if not isinstance(d, str) or not d:
        return None
    if isinstance(s, bool) or not isinstance(s, int):
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

    Same-episode dedup: the first update with a given (date, seq) tuple
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

def render_memo(store: dict[str, Any], current_seq: int) -> str:
    """Render the avoidance memo: lists every hot anchor (per `is_stale`)
    with a single short line each, plus a one-line preamble.

    Temperature shield: the memo only ever names apparatus anchors and
    asks the writer to "avoid / 换说法" — it NEVER advises against
    opinions, takes, or bets. The distiller is the only legitimate
    input source for these anchors; subjective body text is not fed
    here (per the design plan).

    Returns "" when no anchors are hot. The caller decides what to do
    with an empty memo (the brief still includes an "(无 covered-ground
    避让约束)" placeholder, so the writer always sees a structured cue,
    even when there's nothing to avoid).
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
        if is_stale(entry, current_seq):
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


# ---------------------------------------------------------------------------
# update_covered_ground: pipeline-station convenience — fold one episode's
# distilled covered.json into the cross-episode store (B6).
# ---------------------------------------------------------------------------

def update_covered_ground(
    output_dir: str | os.PathLike,
    covered_json_path: str | os.PathLike,
    date: str,
    seq: int,
) -> None:
    """Merge the episode's distilled apparatus anchors into the节目级 store.

    Reads ``covered.json`` (shape ``{"anchors": [str, ...]}`` — the distiller's
    output), then ``load_store → update_store → write_store`` to fold this
    episode's anchors into ``<output_dir>/covered-ground.yaml`` for future
    avoidance memos.

    Fail-soft (B6 regression shield): the WHOLE body is wrapped in try/except
    → stderr → swallow. A distiller/store failure must NEVER halt an
    already-rendered episode (mirrors podcast runner.py:1709). Empty / missing
    / corrupt anchors → early return (no store write, no raise).
    """
    try:
        p = Path(covered_json_path)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        anchors = data.get("anchors") if isinstance(data, dict) else None
        if not anchors or not isinstance(anchors, list):
            return

        store = load_store(output_dir)
        update_store(
            store, anchors, date, {"date": date, "seq": int(seq)}
        )
        write_store(output_dir, store)
    except Exception as exc:  # noqa: BLE001 — fail-soft; never halt the episode
        print(
            f"[covered-update] swallowed error (fail-soft, run continues): {exc}",
            file=sys.stderr,
        )
        return
