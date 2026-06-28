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


# Chinese sentence terminators. Period kept simple to avoid eating
# decimal points; we only split on these specific punctuation marks.
_SENTENCE_TERMINATORS = "。！？；"


def _split_sentences(text: str) -> list[str]:
    """Split text by Chinese terminators, keeping the terminator with each sentence.

    Empty fragments are dropped. Whitespace-only fragments are dropped.
    """
    if not text:
        return []
    # Build a regex that splits AFTER any of the terminators.
    pattern = f"[{re.escape(_SENTENCE_TERMINATORS)}]"
    parts = re.split(f"(?<={pattern})", text)
    return [p for p in parts if p and p.strip()]


def _fmt(ms: int) -> str:
    """Format milliseconds as HH:MM:SS,mmm (SRT timestamp)."""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(beats: list[dict]) -> str:
    """Build SRT text from a list of beats.

    Each beat is a dict with keys:
      - text:     str   — narration text (will be split by Chinese terminators)
      - start_ms: int   — absolute start time in ms (relative to the whole video)
      - dur_ms:   int   — duration of this beat's narration in ms

    Returns the SRT file contents as a single string, trailing newline included.
    """
    cues: list[str] = []
    cue_index = 0

    for beat in beats:
        text = beat.get("text", "") or ""
        start_ms = int(beat.get("start_ms", 0))
        dur_ms = int(beat.get("dur_ms", 0))

        sentences = _split_sentences(text)
        if not sentences:
            continue

        n = len(sentences)
        # Sentence length = character count (including punctuation, which stays attached).
        lengths = [len(s) for s in sentences]
        total = sum(lengths)
        if total <= 0 or dur_ms <= 0:
            continue

        # First n-1 sentences: round(len_i / total * dur_ms).
        # Last sentence: dur_ms - sum_of_others (absorbs any rounding remainder).
        prefix_ms: list[int] = []
        for i in range(n - 1):
            prefix_ms.append(round(lengths[i] / total * dur_ms))

        if n == 1:
            offsets = [0]
            durs = [dur_ms]
        else:
            used = sum(prefix_ms)
            last_dur = dur_ms - used
            # Guard against negative last_dur from accumulated rounding > dur_ms.
            if last_dur < 0:
                last_dur = 0
            offsets = [0]
            for d in prefix_ms:
                offsets.append(offsets[-1] + d)
            durs = prefix_ms + [last_dur]

        for i, sentence in enumerate(sentences):
            cue_index += 1
            cue_start = start_ms + offsets[i]
            cue_end = start_ms + offsets[i] + durs[i]
            cues.append(
                f"{cue_index}\n"
                f"{_fmt(cue_start)} --> {_fmt(cue_end)}\n"
                f"{sentence}"
            )

    # Join cues with a blank line separator (standard SRT block separator).
    return "\n\n".join(cues) + "\n"
