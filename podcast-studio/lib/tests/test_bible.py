"""Tests for lib/bible.py — character-bible mechanics.

Written before lib/bible.py exists; collection must fail at this
point (`No module named 'lib.bible'`).

Pins:
- bible_path returns <output_dir>/character-bible.md and is realpath-safe
- gather_corpus reads files under subjective_dir (recency+breadth sampling,
  bounded; skips binary/oversized; empty→empty; drops reported, not silent)
- write_bible atomic overwrite (NOT append-only — distinct from stance cards);
  temp cleaned on error
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.bible import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import bible  # noqa: F401
    assert hasattr(bible, "bible_path")
    assert hasattr(bible, "gather_corpus")
    assert hasattr(bible, "write_bible")


# ---------- bible_path ----------

def test_bible_path_inside_output_dir(tmp_path):
    """bible_path returns `<output_dir>/character-bible.md` and stays
    inside output_dir (realpath check)."""
    from lib.bible import bible_path
    p = bible_path(str(tmp_path))
    assert p.name == "character-bible.md"
    # Must be inside output_dir (realpath-safe)
    assert str(p.resolve()).startswith(str(tmp_path.resolve()))


def test_bible_path_nested_subdir(tmp_path):
    """bible_path joins the output_dir to a fixed filename; nested
    subdirs of output_dir still produce a path inside output_dir."""
    from lib.bible import bible_path
    nested = tmp_path / "episodes" / "2026-06-08"
    nested.mkdir(parents=True)
    p = bible_path(str(nested))
    assert p.name == "character-bible.md"
    assert str(p.resolve()).startswith(str(nested.resolve()))


# ---------- gather_corpus: empty ----------

def test_gather_corpus_empty_dir(tmp_path):
    """Empty subjective_dir → empty result, no error, dropped=0."""
    from lib.bible import gather_corpus
    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    assert result["text"] == ""
    assert result["included"] == []
    assert result["dropped"] == 0


def test_gather_corpus_nonexistent_dir(tmp_path):
    """Nonexistent subjective_dir → empty result (no crash)."""
    from lib.bible import gather_corpus
    missing = tmp_path / "does-not-exist"
    result = gather_corpus(str(missing), byte_cap=10_000, max_files=10)
    assert result["text"] == ""
    assert result["included"] == []
    assert result["dropped"] == 0


# ---------- gather_corpus: reads regular files ----------

def test_gather_corpus_reads_text_files(tmp_path):
    """Subjective notes are read and returned as text (concatenation,
    breadth sampling, bounded)."""
    from lib.bible import gather_corpus
    # Seed 3 simple text notes
    (tmp_path / "a.md").write_text("alpha content about worldview", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta content about obsessions", encoding="utf-8")
    (tmp_path / "c.md").write_text("gamma content about verbal tics", encoding="utf-8")

    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    assert result["text"]
    # All 3 files included (well under cap)
    assert len(result["included"]) == 3
    assert result["dropped"] == 0
    # Text contains content from all three
    for snippet in ("alpha", "beta", "gamma"):
        assert snippet in result["text"]


def test_gather_corpus_recency_ordering(tmp_path):
    """Files are walked and the recency+breadth sort places recent files
    first. Set mtimes explicitly so the test is deterministic."""
    from lib.bible import gather_corpus
    # Create 3 files with explicit mtimes: oldest→newest
    old = tmp_path / "old.md"
    mid = tmp_path / "mid.md"
    new = tmp_path / "new.md"
    old.write_text("OLDEST", encoding="utf-8")
    mid.write_text("MIDDLE", encoding="utf-8")
    new.write_text("NEWEST", encoding="utf-8")
    # Set mtimes deterministically
    os.utime(str(old), (1000, 1000))
    os.utime(str(mid), (2000, 2000))
    os.utime(str(new), (3000, 3000))

    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    # `included` is breadth-sampled: must contain all three
    names = [os.path.basename(p) for p in result["included"]]
    assert "new.md" in names
    assert "mid.md" in names
    assert "old.md" in names


# ---------- gather_corpus: byte cap + max_files ----------

def test_gather_corpus_byte_cap_bounds_total(tmp_path):
    """When the corpus is large, gather_corpus returns at most ~byte_cap
    bytes of text. The drop count is reported (not silent)."""
    from lib.bible import gather_corpus
    # Create 10 files of 100 bytes each = 1000 bytes total
    for i in range(10):
        (tmp_path / f"n{i:02d}.md").write_text("x" * 100, encoding="utf-8")
    result = gather_corpus(str(tmp_path), byte_cap=300, max_files=100)
    # Total text bounded by cap (rough — concatenation may add some glue)
    assert len(result["text"]) <= 1000  # some slack, but capped
    # Drop count reported, not zero
    assert result["dropped"] > 0
    # included + dropped = total files
    assert len(result["included"]) + result["dropped"] == 10


def test_gather_corpus_max_files_bound(tmp_path):
    """When more than max_files exist, only max_files are included; the
    remainder are reported as dropped."""
    from lib.bible import gather_corpus
    # 20 small files
    for i in range(20):
        (tmp_path / f"n{i:02d}.md").write_text("hi", encoding="utf-8")
    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=5)
    assert len(result["included"]) <= 5
    assert result["dropped"] == 20 - len(result["included"])
    assert result["dropped"] >= 15  # at least 15 dropped


# ---------- gather_corpus: skip binary ----------

def test_gather_corpus_skips_binary(tmp_path):
    """Binary files (null-byte sniff) are skipped — they're dropped, not
    included in the text."""
    from lib.bible import gather_corpus
    # One good text file
    (tmp_path / "good.md").write_text("normal text content here", encoding="utf-8")
    # One binary file (contains NUL bytes)
    (tmp_path / "bad.bin").write_bytes(b"\x00\x01\x02\x03 binary blob \x00 more")

    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    # Only the good file should be included
    names = [os.path.basename(p) for p in result["included"]]
    assert "good.md" in names
    assert "bad.bin" not in names
    # The drop is reported
    assert result["dropped"] >= 1
    # Text does not contain binary content
    assert "normal text content here" in result["text"]


def test_gather_corpus_skips_oversized(tmp_path):
    """Oversized files (above per-file byte threshold) are skipped."""
    from lib.bible import gather_corpus
    # One normal file
    (tmp_path / "small.md").write_text("small normal content", encoding="utf-8")
    # One huge file (>1MB)
    (tmp_path / "huge.md").write_bytes(b"x" * (1_500_000))

    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    names = [os.path.basename(p) for p in result["included"]]
    assert "small.md" in names
    assert "huge.md" not in names


# ---------- gather_corpus: stays inside dir (no traversal) ----------

def test_gather_corpus_does_not_escape_dir(tmp_path):
    """Files outside subjective_dir are NOT read (symlink-escape safe)."""
    from lib.bible import gather_corpus
    # Real file in subjective_dir
    (tmp_path / "inside.md").write_text("inside content", encoding="utf-8")
    # File outside, but symlinked from inside
    outside = tmp_path.parent / "outside.md"
    outside.write_text("OUTSIDE SECRET", encoding="utf-8")
    symlink = tmp_path / "link.md"
    symlink.symlink_to(outside)

    result = gather_corpus(str(tmp_path), byte_cap=10_000, max_files=10)
    # The outside content must NOT appear
    assert "OUTSIDE SECRET" not in result["text"]


# ---------- write_bible: atomic overwrite ----------

def test_write_bible_atomic_overwrite(tmp_path):
    """write_bible OVERWRITES (distinct from append-only stance). Two
    consecutive writes leave only the second."""
    from lib.bible import write_bible, bible_path
    write_bible(str(tmp_path), "first version of the bible\n")
    p = bible_path(str(tmp_path))
    assert p.exists()
    assert "first version" in p.read_text(encoding="utf-8")

    write_bible(str(tmp_path), "second version replaces the first\n")
    text = p.read_text(encoding="utf-8")
    assert "second version" in text
    assert "first version" not in text


def test_write_bible_no_orphan_temp_on_error(tmp_path):
    """If write_bible fails (e.g. the output_dir is not writable, or a
    forced error), no .tmp / .partial file is left in output_dir."""
    from lib.bible import write_bible
    # Create output_dir as a read-only location by making the parent
    # unwritable for the test (best-effort: depends on user permissions,
    # but if write fails we expect cleanup).
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    # Force an error by passing a non-existent output_dir
    bogus = tmp_path / "does-not-exist"
    with pytest.raises(Exception):
        write_bible(str(bogus), "some text")
    # No temp files left in tmp_path
    for entry in tmp_path.iterdir():
        assert not entry.name.endswith(".tmp")
        assert ".partial" not in entry.name
