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
import shutil
from pathlib import Path
from typing import Any

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
        return cls(kind=kind, style=style)


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
