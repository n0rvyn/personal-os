"""Tests for lib/structlint.py — 段数/草稿标记/下注段/念稿时长结构硬门.

Written before lib/structlint.py exists; collection must fail at this point
(`No module named 'lib.structlint'`).

Pins:
- check_sections: morning=4 段 ok, 5段 fail; evening=3段 ok, 4段 fail.
  ATX 段标题正则计数 (raw string with backslash-s, e.g. r"^##\\s*[...]") 优先.
- check_no_draft_marker: H1 `# 草稿 X` → flag; 干净 body → ok.
- check_no_betting_section: `## …我下注…` 独立段标题 → flag; 织入正文的
  可证伪判断（无独立标题）→ ok.
- check_duration: 量的是念稿 .txt (not reader .md) — 5455字 → fail
  (<6570 floor); ≥6570 → ok. 防 06-14 量错对象回归.
- check_structlint: composes the four above into a single {ok, reason, hits}.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.structlint import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
    from lib import structlint  # noqa: F401
    assert hasattr(structlint, "check_sections")
    assert hasattr(structlint, "check_no_draft_marker")
    assert hasattr(structlint, "check_no_betting_section")
    assert hasattr(structlint, "check_duration")
    assert hasattr(structlint, "check_structlint")


# ---------- helpers: 4段 morning body & 3段 evening body ----------

def _morning_4seg() -> str:
    """A 4-段 morning body with ATX 段标题 (①②③④)."""
    return (
        "## ① 开场\n"
        "今天的开场我们聊聊一个所有人都关心的话题——AI 在企业内部的渗透。\n\n"
        "## ② 现象\n"
        "大厂已经把大模型塞进客服系统，效果参差不齐。\n\n"
        "## ③ 纵深\n"
        "为什么客服成了第一站？因为它最容易被量化、最不容易出错。\n\n"
        "## ④ 收束\n"
        "我的判断是接下来一年，HR 系统会成为下一个被 LLM 重塑的场景。\n"
    )


def _morning_5seg() -> str:
    """A 5-段 morning body that adds a `## ⑤` 段 — the 06-14 regression shape."""
    base = _morning_4seg()
    return base + "\n## ⑤ 我下注什么\n我下注 HR 系统的 LLM 化会在 18 个月内完成。\n"


def _evening_3seg() -> str:
    """A 3-段 evening body with ATX 段标题 (①②③)."""
    return (
        "## ① 现场\n"
        "今晚的现场是一则来自深圳的硬件新闻。\n\n"
        "## ② 拆解\n"
        "这款新品在供应链上做了三个非典型选择。\n\n"
        "## ③ 收束\n"
        "我的判断是这家公司会变成接下来三个季度的参照系。\n"
    )


def _evening_4seg() -> str:
    """A 4-段 evening body — the evening over-section shape."""
    base = _evening_3seg()
    return base + "\n## ④ 延伸\n后续值得关注的是它对周边生态的影响。\n"


def _clean_body() -> str:
    """A 干净 reader body (morning 4段, no 草稿头, no ⑤段, no 我下注 标题)."""
    return _morning_4seg()


def _body_with_draft_header() -> str:
    """A body that includes the LLM-tendency 草稿头 H1 — the 06-14 regression shape."""
    return (
        "# 草稿 C — 今日话题\n"
        + _morning_4seg()
    )


def _body_with_betting_section() -> str:
    """A body that includes an independent `## …我下注…` section header."""
    return _morning_4seg() + "\n## ⑤ 我下注什么\n我下注 HR 系统的 LLM 化会在 18 个月内完成。\n"


def _body_with_woven_judgment() -> str:
    """A 干净 morning body with a falsifiable judgment WOVEN into ④正文
    (no independent `我下注` 标题) — the post-betting-section discipline."""
    return (
        "## ① 开场\n"
        "今天的开场我们聊聊一个所有人都关心的话题——AI 在企业内部的渗透。\n\n"
        "## ② 现象\n"
        "大厂已经把大模型塞进客服系统，效果参差不齐。\n\n"
        "## ③ 纵深\n"
        "为什么客服成了第一站？因为它最容易被量化、最不容易出错。\n\n"
        "## ④ 收束\n"
        "我的判断是接下来一年，HR 系统会成为下一个被 LLM 重塑的场景——"
        "这是可证伪的：18 个月内看 HR SaaS 的 LLM 化覆盖率能否翻倍。\n"
    )


def _short_script() -> str:
    """A 5455-字 念稿 (06-14 measured length) — below the 6570 floor."""
    # 5455 CJK chars, all non-ws, no markdown syntax.
    # Pad with a single repeated sentence to hit the exact count.
    sentence = "今天我们讲一个关于产业升级的故事，涉及到供应链的多个环节。"
    # sentence is 29 chars (note: ASCII-aligned width differs from CJK glyph count).
    # 29 * 188 = 5452; + 3-char tail = 5455.
    text = sentence * 188 + "一二三"  # 5452 + 3 = 5455
    assert _script_char_count(text) == 5455, f"expected 5455, got {_script_char_count(text)}"
    return text


def _long_script() -> str:
    """A 念稿 that clears the 6570 floor — ≥18 minutes at 365 non-ws字/分."""
    sentence = "今天我们讲一个关于产业升级的故事，涉及到供应链的多个环节。"
    # 28 chars each. 6570 / 28 = 234.6 → 235 sentences = 6580, above floor.
    text = sentence * 235
    assert _script_char_count(text) >= 6570
    return text


def _script_char_count(text: str) -> int:
    """Mirror episode._count_script_chars: non-whitespace char count."""
    return sum(1 for ch in text if not ch.isspace())


# ---------- check_sections: morning 4-段 ok / 5-段 fail ----------

def test_section_count_morning_ok():
    """4-段 morning body must pass check_sections. Show='morning' expects 4."""
    from lib.structlint import check_sections
    result = check_sections(_morning_4seg(), "morning")
    assert result["ok"] is True, f"4段 morning must be ok, got {result}"
    assert result.get("hits", []) == []


def test_section_count_morning_five_fails():
    """5-段 morning body (06-14 regression shape with `## ⑤`) must FAIL.
    The hit should mention the off-by-one section count."""
    from lib.structlint import check_sections
    result = check_sections(_morning_5seg(), "morning")
    assert result["ok"] is False, "5段 morning must fail"
    assert len(result.get("hits", [])) > 0
    # The hit should reference the expected count
    hits_text = " ".join(result["hits"])
    assert "5" in hits_text or "段数" in hits_text or "section" in hits_text.lower(), (
        f"hit should mention the off-by-one count, got: {result['hits']}"
    )


# ---------- check_sections: evening 3-段 ok / 4-段 fail ----------

def test_section_count_evening():
    """Evening expects 3 段. 3-段 body ok, 4-段 body fail."""
    from lib.structlint import check_sections

    result_ok = check_sections(_evening_3seg(), "evening")
    assert result_ok["ok"] is True, f"3段 evening must be ok, got {result_ok}"

    result_fail = check_sections(_evening_4seg(), "evening")
    assert result_fail["ok"] is False, "4段 evening must fail"
    assert len(result_fail.get("hits", [])) > 0


# ---------- check_no_draft_marker ----------

def test_draft_header_flagged():
    """A body with a `# 草稿 X — …` H1 must be flagged by check_no_draft_marker.
    This is the 06-14 regression shape (LLM leaks its own working label)."""
    from lib.structlint import check_no_draft_marker

    result = check_no_draft_marker(_body_with_draft_header())
    assert result["ok"] is False, "草稿头 H1 must be flagged"
    assert len(result.get("hits", [])) > 0


def test_clean_body_no_draft_marker():
    """A clean body (no 草稿头) must pass check_no_draft_marker."""
    from lib.structlint import check_no_draft_marker

    result = check_no_draft_marker(_clean_body())
    assert result["ok"] is True
    assert result.get("hits", []) == []


# ---------- check_no_betting_section ----------

def test_betting_section_flagged():
    """An independent `## …我下注…` section header must be flagged.
    The 06-14 regression shape: a `## ⑤ 我下注什么` segment."""
    from lib.structlint import check_no_betting_section

    result = check_no_betting_section(_body_with_betting_section())
    assert result["ok"] is False, "独立 `我下注` section must be flagged"
    assert len(result.get("hits", [])) > 0


def test_woven_judgment_not_flagged_as_betting():
    """A falsifiable judgment WOVEN into正文 (no independent section header)
    must NOT be flagged by check_no_betting_section. The betting section ban
    targets the STRUCTURAL pattern, not the semantic content of opinions."""
    from lib.structlint import check_no_betting_section

    result = check_no_betting_section(_body_with_woven_judgment())
    assert result["ok"] is True, (
        "织入正文的可证伪判断 not a `## …我下注` section — must not flag"
    )
    assert result.get("hits", []) == []


# ---------- check_duration: measures 念稿, not .md ----------

def test_duration_measures_script_not_md():
    """The 06-14 root cause: 字数门量的是念稿 .txt, NOT the reader .md.
    A 5455-字 念稿 must FAIL (<6570 floor for 18 min). A ≥6570 念稿 must ok.

    The function signature takes `script_text` — by name and by docstring
    contract, this is the 念稿 not the reader .md. This test pins that."""
    from lib.structlint import check_duration

    # 5455 字 念稿 — the 06-14 measured length — must fail.
    result_short = check_duration(_short_script())
    assert result_short["ok"] is False, (
        "5455-字 念稿 must fail the 6570 (~18 min) floor"
    )
    assert len(result_short.get("hits", [])) > 0

    # ≥6570 字 念稿 must pass.
    result_long = check_duration(_long_script())
    assert result_long["ok"] is True, (
        "≥6570-字 念稿 must pass the floor"
    )
    assert result_long.get("hits", []) == []


# ---------- check_structlint: composite ----------

def test_structlint_all():
    """Composite: the 06-14 regression shape (5段 + 草稿头 + ⑤段 + 短念稿)
    must produce multiple hits across the hard gates."""
    from lib.structlint import check_structlint

    body = _morning_5seg()  # 5段 + ## ⑤ 我下注
    body_with_draft = "# 草稿 C — 今日话题\n" + body  # adds 草稿头
    script = _short_script()  # 5455 字 < 6570

    result = check_structlint(body_with_draft, script, "morning")

    # Multiple gates must fire on this regression input.
    assert result["ok"] is False, "06-14 shape must fail structlint"
    hits = result.get("hits", [])
    assert len(hits) >= 3, (
        f"expected ≥3 hits (段数/草稿头/下注段/时长), got {len(hits)}: {hits}"
    )


def test_structlint_clean_passes():
    """Composite: a clean body (4段 + no 草稿头 + no ⑤段 + long script) must
    pass all four hard gates. Woven judgment does not trigger the betting ban."""
    from lib.structlint import check_structlint

    body = _body_with_woven_judgment()  # 4段 + woven judgment
    script = _long_script()  # ≥6570

    result = check_structlint(body, script, "morning")
    assert result["ok"] is True, (
        f"clean body + long script must pass, got {result}"
    )
    assert result.get("hits", []) == []


def test_module_imports_post_impl():
    """Sanity: now that lib/structlint.py exists, importing it must succeed.
    This is the post-impl companion to test_module_imports — the FAIL-first
    contract was satisfied by `test_collect_fails_pre_impl` (now removed;
    it could not transition to PASS once the module existed)."""
    from lib import structlint  # noqa: F401
    assert structlint is not None
