"""Test-first contract for lib.subtitle.build_srt.

These tests lock down the exact behavior of the srt constructor:
- Splits beat text by Chinese punctuation (。！？；), keeping punctuation with each sentence.
- Drops empty sentences.
- Sentence length = character count including punctuation.
- Within a beat, first n-1 sentences get `round(len_i / total * dur_ms)` ms each.
- The LAST sentence in a beat gets the remainder (`dur_ms - sum_of_others`) to guarantee
  no drift across the beat.
- Timestamps are cumulative across beats (each beat's start_ms added to its internal time).
- Output format: index / HH:MM:SS,mmm --> HH:MM:SS,mmm / text.

These are the "钉死值" (pinned values) from the plan. The implementation must match
character-for-character; changing the test = test tampering.
"""
from lib.subtitle import build_srt


def _norm(s: str) -> str:
    """Strip trailing whitespace per line for stable comparison."""
    return "\n".join(line.rstrip() for line in s.rstrip().splitlines())


def test_build_srt_two_beat_pin_values():
    """Pinned case: beat0 (2 sentences) + beat1 (1 sentence).

    beat0 text "今天聊乳酸阈。配速怎么定？" audio_length=4000ms start=0
      - 2 sentences: "今天聊乳酸阈。" (7 chars) / "配速怎么定？" (6 chars), total=13
      - cue1 = round(7/13*4000) = 2154ms
      - cue2 = 4000 - 2154     = 1846ms
    beat1 text "先测VDOT。" audio_length=2000ms start=4000
      - 1 sentence, occupies the full 2000ms
    """
    beats = [
        {"text": "今天聊乳酸阈。配速怎么定？", "start_ms": 0,    "dur_ms": 4000},
        {"text": "先测VDOT。",                 "start_ms": 4000, "dur_ms": 2000},
    ]
    out = build_srt(beats)
    expected = (
        "1\n"
        "00:00:00,000 --> 00:00:02,154\n"
        "今天聊乳酸阈。\n"
        "\n"
        "2\n"
        "00:00:02,154 --> 00:00:04,000\n"
        "配速怎么定？\n"
        "\n"
        "3\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "先测VDOT。\n"
    )
    assert _norm(out) == _norm(expected), (
        f"srt mismatch.\n--- got ---\n{out}\n--- expected ---\n{expected}"
    )


def test_build_srt_returns_string_with_three_cues():
    """Sanity: exactly 3 subtitle cues (2 from beat0 + 1 from beat1)."""
    beats = [
        {"text": "今天聊乳酸阈。配速怎么定？", "start_ms": 0,    "dur_ms": 4000},
        {"text": "先测VDOT。",                 "start_ms": 4000, "dur_ms": 2000},
    ]
    out = build_srt(beats)
    # A cue is "index\nstart --> end\ntext\n"; 3 cues -> 3 occurrences of "-->"
    assert out.count("-->") == 3, f"expected 3 cues, got {out.count('-->')} in:\n{out}"


def test_build_srt_last_cue_ends_at_total_duration():
    """末条结束 = sum of all audio_length_ms (no drift).

    4000 + 2000 = 6000ms = 00:00:06,000
    """
    beats = [
        {"text": "今天聊乳酸阈。配速怎么定？", "start_ms": 0,    "dur_ms": 4000},
        {"text": "先测VDOT。",                 "start_ms": 4000, "dur_ms": 2000},
    ]
    out = build_srt(beats)
    assert "00:00:06,000" in out, f"final timestamp 00:00:06,000 missing in:\n{out}"
    # The last cue's end timestamp must be 00:00:06,000
    # Split by double newline to get cues
    cues = [c for c in out.strip().split("\n\n") if c.strip()]
    last_cue = cues[-1]
    assert "00:00:06,000" in last_cue, f"last cue should end at 00:00:06,000, got:\n{last_cue}"


def test_build_srt_punctuation_stays_with_sentence():
    """Punctuation (。！？；) stays attached to the preceding sentence, not dropped."""
    beats = [
        {"text": "今天聊乳酸阈。配速怎么定？先测VDOT；", "start_ms": 0, "dur_ms": 6000},
    ]
    out = build_srt(beats)
    # 3 sentences: "今天聊乳酸阈。" (7) / "配速怎么定？" (6) / "先测VDOT；" (6), total=19
    # cue1 = round(7/19*6000) = 2211ms
    # cue2 = round(6/19*6000) = 1895ms
    # cue3 = 6000 - 2211 - 1895 = 1894ms
    assert "今天聊乳酸阈。" in out
    assert "配速怎么定？" in out
    assert "先测VDOT；" in out
    # No empty sentences
    assert "\n\n\n" not in out, f"empty sentence detected in:\n{out}"


def test_build_srt_empty_beat_text_raises_or_skips():
    """Edge: a beat with text that produces zero sentences (only whitespace/punct already removed).
    Pure whitespace should not produce a cue."""
    beats = [
        {"text": "   ", "start_ms": 0, "dur_ms": 1000},
        {"text": "有内容。", "start_ms": 1000, "dur_ms": 2000},
    ]
    out = build_srt(beats)
    # Only 1 cue from the second beat
    assert out.count("-->") == 1, f"expected 1 cue, got {out.count('-->')} in:\n{out}"
    assert "有内容。" in out
