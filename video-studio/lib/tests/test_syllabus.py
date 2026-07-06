"""Syllabus + cursor data model tests (TDD fail-first contract).

Written before lib/syllabus.py exists; collection MUST fail at import
time with ModuleNotFoundError — that red is the Task 1-tests success signal.

Covers:
  - load_syllabus: parse YAML, fail-closed on missing required keys
  - load_cursor: parse cursor YAML
  - next_pending: first pending episode from cursor position; None when all done
  - advance: mark current done + move cursor to next pending (pure-function style)
  - derive_seq_slug: global sequence number + CJK-preserving filesystem-safe slug
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
            status: locked
      - name: 训练区间
        style: tutorial
        episodes:
          - title: 五区训练强度
            doc_refs: ["02-training-zones.md#section-1"]
            source: UESCA
            verify_status: "⚠️"
            learning_goal: 区分5个训练心率/配速区间
            status: locked
          - title: E跑与M跑区别
            doc_refs: ["02-training-zones.md#section-2"]
            source: UESCA
            verify_status: "✅"
            learning_goal: 掌握轻松跑与马拉松配速跑的核心差异
            status: locked
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
    assert ep1.status == "locked"
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
    assert episode.status == "locked"


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
    # Original syllabus episode still locked (unconsumed)
    assert s.modules[0].episodes[1].status == "locked"
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
                status: locked
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
    """derive_seq_slug returns (seq, slug): seq is 1-based global; a Latin title
    keeps its words (only unsafe chars become hyphens), case preserved."""
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
    assert slug == "What-Is-VDOT"


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
    """slug for a Chinese title PRESERVES the Chinese (no romanization/hex) while
    staying filesystem-safe (no spaces or path separators)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    # 什么是VDOT → Chinese kept verbatim
    _, slug = derive_seq_slug(s, module_idx=0, episode_idx=0)
    assert slug == "什么是VDOT"
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
# Tests: CJK-preserving naming (derive_seq_slug via episode._sanitize_component)
# ---------------------------------------------------------------------------

def test_slug_chinese_title_preserved(tmp_path: Path) -> None:
    """A CJK title is preserved verbatim (no pinyin, no hex, no 'ep' fallback)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)
    s = load_syllabus(s_file)
    # module 0 ep 1 title = VDOT配速表解读
    _, slug = derive_seq_slug(s, module_idx=0, episode_idx=1)
    assert slug == "VDOT配速表解读"
    assert not re.search(r"[0-9a-f]{4}", slug)  # not hex-encoded
    assert slug != "ep"


def test_slug_punctuation_collapsed(tmp_path: Path) -> None:
    """Unsafe chars (slash, punctuation, spaces) collapse to single hyphens,
    while CJK is kept — e.g. '80/20训练？法则' → '80-20训练-法则'."""
    s_file = tmp_path / "syllabus.yaml"
    yaml_text = textwrap.dedent("""\
        book: b
        character_ref: ""
        modules:
          - name: m
            style: s
            episodes:
              - title: 80/20训练？法则
                doc_refs: []
                source: X
                verify_status: "✅"
                learning_goal: g
                status: pending
    """)
    s_file.write_text(yaml_text)
    s = load_syllabus(s_file)
    _, slug = derive_seq_slug(s, module_idx=0, episode_idx=0)
    assert slug == "80-20训练-法则"
    assert "/" not in slug
    assert "--" not in slug  # runs collapsed


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
                status: locked
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
                status: locked
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
    assert s2.modules[0].episodes[1].status == "locked"


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
                status: locked
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
            status: locked
      - name: 训练区间
        style: tutorial
        episodes:
          - title: 五区训练强度
            doc_refs: ["02-training-zones.md#section-1"]
            source: UESCA
            verify_status: "✅"
            learning_goal: 区分5个训练心率区间
            status: locked
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
    assert s2.modules[0].episodes[0].status == "locked"
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
    assert orig_ep1_status == "locked"

    # Advance: ep1 becomes done, cursor moves to module=1, ep=0
    s2, c2 = advance(s, c)
    assert s2.modules[0].episodes[1].status == "done"
    assert c2.module_idx == 1
    assert c2.episode_idx == 0

    # Reset: ep1 back to pending, cursor back to module=0, ep=1
    s3, c3 = reset_current(s2, c2)
    assert s3.modules[0].episodes[1].status == "locked"
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
                status: locked
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
    assert s3.modules[0].episodes[0].status == "locked"
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
                status: locked
              - title: VDOT配速表解读
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 学会用VDOT对应训练配速区间
                status: locked
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
    assert s2.modules[0].episodes[0].status == "locked"
    assert s2.modules[0].episodes[1].status == "locked"
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
                status: locked
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
                status: locked
    """), encoding="utf-8")
    cur.write_text("book: running-science-v1\nmodule_idx: 0\nepisode_idx: 0\n", encoding="utf-8")

    cfg = SimpleNamespace(syllabus_path=str(syl), cursor_path=str(cur))
    sel = select_next(cfg)
    assert sel is not None
    assert sel.module_name == "训练科学认知刷新：破除流行神话"


# ---------------------------------------------------------------------------
# Tests: Season layer + dual-read backward compat (Task A1)
# ---------------------------------------------------------------------------

# New shape: book → seasons → modules → episodes.
# Season 0: one module with 2 episodes; Season 1: one module with 1 episode.
SYLLABUS_YAML_SEASONS = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    color_grade: 暖金
    seasons:
      - name: 第一季·基础
        style: educational
        art_style: 简笔日漫
        character_ref: assets/character/season0_ref.jpg
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT
                status: done
              - title: VDOT配速表解读
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 用VDOT对应配速
                status: locked
      - name: 第二季·实战
        modules:
          - name: 赛场策略
            style: tutorial
            episodes:
              - title: 起步配速
                doc_refs: []
                source: UESCA
                verify_status: "✅"
                learning_goal: 起步保守后程发力
                status: locked
""")


def test_load_syllabus_legacy_modules_wrapped_into_one_season(tmp_path: Path) -> None:
    """Legacy top-level `modules:` (no seasons) is wrapped into exactly ONE Season,
    with the original modules preserved verbatim; the `.modules` @property still
    flattens for read-only enumerate consumers (visual_ledger / wiring)."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)  # existing fixture: 2 top-level modules
    s = load_syllabus(f)
    # New season layer: one implicit season wrapping the legacy modules
    assert len(s.seasons) == 1
    assert [m.name for m in s.seasons[0].modules] == ["VDOT基础", "训练区间"]
    assert len(s.seasons[0].modules[0].episodes) == 2
    # .modules property still returns the flat module list, unchanged shape
    assert len(s.modules) == 2
    assert s.modules[0].episodes[0].status == "done"
    assert s.modules[0].episodes[1].status == "locked"


def test_load_syllabus_new_seasons_shape(tmp_path: Path) -> None:
    """New `seasons:` shape parses into multiple Season objects; per-season
    optional overrides (style/art_style/character_ref) are read."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_SEASONS)
    s = load_syllabus(f)
    assert len(s.seasons) == 2
    assert s.seasons[0].name == "第一季·基础"
    assert s.seasons[0].art_style == "简笔日漫"
    assert s.seasons[0].character_ref == "assets/character/season0_ref.jpg"
    assert s.seasons[1].name == "第二季·实战"
    assert len(s.seasons[0].modules) == 1
    assert len(s.seasons[1].modules) == 1
    # flat .modules spans both seasons
    assert [m.name for m in s.modules] == ["VDOT基础", "赛场策略"]


def test_save_syllabus_emits_seasons_wrapper_roundtrip(tmp_path: Path) -> None:
    """save_syllabus emits a top-level `seasons:` wrapper (not `modules:`), and a
    save→load roundtrip preserves the season/module/episode structure."""
    import yaml as _yaml
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_SEASONS)
    s = load_syllabus(f)
    out = tmp_path / "out_syllabus.yaml"
    save_syllabus(s, out)
    raw = _yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "seasons" in raw
    assert "modules" not in raw  # top-level modules replaced by the seasons wrapper
    s2 = load_syllabus(out)
    assert len(s2.seasons) == 2
    assert s2.seasons[0].name == "第一季·基础"
    assert s2.seasons[1].modules[0].episodes[0].title == "起步配速"
    assert s2.modules[0].episodes[0].status == "done"


def test_derive_seq_slug_flat_seq_stable_across_seasons(tmp_path: Path) -> None:
    """Global seq stays flat (001, 002, 003…) across season boundaries — a new
    season does NOT reset the counter."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML_SEASONS)
    s = load_syllabus(f)
    # flat modules: idx0 = season0.mod0 (2 eps), idx1 = season1.mod0 (1 ep)
    seq0, _ = derive_seq_slug(s, module_idx=0, episode_idx=0)
    seq1, _ = derive_seq_slug(s, module_idx=0, episode_idx=1)
    seq2, _ = derive_seq_slug(s, module_idx=1, episode_idx=0)
    assert seq0 == "001"
    assert seq1 == "002"
    assert seq2 == "003"


def test_load_syllabus_legacy_backward_compat_zero_change(tmp_path: Path) -> None:
    """A legacy modules-top syllabus loads with ZERO edits and exposes the same
    two modules under seasons[0] — the running-class file keeps working."""
    f = tmp_path / "syllabus.yaml"
    f.write_text(SYLLABUS_YAML)
    s = load_syllabus(f)
    assert len(s.seasons) == 1
    assert s.seasons[0].name  # implicit season carries a name
    assert len(s.seasons[0].modules) == 2
    # series-level character_ref untouched
    assert s.character_ref == "assets/character/coach_ref.jpg"
    # iter_modules yields (season_idx, module_idx, module) triples, flat order
    triples = list(s.iter_modules())
    assert [(si, mi) for si, mi, _ in triples] == [(0, 0), (0, 1)]


# ---------------------------------------------------------------------------
# Tests: Cursor season_idx + triple-loop traversal + cursor backward-compat (A2)
#
# Design contract (see plan Task A2): `module_idx` stays FLAT (index into the
# `.modules` @property, spanning all seasons — forced by the A1 flat-seq test and
# by out-of-scope callers wiring.py / visual_ledger that pass a flat module_idx).
# `season_idx` is a companion field: default 0 when absent (legacy cursor), and
# set by advance / reset_current to the season that owns the current flat module
# (park = len(seasons)). These assertions FAIL pre-impl: Cursor has no season_idx
# field, and advance/reset_current do not populate it.
# ---------------------------------------------------------------------------

# Old cursor: no season_idx key (the real running-class cursor.yaml shape).
OLD_CURSOR_NO_SEASON_YAML = textwrap.dedent("""\
    book: running-science-v1
    module_idx: 1
    episode_idx: 0
""")


def test_load_cursor_defaults_season_idx_zero(tmp_path: Path) -> None:
    """load_cursor on an OLD cursor.yaml (no season_idx) defaults season_idx=0 —
    the real running-class cursor loads unchanged."""
    f = tmp_path / "cursor.yaml"
    f.write_text(OLD_CURSOR_NO_SEASON_YAML)
    c = load_cursor(f)
    assert c.book == "running-science-v1"
    assert c.module_idx == 1
    assert c.episode_idx == 0
    assert c.season_idx == 0  # FAILS pre-impl: Cursor has no season_idx field


def test_load_cursor_reads_explicit_season_idx(tmp_path: Path) -> None:
    """load_cursor reads an explicit season_idx when present."""
    f = tmp_path / "cursor.yaml"
    f.write_text(textwrap.dedent("""\
        book: running-science-v1
        season_idx: 2
        module_idx: 3
        episode_idx: 1
    """))
    c = load_cursor(f)
    assert c.season_idx == 2
    assert c.module_idx == 3
    assert c.episode_idx == 1


def test_save_load_cursor_season_idx_roundtrip(tmp_path: Path) -> None:
    """save_cursor emits season_idx; a save→load roundtrip preserves it."""
    from lib.syllabus import Cursor
    c = Cursor(book="running-science-v1", module_idx=1, episode_idx=0, season_idx=2)
    out = tmp_path / "cursor.yaml"
    save_cursor(c, out)
    # season_idx is serialized into the YAML text
    assert "season_idx" in out.read_text(encoding="utf-8")
    c2 = load_cursor(out)
    assert c2.season_idx == 2
    assert c2.module_idx == 1
    assert c2.episode_idx == 0


def test_old_cursor_backward_compat_next_pending_unchanged(tmp_path: Path) -> None:
    """An old cursor (no season_idx) still resolves the SAME pending episode via
    next_pending — season_idx defaults to 0 and does not alter flat resolution."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)  # legacy modules-top → one implicit season
    c_file = tmp_path / "cursor.yaml"
    # module_idx=0, episode_idx=1 → flat position of the first pending episode
    c_file.write_text(CURSOR_YAML)
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    assert c.season_idx == 0
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "VDOT配速表解读"
    assert seq == "002"


def test_traversal_next_pending_crosses_season_boundary(tmp_path: Path) -> None:
    """next_pending walks the triple (season, module, episode) loop: a cursor in
    season 0 resolves a pending episode that lives in season 1 (flat module 1)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_SEASONS)  # season0: mod0(ep0 done, ep1 pending); season1: mod0(ep0 pending)
    c_file = tmp_path / "cursor.yaml"
    # cursor at flat module 0 / ep 0 (done) → first pending is season0 mod0 ep1
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """))
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "VDOT配速表解读"
    assert seq == "002"


def test_advance_across_season_boundary_sets_season_idx(tmp_path: Path) -> None:
    """advance from the last pending episode of season 0 lands the cursor in
    season 1: season_idx becomes 1, module_idx is the FLAT index (1), and the
    flat global seq stays continuous (season 1's episode is seq 003)."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_SEASONS)
    c_file = tmp_path / "cursor.yaml"
    # cursor at flat module 0 / ep 1 (season0's last pending)
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 1
    """))
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)
    # season0 mod0 ep1 now done
    assert s2.modules[0].episodes[1].status == "done"
    # cursor moved into season 1 (flat module 1)
    assert c2.season_idx == 1          # FAILS pre-impl: no season_idx populated
    assert c2.module_idx == 1
    assert c2.episode_idx == 0
    # flat global seq unbroken across the season boundary
    seq, _ = derive_seq_slug(s2, module_idx=1, episode_idx=0)
    assert seq == "003"


def test_advance_park_sets_season_idx_past_last_season(tmp_path: Path) -> None:
    """advance on the very last episode parks the cursor past the end: module_idx
    = len(modules) AND season_idx = len(seasons)."""
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT含义
                status: locked
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """))
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)
    assert s2.modules[0].episodes[0].status == "done"
    assert c2.module_idx == len(s2.modules)      # parked past last module (flat)
    assert c2.season_idx == len(s2.seasons)      # FAILS pre-impl: parked past last season
    assert next_pending(s2, c2) is None


def test_reset_current_across_season_boundary_restores_season_idx(tmp_path: Path) -> None:
    """advance into season 1 then reset_current rewinds the done episode back to
    pending AND moves the cursor's season_idx back to 0."""
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_SEASONS)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 1
    """))
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    # advance: season0 mod0 ep1 → done, cursor into season 1
    s2, c2 = advance(s, c)
    assert c2.season_idx == 1
    # reset: rewind the most-recent done episode; cursor back into season 0
    from lib.syllabus import reset_current
    s3, c3 = reset_current(s2, c2)
    assert s3.modules[0].episodes[1].status == "locked"
    assert c3.season_idx == 0                     # FAILS pre-impl: season_idx not restored
    assert c3.module_idx == 0
    assert c3.episode_idx == 1


def test_reset_current_parked_cursor_sets_season_idx(tmp_path: Path) -> None:
    """reset_current from a parked cursor (season_idx=len(seasons)) restores a
    valid in-range season_idx pointing at the rewound episode."""
    from lib.syllabus import reset_current
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: 理解VDOT含义
                status: locked
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 0
        episode_idx: 0
    """))
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    s2, c2 = advance(s, c)  # parked: season_idx = len(seasons)
    assert c2.season_idx == len(s2.seasons)
    s3, c3 = reset_current(s2, c2)
    assert s3.modules[0].episodes[0].status == "locked"
    assert c3.season_idx == 0
    assert c3.module_idx == 0
    assert c3.episode_idx == 0


def test_promote_draft_initializes_cursor_season_idx_zero(tmp_path: Path) -> None:
    """promote_draft initializes the cursor with season_idx=0."""
    from lib.syllabus import promote_draft
    draft = tmp_path / "draft.yaml"
    draft.write_text(DRAFT_YAML_PLAIN)
    syllabus_path = tmp_path / "syllabus.yaml"
    cursor_path = tmp_path / "cursor.yaml"
    promote_draft(draft, syllabus_path, cursor_path, book="running-science-v1")
    c = load_cursor(cursor_path)
    assert c.season_idx == 0
    assert c.module_idx == 0
    assert c.episode_idx == 0


def test_select_next_carries_season_name(tmp_path: Path) -> None:
    """select_next exposes the owning SEASON's name on the Selection (companion to
    module_name), derived via the flat-module → season mapping."""
    from types import SimpleNamespace
    from lib.syllabus import select_next
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_SEASONS)
    c_file = tmp_path / "cursor.yaml"
    # cursor at flat module 1 (season 1)'s pending episode
    c_file.write_text(textwrap.dedent("""\
        book: running-science-v1
        module_idx: 1
        episode_idx: 0
    """))
    cfg = SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))
    sel = select_next(cfg)
    assert sel is not None
    assert sel.season_name == "第二季·实战"   # FAILS pre-impl: Selection has no season_name


# ---------------------------------------------------------------------------
# Tests: locked state + consume-gate flip (Task A3)
#
# Lifecycle is pending → locked → done. The consume gate (next_pending / advance /
# select_next) only picks `locked` episodes; a `pending` planner draft is skipped
# until the user locks it. lock_syllabus / lock_season flip pending → locked.
# reset_current rewinds a done episode back to `locked` (keeps the lock — MR-2),
# so a rewound episode is immediately re-selectable.
# ---------------------------------------------------------------------------

def test_next_pending_skips_pending_draft(tmp_path: Path) -> None:
    """Consume gate is `locked`-only: a `pending` draft episode is NOT selected.

    ep0=done, ep1=pending, cursor at ep1 → next_pending returns None (the pending
    draft is skipped, not consumed)."""
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 什么是VDOT
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g0
                status: done
              - title: VDOT配速表解读
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g1
                status: pending
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text("book: running-science-v1\nmodule_idx: 0\nepisode_idx: 1\n")
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    # The pending draft at (0,1) is skipped → nothing selectable from cursor onward
    assert next_pending(s, c) is None


def test_next_pending_picks_locked_over_pending(tmp_path: Path) -> None:
    """ep0=pending (draft, skipped), ep1=locked → next_pending walks past the
    pending draft and returns the locked episode."""
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: VDOT基础
            style: educational
            episodes:
              - title: 草案期
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g0
                status: pending
              - title: 已锁定期
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g1
                status: locked
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text("book: running-science-v1\nmodule_idx: 0\nepisode_idx: 0\n")
    s = load_syllabus(s_file)
    c = load_cursor(c_file)
    result = next_pending(s, c)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "已锁定期"
    assert episode.status == "locked"


def test_lock_syllabus_flips_all_pending_to_locked(tmp_path: Path) -> None:
    """lock_syllabus loads a syllabus, flips every `pending` episode to `locked`,
    leaves `done` untouched, and persists the result (fail-soft, idempotent)."""
    from lib.syllabus import lock_syllabus
    syllabus_yaml = textwrap.dedent("""\
        book: running-science-v1
        character_ref: ""
        modules:
          - name: 模块A
            style: educational
            episodes:
              - title: 已完成
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g0
                status: done
              - title: 草案一
                doc_refs: []
                source: Daniels
                verify_status: "✅"
                learning_goal: g1
                status: pending
          - name: 模块B
            style: tutorial
            episodes:
              - title: 草案二
                doc_refs: []
                source: UESCA
                verify_status: "✅"
                learning_goal: g2
                status: pending
    """)
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(syllabus_yaml)

    lock_syllabus(s_file)

    reloaded = load_syllabus(s_file)
    # done stays done; both pending drafts became locked
    assert reloaded.modules[0].episodes[0].status == "done"
    assert reloaded.modules[0].episodes[1].status == "locked"
    assert reloaded.modules[1].episodes[0].status == "locked"

    # Idempotent: locking an already-locked syllabus is a no-op
    lock_syllabus(s_file)
    reloaded2 = load_syllabus(s_file)
    assert reloaded2.modules[0].episodes[0].status == "done"
    assert reloaded2.modules[0].episodes[1].status == "locked"
    assert reloaded2.modules[1].episodes[0].status == "locked"


def test_lock_season_flips_only_that_season(tmp_path: Path) -> None:
    """lock_season(syllabus, season_idx) flips pending → locked ONLY within the
    named season; other seasons keep their pending drafts (season-by-season lock)."""
    from lib.syllabus import lock_season
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_SEASONS)  # season0 ep1 locked, season1 ep0 locked
    s = load_syllabus(s_file)
    # Make both consumable episodes pending again so the effect of lock_season is visible
    s.seasons[0].modules[0].episodes[1].status = "pending"
    s.seasons[1].modules[0].episodes[0].status = "pending"

    lock_season(s, 0)

    # Season 0's draft is now locked; Season 1's draft is untouched (still pending)
    assert s.seasons[0].modules[0].episodes[1].status == "locked"
    assert s.seasons[1].modules[0].episodes[0].status == "pending"
    # done episode in season 0 is untouched
    assert s.seasons[0].modules[0].episodes[0].status == "done"

    # Now lock season 1
    lock_season(s, 1)
    assert s.seasons[1].modules[0].episodes[0].status == "locked"


def test_reset_current_locked_is_reselectable(tmp_path: Path) -> None:
    """reset_current rewinds a done episode to `locked` (not a dead `pending`), so
    next_pending can immediately re-select it. Proves reset keeps the lock (MR-2).

    ep0=done, ep1=locked, cursor at ep1 → reset rewinds ep0 to locked, cursor to
    ep0 → next_pending from the reset cursor re-selects ep0."""
    from lib.syllabus import reset_current
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML)  # ep0=done, ep1=locked
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(CURSOR_YAML)  # module_idx=0, episode_idx=1
    s = load_syllabus(s_file)
    c = load_cursor(c_file)

    s2, c2 = reset_current(s, c)
    # ep0 rewound to locked (keeps the lock — not pending)
    assert s2.modules[0].episodes[0].status == "locked"
    assert c2.module_idx == 0 and c2.episode_idx == 0

    # locked → immediately re-selectable by the consume gate
    result = next_pending(s2, c2)
    assert result is not None
    episode, seq, slug, module = result
    assert episode.title == "什么是VDOT"
    assert episode.status == "locked"


# ---------------------------------------------------------------------------
# Tests: select_by_seq (Task 6) — point-name reselection by global seq, ignoring
# cursor position and status; must produce a Selection structurally identical to
# select_next's for the same episode (shared `_build_selection`, no field drift).
# ---------------------------------------------------------------------------

# Multi-season outline mixing done / locked / pending, cursor parked in the middle
# (season0 mod A: ep0=done seq001, ep1=locked seq002; season1 mod B: ep0=pending
# seq003, ep1=locked seq004).
SYLLABUS_YAML_BY_SEQ = textwrap.dedent("""\
    book: running-science-v1
    character_ref: assets/character/coach_ref.jpg
    color_grade: 暖金
    seasons:
      - name: 第一季
        modules:
          - name: 模块A
            style: educational
            art_style: 简笔日漫
            character_ref: assets/character/moduleA_ref.jpg
            episodes:
              - title: 已完成期
                doc_refs: ["01-x.md#a"]
                source: Daniels
                verify_status: "✅"
                learning_goal: 目标A0
                status: done
              - title: 已锁定期
                doc_refs: ["01-x.md#b"]
                source: Daniels
                verify_status: "✅"
                learning_goal: 目标A1
                status: locked
      - name: 第二季
        modules:
          - name: 模块B
            style: tutorial
            episodes:
              - title: 草案期
                doc_refs: ["02-y.md#a"]
                source: UESCA
                verify_status: "⚠️"
                learning_goal: 目标B0
                status: pending
              - title: 未来期
                doc_refs: ["02-y.md#b"]
                source: UESCA
                verify_status: "✅"
                learning_goal: 目标B1
                status: locked
""")


def _write_by_seq_fixture(tmp_path: Path, cursor_module_idx: int = 0, cursor_episode_idx: int = 1):
    s_file = tmp_path / "syllabus.yaml"
    s_file.write_text(SYLLABUS_YAML_BY_SEQ)
    c_file = tmp_path / "cursor.yaml"
    c_file.write_text(
        f"book: running-science-v1\nmodule_idx: {cursor_module_idx}\nepisode_idx: {cursor_episode_idx}\n"
    )
    return s_file, c_file


def test_select_by_seq_matches_select_next_for_same_locked_episode(tmp_path: Path) -> None:
    """select_by_seq(cfg, seq) for a locked episode returns a Selection identical,
    field-for-field, to select_next's Selection when the cursor points directly at
    that same locked episode — proving both share the same `_build_selection`."""
    from lib.syllabus import select_by_seq, select_next
    import types

    # Cursor placed exactly at season0 mod A ep1 ("已锁定期", seq=002, locked) so
    # select_next resolves that very episode via the normal cursor+locked gate.
    s_file, c_file = _write_by_seq_fixture(tmp_path, cursor_module_idx=0, cursor_episode_idx=1)
    cfg = types.SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))

    via_next = select_next(cfg)
    assert via_next is not None
    assert via_next.episode.title == "已锁定期"

    via_seq = select_by_seq(cfg, via_next.seq)
    assert via_seq is not None
    assert via_seq == via_next


def test_select_by_seq_returns_done_episode_ignoring_status(tmp_path: Path) -> None:
    """select_by_seq reaches a `done` episode (select_next's cursor+locked gate
    would never surface it) with all fields populated correctly."""
    from lib.syllabus import select_by_seq
    import types

    s_file, c_file = _write_by_seq_fixture(tmp_path)
    cfg = types.SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))

    sel = select_by_seq(cfg, "001")
    assert sel is not None
    assert sel.episode.title == "已完成期"
    assert sel.episode.status == "done"
    assert sel.seq == "001"
    assert sel.doc_refs == ["01-x.md#a"]
    assert sel.source == "Daniels"
    assert sel.verify_status == "✅"
    assert sel.learning_goal == "目标A0"
    assert sel.topic == "已完成期"
    assert sel.art_style == "简笔日漫"
    assert sel.character_ref == "assets/character/moduleA_ref.jpg"
    assert sel.module_name == "模块A"
    assert sel.season_name == "第一季"
    assert sel.color_grade == "暖金"


def test_select_by_seq_reaches_pending_episode_behind_cursor(tmp_path: Path) -> None:
    """select_by_seq can select a `pending` draft episode (status ignored) even
    though it sits behind the cursor position — cursor placed AFTER seq003."""
    from lib.syllabus import select_by_seq
    import types

    # Cursor at season1 mod B ep1 (seq004, locked) — behind it is the pending seq003.
    s_file, c_file = _write_by_seq_fixture(tmp_path, cursor_module_idx=1, cursor_episode_idx=1)
    cfg = types.SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))

    sel = select_by_seq(cfg, "003")
    assert sel is not None
    assert sel.episode.title == "草案期"
    assert sel.episode.status == "pending"
    assert sel.module_name == "模块B"
    assert sel.season_name == "第二季"


def test_select_by_seq_nonexistent_seq_returns_none(tmp_path: Path) -> None:
    """An out-of-range seq (not present in the outline) resolves to None."""
    from lib.syllabus import select_by_seq
    import types

    s_file, c_file = _write_by_seq_fixture(tmp_path)
    cfg = types.SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))

    assert select_by_seq(cfg, "999") is None


def test_select_by_seq_is_read_only_cursor_and_status_unchanged(tmp_path: Path) -> None:
    """select_by_seq never mutates cursor.yaml or syllabus.yaml on disk (pure
    read/select — no advance, no status flip)."""
    from lib.syllabus import select_by_seq, load_cursor, load_syllabus
    import types

    s_file, c_file = _write_by_seq_fixture(tmp_path, cursor_module_idx=0, cursor_episode_idx=1)
    cfg = types.SimpleNamespace(syllabus_path=str(s_file), cursor_path=str(c_file))

    before_cursor_text = c_file.read_text(encoding="utf-8")
    before_syllabus_text = s_file.read_text(encoding="utf-8")

    sel = select_by_seq(cfg, "001")  # a done episode, distinct from cursor position
    assert sel is not None

    assert c_file.read_text(encoding="utf-8") == before_cursor_text
    assert s_file.read_text(encoding="utf-8") == before_syllabus_text

    c2 = load_cursor(c_file)
    s2 = load_syllabus(s_file)
    assert c2.module_idx == 0 and c2.episode_idx == 1
    assert s2.modules[0].episodes[0].status == "done"
    assert s2.modules[0].episodes[1].status == "locked"
