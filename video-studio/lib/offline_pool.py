"""Offline media pool (Phase 6, D-012/D-006).

Persistent, style-scoped, CROSS-EPISODE pool for styles with no online library
(e.g. anime): a directory of generated still assets + a JSON index tagging each
by the query it was generated for. `OfflinePoolSource` (lib/media_source.py)
retrieves by content overlap (hit → reuse, miss → generate + add), so a
"generate" style also gets library-first economy across episodes.

This index is INCLUSION (reuse is the point), NOT a dedup ledger — do not
conflate with lib.visual_ledger (which EXCLUDES across episodes).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from lib.query_filter import token_overlap


class PoolIndex:
    """On-disk style pool: pool_dir/index.json maps asset filename -> {"query": str}."""

    _FILENAME = "index.json"

    def __init__(self, pool_dir: str | Path):
        self.pool_dir = Path(pool_dir)
        self._entries: dict[str, dict] = {}   # filename -> {"query": str}
        self._load()

    def _load(self) -> None:
        path = self.pool_dir / self._FILENAME
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._entries = {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _abs(self, filename: str) -> str:
        # Path normalization contract (load-bearing): lookup() and add() MUST
        # return byte-identical absolute paths so OfflinePoolSource's per-episode
        # `_used` set (string membership) works between a hit and a fresh add.
        return str((self.pool_dir / filename).resolve())

    def lookup(self, query: str) -> list[tuple[str, int]]:
        """Return (abs_asset_path, overlap) for every pool asset, overlap-desc, stable.

        No filtering, no running bonus — the caller (OfflinePoolSource) applies
        the hit gate (overlap >= 1 AND not already used this episode).
        """
        scored = []
        for idx, (filename, entry) in enumerate(self._entries.items()):
            overlap = token_overlap(query, str(entry.get("query", "")))
            scored.append((self._abs(filename), overlap, idx))
        scored.sort(key=lambda t: (-t[1], t[2]))
        return [(path, overlap) for path, overlap, _ in scored]

    def add(self, asset_src_path: str, query: str) -> str:
        """Copy a generated still into the pool, tag it with query, persist. Returns abs path."""
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        filename = f"pool_{len(self._entries):04d}.png"
        dest = self.pool_dir / filename
        shutil.copyfile(asset_src_path, dest)
        self._entries[filename] = {"query": query}
        self.save()
        return self._abs(filename)

    def save(self) -> None:
        self.pool_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pool_dir / (self._FILENAME + ".tmp")
        tmp.write_text(json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.pool_dir / self._FILENAME)
