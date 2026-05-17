"""End-to-end fixture test: chunker on a realistic podcast-style markdown."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "chunker.py"
FIXTURE = HERE / "fixtures" / "podcast-sample.md"

_spec = importlib.util.spec_from_file_location("chunker", SCRIPT)
chunker = importlib.util.module_from_spec(_spec)
sys.modules["chunker"] = chunker
assert _spec.loader is not None
_spec.loader.exec_module(chunker)


def _cleaned():
    src = FIXTURE.read_text(encoding="utf-8")
    return chunker.clean_markdown(src)


def test_markdown_clean_removes_code_block_and_markers():
    out = _cleaned()
    # Code fence content must be gone.
    assert "this_should_not_appear_in_tts_output" not in out
    assert "filtered out" not in out
    # Heading and emphasis markers gone, content kept.
    assert "卞旸的每日知趣播客" in out
    assert "#" not in out.splitlines()[0]
    # Inline code text kept.
    assert "ComposableArchitecture 2.0" in out
    assert "llama.swift" in out
    # Horizontal rule line gone.
    for line in out.splitlines():
        assert line.strip() not in ("---", "***", "___")


def test_chunker_preserves_all_critical_phrases():
    out = _cleaned()
    segs = chunker.chunk(out, max_chars=120, preserve_terms=["ComposableArchitecture 2.0", "Rapid-MLX", "llama.swift"])

    joined = "".join(s.text for s in segs)
    for phrase in (
        "ComposableArchitecture 2.0",
        "llama.swift",
        "4.2 倍",
        "32 kHz",
        "8 秒",
        "1.2 秒",
        "100 多年",
        "Rapid-MLX",
        "2026 年",
    ):
        assert any(phrase in s.text for s in segs), \
            f"phrase {phrase!r} got split; check segs: {[s.text for s in segs]}"


def test_chunker_respects_max_chars_with_overshoot_only_for_protected():
    out = _cleaned()
    max_chars = 120
    segs = chunker.chunk(out, max_chars=max_chars, preserve_terms=[])
    overshoot = [s for s in segs if s.char_count > max_chars]
    # If anything overshoots, it must be because the segment is a single
    # unbreakable protected span larger than max_chars.
    for s in overshoot:
        assert s.boundary_priority in ("eof", "hard"), \
            f"unexpected overshoot at priority {s.boundary_priority}: {s.text!r}"
