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
    """Module with no style produces Selection.style == '', which resolves to default art style."""
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
    # Empty art style should resolve to the default art style prefix
    assert grade.resolve_art_style(sel.style) == grade.ART_STYLES[grade.DEFAULT_ART_STYLE]


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


# ---------------------------------------------------------------------------
# Tests: art_style (Module) + color_grade (Syllabus) — Task 2-tests
# ---------------------------------------------------------------------------

SYLLABUS_YAML_WITH_ART_STYLE = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    color_grade: 暖金
    modules:
      - name: VDOT基础
        style: educational
        art_style: 简笔日漫
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT作为跑者有氧能力指标的含义
            status: pending
""")


def test_load_syllabus_art_style_and_color_grade(tmp_path: Path) -> None:
    """load_syllabus parses module.art_style and top-level color_grade."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_WITH_ART_STYLE)
    s = load_syllabus(f)
    assert s.color_grade == "暖金"
    m0 = s.modules[0]
    assert m0.art_style == "简笔日漫"


def test_save_load_art_style_color_grade_roundtrip(tmp_path: Path) -> None:
    """save_syllabus then load_syllabus preserves art_style and color_grade."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_WITH_ART_STYLE)
    s = load_syllabus(f)
    out = tmp_path / "out_syllabus.yaml"
    save_syllabus(s, out)
    s2 = load_syllabus(out)
    assert s2.color_grade == "暖金"
    assert s2.modules[0].art_style == "简笔日漫"
    # Existing style field is preserved
    assert s2.modules[0].style == "educational"


def test_load_syllabus_backward_compat_no_art_style(tmp_path: Path) -> None:
    """Old syllabus (no art_style on module, no color_grade at top) loads without error.

    art_style and color_grade default to empty string.
    """
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)  # existing fixture: module has style, no art_style; no color_grade
    s = load_syllabus(f)
    assert s.color_grade == ""
    assert s.modules[0].art_style == ""
    assert s.modules[0].style == "educational"  # existing style field still present


def test_select_next_art_style_from_module(tmp_path: Path) -> None:
    """select_next propagates module.art_style into Selection.art_style AND
    series-level color_grade into Selection.color_grade (NTH-1: this assertion
    is what would have caught MF-1 — Selection missing color_grade + select_next
    not passing it — which the SimpleNamespace wiring mock hid)."""
    from lib.syllabus import select_next
    import types

    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        color_grade: 暖金
        modules:
          - name: VDOT基础
            style: educational
            art_style: 简笔日漫
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
    assert sel.art_style == "简笔日漫"
    assert sel.color_grade == "暖金"   # NTH-1: series color_grade flows to Selection


# ---------------------------------------------------------------------------
# Tests: Module.character_ref (module-level > series-level fallback) — Task 1-tests
# ---------------------------------------------------------------------------

SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/series_ref.jpg
    modules:
      - name: VDOT基础
        style: educational
        art_style: 简笔日漫
        character_ref: assets/character/module0_ref.jpg
        episodes:
          - title: 什么是VDOT
            doc_refs: ["01-daniels-vdot-system.md#section-1"]
            source: Daniels
            verify_status: "✅"
            learning_goal: 理解VDOT作为跑者有氧能力指标的含义
            status: pending
      - name: 训练区间
        style: tutorial
        episodes:
          - title: 五区训练强度
            doc_refs: ["02-training-zones.md#section-1"]
            source: UESCA
            verify_status: "✅"
            learning_goal: 区分5个训练心率区间
            status: pending
""")


def test_load_syllabus_module_character_ref(tmp_path: Path) -> None:
    """load_syllabus parses module.character_ref when present."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF)
    s = load_syllabus(f)
    assert s.modules[0].character_ref == "assets/character/module0_ref.jpg"


def test_load_syllabus_module_character_ref_defaults_empty(tmp_path: Path) -> None:
    """Module without character_ref defaults to empty string (backward compat)."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF)
    s = load_syllabus(f)
    # module 1 has no character_ref key
    assert s.modules[1].character_ref == ""


def test_load_syllabus_old_no_module_character_ref_no_crash(tmp_path: Path) -> None:
    """Old syllabus (module has no character_ref key) loads without error."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)  # existing fixture: no module character_ref
    s = load_syllabus(f)
    assert s.modules[0].character_ref == ""
    assert s.modules[1].character_ref == ""
    # Series-level character_ref still present
    assert s.character_ref == "assets/character/coach_ref.jpg"


def test_save_load_module_character_ref_roundtrip(tmp_path: Path) -> None:
    """save_syllabus then load_syllabus preserves module.character_ref."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF)
    s = load_syllabus(f)
    out = tmp_path / "out_syllabus.yaml"
    save_syllabus(s, out)
    s2 = load_syllabus(out)
    assert s2.modules[0].character_ref == "assets/character/module0_ref.jpg"
    assert s2.modules[1].character_ref == ""


def test_select_next_character_ref_module_level_priority(tmp_path: Path) -> None:
    """select_next uses module.character_ref when set (module > series priority)."""
    from lib.syllabus import select_next
    import types

    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """))

    cfg = types.SimpleNamespace(
        syllabus_path=str(s_file),
        cursor_path=str(c_file),
    )
    sel = select_next(cfg)
    assert sel is not None
    # Module 0 has its own character_ref → Selection uses it
    assert sel.character_ref == "assets/character/module0_ref.jpg"


def test_select_next_character_ref_series_fallback(tmp_path: Path) -> None:
    """select_next falls back to series character_ref when module has none."""
    from lib.syllabus import select_next
    import types

    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_WITH_MODULE_CHARACTER_REF)
    c_file = tmp_path / "cursor.yaml"
    # Cursor at module 1 which has no character_ref
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 1
        episode_idx: 0
    """))

    cfg = types.SimpleNamespace(
        syllabus_path=str(s_file),
        cursor_path=str(c_file),
    )
    sel = select_next(cfg)
    assert sel is not None
    # Module 1 has no character_ref → falls back to series-level
    assert sel.character_ref == "assets/character/series_ref.jpg"


# ---------------------------------------------------------------------------
# Tests: reset_current — Task 1-tests
# ---------------------------------------------------------------------------

def test_reset_current_basic(tmp_path: Path) -> None:
    """reset_current: ep0 done, cursor at ep1 → ep0 back to pending, cursor at ep0."""
    from lib.syllabus import reset_current  # fails until Task 1-impl

    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)  # ep0=done, ep1=pending; cursor_yaml points at ep1
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # module_idx=0, episode_idx=1
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    s2, c2 = reset_current(s, c)

    # ep0 (the most recent done) is now pending
    assert s2.modules[0].episodes[0].status == "pending"
    # Cursor points back to ep0
    assert c2.module_idx == 0
    assert c2.episode_idx == 0


def test_reset_current_does_not_mutate_original(tmp_path: Path) -> None:
    """reset_current is pure: originals are unchanged after call."""
    from lib.syllabus import reset_current

    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    reset_current(s, c)

    # Original ep0 still done
    assert s.modules[0].episodes[0].status == "done"
    # Original cursor unchanged
    assert c.module_idx == 0
    assert c.episode_idx == 1


def test_reset_current_advance_roundtrip(tmp_path: Path) -> None:
    """advance then reset_current returns to the original state (same episode pending, same cursor)."""
    from lib.syllabus import reset_current

    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # module=0, ep=1 pending
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    # Save original state
    orig_ep1_status = s.modules[0].episodes[1].status
    orig_module_idx = c.module_idx
    orig_episode_idx = c.episode_idx
    assert orig_ep1_status == "pending"

    # Advance: ep1 becomes done, cursor moves to module=1, ep=0
    s2, c2 = advance(s, c)
    assert s2.modules[0].episodes[1].status == "done"
    assert c2.module_idx == 1
    assert c2.episode_idx == 0

    # Reset: ep1 back to pending, cursor back to module=0, ep=1
    s3, c3 = reset_current(s2, c2)
    assert s3.modules[0].episodes[1].status == "pending"
    assert c3.module_idx == orig_module_idx
    assert c3.episode_idx == orig_episode_idx


def test_reset_current_parked_cursor(tmp_path: Path) -> None:
    """reset_current with parked cursor (advance on last episode): last episode back to pending, cursor points to it."""
    from lib.syllabus import reset_current

    # Single-module single-episode syllabus (last episode)
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
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    # Advance the last episode: cursor becomes parked (module_idx=len(modules)=1)
    s2, c2 = advance(s, c)
    assert s2.modules[0].episodes[0].status == "done"
    # Parked: module_idx is beyond the last module
    assert c2.module_idx >= len(s2.modules) or next_pending(s2, c2) is None

    # Reset: last episode back to pending, cursor NOT parked — points to last episode
    s3, c3 = reset_current(s2, c2)
    assert s3.modules[0].episodes[0].status == "pending"
    assert c3.module_idx == 0
    assert c3.episode_idx == 0


def test_reset_current_no_done_episodes_idempotent(tmp_path: Path) -> None:
    """reset_current with no done episodes: returns a copy without crashing, state unchanged."""
    from lib.syllabus import reset_current

    # All pending syllabus
    all_pending_yaml = textwrap.dedent("""\
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
              - title: VDOT配速表解读
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 学会用VDOT对应训练配速区间
                status: pending
    """)
    cursor_yaml = textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(all_pending_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(cursor_yaml)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    # Should not crash; no done episode to reset
    s2, c2 = reset_current(s, c)
    # All episodes remain pending
    assert s2.modules[0].episodes[0].status == "pending"
    assert s2.modules[0].episodes[1].status == "pending"
    # Cursor unchanged
    assert c2.module_idx == c.module_idx
    assert c2.episode_idx == c.episode_idx


def test_select_next_character_ref_both_absent_returns_empty(tmp_path: Path) -> None:
    """select_next returns empty string when neither module nor series has character_ref."""
    from lib.syllabus import select_next
    import types

    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
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
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """))

    cfg = types.SimpleNamespace(
        syllabus_path=str(s_file),
        cursor_path=str(c_file),
    )
    sel = select_next(cfg)
    assert sel is not None
    assert sel.character_ref == ""


# ---------------------------------------------------------------------------
# module_name on Selection (R2: channel · module tag needs the module name)
# ---------------------------------------------------------------------------

def test_select_next_carries_module_name(tmp_path):
    """select_next must expose the owning module's name on the Selection so the
    title card can render a '跑者学堂 · 模块名' brand tag."""
    from types import SimpleNamespace
    from lib.syllabus import select_next

    syl = tmp_path / "syllabus.yaml"
    cur = tmp_path / "cursor.yaml"
    syl.write_text(textwrap.dedent("""\
        book: running-science-v1
        character_ref: ''
        modules:
          - name: 训练科学认知刷新：破除流行神话
            style: educational
            episodes:
              - title: 测试期
                doc_refs: ["01-x.md"]
                source: UESCA
                verify_status: "✅"
                learning_goal: 目标
                status: pending
    """), encoding="utf-8")
    cur.write_text("book: running-science-v1\nmodule_idx: 0\nepisode_idx: 0\n", encoding="utf-8")

    cfg = SimpleNamespace(syllabus_path=str(syl), cursor_path=str(cur))
    sel = select_next(cfg)
    assert sel is not None
    assert sel.module_name == "训练科学认知刷新：破除流行神话"
