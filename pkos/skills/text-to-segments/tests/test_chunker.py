"""Tests for text-to-segments chunker."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "chunker.py"
FIXTURES = HERE / "fixtures"

# Load chunker module dynamically (script lives outside a Python package).
# Register in sys.modules so @dataclass can resolve cls.__module__.
_spec = importlib.util.spec_from_file_location("chunker", SCRIPT)
chunker = importlib.util.module_from_spec(_spec)
sys.modules["chunker"] = chunker
assert _spec.loader is not None
_spec.loader.exec_module(chunker)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


def test_clean_markdown_strips_headings_and_bold():
    src = "# Title\n\nSome **bold** and _italic_ here.\n## Section"
    out = chunker.clean_markdown(src)
    assert "#" not in out
    assert "**" not in out
    assert "bold" in out and "italic" in out


def test_clean_markdown_strips_code_fences_entirely():
    src = "Lead.\n\n```python\nprint('hi')\n```\n\nTail."
    out = chunker.clean_markdown(src)
    assert "print" not in out
    assert "Lead." in out and "Tail." in out


def test_clean_markdown_keeps_inline_code_text():
    src = "Use `xxd -r -p` to decode."
    out = chunker.clean_markdown(src)
    assert "xxd -r -p" in out
    assert "`" not in out


def test_clean_markdown_strips_list_bullets_keeps_content():
    src = "- alpha\n- bravo\n1. first\n2. second"
    out = chunker.clean_markdown(src)
    assert "alpha" in out and "bravo" in out
    assert "first" in out and "second" in out
    for marker in ("- ", "1. ", "2. "):
        assert marker not in out


def test_chunk_basic_sentence_boundary():
    text = "第一句话结束了。第二句话也结束了。第三句话也是。"
    segs = chunker.chunk(text, max_chars=10, preserve_terms=[])
    assert len(segs) >= 2
    # Each non-final segment should end at a sentence-final punctuation.
    for s in segs[:-1]:
        assert s.ends_with in "。！？；!?;"


def test_chunk_respects_max_chars():
    text = "甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸"
    segs = chunker.chunk(text, max_chars=20, preserve_terms=[])
    for s in segs:
        assert s.char_count <= 20, f"Segment {s.id} exceeds budget: {s.text}"


def test_chunk_preserves_quote_pairs():
    # The quoted span must not be split. Build a text where the only way to
    # respect max_chars is to either cut inside the quote (forbidden) or to
    # honor the closing punctuation after the quote.
    text = '前导文本。她说"这是一段不能被切开的引用文字"，然后继续叙述。'
    segs = chunker.chunk(text, max_chars=15, preserve_terms=[])
    joined = "".join(s.text for s in segs)
    # No segment should start or end mid-quote (= contain unbalanced quotes).
    for s in segs:
        opens = sum(s.text.count(o) for o in chunker.PAIR_OPEN)
        closes = sum(s.text.count(c) for c in chunker.PAIR_CLOSE)
        # Allow ±1 for trailing punctuation inclusion, but quote count should be balanced.
        assert s.text.count('"') % 2 == 0, f"unbalanced quote in {s.text!r}"
    # Sanity: the whole quoted phrase must appear intact in some chunk.
    assert "这是一段不能被切开的引用文字" in joined
    assert any("这是一段不能被切开的引用文字" in s.text for s in segs)


def test_chunk_preserves_number_plus_unit():
    text = "测试数字 4.2 倍 增长和 32 kHz 采样率，然后是 2026 年的事情，最后是 100% 通过率。结束。"
    segs = chunker.chunk(text, max_chars=15, preserve_terms=[])
    # Every (number + unit) phrase must appear contiguously in one chunk.
    for phrase in ("4.2 倍", "32 kHz", "2026 年", "100%"):
        assert any(phrase in s.text for s in segs), \
            f"phrase {phrase!r} got split across chunks; segs={[s.text for s in segs]}"


def test_chunk_preserves_explicit_terms():
    text = "我们在用 ComposableArchitecture 这个框架，结合 llama.swift 做演示，最后展示给团队成员。"
    segs = chunker.chunk(text, max_chars=12, preserve_terms=["ComposableArchitecture", "llama.swift"])
    for term in ("ComposableArchitecture", "llama.swift"):
        assert any(term in s.text for s in segs), \
            f"preserve-term {term!r} got split; segs={[s.text for s in segs]}"


def test_chunk_empty_input_returns_no_segments():
    assert chunker.chunk("", max_chars=100, preserve_terms=[]) == []
    assert chunker.chunk("   \n\n\n  ", max_chars=100, preserve_terms=[]) == []


def test_chunk_long_paragraph_falls_back_to_sentence_break():
    # Single paragraph longer than max_chars must be broken on sentence punct.
    text = "句子一。" * 50  # 200 chars
    segs = chunker.chunk(text, max_chars=20, preserve_terms=[])
    assert len(segs) > 1
    for s in segs:
        assert s.char_count <= 20


# ---------------------------------------------------------------------------
# Vendor adapter tests
# ---------------------------------------------------------------------------


def test_vendor_format_minimax_shape():
    segs = chunker.chunk("一句话。两句话。", max_chars=5, preserve_terms=[])
    out = chunker.to_vendor(segs, metadata={}, vendor="minimax")
    assert isinstance(out, list)
    assert all(set(item.keys()) >= {"id", "text", "voice_id", "emotion"} for item in out)


def test_vendor_format_volcengine_shape():
    segs = chunker.chunk("一句话。两句话。", max_chars=5, preserve_terms=[])
    out = chunker.to_vendor(segs, metadata={"total_chars": 7}, vendor="volcengine")
    assert "segments" in out
    assert all(set(item.keys()) == {"id", "text"} for item in out["segments"])


def test_vendor_format_unknown_raises():
    try:
        chunker.to_vendor([], metadata={}, vendor="nonexistent")
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown vendor")


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def test_cli_stdin_to_stdout():
    src = "测试一。测试二。测试三。"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--max-chars", "10"],
        input=src,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(res.stdout)
    assert payload["metadata"]["segment_count"] == len(payload["segments"])
    assert payload["metadata"]["total_chars"] > 0


def test_cli_missing_input_file_returns_2(tmp_path):
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(tmp_path / "nope.md")],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2


def test_cli_writes_output_file(tmp_path):
    inp = tmp_path / "in.md"
    out = tmp_path / "out.json"
    inp.write_text("第一段。\n\n第二段。", encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(inp), "--output", str(out), "--max-chars", "10"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(out.read_text())
    assert payload["segments"]
    assert payload["metadata"]["source"] == str(inp)


def test_cli_preserve_terms_flag(tmp_path):
    inp = tmp_path / "in.md"
    inp.write_text("我们用 ComposableArchitecture 做演示。", encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--input", str(inp),
         "--max-chars", "12",
         "--preserve-terms", "ComposableArchitecture"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    joined = "".join(s["text"] for s in payload["segments"])
    assert "ComposableArchitecture" in joined
    assert any("ComposableArchitecture" in s["text"] for s in payload["segments"])
