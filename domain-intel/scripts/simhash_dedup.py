#!/usr/bin/env python3
"""SimHash near-duplicate detection.

Ports lumina-backend/internal/dedup/simhash.go:
  https://github.com/n0rvyn/lumina (reference only — deprecated)

Algorithm:
  - 64-bit SimHash via character-level n-gram hashing (n=3 default)
  - FNV-1a per n-gram (implemented inline; fnv package not required)
  - Hamming distance threshold (default 3)
  - Title weighted 3x over content via CombineFingerprints()

Persistence (DP-A1):
  - {state_dir}/domain-intel/seen.simhash.jsonl — one JSON line per insight
  - Schema: {"id": "...", "fp": <int>, "ts": <epoch>}
  - Aging: keep only last 90 days
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

# FNV-1a constants (64-bit)
_FNV_OFFSET_BASIS = 14695981039346656037
_FNV_PRIME = 1099511628211


def _fnv1a_64(text: str) -> int:
    """FNV-1a 64-bit hash of a string. Returns a non-negative integer."""
    h = _FNV_OFFSET_BASIS
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


# ---------------------------------------------------------------------------
# Core SimHash
# ---------------------------------------------------------------------------

NGRAM_SIZE = 3
HAMMING_THRESHOLD = 3
STATE_SUBDIR = "domain-intel"
STORE_FILENAME = "seen.simhash.jsonl"
RETENTION_DAYS = 90


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on whitespace/punctuation, drop non-alphanumeric."""
    text = text.lower()
    tokens: list[str] = []
    current = ""
    for ch in text:
        if ch.isalnum() or ch == "_":
            current += ch
        else:
            if current:
                tokens.append(current)
                current = ""
    if current:
        tokens.append(current)
    return tokens


def _ngrams_from_tokens(tokens: list[str], n: int = NGRAM_SIZE) -> list[str]:
    """Character-level n-grams from token list (joined with space)."""
    text = " ".join(tokens)
    if len(text) < n:
        return [text] if text else []
    return [text[i : i + n] for i in range(len(text) - n + 1)]


class SimHash:
    """64-bit SimHash with Hamming distance comparison and weighted fingerprints."""

    NGRAM_SIZE = NGRAM_SIZE

    def __init__(self, threshold: int = HAMMING_THRESHOLD):
        self.threshold = threshold

    def fingerprint(self, text: str) -> int:
        """Compute 64-bit fingerprint: sum per-bit vote from n-gram hashes."""
        tokens = _tokenize(text)
        ngrams = _ngrams_from_tokens(tokens)
        if not ngrams:
            return 0

        v = [0] * 64
        for ng in ngrams:
            h = _fnv1a_64(ng)
            for i in range(64):
                v[i] += 1 if (h >> i) & 1 else -1

        fp = 0
        for i in range(64):
            if v[i] > 0:
                fp |= 1 << i
        return fp

    def weighted_fingerprint(
        self, title: str, content: str, title_weight: float = 3.0
    ) -> int:
        """Combine title (higher weight) and content fingerprints."""
        title_tokens = _tokenize(title)
        content_tokens = _tokenize(content)
        title_ngrams = _ngrams_from_tokens(title_tokens)
        content_ngrams = _ngrams_from_tokens(content_tokens)

        # weighted: map[ngram] -> cumulative weight
        weighted: dict[str, float] = {}
        for ng in title_ngrams:
            weighted[ng] = weighted.get(ng, 0) + title_weight
        for ng in content_ngrams:
            weighted[ng] = weighted.get(ng, 0) + 1.0

        if not weighted:
            return 0

        v = [0.0] * 64
        for ng, w in weighted.items():
            h = _fnv1a_64(ng)
            for i in range(64):
                v[i] += w if (h >> i) & 1 else -w

        fp = 0
        for i in range(64):
            if v[i] > 0:
                fp |= 1 << i
        return fp

    def combine_fingerprints(self, title: str, content: str) -> int:
        """Alias for weighted_fingerprint(title, content, title_weight=3.0)."""
        return self.weighted_fingerprint(title, content, title_weight=3.0)

    @staticmethod
    def hamming(a: int, b: int) -> int:
        """Count of differing bits between two 64-bit integers."""
        return (a ^ b).bit_count()

    def is_near_dup(self, fp1: int, fp2: int) -> bool:
        return self.hamming(fp1, fp2) <= self.threshold


# ---------------------------------------------------------------------------
# Persistence layer — SeenStore
# ---------------------------------------------------------------------------

class SeenStore:
    """Persistent JSONL store for SimHash fingerprints, with 90-day retention."""

    def __init__(self, state_dir: Path, retention_days: int = RETENTION_DAYS):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.retention_days = retention_days
        self.store_dir = self.state_dir / STATE_SUBDIR
        self.store_path = self.store_dir / STORE_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, item_id: str, fp: int, ts: Optional[float] = None) -> None:
        """Append a fingerprint entry to the JSONL store."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        entry = {"id": item_id, "fp": fp, "ts": ts or time.time()}
        with self.store_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def is_seen(self, fp: int, threshold: Optional[int] = None) -> bool:
        """Return True if a fingerprint within Hamming distance exists."""
        thr = threshold if threshold is not None else HAMMING_THRESHOLD
        if not self.store_path.exists():
            return False
        with self.store_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                stored_fp = entry["fp"]
                if SimHash.hamming(fp, stored_fp) <= thr:
                    return True
        return False

    def prune_expired(self) -> int:
        """Remove entries older than retention_days. Returns count removed."""
        if not self.store_path.exists():
            return 0
        cutoff = time.time() - (self.retention_days * 86400)
        kept: list[dict] = []
        removed = 0
        with self.store_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("ts", 0) < cutoff:
                    removed += 1
                else:
                    kept.append(entry)
        if removed:
            with self.store_path.open("w", encoding="utf-8") as fh:
                for entry in kept:
                    fh.write(json.dumps(entry) + "\n")
        return removed

    def count(self) -> int:
        """Return total entries (for test assertions)."""
        if not self.store_path.exists():
            return 0
        with self.store_path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SimHash near-duplicate detection CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fp_cmd = sub.add_parser("fingerprint", help="Compute weighted fingerprint for title+content")
    fp_cmd.add_argument("--title", required=True)
    fp_cmd.add_argument("--content", default="")
    fp_cmd.add_argument("--title-weight", type=float, default=3.0)

    check_cmd = sub.add_parser("check", help="Check if fingerprint is seen (within threshold)")
    check_cmd.add_argument("--fp", type=int, required=True)
    check_cmd.add_argument("--state-dir", default="~/.personal-os")
    check_cmd.add_argument("--threshold", type=int, default=HAMMING_THRESHOLD)

    add_cmd = sub.add_parser("add", help="Add a fingerprint to the seen store")
    add_cmd.add_argument("--id", required=True)
    add_cmd.add_argument("--fp", type=int, required=True)
    add_cmd.add_argument("--state-dir", default="~/.personal-os")

    prune_cmd = sub.add_parser("prune", help="Prune entries older than retention_days")
    prune_cmd.add_argument("--state-dir", default="~/.personal-os")

    args = parser.parse_args()

    if args.cmd == "fingerprint":
        hasher = SimHash()
        fp = hasher.weighted_fingerprint(args.title, args.content, args.title_weight)
        print(fp)

    elif args.cmd == "check":
        from pathlib import Path
        store = SeenStore(Path(args.state_dir).expanduser().resolve())
        result = store.is_seen(args.fp, args.threshold)
        print("true" if result else "false")

    elif args.cmd == "add":
        from pathlib import Path
        store = SeenStore(Path(args.state_dir).expanduser().resolve())
        store.add(args.id, args.fp)
        print("added")

    elif args.cmd == "prune":
        from pathlib import Path
        store = SeenStore(Path(args.state_dir).expanduser().resolve())
        removed = store.prune_expired()
        print(f"pruned {removed}")
