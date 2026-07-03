"""Shot-timeline core split (Phase 1): turn one beat's narration + director shots
into N distinct visual "windows" that fill the beat's clip_len exactly.

This replaces the POC-era pattern of stretching ~3 director shots across a whole
beat via Ken-Burns hold/loop (the root of hold/loop bugs ⑤/⑧/⑨): each beat clip
becomes an internal concat of N different visuals instead.

`plan_windows` is the single entry point. The `dur` it assigns per window is
authoritative — the render layer (Task 3) must reuse it verbatim, not recompute
via `shot_durations` a second time (MR-2): re-running shot_durations downstream
would silently drop the WINDOW_MAX cap applied here.
"""
from __future__ import annotations

import re

from lib.ffcmd import MIN_DWELL, WINDOW_MAX, max_shots, shot_durations
from lib.storyboard import pace_weight
from lib.visual_ledger import sig as _ledger_sig

# Target window length used to derive N = round(clip_len / TARGET_WINDOW),
# clamped to max_shots(clip_len). 5.5s sits between MIN_DWELL (0.8s, floor per
# shot) and WINDOW_MAX (7.0s, ceiling per shot, defined in lib.ffcmd and
# re-exported here) so most beats land on N in the 5-9 range without hugging
# either bound. Oversized windows get split into WINDOW_MAX-sized pieces + a
# remainder (see _cap_window_max).
TARGET_WINDOW = 5.5

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？])")

_DEFAULT_CAMERA_MOVE = "静止"
_DEFAULT_CAMERA_INTENSITY = 1.0
_DEFAULT_PACE = "normal"
_DEFAULT_SHOT_TYPE = ""


def _split_narration(text: str, n: int) -> list[str]:
    """Split `text` into `n` ordered, roughly length-balanced slices, splitting
    on sentence punctuation first and falling back to even char slicing when
    there aren't enough natural sentence breaks."""
    text = (text or "").strip()
    if n <= 1:
        return [text]
    if not text:
        return [""] * n

    parts = [p for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    if not parts:
        parts = [text]

    if len(parts) < n:
        length = len(text)
        cuts = [round(i * length / n) for i in range(n + 1)]
        slices = [text[cuts[i]:cuts[i + 1]] for i in range(n)]
        return [s if s else text for s in slices]

    total_len = sum(len(p) for p in parts)
    target = total_len / n
    groups: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in parts:
        cur.append(p)
        cur_len += len(p)
        if cur_len >= target and len(groups) < n - 1:
            groups.append("".join(cur))
            cur = []
            cur_len = 0
    if cur:
        groups.append("".join(cur))

    while len(groups) < n:
        groups.append(groups[-1] if groups else text)
    while len(groups) > n:
        groups[-2] += groups[-1]
        groups.pop()
    return groups


def _derive_interim_prompt(text_slice: str, keywords: list[str], pos: int,
                            used_prompts: set[str], ledger=None) -> str:
    """Build a distinct interim visual prompt from a narration slice + keyword,
    retrying with keyword rotation / a counter suffix until it doesn't collide
    with an already-used prompt (this episode) or a ledger-recorded signature
    (prior episodes)."""
    kw = keywords[pos % len(keywords)] if keywords else ""
    snippet = text_slice.strip()[:16]
    base = f"{kw}·{snippet}" if kw and snippet else (snippet or kw or f"镜头{pos}")

    candidate = base
    attempt = 0
    while candidate in used_prompts or (ledger is not None and ledger.seen(_ledger_sig(candidate))):
        attempt += 1
        if keywords:
            kw2 = keywords[(pos + attempt) % len(keywords)]
            candidate = f"{base}·{kw2}#{attempt}"
        else:
            candidate = f"{base}#{attempt}"
        if attempt > 20:
            break
    return candidate


def _director_window(shot: dict, beat: dict, text_slice: str) -> dict:
    return {
        "beat_id": beat.get("id"),
        "idx": 0,  # reassigned by plan_windows after final ordering
        "text": text_slice,
        "prompt": shot.get("prompt") or shot.get("image_prompt", ""),
        "dur": 0.0,  # filled in by plan_windows
        "source": "director",
        "visual_type": shot.get("visual_type", "image"),
        "with_character": shot.get("with_character", False),
        "pace": shot.get("pace", _DEFAULT_PACE),
        "camera_move": shot.get("camera_move", _DEFAULT_CAMERA_MOVE),
        "camera_intensity": shot.get("camera_intensity", _DEFAULT_CAMERA_INTENSITY),
        "shot_type": shot.get("shot_type", _DEFAULT_SHOT_TYPE),
    }


def _interim_window(beat: dict, text_slice: str, prompt: str) -> dict:
    return {
        "beat_id": beat.get("id"),
        "idx": 0,  # reassigned by plan_windows after final ordering
        "text": text_slice,
        "prompt": prompt,
        "dur": 0.0,  # filled in by plan_windows
        "source": "interim",
        "visual_type": "image",
        "with_character": False,
        "pace": _DEFAULT_PACE,
        "camera_move": _DEFAULT_CAMERA_MOVE,
        "camera_intensity": _DEFAULT_CAMERA_INTENSITY,
        "shot_type": _DEFAULT_SHOT_TYPE,
    }


def _cap_window_max(windows: list[dict]) -> list[dict]:
    """Any window whose dur exceeds WINDOW_MAX is split into WINDOW_MAX-sized
    pieces + a trailing remainder. The first piece keeps the window's original
    position (preserving order/pace of the surrounding windows); overflow
    pieces are appended at the end of the list. This keeps the sum of durs
    exact while guaranteeing no single window overruns WINDOW_MAX."""
    result: list[dict] = []
    extra: list[dict] = []
    for w in windows:
        dur = w["dur"]
        if dur <= WINDOW_MAX + 1e-9:
            result.append(w)
            continue
        remaining = dur
        first = True
        while remaining > WINDOW_MAX + 1e-9:
            piece = dict(w)
            piece["dur"] = WINDOW_MAX
            (result if first else extra).append(piece)
            first = False
            remaining -= WINDOW_MAX
        if remaining > 1e-9:
            last_piece = dict(w)
            last_piece["dur"] = remaining
            (result if first else extra).append(last_piece)
    return result + extra


def plan_windows(beat: dict, clip_len: float, director_shots: list[dict],
                  keywords: list[str], ledger=None) -> list[dict]:
    """Split one beat's clip_len into N content windows: director_shots first
    (kept verbatim, including camera_move/camera_intensity/shot_type/pace —
    R-5), then "interim" narration-derived windows filling out to N when
    director supplied fewer shots than N. If director supplied more than N,
    truncate to the first N (no interim)."""
    keywords = list(keywords or [])
    director_shots = list(director_shots or [])

    n_target = round(clip_len / TARGET_WINDOW)
    n = max(1, min(n_target if n_target > 0 else 1, max_shots(clip_len, MIN_DWELL)))

    text_slices = _split_narration(beat.get("text", ""), n)

    n_director = min(len(director_shots), n)
    windows: list[dict] = []
    used_prompts: set[str] = set()

    for i in range(n_director):
        w = _director_window(director_shots[i], beat, text_slices[i])
        windows.append(w)
        if w["prompt"]:
            used_prompts.add(w["prompt"])

    n_interim = n - n_director
    for j in range(n_interim):
        i = n_director + j
        prompt = _derive_interim_prompt(text_slices[i], keywords, j, used_prompts, ledger)
        used_prompts.add(prompt)
        windows.append(_interim_window(beat, text_slices[i], prompt))

    weights = [pace_weight(w["pace"]) for w in windows]
    durs = shot_durations(clip_len, weights, MIN_DWELL)
    for w, d in zip(windows, durs):
        w["dur"] = d

    windows = _cap_window_max(windows)

    for idx, w in enumerate(windows):
        w["idx"] = idx

    return windows
