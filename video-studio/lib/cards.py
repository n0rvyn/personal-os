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
    "bg_prompt": "清晨田径场跑道，一位跑者迎着晨光在画面右侧奔跑的剪影，"
                 "左侧大量留白晨雾，电影感，冷蓝调",
}
DEFAULT_END = {
    "tag": "本期 3 个要点",
    "bullets": [
        ["01", "乳酸阈 = 你还能稳定坚持的最快配速"],
        ["02", "用 VDOT 表反推各档训练配速"],
        ["03", "每周 1-2 次阈值跑，单次 20-40 分钟"],
    ],
    "footer": "关注 · 科学跑步不靠感觉      下期 · 跑姿与步频",
    "bg_prompt": "清晨空旷红色跑道笔直延伸向远方，晨雾弥漫，冷蓝调，"
                 "大量留白，无人，电影感",
}


def _end_ts(dur: float) -> str:
    return f"0:00:{dur:05.2f}"


def _rect(x: int, y: int, w: int, h: int, color: str, alpha: str, end: str) -> str:
    """A filled rectangle drawn with ASS vector drawing (\\p1), top-left anchored."""
    return (f"Dialogue: 0,0:00:00.00,{end},H1,,0,0,0,,"
            r"{\an7\pos(" + f"{x},{y}" + r")\1c" + color + r"\1a" + alpha +
            r"\bord0\shad0\p1}m 0 0 l " + f"{w} 0 {w} {h} 0 {h}" + r"{\p0}")


def _text(style: str, x: int, y: int, s: str, end: str) -> str:
    """Left-middle anchored text with a gentle fade in/out."""
    return (f"Dialogue: 1,0:00:00.00,{end},{style},,0,0,0,,"
            r"{\an4\pos(" + f"{x},{y}" + r")\fad(450,250)}" + s)


def build_title_ass(tag: str, title: str, subtitle: str, dur: float = TITLE_DUR) -> str:
    """ASS overlay for the title card: split layout (dimmed left text column),
    amber tag bar, large title with an amber underline, a one-line subtitle."""
    end = _end_ts(dur)
    events = [
        _rect(0, 0, 1920, 1080, _BLACK, "&HB0&", end),     # global dim
        _rect(0, 0, 980, 1080, _BLACK, "&H50&", end),      # left text column
        _rect(140, 392, 8, 40, _ACCENT, "&H00&", end),     # tag accent bar
        _text("Tag", 168, 412, tag, end),
        _text("H1", 140, 500, title, end),
        _rect(146, 596, 190, 8, _ACCENT, "&H00&", end),    # title underline
        _text("Body", 148, 660, subtitle, end),
    ]
    return _HEAD + "\n".join(events) + "\n"


def build_end_ass(tag: str, bullets: list, footer: str, dur: float = END_DUR) -> str:
    """ASS overlay for the end card: a screenshottable "本期 3 个要点" summary —
    amber tag bar, numbered bullets with hairline dividers, a footer bar with
    关注 / 下期. `bullets` is a list of [num, line] pairs."""
    end = _end_ts(dur)
    events = [
        _rect(0, 0, 1920, 1080, _BLACK, "&H38&", end),     # light global dim
        _rect(140, 196, 8, 44, _ACCENT, "&H00&", end),     # tag accent bar
        _text("Tag", 168, 218, tag, end),
    ]
    y = 340
    for idx, (num, line) in enumerate(bullets):
        events.append(_text("Num", 140, y, num, end))
        events.append(_text("Body", 230, y + 4, line, end))
        if idx != len(bullets) - 1:
            events.append(_rect(232, y + 70, 1100, 2, _GREY, "&H50&", end))
        y += 150
    events.append(_rect(140, 902, 8, 40, _ACCENT, "&H00&", end))
    events.append(_text("Foot", 168, 922, footer, end))
    return _HEAD + "\n".join(events) + "\n"
