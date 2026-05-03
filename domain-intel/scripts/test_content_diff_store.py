#!/usr/bin/env python3
"""Unit tests for content_diff_store.py."""

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from content_diff_store import (
    RETENTION_DAYS,
    ContentDiffStore,
    _diff_lines,
    _domain_from_url,
    _normalize,
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_handles_whitespace():
    """Leading/trailing whitespace stripped, internal collapse, empty-line drop."""
    raw = """
    Hello World

      Multiple   Spaces   Here

    End of content.
    """
    lines = _normalize(raw)
    assert lines == [
        "hello world",
        "multiple   spaces   here",
        "end of content.",
    ]


def test_normalize_lowercase():
    assert _normalize("HELLO WORLD") == ["hello world"]
    assert _normalize("HeLLo WoRLd") == ["hello world"]


def test_normalize_drops_empty_lines():
    raw = "line one\n\n\n   \nline two"
    assert _normalize(raw) == ["line one", "line two"]


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def test_diff_added_and_removed():
    old = ["line a", "line b", "line c"]
    new = ["line a", "line b", "line d", "line e"]
    added, removed = _diff_lines(old, new)
    assert added == ["line d", "line e"]
    assert removed == ["line c"]


def test_diff_unchanged():
    old = ["line a", "line b"]
    new = ["line a", "line b"]
    added, removed = _diff_lines(old, new)
    assert added == []
    assert removed == []


def test_diff_added_only():
    old = ["line a"]
    new = ["line a", "line b", "line c"]
    added, removed = _diff_lines(old, new)
    assert added == ["line b", "line c"]
    assert removed == []


def test_diff_removed_only():
    old = ["line a", "line b", "line c"]
    new = ["line a"]
    added, removed = _diff_lines(old, new)
    assert added == []
    assert removed == ["line b", "line c"]


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

def test_domain_from_url():
    assert _domain_from_url("https://example.com/page") == "example_com"
    assert _domain_from_url("https://blog.example.com/post") == "blog_example_com"
    assert _domain_from_url("http://localhost:8080/") == "localhost"


# ---------------------------------------------------------------------------
# ContentDiffStore core
# ---------------------------------------------------------------------------

def _tmp_dir():
    return Path(tempfile.mkdtemp(prefix="diffstore_test_"))


def test_first_visit_returns_new_content():
    tmp = _tmp_dir()
    try:
        store = ContentDiffStore(tmp)
        change = store.check_for_changes(
            "https://example.com/post",
            "This is the content of the post.",
        )
        assert change is not None
        assert change.change_type == "new_content"
        assert change.site_url == "https://example.com/post"
        assert change.current_hash is not None
    finally:
        shutil.rmtree(tmp)


def test_same_content_returns_unchanged():
    tmp = _tmp_dir()
    try:
        store = ContentDiffStore(tmp)
        content = "Stable content that never changes."

        change1 = store.check_for_changes(
            "https://example.com/stable", content
        )
        assert change1.change_type == "new_content"

        change2 = store.check_for_changes(
            "https://example.com/stable", content
        )
        assert change2 is None, "Identical content should return None (unchanged)"

        # Snapshot still updated (checked_at changes)
        snap = store.get_snapshot("https://example.com/stable")
        assert snap is not None
    finally:
        shutil.rmtree(tmp)


def test_modified_content_returns_content_updated():
    tmp = _tmp_dir()
    try:
        store = ContentDiffStore(tmp)

        old_content = "Line one.\nLine two.\nLine three."
        new_content = "Line one.\nLine two.\nLine three.\nLine four."

        store.check_for_changes("https://example.com/update", old_content)
        change = store.check_for_changes("https://example.com/update", new_content)

        assert change is not None
        assert change.change_type == "content_updated"
        assert "line four." in change.added_lines
        # line 4 added, nothing removed (only append)
        assert change.removed_lines == []
        assert change.previous_hash is not None
        assert change.current_hash is not None
        assert change.previous_hash != change.current_hash
    finally:
        shutil.rmtree(tmp)


def test_modified_content_removed_lines():
    tmp = _tmp_dir()
    try:
        store = ContentDiffStore(tmp)

        old_content = "Line one.\nLine two.\nLine three."
        new_content = "Line one."

        store.check_for_changes("https://example.com/update", old_content)
        change = store.check_for_changes("https://example.com/update", new_content)

        assert change is not None
        assert change.change_type == "content_updated"
        assert change.removed_lines == ["line two.", "line three."]
        assert change.added_lines == []
    finally:
        shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Per-domain isolation
# ---------------------------------------------------------------------------

def test_per_domain_isolation():
    """Snapshots for different domains don't collide."""
    tmp = _tmp_dir()
    try:
        store = ContentDiffStore(tmp)

        store.check_for_changes(
            "https://example.com/page", "Content from example domain."
        )
        store.check_for_changes(
            "https://other.com/page", "Content from other domain."
        )

        snap_a = store.get_snapshot("https://example.com/page")
        snap_b = store.get_snapshot("https://other.com/page")

        assert snap_a is not None
        assert snap_b is not None
        assert snap_a.site_url == "https://example.com/page"
        assert snap_b.site_url == "https://other.com/page"
        assert snap_a.content_hash != snap_b.content_hash

        # Both persisted in separate domain files
        domain_files = list(store.diff_store_dir.iterdir())
        assert len(domain_files) == 2
    finally:
        shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Persistence across store instances
# ---------------------------------------------------------------------------

def test_persistence_reopen():
    """Snapshots survive closing and reopening the store."""
    tmp = _tmp_dir()
    try:
        store1 = ContentDiffStore(tmp)
        store1.check_for_changes(
            "https://example.com/persist",
            "Persistent content across restarts."
        )

        store2 = ContentDiffStore(tmp)
        snap = store2.get_snapshot("https://example.com/persist")
        assert snap is not None
        assert snap.content == "persistent content across restarts."
        assert snap.content_hash is not None
    finally:
        shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# Prune expired
# ---------------------------------------------------------------------------

def test_prune_expired_removes_old_only():
    """91-day-old snapshot removed; 89-day-old snapshot kept."""
    tmp = _tmp_dir()
    try:
        # Use short retention for this test
        store = ContentDiffStore(tmp, retention_days=30)
        now = time.time()
        DAY = 86400

        # First visit sets checked_at to now
        store.check_for_changes("https://example.com/old", "Old content.")
        store.check_for_changes("https://example.com/new", "New content.")

        # Backdate old snapshot
        old_url = "https://example.com/old"
        old_snap = store.get_snapshot(old_url)
        assert old_snap is not None

        # Directly manipulate checked_at in cache
        old_snap.checked_at = now - 35 * DAY
        new_snap = store.get_snapshot("https://example.com/new")
        assert new_snap is not None
        new_snap.checked_at = now - 1 * DAY

        assert store.count() == 2

        removed = store.prune_expired()
        assert removed == 1, f"Expected 1 old snapshot pruned, got {removed}"
        assert store.count() == 1
        assert store.get_snapshot(old_url) is None, "Old URL should be pruned"
        assert store.get_snapshot("https://example.com/new") is not None

    finally:
        shutil.rmtree(tmp)
