"""Title + end cards for video-studio (POC4, design B).

The AI generates the card BACKGROUND only (a character-less still via
``media_image.gen_still(prompt, None, out)``); the design layer — split
layout, amber accent bar, type hierarchy, the end card's 3-point summary — is
code, rendered as a libass ASS overlay. AI image models cannot lay out text
(they produce garbled glyphs), so all on-screen text comes from here.

These builders are PURE: they return the ASS document string. The driver
(``spike_run.py``) renders a card by feeding the ASS into
``ffcmd.still_card_cmd`` to get a video-only clip that joins the xfade
dissolve chain exactly like a beat clip. Card durations are constants here;
Stage 1 will move card copy + durations into config.
"""
from __future__ import annotations

import re

from lib.ffcmd import DISSOLVE_FADE

# Card on-screen durations (seconds). The clip handed to the xfade chain is
# DISSOLVE_FADE longer (the overlap is faded back out), same as a beat clip.
TITLE_DUR = 2.6
END_DUR = 3.0

# Amber accent (ASS &HBBGGRR → B=00 G=D2 R=FF). Single emphasis colour, shared
# by the tag, the title underline, the bullet numbers, and the footer bar.
_ACCENT = "&H0000D2FF&"
_GREY = "&H00646464&"
_BLACK = "&H000000&"

_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: H1,PingFang SC,128,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,1,0,0,0,100,100,1,0,1,2.5,1,4,0,0,0,1
Style: Tag,PingFang SC,38,&H0000D2FF,&H0000D2FF,&H00101010,&H00000000,1,0,0,0,100,100,4,0,1,1.5,0,4,0,0,0,1
Style: Body,PingFang SC,48,&H00DCDCDC,&H00DCDCDC,&H00101010,&H00000000,0,0,0,0,100,100,1,0,1,2,1,4,0,0,0,1
Style: Num,PingFang SC,54,&H0000D2FF,&H0000D2FF,&H00101010,&H00000000,1,0,0,0,100,100,1,0,1,2,1,4,0,0,0,1
Style: Foot,PingFang SC,36,&H00DCDCDC,&H00DCDCDC,&H00101010,&H00000000,1,0,0,0,100,100,1,0,1,2,1,4,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Default card copy — hand-written placeholder. Stage 1: from config; the end
# bullets ultimately come from the storyboard step summarising the script.
DEFAULT_TITLE = {
    "tag": "跑步知识 · EP.01",
    "title": "乳酸阈配速",
    "subtitle": "搞懂这一个概念，训练效率上一个台阶",
    # bg_prompt stays style-agnostic: the art_style prefix (e.g. 简笔日漫) + the
    # episode color_grade decide the look, so the card matches the body. No baked
    # "电影感/冷蓝调/剪影" here (those fought the flat-cel anime body — R2 fix).
    "bg_prompt": "清晨田径场跑道，一位跑步者在画面右侧轻松奔跑，左侧留白，明亮清爽",
}
DEFAULT_END = {
    "tag": "本期 3 个要点",
    "bullets": [
        ["01", "乳酸阈 = 你还能稳定坚持的最快配速"],
        ["02", "用 VDOT 表反推各档训练配速"],
        ["03", "每周 1-2 次阈值跑，单次 20-40 分钟"],
    ],
    "footer": "关注 · 科学跑步不靠感觉      下期 · 跑姿与步频",
    # style-agnostic (see DEFAULT_TITLE note): let art_style + color_grade decide look.
    "bg_prompt": "清晨空旷跑道向远方笔直延伸，两侧留白，明亮清爽，无人",
}


# ---- doc-mode card copy: derived faithfully from the real episode ----
# Fixture mode hand-authors card copy (fixture["cards"]); doc mode (a real
# syllabus episode) has none, so the copy is built here from the episode's own
# metadata + script. The background prompt stays the column-locked default
# (冷蓝调 morning scene) — only the TEXT is episode-specific.

def _ep_tag(seq: str) -> str:
    """Column tag with the episode number, e.g. seq='003' → '跑步知识 · EP.03'.

    Falls back to the default tag when seq is missing/non-numeric.
    """
    try:
        return f"跑步知识 · EP.{int(seq):02d}"
    except (ValueError, TypeError):
        return DEFAULT_TITLE["tag"]


_SUBTITLE_MAX = 22
_SUBTITLE_SPLIT_RE = re.compile(r"[。，；、,;]")


def _truncate(text: str, max_len: int = _SUBTITLE_MAX) -> str:
    """Truncate ``text`` to the first punctuation boundary or ``max_len`` chars.

    Cuts at the first occurrence of 。，；、,; (whichever comes first) and then
    applies the character cap. Returns the empty string when ``text`` is empty.
    """
    if not text:
        return ""
    parts = _SUBTITLE_SPLIT_RE.split(text, maxsplit=1)
    return parts[0].strip()[:max_len]


def _brand_tag(channel: str, module_name: str, seq: str) -> str:
    """Title-card tag. With a configured channel → '频道 · 模块短名' (module short
    = text before the first ：/: separator, keeping the tag bar compact). Without
    a channel → legacy '跑步知识 · EP.0X' (backward compatible)."""
    if channel:
        mod_short = (module_name or "").split("：")[0].split(":")[0].strip()
        return f"{channel} · {mod_short}" if mod_short else channel
    return _ep_tag(seq) if seq else DEFAULT_TITLE["tag"]


def build_title_card(topic: str, learning_goal: str = "", seq: str = "",
                     subtitle: str = "", channel: str = "", module_name: str = "") -> dict:
    """Doc-mode title card from real episode metadata (mechanical, faithful).

    Subtitle precedence:
    1. ``subtitle`` — authored by the digest-writer (short, fits one line).
    2. ``_truncate(learning_goal)`` — first clause of the learning goal, capped
       at ``_SUBTITLE_MAX`` chars (prevents overflow; annotation prefixes stay
       hidden because the authored subtitle takes priority when supplied).
    3. ``DEFAULT_TITLE["subtitle"]`` — column default (never a fabrication).
    """
    if subtitle:
        sub = subtitle
    else:
        sub = _truncate(learning_goal) or DEFAULT_TITLE["subtitle"]
    return {
        "tag": _brand_tag(channel, module_name, seq),
        "title": topic or DEFAULT_TITLE["title"],
        "subtitle": sub,
        "bg_prompt": DEFAULT_TITLE["bg_prompt"],
    }


# Matches a 结尾要点 enumerator: 第一/第二/… followed by a separator (，：、,: or
# whitespace). Requires the separator so "第一次"/"第一名" don't false-match.
_TAKEAWAY_RE = re.compile(r"第[一二三四五六七八九十]+[，：、,:\s]")


def parse_takeaways(text: str, max_n: int = 3, max_len: int = 22) -> list:
    """Extract up to ``max_n`` takeaway phrases from a 结尾要点 beat that lists
    them as 第一…第二…第三…. Each bullet is the first clause after its marker,
    truncated to ``max_len``. Returns [] when the convention is not cleanly
    present — the caller MUST fall back rather than fabricate a recap.
    """
    if not text:
        return []
    parts = _TAKEAWAY_RE.split(text)
    if len(parts) < 2:          # no 第N enumerators → not a takeaways beat
        return []
    bullets = []
    for seg in parts[1:1 + max_n]:
        seg = seg.strip()
        # keep the first sentence/clause only
        seg = re.split(r"[。\n；;]", seg, maxsplit=1)[0].strip()
        if len(seg) > max_len:
            seg = re.split(r"[，,]", seg, maxsplit=1)[0].strip()
        seg = seg.strip("、，,。；;：: ")   # drop trailing enumerator punctuation
        if seg:
            bullets.append(seg[:max_len])
    return bullets


def _bullet_grounded(bullet: str, script_text: str, threshold: float = 0.5) -> bool:
    """CJK-aware character-bigram overlap check for authored bullets.

    Builds sets of adjacent character pairs from ``bullet`` and ``script_text``
    (stripping punctuation/whitespace so only CJK characters and alphanumerics
    participate). Returns True when the fraction of bullet bigrams that also appear
    in the script meets ``threshold``.

    Design: lenient by design — paraphrased summaries pass, off-topic fabrications
    fail. Do NOT reuse ledger._anchor_grounded: that function uses ASCII-only
    tokenisation ([a-z0-9…]) which returns [] for any Chinese string and would
    vacuously pass every bullet (verifier S1-A2).
    """
    _STRIP = re.compile(r'[\s　-〿＀-￯ -⁯ -/'
                        r':-@[-`{-~]+')

    def _bigrams(text: str) -> set:
        # keep CJK + alnum, strip punctuation/whitespace
        chars = _STRIP.sub("", text)
        return {chars[i:i + 2] for i in range(len(chars) - 1)}

    b_set = _bigrams(bullet)
    if not b_set:
        return True  # degenerate: nothing to check, treat as grounded
    s_set = _bigrams(script_text)
    overlap = len(b_set & s_set) / max(1, len(b_set))
    return overlap >= threshold


def build_end_card(last_beat_text: str, learning_goal: str = "",
                   authored_bullets: list | None = None,
                   script_text: str = "") -> dict:
    """Doc-mode end card.

    Priority:
    1. ``authored_bullets`` (from digest-writer) — each checked against
       ``script_text`` via CJK bigram overlap; grounded bullets used as the card.
       If all bullets fail the overlap check, falls back to 2.
    2. ``parse_takeaways(last_beat_text)`` — extract 第一/二/三 bullets from
       the last beat text.
    3. Neutral card: the learning goal on one line (never fabricates bullets).

    bg_prompt + footer stay the column defaults.
    """
    # 1. Prefer authored bullets (from digest-writer), grounded against script.
    if authored_bullets:
        grounded = [b for b in authored_bullets[:3]
                    if _bullet_grounded(b, script_text)]
        if grounded:
            numbered = [[f"{i + 1:02d}", b] for i, b in enumerate(grounded)]
            return {
                "tag": f"本期 {len(grounded)} 个要点",
                "bullets": numbered,
                "footer": "关注 · 科学跑步不靠感觉",
                "bg_prompt": DEFAULT_END["bg_prompt"],
            }
        # all authored bullets dropped → fall through to parse_takeaways

    # 2. Parse takeaways from last beat text.
    bullets = parse_takeaways(last_beat_text)
    if bullets:
        numbered = [[f"{i + 1:02d}", b] for i, b in enumerate(bullets)]
        tag = f"本期 {len(bullets)} 个要点"
    else:
        numbered = [["", learning_goal]] if learning_goal else []
        tag = "本期小结"
    return {
        "tag": tag,
        "bullets": numbered,
        "footer": "关注 · 科学跑步不靠感觉",
        "bg_prompt": DEFAULT_END["bg_prompt"],
    }


def _end_ts(dur: float) -> str:
    return f"0:00:{dur:05.2f}"


def _rect(x: int, y: int, w: int, h: int, color: str, alpha: str, end: str) -> str:
    """A filled rectangle drawn with ASS vector drawing (\\p1), top-left anchored."""
    return (f"Dialogue: 0,0:00:00.00,{end},H1,,0,0,0,,"
            r"{\an7\pos(" + f"{x},{y}" + r")\1c" + color + r"\1a" + alpha +
            r"\bord0\shad0\p1}m 0 0 l " + f"{w} 0 {w} {h} 0 {h}" + r"{\p0}")


def _text(style: str, x: int, y: int, s: str, end: str, fade_out: int = 250) -> str:
    """Left-middle anchored text with a fade in (and optional fade out).

    ``fade_out=0`` holds the text at full opacity through ``end`` — used by the
    terminal end card so its screenshottable summary is still fully readable on
    the film's last frame (a fade-out leaves the video ending on greyed/blank
    text, defeating the "本期 N 个要点" recap)."""
    return (f"Dialogue: 1,0:00:00.00,{end},{style},,0,0,0,,"
            r"{\an4\pos(" + f"{x},{y}" + r")\fad(450," + str(fade_out) + r")}" + s)


# Title layout: the left text column is ~840px wide (x=140 → 980). At the max
# H1 size only ~6 CJK chars fit one line, but episode titles are often full
# sentences, so the title is length-adaptive: it shrinks the font and breaks at
# a punctuation boundary into ≤2 lines rather than auto-wrapping (which grows
# both ways from the anchor and collides with the tag / underline).
_TITLE_COL_W = 840
_TITLE_FS_MAX = 128
_TITLE_FS_MIN = 56
# Split at CJK punctuation AND sentence enders (？！?!。) so a title like
# "…是伪科学？UESCA…" breaks after the 「？」 instead of mid-word.
_TITLE_SPLIT_RE = re.compile(r"[，,：:、；;？！?!。]")


def _is_ascii_alnum(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _shift_cut_off_ascii_word(title: str, cut: str) -> int:
    """If a hard split would land inside an ASCII alphanumeric run (e.g. cutting
    'UESCA' into 'U'|'ESCA'), move the cut to the nearest boundary where the two
    neighbouring chars are not both ASCII-alnum."""
    n = len(title)
    if not (0 < cut < n and _is_ascii_alnum(title[cut - 1]) and _is_ascii_alnum(title[cut])):
        return cut
    for d in range(1, n):
        for c in (cut - d, cut + d):
            if 0 < c < n and not (_is_ascii_alnum(title[c - 1]) and _is_ascii_alnum(title[c])):
                return c
    return cut


def _layout_title(title: str) -> tuple:
    """Return (lines, fontsize) so the title fits the left column in ≤2 lines.

    A title that fits one line at the max size stays one big line. A longer one
    is split at the punctuation boundary nearest the middle (hard split if it has
    none) and the font shrinks so the longest line fits the column width. A hard
    split never cuts an ASCII word/number in half (_shift_cut_off_ascii_word).
    """
    title = (title or "").strip()
    per_line_max = _TITLE_COL_W // _TITLE_FS_MAX          # ~6 chars at fs128
    if len(title) <= per_line_max:
        return [title], _TITLE_FS_MAX
    seps = [m.end() for m in _TITLE_SPLIT_RE.finditer(title)]
    mid = len(title) / 2
    if seps:
        cut = min(seps, key=lambda i: abs(i - mid))
    else:
        cut = _shift_cut_off_ascii_word(title, round(len(title) / 2))
    parts = [title[:cut].strip("，,：:、；;？！?!。 "), title[cut:].strip("，,：:、；;？！?!。 ")]
    lines = [p for p in parts if p] or [title]
    longest = max(len(p) for p in lines)
    fs = max(_TITLE_FS_MIN, min(_TITLE_FS_MAX, _TITLE_COL_W // longest))
    return lines, fs


def _title_line(y: int, fs: int, s: str, end: str) -> str:
    """One title line: top-left anchored (grows downward, predictable) with an
    inline font-size override so multi-line titles stay inside the column."""
    return (f"Dialogue: 1,0:00:00.00,{end},H1,,0,0,0,,"
            r"{\an7\pos(140," + str(y) + r")\fs" + str(fs) + r"\fad(450,250)}" + s)


def build_title_ass(tag: str, title: str, subtitle: str, dur: float = TITLE_DUR) -> str:
    """ASS overlay for the title card: split layout (dimmed left text column),
    amber tag bar, a length-adaptive title (≤2 lines) with an amber underline,
    and a one-line subtitle. The underline + subtitle follow the title block so
    a two-line title never collides with the tag above or the subtitle below."""
    end = _end_ts(dur)
    lines, fs = _layout_title(title)
    line_h = int(fs * 1.22)
    title_top = 452                                        # below the tag bar (392–432)
    events = [
        _rect(0, 0, 1920, 1080, _BLACK, "&HB0&", end),     # global dim
        _rect(0, 0, 980, 1080, _BLACK, "&H50&", end),      # left text column
        _rect(140, 392, 8, 40, _ACCENT, "&H00&", end),     # tag accent bar
        _text("Tag", 168, 412, tag, end),
    ]
    for i, line in enumerate(lines):
        events.append(_title_line(title_top + i * line_h, fs, line, end))
    underline_y = title_top + len(lines) * line_h + 16
    events.append(_rect(146, underline_y, 190, 8, _ACCENT, "&H00&", end))
    events.append(_text("Body", 148, underline_y + 60, subtitle, end))
    return _HEAD + "\n".join(events) + "\n"


def build_end_ass(tag: str, bullets: list, footer: str, dur: float = END_DUR) -> str:
    """ASS overlay for the end card: a screenshottable "本期 3 个要点" summary —
    amber tag bar, numbered bullets with hairline dividers, a footer bar with
    关注 / 下期. `bullets` is a list of [num, line] pairs.

    The end card is the film's TERMINAL clip: the overlay (and its rendered clip,
    which the assembler makes ``END_DUR + DISSOLVE_FADE`` long so it joins the
    xfade chain) is held to that full length and the text does NOT fade out, so
    the last frame shows the fully-readable, screenshottable recap."""
    end = _end_ts(dur + DISSOLVE_FADE)                      # hold through the dissolve tail
    events = [
        _rect(0, 0, 1920, 1080, _BLACK, "&H38&", end),     # light global dim
        _rect(140, 196, 8, 44, _ACCENT, "&H00&", end),     # tag accent bar
        _text("Tag", 168, 218, tag, end, fade_out=0),
    ]
    y = 340
    for idx, (num, line) in enumerate(bullets):
        events.append(_text("Num", 140, y, num, end, fade_out=0))
        events.append(_text("Body", 230, y + 4, line, end, fade_out=0))
        if idx != len(bullets) - 1:
            events.append(_rect(232, y + 70, 1100, 2, _GREY, "&H50&", end))
        y += 150
    events.append(_rect(140, 902, 8, 40, _ACCENT, "&H00&", end))
    events.append(_text("Foot", 168, 922, footer, end, fade_out=0))
    return _HEAD + "\n".join(events) + "\n"
