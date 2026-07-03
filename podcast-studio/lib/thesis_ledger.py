"""podcast-studio thesis-ledger — 论点账本 (跨期记忆, Phase 3).

A cross-episode memory PEER to covered-ground, deliberately kept separate so
the装置层 (covered-ground: anchors/analogies/frames) stays pure. Where
covered-ground answers "did we reuse this ANALOGY?", the thesis-ledger answers
"did we re-derive this ARGUMENT without advancing it?" — the blind spot that
let 54≈57 ship (same thesis "好奇心想象力太轻，真正剩的是认清自己", zero
shared covered-ground anchors, every gate blind).

Why a separate store (not folded into covered-ground):
- covered-ground's temperature shield forbids storing subjective opinions/bets;
  a thesis IS a subjective argument. Mixing them would either pollute
  covered-ground or weaken its shield.
- The signals differ: covered-ground hotness = "anchor used N times"; thesis
  hotness = "argument re-used but NOT advanced" (a use that settles/advances a
  prior bet is GOOD serialization, not homogenization — DP-001=A: protect
  persistence, catch re-derivation).

Schema (`state/thesis-ledger.yaml`):
    {
      "theses": {
        "<thesis_key>": {
          "first_used": "YYYY-MM-DD",
          "last_used":  "YYYY-MM-DD",
          "count":      <int>,
          "statements": ["<surface phrasing>", ...],   # reskins folded here
          "episodes": [
            {"date": "YYYY-MM-DD", "show": "morning|evening",
             "advanced": <bool>, "bet_ids": ["bet-...", ...]}
          ]
        }
      }
    }

- `advanced` is the LLM judge's per-episode verdict (DP-002=B: "有没有真推进"
  is a judgment, not a rule). The store only RECORDS it; the distiller assigns
  it.
- Reskin fold: `update_store` merges a new thesis whose `similarity_fn`
  score >= `_RESKIN_THRESHOLD` against an existing key (so "判断力是最后护城河"
  and "真正剩下的是认清自己" fold into ONE thesis even with different wording —
  the whole point covered-ground's anchor-only view misses).
- Mirrors `lib.coveredground` atomic-write + realpath-guard discipline; NOT
  a stance card (overwrite projection, not append-only ledger of bets).

Line isolation (docs/02-architecture/ubiquitous-language.md): 论点账本 /
thesis-ledger is an OPINION-line term. It must NOT be confused with the paper
line's `paper-log` (which has no bets / no theses).
"""
from __future__ import annotations

import datetime as _dt
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

# Fixed filename under `state_dir` (mirrors covered-ground).
_STORE_FILENAME = "thesis-ledger.yaml"

# Reskin fold threshold (same value as covered-ground / embed).
_RESKIN_THRESHOLD = 0.93

# Recurrence / staleness windows.
_WINDOW_DAYS = 14

# Episode signature key fields.
_EP_KEY_DATE = "date"
_EP_KEY_SHOW = "show"


# ---------------------------------------------------------------------------
# Path resolution + realpath guard (mirror lib.coveredground.store_path)
# ---------------------------------------------------------------------------

def store_path(state_dir: str | os.PathLike) -> Path:
    """Return `<state_dir>/thesis-ledger.yaml`; realpath-asserted to stay
    inside state_dir (mirror covered-ground's guard). Creates nothing."""
    out_dir_raw = str(state_dir)
    out_dir_resolved = os.path.realpath(out_dir_raw)
    parent_resolved = os.path.realpath(os.path.dirname(out_dir_raw))
    if (
        not out_dir_resolved.startswith(parent_resolved + os.sep)
        and out_dir_resolved != parent_resolved
    ):
        raise ValueError(
            f"store_path escapes state_dir: {out_dir_raw} "
            f"(realpath: {out_dir_resolved})"
        )
    out_dir = Path(out_dir_resolved)
    candidate = out_dir / _STORE_FILENAME
    real = os.path.realpath(str(candidate))
    if not real.startswith(str(out_dir) + os.sep) and real != str(out_dir):
        raise ValueError(
            f"store_path escapes state_dir: {candidate} (realpath: {real})"
        )
    return candidate


# ---------------------------------------------------------------------------
# Load / write (clone of covered-ground atomic discipline)
# ---------------------------------------------------------------------------

def load_store(state_dir: str | os.PathLike) -> dict[str, Any]:
    """Return the store; missing/empty/parse-error/bad-shape → `{"theses": {}}`
    (fail-soft — a corrupt store must not halt the run, just yield an empty
    memo this round)."""
    p = store_path(state_dir)
    if not p.exists():
        return {"theses": {}}
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {"theses": {}}
    if not raw.strip():
        return {"theses": {}}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {"theses": {}}
    if not isinstance(parsed, dict):
        return {"theses": {}}
    theses = parsed.get("theses")
    if not isinstance(theses, dict):
        return {"theses": {}}
    return {"theses": theses}


def write_store(state_dir: str | os.PathLike, store: dict[str, Any]) -> Path:
    """Atomic overwrite (temp in state_dir + os.replace; temp removed on
    error — no orphan). Mirrors covered-ground."""
    out_dir = Path(os.path.realpath(str(state_dir)))
    if not out_dir.exists():
        raise FileNotFoundError(f"state_dir does not exist: {out_dir}")
    if not out_dir.is_dir():
        raise NotADirectoryError(f"state_dir is not a directory: {out_dir}")
    target = store_path(out_dir)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(out_dir))
    tmp_p = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(store, f, allow_unicode=True, sort_keys=False)
        os.replace(str(tmp_p), str(target))
    except Exception:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise
    return target


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_date(s: Any) -> Optional[_dt.date]:
    if not isinstance(s, str):
        return None
    try:
        return _dt.date.fromisoformat(s)
    except ValueError:
        return None


def _episode_signature(ep: Any) -> Optional[tuple[str, str]]:
    if not isinstance(ep, dict):
        return None
    d = ep.get(_EP_KEY_DATE)
    s = ep.get(_EP_KEY_SHOW)
    if not isinstance(d, str) or not isinstance(s, str) or not d or not s:
        return None
    return (d, s)


def _default_similarity(a: str, b: str) -> float:
    """Lazy embed import (mirror covered-ground); n-gram fallback on failure."""
    try:
        from lib import embed  # type: ignore
    except Exception:
        return 0.0
    plugin_root = Path(__file__).resolve().parent.parent
    return embed.similarity(a, b, plugin_root=plugin_root)


# ---------------------------------------------------------------------------
# update_store: fold a batch of theses from one episode
# ---------------------------------------------------------------------------

def _new_entry(statement: str, date: str, episode_rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_used": date,
        "last_used": date,
        "count": 1,
        "statements": [statement],
        "episodes": [episode_rec],
    }


def _fold_into(entry: dict[str, Any], statement: str, date: str, episode_rec: dict[str, Any]) -> None:
    """Append this episode to an existing thesis entry (dedup by (date,show))."""
    stmts = entry.setdefault("statements", [])
    if isinstance(stmts, list) and statement and statement not in stmts:
        stmts.append(statement)
    eps = entry.setdefault("episodes", [])
    if not isinstance(eps, list):
        eps = []
        entry["episodes"] = eps
    sig = _episode_signature(episode_rec)
    existing_sigs = {_episode_signature(e) for e in eps if _episode_signature(e) is not None}
    if sig is None or sig not in existing_sigs:
        eps.append(episode_rec)
        entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_used"] = date
    entry.setdefault("first_used", date)


def update_store(
    store: dict[str, Any],
    theses: Iterable[dict[str, Any]],
    date: str,
    episode: dict[str, Any],
    *,
    similarity_fn: Optional[Callable[[str, str], float]] = None,
) -> None:
    """Fold one episode's theses into `store` in place.

    Each item in `theses` is a dict `{statement, advanced(bool), bet_ids(list)}`
    (the distiller's output; `advance_reason` etc. are ignored here). The
    per-episode record stored is
    `{date, show, advanced, bet_ids}` — so `is_recurring_stale` can later ask
    "was the last use an advance or a re-derivation?".

    Fold rule: exact statement match, else reskin (similarity >= threshold),
    else new entry — mirrors covered-ground.
    """
    if not isinstance(store, dict):
        raise TypeError("store must be a dict")
    bucket = store.get("theses")
    if not isinstance(bucket, dict):
        bucket = {}
        store["theses"] = bucket

    sim: Callable[[str, str], float] = similarity_fn or _default_similarity
    show = episode.get("show") if isinstance(episode, dict) else None

    for raw in theses:
        if not isinstance(raw, dict):
            continue
        statement = (raw.get("statement") or "").strip()
        if not statement:
            continue
        advanced = bool(raw.get("advanced", False))
        bet_ids = [b for b in (raw.get("bet_ids") or []) if isinstance(b, str) and b]
        episode_rec = {"date": date, "show": show, "advanced": advanced, "bet_ids": bet_ids}

        # exact key match
        if statement in bucket and isinstance(bucket[statement], dict):
            _fold_into(bucket[statement], statement, date, episode_rec)
            continue

        # reskin fold
        folded_key: Optional[str] = None
        folded_score = 0.0
        for key in list(bucket.keys()):
            if not isinstance(key, str):
                continue
            try:
                score = float(sim(statement, key))
            except Exception:
                score = 0.0
            if score >= _RESKIN_THRESHOLD and score > folded_score:
                folded_key = key
                folded_score = score
        if folded_key is not None and isinstance(bucket[folded_key], dict):
            _fold_into(bucket[folded_key], statement, date, episode_rec)
            continue

        bucket[statement] = _new_entry(statement, date, episode_rec)


# ---------------------------------------------------------------------------
# staleness: recurring but NOT advancing
# ---------------------------------------------------------------------------

def _sorted_episodes(entry: dict[str, Any]) -> list[dict[str, Any]]:
    eps = entry.get("episodes")
    if not isinstance(eps, list):
        return []
    dated = [(e, _parse_date(e.get("date"))) for e in eps if isinstance(e, dict)]
    dated = [(e, d) for e, d in dated if d is not None]
    dated.sort(key=lambda t: t[1])
    return [e for e, _ in dated]


def is_recurring_stale(
    entry: dict[str, Any],
    today: str,
    *,
    window_days: int = _WINDOW_DAYS,
    min_uses: int = 2,
) -> bool:
    """True iff this thesis is being RE-DERIVED without advancing.

    Conditions (all):
      (1) recurring — used in >= `min_uses` distinct (date,show) episodes;
      (2) recent — the most recent use is within `window_days` of today;
      (3) NOT advancing — the most recent use has `advanced` false.

    Rationale (DP-001=A): a recurring thesis that KEEPS advancing (settling /
    pushing prior bets) is good serialization — NOT stale. Only a recurring
    thesis whose latest re-use added no advance is the homogenization signal.
    """
    td = _parse_date(today)
    if td is None or not isinstance(entry, dict):
        return False
    eps = _sorted_episodes(entry)
    if len(eps) < min_uses:
        return False
    last = eps[-1]
    last_d = _parse_date(last.get("date"))
    if last_d is None or (td - last_d).days > window_days or last_d > td:
        return False
    return not bool(last.get("advanced", False))


# ---------------------------------------------------------------------------
# render_thesis_memo: pre-draft steering (Phase 4 consumes this)
# ---------------------------------------------------------------------------

def render_thesis_memo(store: dict[str, Any], today: str) -> str:
    """List recurring-but-not-advancing theses with continuation semantics:
    本期若要碰这些论点，必须带来新推进（结算/推进旧下注、回答旧问题）或换题。

    Temperature (DP-001=A): this memo targets ARGUMENT re-derivation, never
    tells the host to drop an opinion — it tells him to ADVANCE it or pick a
    different spine. Returns "" when nothing is hot.
    """
    if not isinstance(store, dict):
        return ""
    bucket = store.get("theses")
    if not isinstance(bucket, dict) or not bucket:
        return ""
    hot: list[tuple[str, dict[str, Any]]] = []
    for key, entry in bucket.items():
        if isinstance(entry, dict) and is_recurring_stale(entry, today):
            hot.append((key, entry))
    if not hot:
        return ""

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        _, e = item
        try:
            c = int(e.get("count", 0))
        except (TypeError, ValueError):
            c = 0
        return (-c, e.get("last_used") or "")

    hot.sort(key=_sort_key)
    lines: list[str] = [
        "近期讲过、但没往前推进的论点 — 本期若要碰，必须带来新推进（结算/推进旧下注、"
        "回答旧问题），否则换一个脊柱问题；观点本身可自由发挥，这里只拦「原地重讲」："
    ]
    for key, entry in hot:
        last = entry.get("last_used") or "?"
        try:
            c = int(entry.get("count", 0))
        except (TypeError, ValueError):
            c = 0
        lines.append(f"- {key}（已用 {c} 次，最近 {last}，上次未推进）")
    lines.append("以上论点若本期没有新推进，请换题——不要换个新闻壳子把同一套再讲一遍。")
    return "\n".join(lines)
