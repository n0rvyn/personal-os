"""video-studio episode helper.

Deterministic per-episode directory structure and artifact gates:

- episode_paths: canonical paths for all video episode artifacts
  ({output_dir}/episodes/{seq}-{slug}/)
- check_artifact: per-step artifact-presence gate (presence + non-empty only)
- make_scratch: per-invocation scratch directory lifecycle
- EpisodeError: domain exception for invalid episode parameters
- _resolve_within: path-traversal guard (internal)
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class EpisodeError(Exception):
    """Raised for invalid episode parameters (e.g. path traversal, bad inputs)."""


# Pattern for stripping unsafe characters from seq/slug values.
_UNSAFE_RE = re.compile(r"[^\w\-.]")
_MULTI_DASH_RE = re.compile(r"-+")


def _sanitize_component(value: str) -> str:
    """Replace unsafe filesystem characters in a path component with '-'.

    Keeps ASCII word chars, hyphens, and dots; collapses runs of hyphens;
    strips leading/trailing hyphens and dots.
    """
    kept = _UNSAFE_RE.sub("-", value)
    kept = _MULTI_DASH_RE.sub("-", kept)
    return kept.strip("-.")


def _resolve_within(base: Path, rel: str) -> Path:
    """Join base / rel and assert the result stays inside base.

    Raises EpisodeError if the resolved path would escape base (path traversal).
    """
    joined = (base / rel).resolve()
    real_base = base.resolve()
    # Path must start with base (add sep to avoid /foo matching /foobar).
    if str(joined) != str(real_base) and not str(joined).startswith(
        str(real_base) + os.sep
    ):
        raise EpisodeError(
            f"path traversal detected: '{rel}' resolves to {joined}, "
            f"which is outside base {real_base}"
        )
    return joined


def episode_paths(
    output_dir: str | os.PathLike,
    seq: str,
    slug: str,
) -> dict[str, Path]:
    """Return canonical paths for all artifacts of a video episode.

    All artifacts live under:
        {output_dir}/episodes/{seq}-{slug}/

    Returned keys:
        script      — script.md        (narration / beat script)
        beats       — beats.json        (durable digest-writer beats, for resume/reuse)
        shotlist    — shotlist.json     (shot descriptions + timing)
        assets_dir  — assets/           (downloaded images / clip frames)
        audio       — audio.mp3         (TTS-synthesized narration)
        subtitles   — subtitles.srt     (burned-in subtitle file)
        final       — {seq}-{slug}.mp4  (rendered output video; name matches ep dir)
        provenance  — provenance.json   (run metadata + asset fingerprints)

    Path-traversal guard: seq and slug are sanitized; any path that would
    escape output_dir raises EpisodeError.

    The episode directory is NOT created here — the runner creates it when
    it first needs to write an artifact. episode_paths is pure path computation.
    """
    if not seq or not isinstance(seq, str):
        raise EpisodeError(f"seq must be a non-empty string, got {seq!r}")
    if not slug or not isinstance(slug, str):
        raise EpisodeError(f"slug must be a non-empty string, got {slug!r}")

    # Sanitize seq and slug to prevent path traversal via component manipulation.
    safe_seq = _sanitize_component(seq)
    safe_slug = _sanitize_component(slug)

    out_dir = Path(os.path.realpath(str(output_dir)))

    # Compute the episode directory path.
    ep_dir_name = f"{safe_seq}-{safe_slug}"
    ep_dir = _resolve_within(out_dir, f"episodes/{ep_dir_name}")

    # Check that the traversal guard was not silently bypassed by sanitization.
    # (If seq or slug had '..' the sanitizer strips '.' so safe_seq/safe_slug
    # would be very different from the original; the _resolve_within above on
    # the sanitized path already ensures containment, but verify the final
    # paths too for belt-and-suspenders.)
    paths: dict[str, Path] = {
        "script": ep_dir / "script.md",
        "beats": ep_dir / "beats.json",
        "shotlist": ep_dir / "shotlist.json",
        "assets_dir": ep_dir / "assets",
        "audio": ep_dir / "audio.mp3",
        "subtitles": ep_dir / "subtitles.srt",
        # Video shares the episode directory's name so the two always match,
        # e.g. episodes/002-别信.../002-别信....mp4 (see derive_seq_slug).
        "final": ep_dir / f"{ep_dir_name}.mp4",
        "provenance": ep_dir / "provenance.json",
    }

    # Final traversal check: every returned path must stay inside out_dir.
    for key, p in paths.items():
        real = os.path.realpath(str(p))
        real_base = str(out_dir)
        if real != real_base and not real.startswith(real_base + os.sep):
            raise EpisodeError(
                f"episode path '{key}' escapes output_dir: {p} → {real}"
            )

    return paths


def check_artifact(path: str | os.PathLike) -> dict[str, Any]:
    """Return whether an artifact is present and non-empty.

    Returns: {"ok": bool, "reason": str}.
    ok is True iff the file exists and has size > 0.
    Zero-byte files count as missing (ok=False).
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "reason": f"missing: {p.name}"}
    if not p.is_file():
        return {"ok": False, "reason": f"not a file: {p}"}
    if p.stat().st_size == 0:
        return {"ok": False, "reason": f"empty: {p.name}"}
    return {"ok": True, "reason": f"present: {p}"}


def make_scratch(output_dir: str | os.PathLike, tag: str) -> Path:
    """Create a per-invocation scratch directory under output_dir.

    Each call produces a UNIQUE, EMPTY directory — the tag is suffixed with a
    wall-clock timestamp (HHMMSS) so re-running the same episode slug lands in
    a fresh dir and cannot inherit a prior run's intermediate files.

    The directory is created with exist_ok=False; if the timestamp collides a
    counter suffix is appended until a fresh name is found.

    Returns the Path of the created scratch directory.
    Raises EpisodeError if tag is empty or not a string.
    Raises EpisodeError if the scratch would escape output_dir.
    """
    if not tag or not isinstance(tag, str):
        raise EpisodeError(f"tag must be a non-empty string, got {tag!r}")

    safe_tag = _sanitize_component(tag) or "run"
    base = Path(os.path.realpath(str(output_dir)))

    stamp = datetime.now().strftime("%H%M%S")
    scratch = base / f".scratch-{safe_tag}-{stamp}"
    n = 2
    while scratch.exists():
        scratch = base / f".scratch-{safe_tag}-{stamp}-{n}"
        n += 1

    # Traversal guard: scratch must stay inside output_dir.
    real = os.path.realpath(str(scratch))
    if not real.startswith(str(base) + os.sep) and real != str(base):
        raise EpisodeError(
            f"scratch dir would escape output_dir: {scratch}"
        )

    scratch.mkdir(parents=True, exist_ok=False)
    return scratch


def archive_episode_output(
    output_dir: str | os.PathLike,
    seq: str,
    slug: str,
) -> Path | None:
    """Move an episode output directory to the archive.

    Moves `{output_dir}/episodes/{seq}-{slug}/` to
    `{output_dir}/episodes/.archive/{seq}-{slug}-{n}/` where n is the
    smallest positive integer that yields a non-existent destination
    (no-overwrite guarantee).

    Returns the destination Path on success, or None if the source
    episode directory does not exist (no exception raised).

    Raises EpisodeError if seq or slug are invalid.
    """
    if not seq or not isinstance(seq, str):
        raise EpisodeError(f"seq must be a non-empty string, got {seq!r}")
    if not slug or not isinstance(slug, str):
        raise EpisodeError(f"slug must be a non-empty string, got {slug!r}")

    safe_seq = _sanitize_component(seq)
    safe_slug = _sanitize_component(slug)
    ep_dir_name = f"{safe_seq}-{safe_slug}"

    out_dir = Path(os.path.realpath(str(output_dir)))
    src = out_dir / "episodes" / ep_dir_name

    if not src.exists():
        return None

    archive_base = out_dir / "episodes" / ".archive"
    archive_base.mkdir(parents=True, exist_ok=True)

    n = 1
    while True:
        dst = archive_base / f"{ep_dir_name}-{n}"
        if not dst.exists():
            break
        n += 1

    shutil.move(str(src), str(dst))
    return dst


def cleanup_scratch(scratch: str | os.PathLike) -> None:
    """Remove a scratch directory — best-effort.

    Safe to call from success and error/finally paths. Tolerates a
    non-existent path (no-op). Recursive removal.

    OSError is logged to stderr and swallowed — the final artifacts already
    live in the episode dir; a leftover scratch is harmless.
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
        print(
            f"cleanup_scratch: best-effort skip, could not remove {p}: {e}",
            file=sys.stderr,
        )
