"""SRT subtitle builder for video-studio.

Builds SRT cue text from a list of beats, where each beat is
{text, start_ms, dur_ms}. Sentence splitting uses Chinese punctuation
(。！？；), keeping the punctuation attached to the preceding sentence.

Timing math: within a beat, sentence length = character count (including
punctuation). First n-1 sentences get round(len_i / total * dur_ms) ms
each; the LAST sentence absorbs the remainder (dur_ms - sum_of_others)
to guarantee no drift across the beat. Timestamps are cumulative across
beats.
"""
from __future__ import annotations

import re


# Sentence terminators — full-width AND their half-width twins, because the
# script LLM mixes them (writes "那是 E 档;到了…" with a half-width ';'). The
# half-width '.' is deliberately EXCLUDED so decimals ("4.57") aren't split.
_SENTENCE_TERMINATORS = "。！？；!?;"

# A cue longer than this (chars) is hard to read as one line, so an over-long
# segment is split further at commas. ~24 keeps a cue within the usable width
# (PlayResX minus L/R margins) even with keyword emphasis widening some glyphs.
_SOFT_MAX = 24

# Split AFTER a comma (full/half-width), but NOT when a digit follows — guards
# thousands separators like "4,000" while still splitting "5.34,这个" (a decimal
# followed by a clause comma) at the comma.
_COMMA_SPLIT = re.compile(r"(?<=[，,])(?![0-9])")


def _split_long_at_commas(seg: str) -> list[str]:
    """Break an over-long segment at comma boundaries, then greedily re-merge
    the pieces so each cue is as full as possible without exceeding _SOFT_MAX."""
    pieces = [p for p in _COMMA_SPLIT.split(seg) if p and p.strip()]
    if len(pieces) < 2:
        return [seg]
    merged: list[str] = []
    for p in pieces:
        if merged and len(merged[-1]) + len(p) <= _SOFT_MAX:
            merged[-1] += p
        else:
            merged.append(p)
    return merged


def _split_sentences(text: str) -> list[str]:
    """Split text into subtitle-sized cues.

    First split after any sentence terminator (keeping the terminator attached);
    then break any still-over-long segment at commas. Empty/whitespace-only
    fragments are dropped.
    """
    if not text:
        return []
    pattern = f"[{re.escape(_SENTENCE_TERMINATORS)}]"
    parts = [p for p in re.split(f"(?<={pattern})", text) if p and p.strip()]
    out: list[str] = []
    for seg in parts:
        if len(seg) <= _SOFT_MAX:
            out.append(seg)
        else:
            out.extend(_split_long_at_commas(seg))
    return out


def _fmt(ms: int) -> str:
    """Format milliseconds as HH:MM:SS,mmm (SRT timestamp)."""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _beat_cues(beat: dict) -> list[tuple[int, int, str]]:
    """Split one beat into (start_ms, end_ms, sentence) cues. Single source of
    the timing math shared by build_srt and build_ass."""
    text = beat.get("text", "") or ""
    start_ms = int(beat.get("start_ms", 0))
    dur_ms = int(beat.get("dur_ms", 0))

    sentences = _split_sentences(text)
    if not sentences:
        return []
    n = len(sentences)
    lengths = [len(s) for s in sentences]
    total = sum(lengths)
    if total <= 0 or dur_ms <= 0:
        return []

    prefix_ms = [round(lengths[i] / total * dur_ms) for i in range(n - 1)]
    if n == 1:
        offsets = [0]
        durs = [dur_ms]
    else:
        last_dur = max(dur_ms - sum(prefix_ms), 0)
        offsets = [0]
        for d in prefix_ms:
            offsets.append(offsets[-1] + d)
        durs = prefix_ms + [last_dur]

    return [(start_ms + offsets[i], start_ms + offsets[i] + durs[i], s)
            for i, s in enumerate(sentences)]


def build_srt(beats: list[dict]) -> str:
    """Build SRT text from a list of beats (each {text, start_ms, dur_ms}).

    Returns the SRT file contents as a single string, trailing newline included.
    """
    cues: list[str] = []
    cue_index = 0
    for beat in beats:
        for cue_start, cue_end, sentence in _beat_cues(beat):
            cue_index += 1
            cues.append(f"{cue_index}\n{_fmt(cue_start)} --> {_fmt(cue_end)}\n{sentence}")
    return "\n\n".join(cues) + "\n"


# ---------------------------------------------------------------------------
# ASS (libass) builder — richer subtitles: keyword emphasis (放大+金色+弹入).
# ---------------------------------------------------------------------------

# Style B (chosen 2026-06-29): bold white, clean dark outline + soft shadow,
# NO box; keyword = gold (&HBBGGRR=00D2FF) + deep outline + scale-in pop.
ASS_STYLE_DEFAULT = (
    "Style: Default,PingFang SC,52,&H00FFFFFF,&H000000FF,&H00121212,&H78000000,"
    "1,0,0,0,100,100,1,0,1,3.6,1.6,2,90,90,78,1"
)
_EMPH_OPEN = (r"{\fs74\c&H00D2FF&\3c&H00102A45&\b1"
              r"\t(0,120,\fscx115\fscy115)\t(120,220,\fscx100\fscy100)}")
_EMPH_CLOSE = r"{\r}"

_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1920\n"
    "PlayResY: 1080\n"
    # WrapStyle 0 = smart balanced wrapping: a sentence longer than the usable
    # width (PlayResX minus the 90px L/R margins) folds into balanced lines
    # instead of running off-frame. WrapStyle 2 (no auto-wrap) let long
    # narration sentences overflow horizontally.
    "WrapStyle: 0\n"
    "ScaledBorderAndShadow: yes\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding\n"
    f"{ASS_STYLE_DEFAULT}\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _ass_t(ms: int) -> str:
    """Format ms as ASS timestamp H:MM:SS.cc (centiseconds)."""
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, millis = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{millis // 10:02d}"


def _emphasize(text: str, keywords: list[str]) -> str:
    """Wrap each keyword phrase occurrence with the emphasis override. Longest
    first so overlapping terms wrap cleanly. Empty/absent keywords = no-op."""
    for kw in sorted((k for k in keywords if k), key=len, reverse=True):
        if kw in text:
            text = text.replace(kw, _EMPH_OPEN + kw + _EMPH_CLOSE)
    return text


def build_ass(beats: list[dict]) -> str:
    """Build an ASS subtitle file from beats. Each beat may carry an optional
    "keywords" list (phrases to emphasize within that beat's cues; may be empty
    — emphasis is selective, never forced)."""
    lines: list[str] = []
    for beat in beats:
        keywords = beat.get("keywords") or []
        for cue_start, cue_end, sentence in _beat_cues(beat):
            txt = _emphasize(sentence, keywords)
            lines.append(
                f"Dialogue: 0,{_ass_t(cue_start)},{_ass_t(cue_end)},Default,,0,0,0,,{txt}")
    return _ASS_HEADER + "\n".join(lines) + "\n"
