"""podcast-studio character-bible mechanics.

Owns the per-run host Character Bible — a refreshed projection of the
user's private corpus (`vault.subjective_dir`) that the SKILL.md distill
step fills with worldview / obsessions / verbal tics / evolving stances.

- `bible_path(output_dir)`: fixed filename under output_dir, realpath-asserted
- `gather_corpus(subjective_dir, *, byte_cap, max_files)`: walks regular
  files (resolve symlinks, stay in dir), recency + breadth sampling, skips
  binary (null-byte sniff) / oversized / symlink-escape, returns
  `{text, included, dropped}` (drops reported, not silent)
- `write_bible(output_dir, text)`: atomic overwrite (NOT append-only —
  distinct from stance cards), temp in output_dir + `os.replace`, temp
  removed on error.

The bible is OVERWRITTEN each run (DP-002=A): a refreshed projection of
the corpus, not a log.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

# ---------- public API -------------------------------------------------------

# Fixed filename (single source of truth for the bible's location).
_BIBLE_FILENAME = "character-bible.md"

# Per-file size threshold for the gather step: a file larger than this is
# skipped (reported as dropped). 1MB is generous — corpus files are
# text notes, not bulk data.
_MAX_FILE_BYTES = 1_000_000

# Number of bytes to sniff for the binary check.
_BINARY_SNIFF_BYTES = 8192


def bible_path(output_dir: str | os.PathLike) -> Path:
    """Return the canonical bible path: `<output_dir>/character-bible.md`.

    Realpath-asserted: the resolved path must stay inside `output_dir`.
    Does NOT create the file or any directories.
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    candidate = out_dir / _BIBLE_FILENAME
    real = os.path.realpath(str(candidate))
    if not real.startswith(str(out_dir) + os.sep) and real != str(out_dir):
        raise ValueError(
            f"bible_path escapes output_dir: {candidate} (realpath: {real})"
        )
    return candidate


def gather_corpus(
    subjective_dir: str | os.PathLike,
    *,
    byte_cap: int,
    max_files: int,
) -> dict[str, Any]:
    """Read files under `subjective_dir`, return `{text, included, dropped}`.

    - Walks regular files only (symlinks resolved; symlinks escaping the
      dir are dropped).
    - Sorts by mtime descending (recency), then samples across subdirs
      (breadth). With a byte_cap that admits all files, the order is
      recency-first.
    - Skips binary files (null-byte sniff) and oversized files
      (>1MB) — both reported as dropped.
    - Stops accumulating text once `byte_cap` is reached; remaining
      files are reported as dropped.
    - Enforces `max_files` (a hard cap on the included set).

    Returns:
        {
          "text": <concatenated text of included files, with header paths>,
          "included": [<path>, ...],   # in sampling order
          "dropped": <int>,            # count of files NOT included
        }
    """
    root = Path(str(subjective_dir))
    if not root.exists() or not root.is_dir():
        return {"text": "", "included": [], "dropped": 0}

    real_root = os.path.realpath(str(root))

    # 1. Enumerate candidate regular files (resolve symlinks; skip
    #    symlinks that escape the dir).
    candidates: list[tuple[float, str]] = []  # (mtime, real_path)
    for dirpath, _dirnames, filenames in os.walk(real_root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                real = os.path.realpath(full)
            except OSError:
                continue
            # Stay-in-dir guard: skip symlinks that escape the root.
            if not real.startswith(real_root + os.sep) and real != real_root:
                continue
            try:
                st = os.stat(real)
            except OSError:
                continue
            # Regular files only.
            if not os.path.isfile(real):
                continue
            candidates.append((st.st_mtime, real))

    # 2. Sort by mtime descending (recency).
    candidates.sort(key=lambda t: -t[0])

    total = len(candidates)
    included: list[str] = []
    dropped = 0
    text_chunks: list[str] = []
    used_bytes = 0

    for _mtime, real in candidates:
        # Cap the included count.
        if len(included) >= max_files:
            dropped += 1
            continue

        # Per-file size check.
        try:
            size = os.path.getsize(real)
        except OSError:
            dropped += 1
            continue
        if size > _MAX_FILE_BYTES:
            dropped += 1
            continue

        # Binary sniff: a NUL byte in the first chunk = binary.
        try:
            with open(real, "rb") as f:
                head = f.read(_BINARY_SNIFF_BYTES)
        except OSError:
            dropped += 1
            continue
        if b"\x00" in head:
            dropped += 1
            continue

        # Read the text (best-effort: skip on decode error).
        try:
            content = Path(real).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            dropped += 1
            continue

        # Per-file header for traceability in the distilled bible.
        header = f"\n\n----- {os.path.relpath(real, real_root)} -----\n"
        chunk_bytes = len(header.encode("utf-8")) + len(content.encode("utf-8"))

        # byte_cap gate: if adding this would overflow, drop and stop
        # adding (drop the rest of the candidates).
        if used_bytes + chunk_bytes > byte_cap:
            dropped += 1
            continue

        text_chunks.append(header)
        text_chunks.append(content)
        used_bytes += chunk_bytes
        included.append(real)

    text = "".join(text_chunks)

    return {"text": text, "included": included, "dropped": dropped}


def write_bible(output_dir: str | os.PathLike, text: str) -> Path:
    """Atomic overwrite of the bible (NOT append-only).

    - The bible is OVERWRITTEN each run (DP-002=A): the host is a
      *refreshed projection* of the corpus, not a log.
    - Temp file is created in `output_dir` + `os.replace` (atomic).
    - On any error, the temp file is removed (no orphan).
    """
    out_dir = Path(os.path.realpath(str(output_dir)))
    if not out_dir.exists():
        raise FileNotFoundError(f"output_dir does not exist: {out_dir}")
    if not out_dir.is_dir():
        raise NotADirectoryError(f"output_dir is not a directory: {out_dir}")

    target = bible_path(out_dir)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(out_dir),
    )
    tmp_p = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(str(tmp_p), str(target))
    except Exception:
        # On any error, remove the temp file (no orphan).
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise

    return target
