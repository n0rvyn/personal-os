"""结构硬门 (lib.structlint) — 段数 / 草稿标记 / 下注段 / 念稿时长.

Phase 3 工艺门的产品层硬约束:每次 e2e run 之前,念稿必须满足四件事 —
正确的段数(早 4 / 晚 3)、不留 LLM 工作标签(草稿头 H1)、不单列「我下注」段、
念稿字数达到 18 分钟的下限(6570 非空白字,按 365 字/分语速派生)。

These are advisory in Phase 3 v1 — `lib/scorecard` records them; `lib/runner`
turns them into a halt only when `--enforce-scorecard` is set. The gates
themselves are deterministic and can be exercised on any platform without
LLM/embed/network.

Thresholds (frozen at fixture calibration in Task 7):
- `SECTION_COUNT` per show.
- `BROADCAST_MIN_CHARS` derived from `SPOKEN_CHARS_PER_MIN * MIN_BROADCAST_MINUTES`.

基础层一致性 note: `lib.episode._FLOOR_CHARS_BY_SHOW` (=6500) gates the
reader body finalize step; `BROADCAST_MIN_CHARS` (=6570) gates the broadcast
念稿. They target DIFFERENT objects with DIFFERENT purposes — broadcast floor
is intentionally stricter (the product promises ~18 min of spoken audio, not
just a body of minimum prose). Do NOT collapse these to a single constant;
that would conflate the草稿下限 with the产品时长门.
"""
from __future__ import annotations

import re
from typing import Any

from lib.episode import _count_script_chars


# ---- Thresholds (命名常量; fixture 标定后冻结) ----

SECTION_COUNT: dict[str, int] = {"morning": 4, "evening": 3}

# Spoken-pace convention used in kuaidao.md:45 and SKILL.md — 365 非空白字/分.
SPOKEN_CHARS_PER_MIN: int = 365

# 18-minute floor at the spoken-pace convention.
MIN_BROADCAST_MINUTES: int = 18

# Derived: 6570. Re-derived here (not inlined) so the relationship to the
# spoken-pace + minute floor is auditable in code.
BROADCAST_MIN_CHARS: int = SPOKEN_CHARS_PER_MIN * MIN_BROADCAST_MINUTES


# ---- ATX 段标题 / 草稿头 / 下注段 正则 ----

# 段标题: `## ` 后跟 ①②③④⑤(全角圆数字)。这是早/晚间正文段标题的约定形状。
# Raw string with backslash-s so `re.MULTILINE` lines are matched.
_SECTION_HEADER_RE = re.compile(r"^##\s*[①②③④⑤]", re.MULTILINE)

# 草稿头: H1 标题里出现 `草稿` 工作标签的所有形式 (`# 草稿 C — …`, `# 草稿A`, `# 草稿`)。
# This catches the LLM-tendency to leak its own working label into the body.
# CJK-correct token boundary: ASCII `\b` does NOT fire between two Han chars,
# so `# 草稿A` (no space) slipped through. A negative lookahead "not followed
# by another Han char" is the Unicode equivalent — it still ignores a genuine
# `# 草稿的来历` essay title (的 is Han) while catching 草稿A / 草稿<space> / 草稿<EOL>.
_DRAFT_HEADER_RE = re.compile(r"^#\s*草稿(?![一-鿿])", re.MULTILINE)

# 下注段: ATX 段标题里出现 `我下注`(`## ⑤ 我下注什么` / `### 我下注` / `## 我下注`)。
# 只禁**独立标题段**(任意 ATX 层级);织入正文里的可证伪判断(`我的判断是…`,
# 无标题)不在此命中。`^#+` 覆盖 H1–H6:一个 `### 我下注` 子标题同样是 CLAUDE.md
# 明令禁止的独立下注段(「No 我下注 section … woven into the body」),旧的 `^##`
# 既漏判单 `#` 的 H1,语义上也无理由放过更深层级的下注标题。Bodies with no
# `#…我下注…` heading still pass (woven judgments use `我的判断是…` → no match).
_BETTING_SECTION_RE = re.compile(r"^#+\s*.*我下注", re.MULTILINE)


# ---- Gates ----

def check_sections(body: str, show: str) -> dict[str, Any]:
    """段数硬门:早 4 段 / 晚 3 段。ATX `## ①②③④⑤` 段标题计数。

    Returns: `{ok: bool, reason: str, hits: list[str]}`. On ok=True, hits is [].
    On ok=False, hits contains a single human-readable message describing the
    off-by-one count and the expected count for `show`.
    """
    expected = SECTION_COUNT.get(show)
    if expected is None:
        return {
            "ok": False,
            "reason": f"unknown show={show!r}; expected one of {sorted(SECTION_COUNT)}",
            "hits": [f"unknown show={show!r}"],
        }

    actual = len(_SECTION_HEADER_RE.findall(body))
    if actual == expected:
        return {"ok": True, "reason": "", "hits": []}

    return {
        "ok": False,
        "reason": f"段数={actual} 期望 {expected} (show={show})",
        "hits": [f"段数={actual} 期望 {expected} (show={show})"],
    }


def check_no_draft_marker(body: str) -> dict[str, Any]:
    """无草稿头硬门:正文 H1 不应出现 `# 草稿 X — …` 这类 LLM 工作标签。

    Returns: `{ok: bool, reason: str, hits: list[str]}`. On hit, hits contains
    the matched line(s) for diagnostic display in the scorecard.
    """
    matches = _DRAFT_HEADER_RE.findall(body)
    if not matches:
        return {"ok": True, "reason": "", "hits": []}

    # Preserve the diagnostic shape: the first matched line is enough; if the
    # body has multiple `# 草稿 …` headers we still report once with a count.
    return {
        "ok": False,
        "reason": f"检测到 {len(matches)} 处草稿头 H1 (e.g. `# 草稿 …`)",
        "hits": [f"草稿头 H1 ×{len(matches)}"],
    }


def check_no_betting_section(body: str) -> dict[str, Any]:
    """无独立下注段硬门:`## …我下注…` 段标题不应出现;判断应织入正文。

    The bet-as-section structural pattern was retired because it bred凑数 bets.
    The stance card's `bets[]` are DISTILLED from the woven body at step 16.
    We only flag the STRUCTURAL pattern — woven judgments in段正文 (no独立标题)
    pass through.

    Returns: `{ok: bool, reason: str, hits: list[str]}`.
    """
    matches = _BETTING_SECTION_RE.findall(body)
    if not matches:
        return {"ok": True, "reason": "", "hits": []}

    return {
        "ok": False,
        "reason": f"检测到 {len(matches)} 处独立「我下注」段标题 (应织入正文)",
        "hits": [f"独立「我下注」段标题 ×{len(matches)}"],
    }


def check_duration(script_text: str) -> dict[str, Any]:
    """念稿时长硬门:念稿 .txt 非空白字数 ≥ 6570 (≈18 分钟 @ 365 字/分)。

    ⚠️ This gate measures the **念稿 .txt**, NOT the reader .md. 量错对象 was
    the 06-14 短节目漏网 root cause — the .md was 7131 字 (passed the gate)
    but the actual broadcast 念稿 was 5455 字 (~14.9 min, short of 18).

    The entry-parameter name `script_text` is part of the contract: callers
    must pass the broadcast 念稿, never the reader body. See plan Bug diagnosis
    for the 06-14 root cause analysis.

    Returns: `{ok: bool, reason: str, hits: list[str]}`.
    """
    n = _count_script_chars(script_text)
    if n >= BROADCAST_MIN_CHARS:
        return {"ok": True, "reason": "", "hits": []}

    minutes = n / SPOKEN_CHARS_PER_MIN
    return {
        "ok": False,
        "reason": (
            f"念稿 {n} 字 < {BROADCAST_MIN_CHARS} "
            f"(~{minutes:.1f} 分钟,floor={MIN_BROADCAST_MINUTES} min @ "
            f"{SPOKEN_CHARS_PER_MIN} 字/分)"
        ),
        "hits": [
            f"念稿 {n} 字 < {BROADCAST_MIN_CHARS} (~{minutes:.1f} 分钟)"
        ],
    }


def check_structlint(body: str, script_text: str, show: str) -> dict[str, Any]:
    """Composite: runs all four structlint gates and aggregates hits.

    `ok` is True iff every gate is True. `hits` concatenates every gate's
    `hits` list (each gate self-documents its own diagnostic). `reason` is the
    first failing gate's reason (or empty when ok=True) — used as the headline
    by `lib.scorecard` when rendering the scorecard markdown.
    """
    section_result = check_sections(body, show)
    draft_result = check_no_draft_marker(body)
    betting_result = check_no_betting_section(body)
    duration_result = check_duration(script_text)

    all_hits: list[str] = []
    all_hits.extend(section_result["hits"])
    all_hits.extend(draft_result["hits"])
    all_hits.extend(betting_result["hits"])
    all_hits.extend(duration_result["hits"])

    ok = (
        section_result["ok"]
        and draft_result["ok"]
        and betting_result["ok"]
        and duration_result["ok"]
    )

    first_fail = next(
        (r for r in (section_result, draft_result, betting_result, duration_result) if not r["ok"]),
        None,
    )
    reason = first_fail["reason"] if first_fail else ""

    return {"ok": ok, "reason": reason, "hits": all_hits}