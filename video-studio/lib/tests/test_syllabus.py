"""Syllabus + cursor data model tests (TDD fail-first contract).

Written before lib/syllabus.py exists; collection MUST fail at import
time with ModuleNotFoundError — that red is the Task 1-tests success signal.

Covers:
  - load_syllabus: parse YAML, fail-closed on missing required keys
  - load_cursor: parse cursor YAML
  - next_pending: first pending episode from cursor position; None when all done
  - advance: mark current done + move cursor to next pending (pure-function style)
  - derive_seq_slug: global sequence number + ASCII/kebab slug
  - Edge cases: bad YAML, empty modules, no pending, cursor past end
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

# Ensure plugin root (parent of lib/) is on sys.path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# TDD fail-first: this import raises ModuleNotFoundError until Task 1-impl lands
from lib.syllabus import (  # noqa: E402
    load_syllabus,
    load_cursor,
    save_syllabus,
    save_cursor,
    next_pending,
    advance,
    derive_seq_slug,
)

# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------

SYLLABUS_YAML = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT作为跑者有氧能力指标的含义
            status: done
          - title: VDOT配速表解读
            doc_refs: ["01-daniels-vdot-system.md#section-2", "vdot_pace_data.json"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 学会用VDOT对应训练配速区间
            status: pending
      - name: 训练区间
        style: tutorial
        episodes:
          - title: 五区训练强度
            doc_refs: ["02-training-zones.md#section-1"]
            source: UESCA
            verify_status: "⚠️"
            learning_goal: 区分5个训练心率/配速区间
            status: pending
          - title: E跑与M跑区别
            doc_refs: ["02-training-zones.md#section-2"]
            source: UESCA
            verify_status: "✅"
            learning_goal: 掌握轻松跑与马拉松配速跑的核心差异
            status: pending
""")

CURSOR_YAML = textwrap.dedent("""\
    book: running-science-v1
    module_idx: 0
    episode_idx: 1
""")

ALL_DONE_SYLLABUS_YAML = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT作为跑者有氧能力指标的含义
            status: done
          - title: VDOT配速表解读
            doc_refs: ["01-daniels-vdot-system.md#section-2"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 学会用VDOT对应训练配速区间
            status: done
""")

BAD_YAML = "book: [unclosed bracket\n  - bad"

MISSING_KEY_YAML = textwrap.dedent("""\
    book: running-science-v1
    modules:
      - name: VDOT基础
        episodes:
          - title: 什么是VDOT
            doc_refs: []
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT含义
            # status key intentionally missing
""")

MISSING_BOOK_YAML = textwrap.dedent("""\
    character_ref: assets/character/coach_ref.jpg
    modules: []
""")


# ---------------------------------------------------------------------------
# Tests: load_syllabus
# ---------------------------------------------------------------------------

def test_load_syllabus_basic(tmp_path: Path) -> None:
    """load_syllabus parses a valid YAML into a Syllabus with nested structure."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)
    s = load_syllabus(f)
    assert s.book == "running-science-v1"
    assert s.character_ref == "assets/character/coach_ref.jpg"
    assert len(s.modules) == 2
    m0 = s.modules[0]
    assert m0.name == "VDOT基础"
    assert len(m0.episodes) == 2
    ep0 = m0.episodes[0]
    assert ep0.title == "什么是VDOT"
    assert ep0.status == "done"
    ep1 = m0.episodes[1]
    assert ep1.status == "pending"
    assert "vdot_pace_data.json" in ep1.doc_refs


def test_load_syllabus_episode_fields(tmp_path: Path) -> None:
    """Each episode carries doc_refs, source, verify_status, learning_goal."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)
    s = load_syllabus(f)
    ep = s.modules[1].episodes[0]
    assert ep.source == "UESCA"
    assert ep.verify_status == "⚠️"
    assert ep.learning_goal != ""


def test_load_syllabus_missing_book(tmp_path: Path) -> None:
    """Missing 'book' key raises a named error, not a bare KeyError."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(MISSING_BOOK_YAML)
    with pytest.raises((KeyError, ValueError, TypeError)) as exc_info:
        load_syllabus(f)
    assert exc_info.value is not None


def test_load_syllabus_missing_episode_status(tmp_path: Path) -> None:
    """Episode missing 'status' key raises a named error."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(MISSING_KEY_YAML)
    with pytest.raises((KeyError, ValueError, TypeError)):
        load_syllabus(f)


def test_load_syllabus_bad_yaml(tmp_path: Path) -> None:
    """Malformed YAML raises an error (yaml.YAMLError or equivalent)."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(BAD_YAML)
    with pytest.raises(Exception):
        load_syllabus(f)


def test_load_syllabus_missing_file(tmp_path: Path) -> None:
    """Missing file raises FileNotFoundError or equivalent."""
    with pytest.raises((FileNotFoundError, OSError)):
        load_syllabus(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# Tests: load_cursor
# ---------------------------------------------------------------------------

def test_load_cursor_basic(tmp_path: Path) -> None:
    """load_cursor parses cursor YAML into a Cursor with book/module_idx/episode_idx."""
    f = tmp_path / "cursor.yaml"
    f.write_text(CURSOR_YAML)
    c = load_cursor(f)
    assert c.book == "running-science-v1"
    assert c.module_idx == 0
    assert c.episode_idx == 1


def test_load_cursor_missing_file(tmp_path: Path) -> None:
    """Missing cursor file raises FileNotFoundError or equivalent."""
    with pytest.raises((FileNotFoundError, OSError)):
        load_cursor(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# Tests: next_pending
# ---------------------------------------------------------------------------

def test_next_pending_returns_first_pending_from_cursor(tmp_path: Path) -> None:
    """next_pending returns the first pending episode at or after cursor position."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # module_idx=0, episode_idx=1 → ep[0][1] is pending
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "VDOT配速表解读"
    assert episode.status == "pending"


def test_next_pending_skips_done_wraps_to_next_module(tmp_path: Path) -> None:
    """next_pending advances past done episodes into the next module."""
    # cursor at module=0, ep=1 (pending). After that module=1 ep=0 is also pending.
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    # cursor at module=1, episode_idx=0 should return module 1 ep 0
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 1
        episode_idx: 0
    """)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "五区训练强度"


def test_next_pending_returns_none_when_all_done(tmp_path: Path) -> None:
    """next_pending returns None when all episodes from cursor onward are done."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(ALL_DONE_SYLLABUS_YAML)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    assert next_pending(s, c) is None


def test_next_pending_returns_none_cursor_past_end(tmp_path: Path) -> None:
    """next_pending returns None when cursor is past all episodes."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(ALL_DONE_SYLLABUS_YAML)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 99
        episode_idx: 99
    """)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    assert next_pending(s, c) is None


# ---------------------------------------------------------------------------
# Tests: advance
# ---------------------------------------------------------------------------

def test_advance_marks_current_done_moves_cursor(tmp_path: Path) -> None:
    """advance marks the current pending episode done and moves cursor to next pending."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # pointing at module=0, ep=1 (pending)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)
    # The episode that was pending is now done
    assert s2.modules[0].episodes[1].status == "done"
    # Cursor moved to next pending: module=1, ep=0
    assert c2.module_idx == 1
    assert c2.episode_idx == 0


def test_advance_does_not_mutate_original(tmp_path: Path) -> None:
    """advance returns new objects; originals are unchanged (pure-function or copy)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)
    # Original syllabus episode still pending
    assert s.modules[0].episodes[1].status == "pending"
    # Original cursor unchanged
    assert c.module_idx == 0
    assert c.episode_idx == 1


def test_advance_when_all_episodes_done_after(tmp_path: Path) -> None:
    """advance on last pending episode: marks done, cursor at end (module/ep past last)."""
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: assets/character/coach_ref.jpg
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT含义
                status: pending
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)
    assert s2.modules[0].episodes[0].status == "done"
    # next_pending should be None after advance
    assert next_pending(s2, c2) is None


# ---------------------------------------------------------------------------
# Tests: derive_seq_slug
# ---------------------------------------------------------------------------

def test_derive_seq_slug_simple_ascii(tmp_path: Path) -> None:
    """derive_seq_slug returns (seq, slug): seq is 1-based global, slug is ASCII kebab."""
    s_file = tmp_path / "syllabus.yaml"
    # Build a simple 1-module 1-episode syllabus
    simple_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: assets/character/coach_ref.jpg
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: What Is VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: Understand VDOT
                status: pending
    """)
    s_file.write_text(simple_yaml)
    s = load_syllabus(s_file)
    ep = s.modules[0].episodes[0]
    seq, slug = derive_seq_slug(s, module_idx=0, episode_idx=0)
    assert seq == "001"
    assert slug == "what-is-vdot"


def test_derive_seq_slug_global_sequence(tmp_path: Path) -> None:
    """seq counts all episodes globally across modules (not per-module)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    # module=0 ep=0 → global #1, module=0 ep=1 → #2, module=1 ep=0 → #3
    seq0, _ = derive_seq_slug(s, module_idx=0, episode_idx=0)
    seq1, _ = derive_seq_slug(s, module_idx=0, episode_idx=1)
    seq2, _ = derive_seq_slug(s, module_idx=1, episode_idx=0)
    assert seq0 == "001"
    assert seq1 == "002"
    assert seq2 == "003"


def test_derive_seq_slug_chinese_title(tmp_path: Path) -> None:
    """slug for a Chinese title is non-empty ASCII-safe (e.g. romanized or transliterated)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    # 什么是VDOT → some ASCII-safe slug
    _, slug = derive_seq_slug(s, module_idx=0, episode_idx=0)
    # Must be non-empty, no spaces, no forbidden path chars
    assert slug
    assert " " not in slug
    assert "/" not in slug
    assert "\\" not in slug


def test_derive_seq_slug_second_episode(tmp_path: Path) -> None:
    """Second episode in module 0 has seq=002."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    seq, slug = derive_seq_slug(s, module_idx=0, episode_idx=1)
    assert seq == "002"
    assert slug  # non-empty


# ---------------------------------------------------------------------------
# Tests: _slugify CJK pinyin behavior
# ---------------------------------------------------------------------------

def test_slug_chinese_title_pinyin() -> None:
    """CJK title produces pinyin slug (not hex) when pypinyin is available."""
    from lib.syllabus import _slugify

    pytest.importorskip("pypinyin")  # skip if pypinyin not installed
    slug = _slugify("坡度配速")
    # Should be hyphen-joined pinyin syllables, all lowercase ASCII
    assert slug
    assert all(c.isascii() for c in slug)
    assert slug not in ("ep",)
    # Must look like pinyin (letters and hyphens only)
    assert re.match(r"^[a-z][a-z-]*[a-z]$", slug), f"Unexpected slug: {slug!r}"


def test_slug_chinese_title_no_hex() -> None:
    """CJK title must not produce a hex slug regardless of pypinyin availability."""
    from lib.syllabus import _slugify

    slug = _slugify("坡度配速")
    # Old behavior was hex: 5761523660d50901 … must not appear
    assert not re.search(r"[0-9a-f]{4}", slug), (
        f"Slug looks like hex-encoded CJK: {slug!r}"
    )


def test_slug_latin_title_unchanged() -> None:
    """Latin title still produces normal kebab slug regardless of pypinyin."""
    from lib.syllabus import _slugify

    slug = _slugify("Running Science 101")
    assert slug == "running-science-101"


def test_slug_fallback_without_pypinyin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When pypinyin is not importable, CJK slug falls back to 'ep' (not hex)."""
    import sys
    import importlib

    # Temporarily hide pypinyin from imports
    original = sys.modules.pop("pypinyin", None)
    # Reload _slugify's module so the import guard fires fresh
    import lib.syllabus as syllabus_mod

    monkeypatch.setitem(sys.modules, "pypinyin", None)  # type: ignore[arg-type]
    try:
        slug = syllabus_mod._slugify("坡度配速")
        assert slug == "ep", f"Expected 'ep' fallback, got {slug!r}"
        # Must not be hex
        assert not re.search(r"[0-9a-f]{4}", slug)
    finally:
        if original is not None:
            sys.modules["pypinyin"] = original
        else:
            sys.modules.pop("pypinyin", None)


# ---------------------------------------------------------------------------
# Tests: promote_draft
# ---------------------------------------------------------------------------

# Valid draft YAML (plain, no fence) — planner output shape
DRAFT_YAML_PLAIN = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT作为跑者有氧能力指标的含义
            status: pending
          - title: VDOT配速表解读
            doc_refs: ["01-daniels-vdot-system.md#section-2"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 学会用VDOT对应训练配速区间
            status: pending
""")

# Same content wrapped in a ```yaml fence (as a planner LLM would output)
DRAFT_YAML_FENCED = "```yaml\n" + DRAFT_YAML_PLAIN + "```\n"

# Draft missing 'modules' key entirely
DRAFT_NO_MODULES = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
""")

# Draft with an episode missing required field 'source'
DRAFT_EPISODE_MISSING_SOURCE = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            verify_status: "✅"
            learning_goal: 理解VDOT含义
            status: pending
""")

# Draft with an episode missing 'verify_status'
DRAFT_EPISODE_MISSING_VERIFY_STATUS = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            learning_goal: 理解VDOT含义
            status: pending
""")

# Draft with an episode missing 'doc_refs'
DRAFT_EPISODE_MISSING_DOC_REFS = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        episodes:
          - title: 什么是VDOT
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT含义
            status: pending
""")


def test_promote_draft_valid_plain(tmp_path: Path) -> None:
    """promote_draft with a plain (no-fence) draft: writes syllabus + cursor(0,0)."""
    from lib.syllabus import promote_draft  # fails until Task 4-impl
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_YAML_PLAIN)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    result = promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    assert result.book == "running-science-v1"
    assert len(result.modules) == 1
    assert result.modules[0].name == "VDOT基础"
    assert syllabus_path.exists()
    assert cursor_path.exists()


def test_promote_draft_strips_yaml_fence(tmp_path: Path) -> None:
    """promote_draft tolerates a ```yaml ... ``` fence around the draft content."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft_fenced.yaml"
    draft.write_text(DRAFT_YAML_FENCED)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    result = promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    assert result.book == "running-science-v1"
    assert len(result.modules) == 1


def test_promote_draft_cursor_initialized_at_zero(tmp_path: Path) -> None:
    """promote_draft initializes cursor to (book, module_idx=0, episode_idx=0)."""
    from lib.syllabus import promote_draft
    from lib.syllabus import load_cursor
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_YAML_PLAIN)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    cursor = load_cursor(cursor_path)
    assert cursor.book == "running-science-v1"
    assert cursor.module_idx == 0
    assert cursor.episode_idx == 0


def test_promote_draft_roundtrip(tmp_path: Path) -> None:
    """After promote_draft, load_syllabus + load_cursor can round-trip correctly."""
    from lib.syllabus import promote_draft, load_syllabus, load_cursor
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_YAML_PLAIN)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    s = load_syllabus(syllabus_path)
    c = load_cursor(cursor_path)
    assert s.book == "running-science-v1"
    assert s.modules[0].episodes[0].source == "Daniels"
    assert c.module_idx == 0
    assert c.episode_idx == 0


def test_promote_draft_missing_modules_raises(tmp_path: Path) -> None:
    """promote_draft raises a named error when draft has no modules; no files written."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft_no_modules.yaml"
    draft.write_text(DRAFT_NO_MODULES)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    with pytest.raises((KeyError, ValueError, Exception)):
        promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    # No half-written files
    assert not syllabus_path.exists()
    assert not cursor_path.exists()


def test_promote_draft_episode_missing_source_raises(tmp_path: Path) -> None:
    """promote_draft raises a named error when an episode is missing 'source'."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_EPISODE_MISSING_SOURCE)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    with pytest.raises(Exception):
        promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    assert not syllabus_path.exists()
    assert not cursor_path.exists()


def test_promote_draft_episode_missing_verify_status_raises(tmp_path: Path) -> None:
    """promote_draft raises a named error when an episode is missing 'verify_status'."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_EPISODE_MISSING_VERIFY_STATUS)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    with pytest.raises(Exception):
        promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    assert not syllabus_path.exists()
    assert not cursor_path.exists()


def test_promote_draft_episode_missing_doc_refs_raises(tmp_path: Path) -> None:
    """promote_draft raises a named error when an episode is missing 'doc_refs'."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_EPISODE_MISSING_DOC_REFS)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"

    with pytest.raises(Exception):
        promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")

    assert not syllabus_path.exists()
    assert not cursor_path.exists()


# ---------------------------------------------------------------------------
# Tests: Selection.style transparency (Task 2)
# ---------------------------------------------------------------------------

def test_select_next_style_from_module(tmp_path: Path) -> None:
    """select_next propagates Module.style into Selection.style."""
    from lib.syllabus import select_next
    import types

    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            style: 简笔日漫
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT含义
                status: pending
    """)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)

    cfg = types.SimpleNamespace(
        syllabus_path=str(s_file),
        cursor_path=str(c_file),
    )
    sel = select_next(cfg)
    assert sel is not None
    assert sel.style == "简笔日漫"


def test_select_next_style_empty_when_module_has_no_style(tmp_path: Path) -> None:
    """Module with no style produces Selection.style == '', which resolves to SERIES_LOOK."""
    from lib.syllabus import select_next
    from lib import grade
    import types

    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT含义
                status: pending
    """)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)

    cfg = types.SimpleNamespace(
        syllabus_path=str(s_file),
        cursor_path=str(c_file),
    )
    sel = select_next(cfg)
    assert sel is not None
    assert sel.style == ""
    # Empty style should resolve to SERIES_LOOK (冷蓝晨光)
    assert grade.resolve_look(sel.style) == grade.SERIES_LOOK


def test_next_pending_returns_module_in_tuple(tmp_path: Path) -> None:
    """next_pending 4-tuple: fourth element is the Module containing the episode."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # module_idx=0, episode_idx=1
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert module.name == "VDOT基础"
    assert module.style == "educational"


# ---------------------------------------------------------------------------
# Tests: save round-trip
# ---------------------------------------------------------------------------

def test_save_load_syllabus_roundtrip(tmp_path: Path) -> None:
    """save_syllabus then load_syllabus preserves book, module names, episode status."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    out_file = tmp_path / "out_syllabus.yaml"
    save_syllabus(s, out_file)
    s2 = load_syllabus(out_file)
    assert s2.book == s.book
    assert len(s2.modules) == len(s.modules)
    assert s2.modules[0].episodes[0].status == "done"
    assert s2.modules[0].episodes[1].status == "pending"


def test_save_load_cursor_roundtrip(tmp_path: Path) -> None:
    """save_cursor then load_cursor preserves all fields."""
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)
    c = load_cursor(c_file)
    out_file = tmp_path / "out_cursor.yaml"
    save_cursor(c, out_file)
    c2 = load_cursor(out_file)
    assert c2.book == c.book
    assert c2.module_idx == c.module_idx
    assert c2.episode_idx == c.episode_idx
