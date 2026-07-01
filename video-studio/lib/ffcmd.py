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

# ---------------------------------------------------------------------------
# Camera-move constants (AR-3 — single canonical source; storyboard.py and
# steps.py import from here, never redefine).
# ---------------------------------------------------------------------------

CAMERA_MOVES = ("推近", "拉远", "横移", "竖摇", "静止")

# DP-002: column-locked zoom range — restrained, NOT vlog.
_ZOOM_MAX = 1.2   # upper bound for all moves
_ZOOM_MIN = 1.0   # lower bound / static level
# Pan budget: half of the 1.2x overscan available for each axis, calculated on
# the pre-upscaled 3840-wide frame (2x the original 1920).  After scale=3840:-2
# the zoomed virtual frame is 3840*1.2=4608 wide, 2160*1.2=2592 tall.
# overscan_x = 3840*(1.2-1.0) = 768 → half = 384
# overscan_y = 2160*(1.2-1.0) = 432 → half = 216
_PAN_X = 384.0
_PAN_Y = 216.0


def _fmt_zoom(v: float) -> str:
    """Format a zoom literal at 4dp then strip trailing zeros, keeping ≥1 decimal.

    This rounds float noise out (0.1*0.3 = 0.0300000…2 → 1.03) AND reproduces the
    historical literals byte-for-byte: cap=1.1 → '1.1' (NOT '1.1000'), so the
    intensity=1.0 backward-compat output is identical to the pre-intensity code.
    """
    s = f"{v:.4f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _still_or_chart_filtergraph(
    dur_s: float, camera_move: str = "推近", intensity: float = 1.0
) -> str:
    """Build the -vf filtergraph for a still (or chart) segment.

    camera_move selects the zoompan expression family (AR-3 / DP-002):
      推近 — Ken Burns push-in 1.0→1.0+δ (default, backward-compat)
      拉远 — zoom-out 1.0+δ→1.0
      横移 — z=1.1 fixed, pan-x across overscan
      竖摇 — z=1.1 fixed, pan-y across overscan
      静止 — z=1.0..1.02 极缓微推(δ=0.02 hardcoded, intensity ignored)
      (unknown/illegal) → fall back to 推近

    intensity ∈ [0,1] (clamped) is the director-chosen camera-move MAGNITUDE:
    it scales the zoom delta (δ = 0.2 * intensity) and the pan step within the
    fixed 克制 ceiling, so the move can never exceed today's restrained max.
    intensity=1.0 reaches the full 0.2 delta (cap=1.2); lower = gentler.
    静止 ignores intensity — drift is hardcoded at δ=0.02.

    DP-002: zoom range [1.0, 1.2]; pan stays within 1.2x overscan.
    d={frames} and s=1920x1080:fps=30 are NEVER changed by camera_move.
    """
    if camera_move not in CAMERA_MOVES:
        camera_move = "推近"

    intensity = max(0.0, min(1.0, float(intensity)))
    delta = 0.2 * intensity  # scaled zoom range (0.2 at intensity=1.0)

    frames = max(int(dur_s * 30), 1)
    prefix = (
        "scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,"
        "scale=3840:-2,"
    )
    suffix = f":d={frames}:s=1920x1080:fps=30"

    if camera_move == "推近":
        inc = delta / frames  # total 1.0 -> 1.0+δ spread evenly over the segment
        cap = 1.0 + delta
        zp = f"zoompan=z='min(zoom+{inc:.6f},{_fmt_zoom(cap)})'{suffix}"
    elif camera_move == "拉远":
        start = 1.0 + delta
        dec = delta / frames  # 1.0+δ -> 1.0 spread evenly
        zp = f"zoompan=z='max({_fmt_zoom(start)}-on*{dec:.6f},1.0)'{suffix}"
    elif camera_move == "横移":
        x_step = (_PAN_X * intensity) / frames
        zp = (
            f"zoompan=z='1.2'"
            f":x='on*{x_step:.6f}'"
            f":y='(ih-ih/zoom)/2'"
            f"{suffix}"
        )
    elif camera_move == "竖摇":
        y_step = (_PAN_Y * intensity) / frames
        zp = (
            f"zoompan=z='1.2'"
            f":x='(iw-iw/zoom)/2'"
            f":y='on*{y_step:.6f}'"
            f"{suffix}"
        )
    else:  # 静止 — 极缓微推(δ=0.02 硬编码极小值, intensity ignored)
        _static_drift = 0.02
        inc = _static_drift / frames
        cap = 1.0 + _static_drift
        zp = f"zoompan=z='min(zoom+{inc:.6f},{_fmt_zoom(cap)})'{suffix}"

    return prefix + zp


def still_segment_cmd(
    png: str, dur_s: float, out_mp4: str, audio_mp3: str,
    camera_move: str = "推近", intensity: float = 1.0,
) -> list[str]:
    """Build argv to render a still-image segment with narration audio.

    Inputs:
        png:         path to the still image (e.g. character_ref or scene).
        dur_s:       segment length in seconds (drives -t and zoompan duration).
        out_mp4:     path to write the segment mp4.
        audio_mp3:   beat's narration mp3 (wired as -i input 1, mapped to 1:a).
        camera_move: zoompan family (CAMERA_MOVES); defaults to 推近 (current behavior).
        intensity:   camera-move magnitude ∈ [0,1]; 1.0 = current amounts (default).
    """
    vf = _still_or_chart_filtergraph(dur_s, camera_move, intensity)
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
    camera_move: str = "推近", intensity: float = 1.0,
) -> list[str]:
    """Build argv to render a chart-image segment with narration audio.

    Same wiring as still_segment_cmd — a chart is just a static image that
    benefits from a slight Ken Burns push-in to avoid the 'dead frame' feel.
    chart beats default to 推近 (AR-2: chart不经 step_render_shots_clip，
    不对 chart beat 出 camera_move，保持默认缓推可读).
        intensity:   camera-move magnitude ∈ [0,1]; 1.0 = current amounts (default).
    """
    vf = _still_or_chart_filtergraph(dur_s, camera_move, intensity)
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
                   grade: str | None = None,
                   camera_move: str = "推近",
                   intensity: float = 1.0) -> list[str]:
    """Video-only Ken Burns clip (no narration) — for the xfade assembly.

    `grade` (POC5): an optional color-grade filterchain appended AFTER the Ken
    Burns push-in, so photographic stills share the series look. Pass None for
    charts (keep the designed data readable).
    `camera_move`: zoompan family (CAMERA_MOVES); defaults to 推近 (current behavior).
    `intensity`: camera-move magnitude ∈ [0,1]; 1.0 = current amounts (default).
    """
    vf = _still_or_chart_filtergraph(dur_s, camera_move, intensity)
    if grade:
        vf += f",{grade}"
    return [
        "-loop", "1", "-i", png, "-t", str(dur_s), "-r", "30",
        "-vf", vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4,
    ]


def _static_filtergraph() -> str:
    """Full-frame STATIC filtergraph: normalize to 1920x1080/30fps with NO
    zoompan. For code-rendered scientific charts whose axes / tick labels / units
    must stay entirely in frame for the whole beat — a Ken Burns push-in (the
    default for photographic stills) zooms to 1.2x and crops the axes out of
    frame, which destroys a precise data chart's meaning.
    """
    return "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30"


def static_clip_cmd(png: str, dur_s: float, out_mp4: str,
                    grade: str | None = None) -> list[str]:
    """Video-only STATIC clip (no camera move) — for charts that must stay fully
    readable end to end (axes never leave frame). Same wiring as still_clip_cmd
    minus the zoompan. `grade` is normally None for charts (keep matplotlib data
    colours designed/readable), accepted for symmetry.
    """
    vf = _static_filtergraph()
    if grade:
        vf += f",{grade}"
    return [
        "-loop", "1", "-i", png, "-t", str(dur_s), "-r", "30",
        "-vf", vf,
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_mp4,
    ]


def still_card_cmd(bg_png: str, dur_s: float, ass: str, out_mp4: str,
                   grade: str | None = None,
                   camera_move: str = "推近",
                   intensity: float = 1.0) -> list[str]:
    """Video-only title/end card clip: Ken Burns push-in on the AI background,
    with the libass design overlay (`ass`) burned in the same pass. No audio —
    the card joins the xfade dissolve chain like a still clip; its narration
    slot is filled by a matching silence segment (see silence_mp3_cmd).

    `grade` (POC5): applied to the background BEFORE the ass overlay, so the card
    bg matches the body's look while the amber/white design layer stays pure.
    `camera_move`: zoompan family (CAMERA_MOVES); defaults to 推近 (current behavior).
    `intensity`: camera-move magnitude ∈ [0,1]; 1.0 = current amounts (default).
    """
    vf = _still_or_chart_filtergraph(dur_s, camera_move, intensity)
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
    the beat (S2V clips from Seedance are 30fps; we loop the whole clip to fill
    the beat duration rather than slow-motion-stretching it, which would produce
    duplicate frames). If src_dur is unknown or the source is long enough, use
    tpad-clone to freeze the last frame as a fallback.
    """
    chain = ["scale=1920:1080:force_original_aspect_ratio=increase", "crop=1920:1080"]
    if src_dur and src_dur < dur_s:
        # Seedance S2V clips are 30fps; size covers all source frames.
        # loop=-1 repeats indefinitely; -t dur_s (output option) trims to beat.
        frames = max(1, round(src_dur * 30))
        chain.append(f"loop=loop=-1:size={frames}")
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
