"""Cross-episode visual-dedup account (Phase 1 shot-timeline).

VisualLedger persists a set of normalized prompt signatures per episode
(``{episode_dir}/visual_ledger.json``) so ``lib.shotplan.plan_windows`` can
avoid re-emitting a visual (within this episode AND across prior episodes)
when deriving interim windows.

Phase 1 dedup is purely literal (no semantic near-match) — see
``sig()``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# Strip anything that is not a letter/digit (ASCII or CJK) before case-folding.
_NON_ALNUM_RE = re.compile(r"[^\w]+", re.UNICODE)


def sig(prompt: str) -> str:
    """Normalize *prompt* to a stable, literal signature.

    Strips whitespace/punctuation and case-folds. Purely literal for Phase 1 —
    no semantic near-match.
    """
    stripped = _NON_ALNUM_RE.sub("", prompt or "")
    return stripped.lower()


class VisualLedger:
    """Per-episode + cross-episode account of already-used visual signatures.

    ``episode_dir`` is this episode's directory (caller passes
    ``ctx["ep"]["final"].parent`` — MR-1: ``ctx["episode_dir"]`` does not
    exist). ``prior_dirs`` is an iterable of previously-completed episode
    directories whose ``visual_ledger.json`` (if present) is also loaded into
    the in-memory ``seen`` set for cross-episode dedup.
    """

    _FILENAME = "visual_ledger.json"

    def __init__(self, episode_dir: str | Path, prior_dirs: Iterable[str | Path] = ()):
        self.episode_dir = Path(episode_dir)
        self._seen: set[str] = set()
        self._ids: set[int] = set()

        # Load this episode's own already-recorded signatures (if any) —
        # supports round-tripping across separate pipeline runs.
        self._load_dir(self.episode_dir)

        # Load every prior episode dir's visual_ledger.json (if present).
        for prior in prior_dirs:
            self._load_dir(Path(prior))

    def _load_dir(self, ep_dir: Path) -> None:
        path = ep_dir / self._FILENAME
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        sigs = data.get("signatures") if isinstance(data, dict) else data
        if isinstance(sigs, list):
            self._seen.update(str(s) for s in sigs)

        # MR#4: "ids" key alongside "signatures"; tolerate absence in old json.
        ids = data.get("ids", []) if isinstance(data, dict) else []
        if isinstance(ids, list):
            for i in ids:
                try:
                    self._ids.add(int(i))
                except (TypeError, ValueError):
                    continue

    def seen(self, signature: str) -> bool:
        return signature in self._seen

    def record(self, signature: str) -> None:
        self._seen.add(signature)

    def seen_id(self, video_id: int) -> bool:
        return video_id in self._ids

    def record_id(self, video_id: int) -> None:
        self._ids.add(video_id)

    def save(self) -> None:
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.episode_dir / self._FILENAME
        out_path.write_text(
            json.dumps(
                {"signatures": sorted(self._seen), "ids": sorted(self._ids)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _done_episode_dirs(cfg: Any, ctx: dict) -> tuple:
    """Enumerate already-completed episode directories for cross-episode dedup.

    MF-4: fail-closed in fixture/CLI mode. Returns an empty tuple (without
    raising) when ``cfg`` lacks ``syllabus_path``/``cursor_path`` or ``ctx``
    lacks a ``"seq"`` key — this is the no-syllabus Phase-2 path and must not
    crash the pipeline. ctx carries no "syllabus"/"cursor" keys; the real
    data source is ``cfg.syllabus_path``/``cfg.cursor_path`` (loaded via
    ``load_syllabus``/``load_cursor``, mirroring ``publish``@wiring.py:952-953)
    plus the current ``ctx["seq"]`` (seeded by ``step_cursor_read``).
    """
    if not (getattr(cfg, "syllabus_path", "") and getattr(cfg, "cursor_path", "") and ctx.get("seq")):
        return ()

    from lib.syllabus import load_syllabus, derive_seq_slug
    from lib.episode import episode_paths

    try:
        syllabus = load_syllabus(Path(cfg.syllabus_path))
    except Exception:
        return ()

    try:
        current_seq = int(ctx["seq"])
    except (TypeError, ValueError):
        return ()

    dirs = []
    global_idx = 0
    for m_i, module in enumerate(syllabus.modules):
        for e_i in range(len(module.episodes)):
            seq, slug = derive_seq_slug(syllabus, m_i, e_i)
            if int(seq) < current_seq:
                ep_dir = episode_paths(cfg.output_dir, seq, slug)["final"].parent
                if ep_dir.exists():
                    dirs.append(ep_dir)
            global_idx += 1

    return tuple(dirs)
