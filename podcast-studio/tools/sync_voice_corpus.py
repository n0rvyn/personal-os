#!/usr/bin/env python3
"""Sync the host's dev-log (开发日志) posts from norvyn.com into a local
voice-corpus dir for the podcast Character Bible.

Manual / cron only — deliberately NOT wired into lib.runner, so the daily
pipeline stays offline-deterministic. Reads the public posts API (no auth),
filters to the 开发日志 series, and writes each post as a clean `<slug>.md`
(title frontmatter + body) into --out. The dev-log is a VOICE reference for
the bible (how the host SOUNDS), never a CONTENT/topic source.

Network-fail-soft: a fetch failure leaves any existing corpus untouched and
exits non-zero — it never wipes the dir it could not refresh.

Usage:
  python3 tools/sync_voice_corpus.py [--out DIR] [--source-url URL] [--filter STR]

--out defaults to vault.voice_corpus_dir from the podcast config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE_URL = "https://norvyn.com/api/posts?limit=200"
DEFAULT_FILTER = "开发日志"
_DEVLOG_SLUG_PREFIX = "kai-fa-ri-zhi"
# Mandatory fetch timeout. WITHOUT it a hung host blocks forever and is NOT
# catchable as an exception, so the fail-soft path in main() never runs.
_FETCH_TIMEOUT = 30


def fetch_posts(
    url: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = _FETCH_TIMEOUT,
) -> list[dict]:
    """GET the posts API and return its `data` list. Raises on network error
    (the caller turns that into a fail-soft non-zero exit)."""
    with opener(url, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data if isinstance(data, list) else []


def is_devlog(post: dict, filter_str: str) -> bool:
    """A post is a dev-log entry if its title carries the filter string OR its
    slug starts with the dev-log pinyin prefix (posts 1/2 spell the title
    differently, so title-match is the primary signal)."""
    title = str(post.get("title", ""))
    slug = str(post.get("slug", ""))
    return filter_str in title or slug.startswith(_DEVLOG_SLUG_PREFIX)


def safe_name(slug: str) -> Optional[str]:
    """basename(slug) with path-traversal rejected. None → skip the post."""
    if not slug or "/" in slug or ".." in slug:
        return None
    name = os.path.basename(slug)
    if not name or name in {".", ".."}:
        return None
    return name


def _write_post(out_dir: Path, post: dict) -> bool:
    """Atomically write one post as `<slug>.md`. temp + os.replace; the temp is
    unlinked on any write error (no orphan). Returns False for an unsafe slug."""
    slug = str(post.get("slug", ""))
    name = safe_name(slug)
    if name is None:
        print(f"sync: skip unsafe slug {slug!r}", file=sys.stderr)
        return False

    title = str(post.get("title", "")).strip()
    # Defensive: if the API ever returns double-escaped newlines, normalize.
    content = str(post.get("content", "")).replace("\\n", "\n")
    body = f"---\ntitle: {title}\n---\n\n{content}\n"

    target = out_dir / f"{name}.md"
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=str(out_dir))
    tmp_p = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(str(tmp_p), str(target))
    except Exception:
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise
    return True


def sync(
    out_dir: str,
    *,
    source_url: str,
    filter_str: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> int:
    """Fetch → filter → write. Returns the count written.

    Fetch happens FIRST (before mkdir / any write), so a network failure raises
    before the out dir is touched — existing corpus is never wiped. The dir is
    NOT pre-cleaned; refreshed posts overwrite by name, stale ones are left.
    """
    posts = fetch_posts(source_url, opener=opener)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in posts:
        if not is_devlog(p, filter_str):
            continue
        if _write_post(out, p):
            n += 1
    return n


def main(
    argv: Optional[list[str]] = None,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> int:
    parser = argparse.ArgumentParser(prog="sync_voice_corpus")
    parser.add_argument(
        "--out", default=None,
        help="voice-corpus dir (default: config vault.voice_corpus_dir)",
    )
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--filter", dest="filter_str", default=DEFAULT_FILTER)
    args = parser.parse_args(argv)

    out_dir = args.out
    if out_dir is None:
        if str(PLUGIN_ROOT) not in sys.path:
            sys.path.insert(0, str(PLUGIN_ROOT))
        try:
            from lib.config import load_config

            out_dir = load_config().vault.voice_corpus_dir
        except Exception as e:  # noqa: BLE001 — surface and exit, never trace-dump
            print(f"sync: --out not given and config read failed: {e}", file=sys.stderr)
            return 2
        if not out_dir:
            print(
                "sync: --out not given and config has no vault.voice_corpus_dir",
                file=sys.stderr,
            )
            return 2

    try:
        n = sync(out_dir, source_url=args.source_url, filter_str=args.filter_str, opener=opener)
    except Exception as e:  # noqa: BLE001 — fail-soft: existing corpus untouched
        print(f"sync: fetch/write failed (existing corpus untouched): {e}", file=sys.stderr)
        return 2

    print(f"sync: wrote {n} dev-log post(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
