"""ffmpeg argv constructor for video-studio spike.

These pure functions build the ffmpeg command-line argument lists the driver
(`spike_run.py`) will pass to `subprocess.run`. They do NOT invoke ffmpeg.

Design contract (see lib/tests/test_ffcmd.py):
- Every beat segment (still / video / chart) must wire the beat's narration
  mp3 as a SECOND input (`-i {audio_mp3}`) and map audio from that input
  (`-map 1:a`). This guarantees the concat output carries a continuous
  narration track — the fix for the "audio never wired up" gap caught by
  the previous verifier.
- All segments are normalized to 1920x1080, 30 fps, yuv420p, aac stereo so
  the concat demuxer doesn't choke on mismatched A/V params.
- mux_final_cmd burns the .srt subtitles in (PingFang SC) and ducks the BGM
  under the narration via sidechaincompress + amix.
- All functions return list[str] (NEVER shell=True) — the driver will pass
  these straight to subprocess.run.
"""
from __future__ import annotations

# Hard-subtitle style. Hard-coded for the spike; Stage 1 will move to config.
FONT_STYLE = (
    "FontName=PingFang SC,FontSize=18,OutlineColour=&H80000000,"
    "BorderStyle=3,Outline=1,MarginV=40"
)


def _still_or_chart_filtergraph(dur_s: float) -> str:
    """Build the -vf filtergraph for a still (or chart) segment.

    Ken Burns via zoompan (subtle 1.0 -> 1.1 over the segment), preceded by a
    scale+crop that forces the source to fill 1920x1080.

    The per-frame zoom increment is derived from the segment's frame count so
    the push-in spreads across the WHOLE segment and reaches 1.1 only on the
    last frame. A fixed increment (e.g. 0.0015) hits the 1.1 cap in ~2.2s and
    then freezes the image while the narration keeps playing — the late-frame
    "image stops moving but subtitle still reading" bug.
    """
    frames = max(int(dur_s * 30), 1)
    inc = 0.1 / frames  # total 1.0 -> 1.1 spread evenly over the segment
    return (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        f"zoompan=z='min(zoom+{inc:.6f},1.1)':d={frames}:s=1920x1080:fps=30"
    )


def still_segment_cmd(
    png: str, dur_s: float, out_mp4: str, audio_mp3: str,
) -> list[str]:
    """Build argv to render a still-image segment with narration audio.

    Inputs:
        png:        path to the still image (e.g. character_ref or scene).
        dur_s:      segment length in seconds (drives -t and zoompan duration).
        out_mp4:    path to write the segment mp4.
        audio_mp3:  beat's narration mp3 (wired as -i input 1, mapped to 1:a).
    """
    vf = _still_or_chart_filtergraph(dur_s)
    return [
        "-loop", "1",
        "-i", png,
        "-i", audio_mp3,
        "-t", str(dur_s),
        "-r", "30",
        "-vf", vf,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-shortest",
        out_mp4,
    ]


def video_segment_cmd(
    src_mp4: str, dur_s: float, out_mp4: str, audio_mp3: str,
) -> list[str]:
    """Build argv to render a video segment (e.g. S2V) with narration audio.

    The S2V clip's own audio track is intentionally discarded (-map 0:v
    only on the video side) and the beat's narration mp3 takes its place
    (-map 1:a). This keeps the narration continuous across mixed beat types.

    For S2V clips that may be shorter than dur_s, we let tpad clone the
    last frame to pad out; for clips that are longer, -t caps the length.
    """
    frames = int(dur_s * 30)
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        f"tpad=stop_mode=clone:stop_duration={int(dur_s)},"
        f"fps=30"
    )
    return [
        "-i", src_mp4,
        "-i", audio_mp3,
        "-t", str(dur_s),
        "-r", "30",
        "-vf", vf,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-shortest",
        out_mp4,
    ]


def chart_segment_cmd(
    png: str, dur_s: float, out_mp4: str, audio_mp3: str,
) -> list[str]:
    """Build argv to render a chart-image segment with narration audio.

    Same wiring as still_segment_cmd — a chart is just a static image that
    benefits from a slight Ken Burns push-in to avoid the 'dead frame' feel.
    """
    vf = _still_or_chart_filtergraph(dur_s)
    return [
        "-loop", "1",
        "-i", png,
        "-i", audio_mp3,
        "-t", str(dur_s),
        "-r", "30",
        "-vf", vf,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-shortest",
        out_mp4,
    ]


def concat_cmd(segment_list_file: str, out_mp4: str) -> list[str]:
    """Build argv to concat per-beat segments into base.mp4.

    Uses the concat demuxer with -safe 0 (paths are local). Re-encodes both
    video and audio to libx264 / aac so any tiny drift in the per-segment
    params (e.g. SAR) doesn't break the concat.
    """
    return [
        "-f", "concat",
        "-safe", "0",
        "-i", segment_list_file,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        out_mp4,
    ]


def mux_final_cmd(
    base_mp4: str,
    srt: str,
    bgm: str,
    out_mp4: str,
    duck_threshold: float = 0.03,
    bgm_volume: str = "0.12",
) -> list[str]:
    """Build argv to burn subtitles and duck BGM under narration.

    Filter chain (audio):
        [1:a] volume=BGM_VOL, aloop=... [bg];
        [bg] [0:a] sidechaincompress=threshold=DUCK_THRESH ... [duck];
        [0:a] [duck] amix=inputs=2:duration=first [aout].

    Video chain:
        -vf subtitles=SRT:force_style='FONT_STYLE'

    Defaults keep the BGM well below narration (0.12 is a conservative
    starting point; the spike's calibration loop in spike_run.py will
    lower this if the measured gap is below 18 dB).
    """
    filter_complex = (
        f"[1:a]volume={bgm_volume},aloop=loop=-1:size=2e9[bg];"
        f"[bg][0:a]sidechaincompress="
        f"threshold={duck_threshold}:ratio=8:attack=5:release=300[duck];"
        f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    vf = f"subtitles={srt}:force_style='{FONT_STYLE}'"
    return [
        "-i", base_mp4,
        "-i", bgm,
        "-filter_complex", filter_complex,
        "-vf", vf,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        out_mp4,
    ]


def _duck_filter(bgm_volume: str, duck_threshold: float) -> str:
    return (
        f"[1:a]volume={bgm_volume},aloop=loop=-1:size=2e9[bg];"
        f"[bg][0:a]sidechaincompress="
        f"threshold={duck_threshold}:ratio=8:attack=5:release=300[duck];"
        f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )


def mux_final_ass_cmd(
    base_mp4: str,
    ass: str,
    bgm: str,
    out_mp4: str,
    duck_threshold: float = 0.03,
    bgm_volume: str = "0.12",
) -> list[str]:
    """Like mux_final_cmd but burns a libass ASS file (rich styling: keyword
    emphasis lives in the ASS itself, so no force_style here). Same BGM ducking.
    """
    return [
        "-i", base_mp4,
        "-i", bgm,
        "-filter_complex", _duck_filter(bgm_volume, duck_threshold),
        "-vf", f"ass={ass}",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        out_mp4,
    ]


# Light narration polish: gentle compression for level evenness + loudnorm to a
# broadcast target. Tonal EQ/exciter add no audible value on clean TTS (verified
# by ear in a loudness-matched A/B), so we keep only the "invisible but correct"
# loudness consistency — which also stabilizes the BGM sidechain ducking.
LIGHT_VOICE_AF = (
    "acompressor=threshold=-20dB:ratio=2:attack=10:release=150,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


def voice_polish_cmd(in_mp3: str, out_mp3: str) -> list[str]:
    """argv to apply the light narration polish (consistency + loudness)."""
    return ["-i", in_mp3, "-af", LIGHT_VOICE_AF, out_mp3]


# ---------------------------------------------------------------------------
# Dissolve assembly: video-only clips (each +fade longer than its narration)
# are xfade-chained; narration is a separate hard-concat track; then muxed.
# This keeps the narration-driven timing + subtitle alignment exact while the
# video cross-dissolves at each beat boundary.
# ---------------------------------------------------------------------------

DISSOLVE_FADE = 0.3  # seconds of cross-dissolve between beats (0.3 ≈ less face-on-face ghosting)

# POC6 shot-rhythm: shortest a single shot may dwell. A cut shorter than this
# reads as a glitch/flash, not an edit — so it floors both the per-shot
# duration and (via max_shots) how many shots a beat of given length can hold.
MIN_DWELL = 0.8


def max_shots(clip_len: float, min_dwell: float = MIN_DWELL) -> int:
    """How many shots a beat of clip_len seconds can hold without any shot
    dropping below min_dwell (always ≥ 1)."""
    # +eps so an exact multiple (e.g. 2.4 / 0.8) yields 3, not 2 via float floor.
    return max(1, int(clip_len / min_dwell + 1e-6))


def shot_durations(clip_len: float, weights: list[float],
                   min_dwell: float = MIN_DWELL) -> list[float]:
    """Split clip_len across shots by relative `weights` (content-driven dwell:
    a 金句 hold gets more time, a 列举 quick-cut less). Falls back to an EQUAL
    split if any weighted slice would drop below min_dwell — so quick cuts never
    become subliminal flashes. Sum is always exactly clip_len.

    Caller must cap len(weights) to max_shots(clip_len) first (equal split is
    only guaranteed ≥ min_dwell when the count fits)."""
    n = len(weights)
    if n <= 1:
        return [clip_len]
    total = sum(weights) or float(n)
    durs = [clip_len * w / total for w in weights]
    if min(durs) < min_dwell:
        durs = [clip_len / n] * n
    # absorb float remainder into the last slice so the sum is exact
    durs[-1] = clip_len - sum(durs[:-1])
    return durs


def still_clip_cmd(png: str, dur_s: float, out_mp4: str,
                   grade: str | None = None) -> list[str]:
    """Video-only Ken Burns clip (no narration) — for the xfade assembly.

    `grade` (POC5): an optional color-grade filterchain appended AFTER the Ken
    Burns push-in, so photographic stills share the series look. Pass None for
    charts (keep the designed data readable)."""
    vf = _still_or_chart_filtergraph(dur_s)
    if grade:
        vf += f",{grade}"
    return [
        "-loop", "1", "-i", png, "-t", str(dur_s), "-r", "30",
        "-vf", vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4,
    ]


def still_card_cmd(bg_png: str, dur_s: float, ass: str, out_mp4: str,
                   grade: str | None = None) -> list[str]:
    """Video-only title/end card clip: Ken Burns push-in on the AI background,
    with the libass design overlay (`ass`) burned in the same pass. No audio —
    the card joins the xfade dissolve chain like a still clip; its narration
    slot is filled by a matching silence segment (see silence_mp3_cmd).

    `grade` (POC5): applied to the background BEFORE the ass overlay, so the card
    bg matches the body's look while the amber/white design layer stays pure."""
    vf = _still_or_chart_filtergraph(dur_s)
    if grade:
        vf += f",{grade}"
    vf += f",ass={ass}"
    return [
        "-loop", "1", "-i", bg_png, "-t", str(dur_s), "-r", "30", "-vf", vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4,
    ]


def silence_mp3_cmd(dur_s: float, out_mp3: str) -> list[str]:
    """Generate a silence mp3 matching the MiniMax TTS narration params
    (mono, 32000 Hz, mp3) so it concats cleanly with the beat mp3s in the
    narration track. Used to pad the front/back of the narration for the
    (silent) title/end cards so mux's -shortest doesn't truncate them."""
    return [
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=mono:sample_rate=32000",
        "-t", str(dur_s), "-ar", "32000", "-ac", "1",
        "-c:a", "libmp3lame", out_mp3,
    ]


def video_clip_cmd(src_mp4: str, dur_s: float, out_mp4: str,
                   grade: str | None = None, src_dur: float | None = None) -> list[str]:
    """Video-only S2V clip normalized to 1920x1080/30, covering dur_s.

    `grade` (POC5): optional color-grade filterchain.
    `src_dur`: the source clip's real duration. When the source is SHORTER than
    the beat (S2V is a fixed ~5s but the narration runs longer), the clip is
    time-stretched (slow motion) to fill the whole beat instead of freezing on
    the last frame — a frozen picture under continuing narration reads as a
    glitch. If src_dur is unknown or the source is long enough, normalize + trim.
    """
    chain = ["scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080"]
    if src_dur and src_dur < dur_s:
        chain.append(f"setpts={dur_s / src_dur:.4f}*PTS")   # slow-mo to fill
    else:
        chain.append(f"tpad=stop_mode=clone:stop_duration={int(dur_s) + 1}")
    chain.append("fps=30")
    vf = ",".join(chain)
    if grade:
        vf += f",{grade}"
    return [
        "-i", src_mp4, "-t", str(dur_s), "-r", "30", "-vf", vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4,
    ]


def xfade_chain_cmd(clips: list[str], clip_durs: list[float], out_mp4: str,
                    fade: float = DISSOLVE_FADE) -> list[str]:
    """Cross-dissolve a list of video-only clips. clip_durs[i] are the clip
    lengths; each adjacent pair overlaps `fade` s. Output length =
    sum(clip_durs) - (n-1)*fade. With clip_durs[i] = narration_i + fade, the
    xfade offsets land exactly on the cumulative narration boundaries."""
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", c]
    if len(clips) == 1:
        return [*inputs, "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4]
    fc = []
    prev = "[0:v]"
    acc = clip_durs[0]
    for i in range(1, len(clips)):
        off = acc - fade
        label = "[vout]" if i == len(clips) - 1 else f"[x{i}]"
        fc.append(f"{prev}[{i}:v]xfade=transition=fade:duration={fade}:offset={off:.3f}{label}")
        prev = label
        acc += clip_durs[i] - fade
    return [*inputs, "-filter_complex", ";".join(fc), "-map", "[vout]",
            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4]


def concat_audio_cmd(list_file: str, out_aac: str) -> list[str]:
    """Hard-concat narration mp3s into one continuous aac track."""
    return ["-f", "concat", "-safe", "0", "-i", list_file,
            "-c:a", "aac", "-ar", "48000", "-ac", "2", out_aac]


def mux_av_cmd(video: str, audio: str, out_mp4: str) -> list[str]:
    """Mux a silent video track with the narration audio (trims video tail)."""
    return ["-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-shortest", out_mp4]
