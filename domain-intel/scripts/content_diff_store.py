#!/usr/bin/env python3
"""ContentDiffStore: per-URL normalized-snapshot store for change detection.

Ports lumina-backend/internal/dedup/content_diff.go:
  https://github.com/n0rvyn/lumina (reference only — deprecated)

Algorithm:
  - Per-URL normalized snapshot (whitespace-collapsed, lowercase)
  - ChangeType: "new_content" | "unchanged" | "content_updated"
  - Diff: AddedLines + RemovedLines via set comparison

Storage (DP-A2):
  - {state_dir}/domain-intel/diff_store/{domain}.json — split by domain to avoid
    one giant file; domain extracted from URL hostname
  - Retention: 90 days default; prune by age on prune_expired()
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_SUBDIR = "domain-intel"
DIFF_STORE_SUBDIR = "diff_store"
RETENTION_DAYS = 90


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ContentChange:
    """Result of comparing new content against stored snapshot."""

    site_url: str
    site_name: str
    change_type: str  # "new_content" | "unchanged" | "content_updated"
    previous_hash: Optional[str] = None
    current_hash: Optional[str] = None
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Storage format
# ---------------------------------------------------------------------------

@dataclass
class Snapshot:
    """Stored snapshot for one URL."""

    site_url: str
    site_name: str
    content_hash: str
    content: str  # Normalized text
    checked_at: float  # epoch


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize(content: str) -> list[str]:
    """Split, strip, lowercase, drop empty, return ordered line list."""
    lines: list[str] = []
    for raw in content.splitlines():
        stripped = raw.strip().lower()
        if stripped:
            lines.append(stripped)
    return lines


def _hash_content(content: str) -> str:
    """SHA-256 hex digest of normalized content."""
    # Normalize first so identical content always produces same hash
    normalized = "\n".join(_normalize(content))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _diff_lines(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    """Set-based line diff: added = in new not old, removed = in old not new."""
    old_set = set(old)
    new_set = set(new)
    added = [line for line in new if line not in old_set]
    removed = [line for line in old if line not in new_set]
    return added, removed


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

def _domain_from_url(url: str) -> str:
    """Extract safe domain name from URL for file naming."""
    try:
        from urllib.parse import urlparse
    except ImportError:
        import urlparse as _urllib  # Python 2 compat (not used in practice)
    parsed = urlparse(url)
    domain = parsed.netloc or ""
    # Strip port
    domain = domain.split(":")[0]
    # Replace dots with underscores for filesystem safety
    return domain.replace(".", "_") or "unknown"


# ---------------------------------------------------------------------------
# ContentDiffStore
# ---------------------------------------------------------------------------

class ContentDiffStore:
    """Per-URL normalized snapshot store with change detection."""

    def __init__(
        self,
        state_dir: Path,
        retention_days: int = RETENTION_DAYS,
    ):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.retention_days = retention_days
        self.diff_store_dir = self.state_dir / STATE_SUBDIR / DIFF_STORE_SUBDIR
        self._cache: dict[str, Snapshot] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Internal IO
    # ------------------------------------------------------------------

    def _domain_file(self, domain: str) -> Path:
        return self.diff_store_dir / f"{domain}.json"

    def _load_all(self) -> None:
        """Load all domain snapshot files into memory cache."""
        if not self.diff_store_dir.exists():
            return
        for fp in self.diff_store_dir.iterdir():
            if not fp.suffix == ".json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                # data is dict[str, Snapshot dict]
                for url, snap in data.items():
                    self._cache[url] = Snapshot(
                        site_url=snap["site_url"],
                        site_name=snap.get("site_name", ""),
                        content_hash=snap["content_hash"],
                        content=snap["content"],
                        checked_at=snap.get("checked_at", 0.0),
                    )
            except (json.JSONDecodeError, KeyError, TypeError):
                # Corrupt file — skip
                pass

    def _save_domain(self, domain: str) -> None:
        """Write the cache subset for a given domain to disk."""
        self.diff_store_dir.mkdir(parents=True, exist_ok=True)
        domain_data = {
            url: snap.__dict__
            for url, snap in self._cache.items()
            if _domain_from_url(url) == domain
        }
        tmp = self._domain_file(domain).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(domain_data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.rename(self._domain_file(domain))

    def _save_all(self) -> None:
        """Write all cache entries back to disk."""
        by_domain: dict[str, list[str]] = {}
        for url in self._cache:
            d = _domain_from_url(url)
            by_domain.setdefault(d, []).append(url)

        for domain in by_domain:
            self._save_domain(domain)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_for_changes(
        self,
        url: str,
        content: str,
        site_name: Optional[str] = None,
    ) -> Optional[ContentChange]:
        """Compare new content against stored snapshot.

        Returns:
          - ContentChange(change_type="new_content") on first visit
          - None if content unchanged
          - ContentChange(change_type="content_updated", added_lines, removed_lines)
            on change
        """
        site_name = site_name or ""

        # Normalize once
        normalized = _normalize(content)
        current_hash = _hash_content(content)

        now = time.time()

        previous = self._cache.get(url)

        if previous is None:
            change = ContentChange(
                site_url=url,
                site_name=site_name,
                change_type="new_content",
                current_hash=current_hash,
            )
        elif previous.content_hash != current_hash:
            added, removed = _diff_lines(
                _normalize(previous.content), normalized
            )
            change = ContentChange(
                site_url=url,
                site_name=site_name,
                change_type="content_updated",
                previous_hash=previous.content_hash,
                current_hash=current_hash,
                added_lines=added,
                removed_lines=removed,
            )
        else:
            # Unchanged — return None per API contract
            change = None

        # Update snapshot
        self._cache[url] = Snapshot(
            site_url=url,
            site_name=site_name,
            content_hash=current_hash,
            content="\n".join(normalized),
            checked_at=now,
        )

        # Persist domain file
        domain = _domain_from_url(url)
        self._save_domain(domain)

        return change

    def get_snapshot(self, url: str) -> Optional[Snapshot]:
        """Return the stored snapshot for a URL, or None."""
        return self._cache.get(url)

    def prune_expired(self) -> int:
        """Remove snapshots older than retention_days. Returns count removed."""
        cutoff = time.time() - (self.retention_days * 86400)
        to_remove: list[str] = []
        for url, snap in self._cache.items():
            if snap.checked_at < cutoff:
                to_remove.append(url)

        for url in to_remove:
            del self._cache[url]

        if to_remove:
            # Re-write all remaining domains
            self._save_all()

        return len(to_remove)

    def count(self) -> int:
        """Total snapshot count (for test assertions)."""
        return len(self._cache)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="ContentDiffStore CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check_cmd = sub.add_parser("check", help="Check content for changes")
    check_cmd.add_argument("--url", required=True)
    check_cmd.add_argument("--content", required=True)
    check_cmd.add_argument("--site-name", default="")
    check_cmd.add_argument("--state-dir", default="~/.personal-os")
    check_cmd.add_argument("--retention-days", type=int, default=90)

    prune_cmd = sub.add_parser("prune", help="Prune expired snapshots")
    prune_cmd.add_argument("--state-dir", default="~/.personal-os")

    args = parser.parse_args()

    from pathlib import Path

    if args.cmd == "check":
        store = ContentDiffStore(
            Path(args.state_dir).expanduser().resolve(),
            retention_days=args.retention_days,
        )
        result = store.check_for_changes(args.url, args.content, args.site_name or None)
        if result is None:
            # Unchanged — API returns None; output is useful metadata
            snap = store.get_snapshot(args.url)
            print(json.dumps({
                "change_type": "unchanged",
                "site_url": args.url,
                "added_lines": [],
                "removed_lines": [],
            }))
        else:
            print(json.dumps({
                "change_type": result.change_type,
                "site_url": result.site_url,
                "site_name": result.site_name,
                "previous_hash": result.previous_hash,
                "current_hash": result.current_hash,
                "added_lines": result.added_lines,
                "removed_lines": result.removed_lines,
            }, default=str))

    elif args.cmd == "prune":
        store = ContentDiffStore(Path(args.state_dir).expanduser().resolve())
        removed = store.prune_expired()
        print(f"pruned {removed}")
