"""Media source layer (Phase 2, D-003/D-004/D-005).

Introduces a routing seam in front of the existing generation seam
(`MediaSeams`, see `lib/wiring.py`): given a content-window prompt + the
episode's art style, return a still/video, trying a per-style library first
and falling back to generation (D-003 — library-first, generate-fallback).

Phase 2 ships `LibrarySource` as a stub (kind="stub" copies a known-good real
jpg to prove the routing plumbing end-to-end without a real stock-media
integration yet; Phase 4 replaces it with a real provider e.g. Pexels).

Miss/hit is self-reported by each source: `fetch`/`fetch_video` return
`None` for a miss, or the (str path / dict) result for a hit
(DP-P2-001) — the router just tries sources in order and takes the first
non-None result.
"""
from __future__ import annotations

import abc
import http.client
import shutil
import urllib.error
from pathlib import Path
from typing import Any

from lib import ffcmd, pexels
from lib.pexels import PexelsError
from lib.query_filter import rerank_candidates

# Phase 2 stub asset: a real, decodable jpg (ffmpeg probes/muxes it, so it
# cannot be a sentinel/empty file). Reused from the Phase 1 spike output.
_STUB_ASSET = Path(__file__).resolve().parent.parent / ".spike" / "b00_shot0.jpg"

# NOTE: the authoritative kind whitelist lives in lib.config.VALID_LIBRARY_KINDS
# (used by _validate_library at load time). Do NOT duplicate it here — a second
# copy drifts as Phase 4/6 add "pexels"/"offline".


class MediaSource(abc.ABC):
    """A source of visual media for a content window."""

    @abc.abstractmethod
    def fetch(self, prompt: str, ref_url: str | None, out_path: str) -> str | None:
        """Return a path to a still asset written to out_path, or None (miss)."""
        raise NotImplementedError

    def fetch_video(self, prompt: str, ref_url: str | None, out_path: str) -> dict | None:
        """Return a video result dict, or None (miss). Most sources have no video."""
        return None

    def fetch_clip(self, window: dict, dur_s: float, out_path: str, run_ff,
                    grade: str | None = None, ledger=None) -> str | None:
        """Return a path to a window-duration sub-clip (mp4), or None (miss).

        Phase 4 (DP-P4-001): the library-video grain is a clip, not a still —
        distinct from `fetch` (still assets for the generate path). Default
        None; most sources (besides `PexelsSource`) don't implement this.
        """
        return None


class GenerateSource(MediaSource):
    """Wraps the existing generation seam (`MediaSeams`). Never misses."""

    def __init__(self, seams):
        self.seams = seams

    def fetch(self, prompt: str, ref_url: str | None, out_path: str) -> str | None:
        return self.seams.gen_still(prompt, ref_url, out_path)

    def fetch_video(self, prompt: str, ref_url: str | None, out_path: str) -> dict | None:
        return self.seams.gen_video(prompt, ref_url, out_path)


class LibrarySource(MediaSource):
    """Phase 2 stub library source, bound to a style via config."""

    def __init__(self, kind: str, style: str | None = None):
        self.kind = kind
        self.style = style

    def fetch(self, prompt: str, ref_url: str | None, out_path: str) -> str | None:
        if self.kind == "stub":
            shutil.copyfile(_STUB_ASSET, out_path)
            return out_path
        return None

    @classmethod
    def from_config(cls, cfg: Any, style: str | None):
        """Build a LibrarySource for `style` from `cfg.library` (style -> kind).

        Returns None when there is no config, or the style has no binding, or
        the binding is "none" (i.e. no library for this style — generate-only).
        """
        if cfg is None:
            return None
        library_cfg = getattr(cfg, "library", None) or {}
        kind = library_cfg.get(style)
        if not kind or kind == "none":
            return None
        if kind == "pexels":
            import os

            return PexelsSource(api_key=os.environ.get("PEXELS_API_KEY", ""))
        return cls(kind=kind, style=style)


class PexelsSource(MediaSource):
    """Online-library source for the "写实" (realistic) style (D-006).

    Pexels only serves *video clips* (b-roll sub-clips at window duration),
    never still assets — `fetch` is implemented purely to satisfy the
    abstract `MediaSource` contract (MR#2), so `MediaRouter`/`from_config`
    can instantiate it without `TypeError`. Real work happens in
    `fetch_clip`.
    """

    def __init__(self, api_key: str, per_page: int = 15):
        self.api_key = api_key
        self.per_page = per_page

    def fetch(self, prompt: str, ref_url: str | None, out_path: str) -> str | None:
        return None

    def fetch_clip(self, window: dict, dur_s: float, out_path: str, run_ff,
                    grade: str | None = None, ledger=None) -> str | None:
        q = window.get("query")
        if not q:
            # MR#6: a window without a query-writer query must NEVER search
            # using the abstract generation prompt (window["prompt"]) — that
            # is exactly the D-007 office/code drift the query-writer step
            # was built to avoid.
            return None

        if not self.api_key:
            # MR#5: missing key is a configuration error — fail fast, distinct
            # from the transient-HTTP None path below.
            raise PexelsError("Pexels API key missing — set PEXELS_API_KEY")

        try:
            candidates = pexels.search(q, self.api_key, self.per_page)
            candidates = rerank_candidates(q, candidates)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                http.client.IncompleteRead, PexelsError) as exc:
            # MR#5: transient HTTP failures (5xx/timeout/403/IncompleteRead) fall
            # back to generation, not crash a paid render (D-003).
            print(f"[pexels] transient search failure for {q!r}: {exc} — falling back to generation")
            return None

        chosen = None
        for cand in candidates:
            if cand.get("duration", 0) < dur_s - 1e-3:
                # D-008 THE trap: never pick a candidate shorter than the
                # window duration — that would force a loop/hold to fill it.
                continue
            cand_id = cand.get("id")
            if ledger is not None and ledger.seen_id(cand_id):
                continue
            chosen = cand
            break

        if chosen is None:
            return None

        video_file = pexels.best_video_file(chosen)
        if video_file is None:
            return None

        download_name = Path(video_file["link"]).name or f"{chosen.get('id')}.mp4"
        download_path = str(Path(out_path).with_name(f"dl_{download_name}"))
        try:
            pexels.download(video_file["link"], download_path)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                http.client.IncompleteRead, PexelsError) as exc:
            # MR#5 (real-render caught): a transient DOWNLOAD failure (large video
            # stream IncompleteRead / 5xx) must fall back to generation, not crash
            # a paid render — same guarantee as the search guard above (D-003).
            print(f"[pexels] transient download failure id={chosen.get('id')}: {exc} — fallback to generation")
            return None
        # MR#1: no src_dur — actual_dur == dur_s exactly (not min(src_dur, WINDOW_MAX)).
        run_ff(ffcmd.video_clip_cmd(download_path, dur_s, out_path, grade), f"pexels-{chosen.get('id')}")

        if ledger is not None:
            ledger.record_id(chosen.get("id"))

        return out_path


class MediaRouter:
    """Routes still/video requests: library first, generate fallback (D-003).

    Persists `cfg` at construction time (MR-1) because the episode's art
    style is only known later (bound in `step_extract`); `set_style` uses the
    injected `self.cfg` to build the per-episode `LibrarySource` at that
    point.
    """

    def __init__(self, generate, cfg=None, library=None, log=None):
        self.generate = generate
        self.cfg = cfg
        self.library = library
        self.style = None
        self.log = log

    def set_style(self, style: str) -> None:
        self.style = style
        self.library = LibrarySource.from_config(self.cfg, style)

    def gen_still(self, prompt: str, ref_url: str | None, out_path: str) -> str | None:
        for src in (self.library, self.generate):
            if src is None:
                continue
            result = src.fetch(prompt, ref_url, out_path)
            if result is not None:
                if self.log and src is self.library:
                    self.log(f"[media] library hit ({self.style}): {prompt[:30]}")
                return result
        return None

    def gen_video(self, prompt: str, ref_url: str | None, out_path: str) -> dict | None:
        for src in (self.library, self.generate):
            if src is None:
                continue
            result = src.fetch_video(prompt, ref_url, out_path)
            if result is not None:
                if self.log and src is self.library:
                    self.log(f"[media] library video hit ({self.style}): {prompt[:30]}")
                return result
        return None

    def fetch_library_clip(self, window: dict, dur_s: float, out_path: str, run_ff,
                            grade: str | None = None, ledger=None) -> str | None:
        """Try the bound library's window-duration sub-clip (Phase 4).

        Library-only (no generate fallback here — the render call site
        (`step_render_shots_clip`) falls back to its existing gen_still path
        on None, per Task 2's no-regression contract).
        """
        if self.library is None:
            return None
        result = self.library.fetch_clip(window, dur_s, out_path, run_ff, grade, ledger)
        if result is not None and self.log:
            self.log(f"[media] library clip hit ({self.style}): {window.get('query', '')[:30]}")
        return result
