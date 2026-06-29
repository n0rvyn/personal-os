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
