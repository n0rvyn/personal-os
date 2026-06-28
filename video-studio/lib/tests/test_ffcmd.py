"""Test-first contract for lib.ffcmd (ffmpeg argv constructor).

These tests lock down the shape of the ffmpeg command lists the driver will pass to
subprocess.run. They do NOT actually invoke ffmpeg — they only assert the argv lists
and (stringified) filtergraph contents. Critical contract:

- Each segment constructor (still / video / chart) MUST accept the beat's narration
  mp3 and wire it as an audio input (-i {audio_mp3}) with -map 1:a. This is the
  hard requirement that fixes the "audio track never wired up" gap caught by the
  previous verifier.
- All segment outputs are normalized to 1920x1080, 30 fps, yuv420p, aac stereo.
- concat_cmd uses the concat demuxer and re-encodes to keep A/V params consistent.
- mux_final_cmd bakes the .srt subtitles in (PingFang SC) and ducks BGM via
  sidechaincompress + amix on the narration track [0:a].
- All functions return list[str] (never shell=True).

The implementation must match these pinned shapes; changing the test = test tampering.
"""
from lib.ffcmd import (
    still_segment_cmd,
    video_segment_cmd,
    chart_segment_cmd,
    concat_cmd,
    mux_final_cmd,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _joined(argv: list[str]) -> str:
    """Render argv list as a single space-joined string for substring assertions."""
    return " ".join(str(x) for x in argv)


def _has_input_after(argv: list[str], audio_mp3: str) -> bool:
    """True iff `audio_mp3` appears in the argv immediately after some `-i`."""
    s = _joined(argv)
    return f"-i {audio_mp3}" in s


# ---------------------------------------------------------------------------
# still_segment_cmd
# ---------------------------------------------------------------------------

def test_still_segment_returns_list():
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    assert isinstance(argv, list), f"must return list, got {type(argv)}"
    assert all(isinstance(x, str) for x in argv), "all elements must be str"


def test_still_segment_includes_image_and_audio_inputs():
    """Both the still image and the narration mp3 must be wired in as -i inputs."""
    png = "/tmp/scene.png"
    audio = "/tmp/voice.mp3"
    argv = still_segment_cmd(png=png, dur_s=4.0, out_mp4="/tmp/seg.mp4", audio_mp3=audio)
    s = _joined(argv)
    assert f"-i {png}" in s, f"image input missing: {s}"
    assert f"-i {audio}" in s, f"narration audio input missing: {s}"


def test_still_segment_uses_loop_and_duration():
    """A still image needs -loop 1 and -t dur_s so the stream runs the right length."""
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-loop 1" in s, f"-loop 1 missing: {s}"
    assert "-t 4.0" in s, f"-t 4.0 missing: {s}"


def test_still_segment_filtergraph_scales_and_crops_to_1920x1080():
    """Filtergraph must normalize to 1920x1080 via scale+crop+zoompan."""
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in s, (
        f"scale filter missing: {s}"
    )
    assert "crop=1920:1080" in s, f"crop filter missing: {s}"
    assert "zoompan" in s, f"zoompan missing (Ken Burns): {s}"


def test_still_segment_maps_video_and_audio_and_shortest():
    """Maps the still video (0:v) and narration audio (1:a); -shortest terminates at audio end."""
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-map 0:v" in s, f"video map missing: {s}"
    assert "-map 1:a" in s, f"audio map (1:a from narration) missing: {s}"
    assert "-c:a aac" in s, f"aac codec missing: {s}"
    assert "-shortest" in s, f"-shortest missing: {s}"


def test_still_segment_normalizes_fps_and_pixfmt():
    """All segments must normalize to 30fps + yuv420p so concat demuxer doesn't choke."""
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-r 30" in s, f"-r 30 missing: {s}"
    assert "-pix_fmt yuv420p" in s, f"-pix_fmt yuv420p missing: {s}"


def test_still_segment_output_path_is_argv_tail():
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=4.0, out_mp4="/tmp/out_seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    assert argv[-1] == "/tmp/out_seg.mp4", f"output path must be last argv: {argv}"


def test_still_segment_audio_input_appears_after_dash_i():
    """audio_mp3 must be an -i input (not just any string in the argv)."""
    argv = still_segment_cmd(
        png="/tmp/x.png", dur_s=3.5, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/some_audio.mp3",
    )
    assert _has_input_after(argv, "/tmp/some_audio.mp3"), (
        f"audio_mp3 not wired as -i input: {argv}"
    )


# ---------------------------------------------------------------------------
# video_segment_cmd
# ---------------------------------------------------------------------------

def test_video_segment_includes_src_and_audio_inputs():
    src = "/tmp/s2v_src.mp4"
    audio = "/tmp/narration.mp3"
    argv = video_segment_cmd(
        src_mp4=src, dur_s=6.0, out_mp4="/tmp/seg.mp4", audio_mp3=audio,
    )
    s = _joined(argv)
    assert f"-i {src}" in s, f"src video input missing: {s}"
    assert f"-i {audio}" in s, f"narration audio input missing (must replace S2V audio): {s}"


def test_video_segment_overrides_src_audio_with_narration():
    """video_segment must map src video (0:v) and narration audio (1:a), discarding S2V audio."""
    argv = video_segment_cmd(
        src_mp4="/tmp/s2v.mp4", dur_s=6.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-map 0:v" in s, f"src video map missing: {s}"
    assert "-map 1:a" in s, f"narration audio map (must override S2V audio) missing: {s}"
    assert "-c:a aac" in s, f"aac codec missing: {s}"


def test_video_segment_normalizes_to_1920x1080_30fps_yuv420p():
    argv = video_segment_cmd(
        src_mp4="/tmp/s2v.mp4", dur_s=6.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "scale=1920:1080" in s, f"scale filter missing: {s}"
    assert "crop=1920:1080" in s, f"crop filter missing: {s}"
    assert "-r 30" in s, f"-r 30 missing: {s}"
    assert "-pix_fmt yuv420p" in s, f"-pix_fmt yuv420p missing: {s}"


def test_video_segment_caps_duration_at_dur_s():
    argv = video_segment_cmd(
        src_mp4="/tmp/s2v.mp4", dur_s=6.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-t 6.0" in s, f"-t 6.0 missing (must cap to beat duration): {s}"
    assert "-shortest" in s, f"-shortest missing: {s}"


def test_video_segment_audio_input_appears_after_dash_i():
    argv = video_segment_cmd(
        src_mp4="/tmp/s2v.mp4", dur_s=6.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narr.mp3",
    )
    assert _has_input_after(argv, "/tmp/narr.mp3"), (
        f"audio_mp3 not wired as -i input: {argv}"
    )


# ---------------------------------------------------------------------------
# chart_segment_cmd
# ---------------------------------------------------------------------------

def test_chart_segment_includes_image_and_audio_inputs():
    png = "/tmp/chart.png"
    audio = "/tmp/narration.mp3"
    argv = chart_segment_cmd(
        png=png, dur_s=5.0, out_mp4="/tmp/seg.mp4", audio_mp3=audio,
    )
    s = _joined(argv)
    assert f"-i {png}" in s, f"chart image input missing: {s}"
    assert f"-i {audio}" in s, f"narration audio input missing: {s}"


def test_chart_segment_normalizes_and_maps_both_streams():
    argv = chart_segment_cmd(
        png="/tmp/chart.png", dur_s=5.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in s, f"scale missing: {s}"
    assert "crop=1920:1080" in s, f"crop missing: {s}"
    assert "-map 0:v" in s, f"video map missing: {s}"
    assert "-map 1:a" in s, f"audio map missing: {s}"
    assert "-c:a aac" in s, f"aac codec missing: {s}"
    assert "-shortest" in s, f"-shortest missing: {s}"


def test_chart_segment_uses_loop():
    argv = chart_segment_cmd(
        png="/tmp/chart.png", dur_s=5.0, out_mp4="/tmp/seg.mp4",
        audio_mp3="/tmp/narration.mp3",
    )
    s = _joined(argv)
    assert "-loop 1" in s, f"-loop 1 missing: {s}"
    assert "-t 5.0" in s, f"-t 5.0 missing: {s}"


# ---------------------------------------------------------------------------
# concat_cmd
# ---------------------------------------------------------------------------

def test_concat_cmd_uses_concat_demuxer():
    """Use -f concat -safe 0 with the segment list file as input."""
    argv = concat_cmd(segment_list_file="/tmp/segs.txt", out_mp4="/tmp/base.mp4")
    s = _joined(argv)
    assert "-f concat" in s, f"-f concat missing: {s}"
    assert "-safe 0" in s, f"-safe 0 missing: {s}"
    assert "-i /tmp/segs.txt" in s, f"list file input missing: {s}"


def test_concat_cmd_re_encodes_to_keep_params_consistent():
    """Re-encode with libx264 + aac so concat doesn't fail on mismatched A/V params."""
    argv = concat_cmd(segment_list_file="/tmp/segs.txt", out_mp4="/tmp/base.mp4")
    s = _joined(argv)
    assert "libx264" in s, f"libx264 re-encode missing (avoids concat copy-time drift): {s}"
    assert "aac" in s, f"aac re-encode missing: {s}"


def test_concat_cmd_returns_list_with_output_path_last():
    argv = concat_cmd(segment_list_file="/tmp/segs.txt", out_mp4="/tmp/base.mp4")
    assert isinstance(argv, list)
    assert argv[-1] == "/tmp/base.mp4", f"output path must be last argv: {argv}"


# ---------------------------------------------------------------------------
# mux_final_cmd
# ---------------------------------------------------------------------------

def test_mux_final_takes_base_srt_bgm_and_returns_list():
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    assert isinstance(argv, list)
    assert all(isinstance(x, str) for x in argv)


def test_mux_final_inputs_base_and_bgm():
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    s = _joined(argv)
    assert "-i /tmp/base.mp4" in s, f"base input missing: {s}"
    assert "-i /tmp/bgm.mp3" in s, f"bgm input missing: {s}"


def test_mux_final_burns_subtitles_with_pingfang_sc():
    """Hard-subtitle the srt using PingFang SC (per crystal D-001 + macOS Chinese font)."""
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    s = _joined(argv)
    assert "subtitles=/tmp/subs.srt" in s, f"subtitles filter missing: {s}"
    assert "FontName=PingFang SC" in s, f"FontName=PingFang SC missing: {s}"


def test_mux_final_uses_sidechaincompress_and_amix_for_ducking():
    """BGM must duck under narration via sidechaincompress; final mix via amix."""
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    s = _joined(argv)
    assert "sidechaincompress" in s, f"sidechaincompress missing (no ducking): {s}"
    assert "amix" in s, f"amix missing: {s}"
    # BGM volume reduction must appear before the duck chain
    assert "volume=" in s, f"BGM volume reduction missing: {s}"


def test_mux_final_maps_video_and_mixed_audio():
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    s = _joined(argv)
    assert "-map 0:v" in s, f"video map from base missing: {s}"
    # The mixed audio output is referenced as [aout] by the amix filter and must be mapped
    assert "-map [aout]" in s, f"mixed audio [aout] map missing: {s}"


def test_mux_final_output_path_is_last_argv():
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    assert argv[-1] == "/tmp/final.mp4", f"output path must be last argv: {argv}"


def test_mux_final_default_bgm_volume_is_low():
    """Default bgm_volume should be a small fraction (e.g. 0.12) so BGM sits below narration."""
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
    )
    s = _joined(argv)
    # Find a volume= occurrence that's clearly the BGM attenuation (single-digit fraction)
    import re
    m = re.search(r"volume=([0-9.]+)", s)
    assert m, f"no volume= filter found: {s}"
    val = float(m.group(1))
    assert 0.0 < val < 0.5, f"BGM volume {val} out of (0, 0.5) range: {s}"


def test_mux_final_accepts_duck_threshold_and_bgm_volume_kwargs():
    """Calibration loop in Task 8 needs to pass duck_threshold and bgm_volume."""
    argv = mux_final_cmd(
        base_mp4="/tmp/base.mp4", srt="/tmp/subs.srt",
        bgm="/tmp/bgm.mp3", out_mp4="/tmp/final.mp4",
        duck_threshold=0.03, bgm_volume="0.06",
    )
    s = _joined(argv)
    assert "volume=0.06" in s, f"custom bgm_volume=0.06 not honored: {s}"
    assert "threshold=0.03" in s, f"custom duck_threshold=0.03 not honored: {s}"