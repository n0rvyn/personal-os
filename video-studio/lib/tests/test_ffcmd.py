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
    mux_final_ass_cmd,
    voice_polish_cmd,
    still_clip_cmd,
    static_clip_cmd,
    xfade_chain_cmd,
    mux_av_cmd,
    video_clip_cmd,
    assert_video_ge_audio,
    pad_video_to_cmd,
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

# ---------------------------------------------------------------------------
# mux_final_ass_cmd — burns an ASS file (rich subtitles) + same BGM ducking
# ---------------------------------------------------------------------------


def test_mux_final_ass_burns_ass_and_ducks_bgm():
    argv = mux_final_ass_cmd("/t/base.mp4", "/t/subs.ass", "/t/bgm.mp3", "/t/out.mp4")
    s = _joined(argv)
    assert "ass=/t/subs.ass" in s, f"ass filter missing: {s}"
    assert "sidechaincompress" in s, f"BGM duck missing: {s}"
    assert "amix=inputs=2" in s, f"amix missing: {s}"
    assert "-map [aout]" in s, f"ducked audio not mapped: {s}"
    assert "subtitles=" not in s, f"should use ass= not subtitles=: {s}"
    assert isinstance(argv, list)  # no shell=True


def test_voice_polish_cmd_loudnorm_and_compress():
    argv = voice_polish_cmd("/t/in.mp3", "/t/out.mp3")
    s = _joined(argv)
    assert "-i /t/in.mp3" in s and argv[-1] == "/t/out.mp3"
    assert "loudnorm" in s and "acompressor" in s
    assert isinstance(argv, list)


def test_still_clip_cmd_is_silent_video_only():
    argv = still_clip_cmd("/t/x.png", 3.0, "/t/c.mp4")
    s = _joined(argv)
    assert "-an" in s and "zoompan" in s and "-loop 1" in s
    assert "-map 1:a" not in s and argv[-1] == "/t/c.mp4"


def test_static_clip_cmd_has_no_camera_move():
    """static_clip_cmd (used for charts) must be silent video-only with NO
    zoompan — a Ken Burns push-in crops a chart's axes / units out of frame."""
    argv = static_clip_cmd("/t/chart.png", 3.0, "/t/c.mp4")
    s = _joined(argv)
    assert "-an" in s and "-loop 1" in s and argv[-1] == "/t/c.mp4"
    assert "zoompan" not in s, "chart clip must be static (no zoompan)"
    assert "-map 1:a" not in s
    vf = argv[argv.index("-vf") + 1]
    assert "fps=30" in vf and "crop=1920:1080" in vf   # normalized, full frame


def test_static_clip_cmd_appends_grade_when_given():
    argv = static_clip_cmd("/t/chart.png", 3.0, "/t/c.mp4", grade="eq=contrast=1.1")
    vf = argv[argv.index("-vf") + 1]
    assert vf.endswith("eq=contrast=1.1") and "zoompan" not in vf


def test_xfade_chain_offsets_land_on_narration_boundaries():
    # clip_durs = narration + fade; offsets must equal cumulative narration.
    fade = 0.5
    durs = [3.0 + fade, 4.0 + fade, 2.0 + fade]  # narrations 3,4,2
    argv = xfade_chain_cmd(["/a.mp4", "/b.mp4", "/c.mp4"], durs, "/out.mp4", fade=fade)
    s = _joined(argv)
    assert "xfade=transition=fade:duration=0.5:offset=3.000" in s   # after beat0 (3s)
    assert "xfade=transition=fade:duration=0.5:offset=7.000" in s   # after beat1 (3+4s)
    assert "[vout]" in s and "-map [vout]" in s


def test_mux_av_copies_video_and_shortest():
    argv = mux_av_cmd("/v.mp4", "/a.m4a", "/o.mp4")
    s = _joined(argv)
    assert "-c:v copy" in s and "-map 0:v" in s and "-map 1:a" in s and "-shortest" in s


# ---------------------------------------------------------------------------
# video_clip_cmd — S2V "once + relay" contract (Task 4)
#
# NEW contract (replaces the Task 2 loop-fill contract below, migrated per
# plan MR-3): video_clip_cmd no longer loops or tpad-clones the S2V source to
# fill the WHOLE beat. It plays the S2V source ONCE, capped at WINDOW_MAX
# (7.0s, lib.shotplan.WINDOW_MAX) — the remainder of the beat is covered by
# relay still windows wired in lib.wiring.step_gen_video (Task 4-impl), not by
# looping/padding the video itself. So:
#   - actual_dur = min(src_dur, WINDOW_MAX) when src_dur is known
#   - src_dur unknown (None) → actual_dur = dur_s as given (no loop, no tpad
#     fill-to-beat requirement)
#   - loop=-1 must NEVER appear (old short-src loop-fill contract retired)
# ---------------------------------------------------------------------------

from lib.shotplan import WINDOW_MAX as _WINDOW_MAX


def test_video_clip_never_loops_when_src_shorter_than_dur_s():
    """src_dur < dur_s must NOT use loop filter (old loop-fill contract retired).

    Was `test_video_clip_short_src_uses_loop_not_setpts` (Task 2) — MR-3
    migration: the new contract renders the S2V window ONCE, capped at
    WINDOW_MAX; the beat's remaining duration is covered by relay still
    windows (lib.wiring.step_gen_video), not by looping the source.
    """
    argv = video_clip_cmd("/t/clip.mp4", 10.0, "/t/out.mp4", src_dur=5.0)
    s = _joined(argv)
    assert "loop=" not in s, f"S2V clip must NOT loop-fill the beat: {s}"
    assert "setpts" not in s, f"S2V clip must NOT use setpts slow-motion: {s}"


def test_video_clip_caps_dur_at_window_max_when_src_dur_exceeds_it():
    """src_dur (9.0s) exceeds WINDOW_MAX (7.0s) for a long beat (dur_s=20.0)
    → the rendered window must be capped at WINDOW_MAX, not the full src_dur
    and not the full beat length."""
    argv = video_clip_cmd("/t/clip.mp4", 20.0, "/t/out.mp4", src_dur=9.0)
    s = _joined(argv)
    assert f"-t {_WINDOW_MAX}" in s, (
        f"S2V window must cap at WINDOW_MAX={_WINDOW_MAX}: {s}"
    )
    assert "-t 20.0" not in s, f"must NOT play the full beat length: {s}"
    assert "-t 9.0" not in s, f"must NOT play the full (uncapped) src_dur: {s}"


def test_video_clip_uses_src_dur_when_shorter_than_window_max():
    """src_dur (4.0s) is shorter than WINDOW_MAX (7.0s) → play the S2V source
    once at its own (shorter) length, not stretched/looped to fill anything."""
    argv = video_clip_cmd("/t/clip.mp4", 20.0, "/t/out.mp4", src_dur=4.0)
    s = _joined(argv)
    assert "-t 4.0" in s, f"S2V window must use the shorter src_dur=4.0: {s}"
    assert "loop=" not in s, f"short src must NOT loop-fill: {s}"


def test_video_clip_unknown_src_dur_no_loop_uses_dur_s_as_is():
    """Was `test_video_clip_unknown_src_dur_uses_tpad` (Task 2) — MR-3
    migration: unknown src_dur no longer implies a tpad-clone fill; it plays
    dur_s as given, with no loop filter."""
    argv = video_clip_cmd("/t/clip.mp4", 5.0, "/t/out.mp4")
    s = _joined(argv)
    assert "-t 5.0" in s, f"unknown src_dur must use dur_s=5.0 as-is: {s}"
    assert "loop=" not in s, f"unknown src_dur must NOT use loop filter: {s}"


def test_video_clip_is_video_only_silent():
    """video_clip_cmd must be -an (audio-less) — narration is on a separate track."""
    argv = video_clip_cmd("/t/clip.mp4", 8.0, "/t/out.mp4", src_dur=5.0)
    s = _joined(argv)
    assert "-an" in s, f"video_clip_cmd must be silent (-an): {s}"


def test_video_clip_output_path_last():
    """Output path must be the last argv element."""
    argv = video_clip_cmd("/t/clip.mp4", 8.0, "/t/out.mp4", src_dur=5.0)
    assert argv[-1] == "/t/out.mp4", f"output path must be last argv: {argv}"


# ---------------------------------------------------------------------------
# Camera move expression family tests (Task 1-tests — TDD red phase)
#
# These tests call _still_or_chart_filtergraph(dur_s, camera_move=...) which
# currently does NOT accept `camera_move`. All new cases below will fail with
# TypeError until Task 1-impl adds the parameter. That RED is the expected
# outcome for this task.
# ---------------------------------------------------------------------------

from lib.ffcmd import _still_or_chart_filtergraph  # private, but accessible for contract pinning

_MOVE_DUR = 4.0
_MOVE_FRAMES = max(int(_MOVE_DUR * 30), 1)  # 120


def _zoompan_str(fg: str) -> str:
    """Return the zoompan=... tail of a filtergraph string (zoompan is always last filter)."""
    idx = fg.find("zoompan")
    return fg[idx:] if idx != -1 else ""


# -- 推近 (push-in / zoom-in) --

def test_camera_move_推近_push_in_formula():
    """推近 must produce the push-in formula z=min(zoom+inc,cap). At _MOVE_DUR=4s the
    cap is 1.2 (constant-speed travel 0.05*4=0.2), NOT the 1.4 ceiling — see
    test_camera_move_zoom_ceiling_1_4 for the ceiling guard."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近")
    zp = _zoompan_str(fg)
    assert "z='min(zoom+" in zp, f"推近 must use min(zoom+inc,cap) z-expr: {zp}"
    assert ",1.2)" in zp, f"推近 4s cap must be 1.2 (0.05*4 travel): {zp}"
    assert f"d={_MOVE_FRAMES}" in zp, f"推近 duration must be d={_MOVE_FRAMES}: {zp}"
    assert "s=1920x1080:fps=30" in zp, f"推近 must keep s=1920x1080:fps=30: {zp}"


def test_camera_move_推近_explicit_equals_no_arg_default():
    """camera_move='推近' must produce the same filtergraph as the old no-arg call."""
    fg_default = _still_or_chart_filtergraph(_MOVE_DUR)
    fg_explicit = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近")
    assert fg_default == fg_explicit, (
        f"推近 and no-arg must be identical:\n  default:  {fg_default}\n  explicit: {fg_explicit}"
    )


# -- 拉远 (zoom-out) --

def test_camera_move_拉远_zoom_out_to_1_0():
    """拉远 must zoom out from cap → 1.0. At _MOVE_DUR=4s the start cap is 1.2
    (constant-speed travel), floor 1.0."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="拉远")
    zp = _zoompan_str(fg)
    assert "zoompan" in zp, f"拉远 must use zoompan: {fg}"
    assert "1.2" in zp, f"拉远 z must start at the 4s cap 1.2: {zp}"
    assert "1.0" in zp or "max(" in zp, f"拉远 z must have 1.0 floor (zoom-out): {zp}"
    # Must NOT be a push-in expression
    assert "min(zoom+" not in zp, f"拉远 must NOT use push-in z-expr: {zp}"
    assert f"d={_MOVE_FRAMES}" in zp, f"拉远 duration must be d={_MOVE_FRAMES}: {zp}"
    assert "s=1920x1080:fps=30" in zp, f"拉远 must keep s=1920x1080:fps=30: {zp}"


# -- 横移 (pan-x) --

def test_camera_move_横移_fixed_z_dynamic_x():
    """横移 must fix z ~1.1 and pan x laterally (x-expr references frame index)."""
    import re
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="横移")
    zp = _zoompan_str(fg)
    assert "zoompan" in zp, f"横移 must use zoompan: {fg}"
    assert "z='1.4'" in zp, f"横移 z must be fixed at ceiling 1.4: {zp}"
    assert "x=" in zp, f"横移 must have explicit x= param: {zp}"
    # x= must be a dynamic expression, not a literal 0
    x_match = re.search(r"x='([^']*)'", zp)
    assert x_match, f"横移 x= must be a single-quoted expression: {zp}"
    x_expr = x_match.group(1)
    assert any(tok in x_expr for tok in ("on", "iw", "zoom")), (
        f"横移 x-expr must reference frame position (on/iw/zoom): {x_expr}"
    )
    assert f"d={_MOVE_FRAMES}" in zp, f"横移 duration must be d={_MOVE_FRAMES}: {zp}"
    assert "s=1920x1080:fps=30" in zp, f"横移 must keep s=1920x1080:fps=30: {zp}"


def test_camera_move_横移_y_is_centered():
    """横移 y must be centered (not dynamic) — only horizontal pan."""
    import re
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="横移")
    zp = _zoompan_str(fg)
    if "y=" in zp:
        y_match = re.search(r"y='([^']*)'", zp)
        if y_match:
            y_expr = y_match.group(1)
            # y-expression must not drift vertically (no 'on' term for y-pan)
            assert "on" not in y_expr or "ih" in y_expr, (
                f"横移 y must be centered or static, not tracking frame position: {y_expr}"
            )


# -- 竖摇 (pan-y) --

def test_camera_move_竖摇_fixed_z_dynamic_y():
    """竖摇 must fix z ~1.1 and pan y vertically (y-expr references frame index)."""
    import re
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="竖摇")
    zp = _zoompan_str(fg)
    assert "zoompan" in zp, f"竖摇 must use zoompan: {fg}"
    assert "z='1.4'" in zp, f"竖摇 z must be fixed at ceiling 1.4: {zp}"
    assert "y=" in zp, f"竖摇 must have explicit y= param: {zp}"
    y_match = re.search(r"y='([^']*)'", zp)
    assert y_match, f"竖摇 y= must be a single-quoted expression: {zp}"
    y_expr = y_match.group(1)
    assert any(tok in y_expr for tok in ("on", "ih", "zoom")), (
        f"竖摇 y-expr must reference frame position (on/ih/zoom): {y_expr}"
    )
    assert f"d={_MOVE_FRAMES}" in zp, f"竖摇 duration must be d={_MOVE_FRAMES}: {zp}"
    assert "s=1920x1080:fps=30" in zp, f"竖摇 must keep s=1920x1080:fps=30: {zp}"


def test_camera_move_竖摇_x_is_centered():
    """竖摇 x must be centered (not dynamic) — only vertical pan."""
    import re
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="竖摇")
    zp = _zoompan_str(fg)
    if "x=" in zp:
        x_match = re.search(r"x='([^']*)'", zp)
        if x_match:
            x_expr = x_match.group(1)
            assert "on" not in x_expr or "iw" in x_expr, (
                f"竖摇 x must be centered or static, not tracking frame position: {x_expr}"
            )


# -- 静止 (static) --

def test_camera_move_静止_no_zoom_no_pan():
    """静止 has subtle drift δ=0.02 (not dead-static z=1.0); scale+crop+zoompan present.

    Task 3: 静止 branch changed from z='1.0' constant to a very slow push-in
    (δ=0.02 hardcoded, much less than the full 0.1). scale/crop normalization
    and duration are unchanged.
    """
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止")
    # scale+crop must still be present for normalization
    assert "scale=1920:1080" in fg, f"静止 must still scale to 1920x1080: {fg}"
    assert "crop=1920:1080" in fg, f"静止 must still crop to 1920x1080: {fg}"
    zp = _zoompan_str(fg)
    assert "zoompan" in fg, f"静止 must still use zoompan: {fg}"
    # Task 3: static now has subtle drift — z-expr is dynamic (not z='1.0' constant)
    assert "min(zoom+" in zp, f"静止 must now have subtle drift (min(zoom+...)) : {zp}"
    assert f"d={_MOVE_FRAMES}" in zp, f"静止 duration must be d={_MOVE_FRAMES}: {zp}"
    assert "s=1920x1080:fps=30" in zp, f"静止 must keep s=1920x1080:fps=30: {zp}"


# -- Fallback / error handling --

def test_camera_move_illegal_falls_back_to_推近():
    """Unknown/illegal camera_move must NOT raise — must silently fall back to 推近."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="飞天")
    zp = _zoompan_str(fg)
    assert "z='min(zoom+" in zp, f"illegal move must fall back to 推近 z-expr: {zp}"
    assert "1.2" in zp, f"illegal fallback must use 推近 max zoom 1.2: {zp}"


def test_camera_move_empty_string_falls_back_to_推近():
    """Empty-string camera_move must NOT raise — must fall back to 推近."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="")
    zp = _zoompan_str(fg)
    assert "z='min(zoom+" in zp, f"empty-string move must fall back to 推近: {zp}"


# -- DP-002 zoom constraint across all 5 moves --

def test_camera_move_all_duration_unchanged():
    """All 5 moves must keep d={frames} — timing must be unaffected by camera move."""
    moves = ["推近", "拉远", "横移", "竖摇", "静止"]
    for move in moves:
        fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move=move)
        zp = _zoompan_str(fg)
        if "zoompan" in fg:
            assert f"d={_MOVE_FRAMES}" in zp, (
                f"move={move!r}: timing d={_MOVE_FRAMES} must be unchanged: {zp}"
            )


def test_camera_move_zoom_ceiling_1_4():
    """⑤ ceiling: zoom (z=) literal values must stay within [1.0, 1.4] — no hyper-zoom.

    Reverses DP-002's 1.2 cap to the A/B "C" pick (1.4). Only values in the z='...'
    expression are zoom factors; x= and y= pan-step values are in pixel units relative
    to the upscaled frame and are excluded.
    """
    import re
    moves = ["推近", "拉远", "横移", "竖摇", "静止"]
    for move in moves:
        fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move=move)
        # Extract only the z='...' expression from zoompan (zoom factor, not pan step)
        z_match = re.search(r"z='([^']*)'", fg)
        z_expr = z_match.group(1) if z_match else ""
        # Find numeric zoom literals and flag any > 1.4
        candidates = re.findall(r'\b([1-9]\.[0-9]+)\b', z_expr)
        bad = [v for v in candidates if float(v) > 1.4 + 1e-9]
        assert not bad, (
            f"move={move!r}: zoom z-expr literal(s) {bad} exceed ⑤ ceiling 1.4: {z_expr}"
        )


# ---------------------------------------------------------------------------
# camera_intensity — director-chosen move MAGNITUDE, now the SPEED scalar (⑤).
#
# intensity ∈ [0,1] (clamped) scales the constant zoom/pan SPEED (per second):
# intensity=0 → no motion, 1.0 → full speed. The strings below pin the exact output
# of the ⑤ constant-speed model (reverses DP-002); they are regression pins, NOT
# backward-compat with the old delta-spread code.
# ---------------------------------------------------------------------------

# Reference strings from the constant-speed model at dur_s=3.0 (frames=90), intensity=1.0.
# At 3.0s the zoom travels rate*dur = 0.05*3 = 0.15 (cap 1.15, below the 1.4 ceiling);
# pan travels _PAN_RATE*dur = 96*3 = 288 px (below the 768/432 half-overscan caps).
_BC_DUR = 3.0
_BC_PREFIX = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,scale=3840:-2,"
_BC_EXPECTED = {
    # rate=0.05; inc=rate/30=0.001667; cap=1.0+min(0.05*3,0.4)=1.15
    "推近": _BC_PREFIX + "zoompan=z='min(zoom+0.001667,1.15)':d=90:s=1920x1080:fps=30",
    # start=cap=1.15; dec=inc=0.001667; floor 1.0
    "拉远": _BC_PREFIX + "zoompan=z='max(1.15-on*0.001667,1.0)':d=90:s=1920x1080:fps=30",
    # z fixed at ceiling 1.4; step=_PAN_RATE/30=3.2; maxpan=min(96*3,768)=288
    "横移": _BC_PREFIX + "zoompan=z='1.4':x='min(on*3.200000,288.000000)':y='(ih-ih/zoom)/2':d=90:s=1920x1080:fps=30",
    # z fixed 1.4; step 3.2; maxpan=min(96*3,432)=288
    "竖摇": _BC_PREFIX + "zoompan=z='1.4':x='(iw-iw/zoom)/2':y='min(on*3.200000,288.000000)':d=90:s=1920x1080:fps=30",
    # 静止: constant gentle drift; inc=_STATIC_RATE/30=0.000117; cap=_STATIC_MAX=1.05; ignores intensity
    "静止": _BC_PREFIX + "zoompan=z='min(zoom+0.000117,1.05)':d=90:s=1920x1080:fps=30",
}


def test_intensity_default_is_byte_identical_for_all_moves():
    """intensity defaulting (no arg) must reproduce the pre-intensity output
    EXACTLY — full-string equality, not a loose substring. This is the guard
    that proves backward-compat (a substring check like `'1.1' in fg` would
    falsely pass on a broken '1.1000' format)."""
    for move, expected in _BC_EXPECTED.items():
        fg = _still_or_chart_filtergraph(_BC_DUR, camera_move=move)
        assert fg == expected, (
            f"move={move!r} default intensity must be byte-identical:\n"
            f"  expected: {expected}\n  got:      {fg}"
        )


def test_intensity_explicit_1_0_is_byte_identical_for_all_moves():
    """intensity=1.0 passed explicitly must equal the no-arg default output."""
    for move, expected in _BC_EXPECTED.items():
        fg = _still_or_chart_filtergraph(_BC_DUR, camera_move=move, intensity=1.0)
        assert fg == expected, (
            f"move={move!r} intensity=1.0 must be byte-identical:\n"
            f"  expected: {expected}\n  got:      {fg}"
        )


def test_intensity_push_in_cap_is_bare_not_padded():
    """Discriminating check: the 推近 cap at 3.0s must serialize as bare '1.15'
    (closing `,1.15)`), NOT '1.1500' — the exact regression a `:.4f` format would cause."""
    fg = _still_or_chart_filtergraph(_BC_DUR, camera_move="推近", intensity=1.0)
    zp = _zoompan_str(fg)
    assert ",1.15)" in zp, f"推近 cap must be bare '1.15', got: {zp}"
    assert "1.1500" not in zp, f"推近 cap must NOT be padded to 1.1500: {zp}"


def test_intensity_half_halves_push_in_delta():
    """intensity=0.5 → 推近 cap halves to 1.1 (delta 0.2→0.1) and inc halves."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近", intensity=0.5)
    zp = _zoompan_str(fg)
    # frames=120; delta=0.2*0.5=0.1; inc=0.1/120=0.000833; cap=1.1
    assert "zoompan=z='min(zoom+0.000833,1.1)'" in zp, (
        f"intensity=0.5 推近 must use inc=0.000833 cap=1.1: {zp}"
    )


def test_intensity_half_halves_pan_step():
    """intensity=0.5 → 横移 pan-x SPEED halves (constant rate _PAN_RATE=96 px/s → step/frame 3.2→1.6)."""
    full = _zoompan_str(_still_or_chart_filtergraph(_MOVE_DUR, camera_move="横移", intensity=1.0))
    half = _zoompan_str(_still_or_chart_filtergraph(_MOVE_DUR, camera_move="横移", intensity=0.5))
    # step/frame = _PAN_RATE*intensity/30: full=96/30=3.2 ; half=48/30=1.6 (wrapped in min(...,maxpan))
    assert "on*3.200000" in full, f"intensity=1.0 横移 step must be 3.200000: {full}"
    assert "on*1.600000" in half, f"intensity=0.5 横移 step must be 1.600000: {half}"


def test_intensity_half_halves_pan_y_step():
    """intensity=0.5 → 竖摇 pan-y SPEED halves (constant rate → step/frame 3.2→1.6)."""
    half = _zoompan_str(_still_or_chart_filtergraph(_MOVE_DUR, camera_move="竖摇", intensity=0.5))
    # step/frame = _PAN_RATE*0.5/30 = 48/30 = 1.6 (wrapped in min(...,maxpan))
    assert "on*1.600000" in half, f"intensity=0.5 竖摇 step must be 1.600000: {half}"


def test_intensity_0_3_push_in_cap_is_1_06():
    """intensity=0.3 → 推近 cap = 1.0 + 0.2*0.3 = 1.06 (float noise rounded out)."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近", intensity=0.3)
    zp = _zoompan_str(fg)
    assert ",1.06)" in zp, f"intensity=0.3 推近 cap must be 1.06: {zp}"
    # must NOT leak float noise like 1.0600000000000001
    assert "1.0600" not in zp and "1.06000" not in zp, f"cap must be clean 1.06: {zp}"


def test_intensity_clamp_above_1_treated_as_1():
    """intensity > 1 must clamp to 1.0 (== current amounts), never exceed 克制 max."""
    over = _still_or_chart_filtergraph(_BC_DUR, camera_move="推近", intensity=5.0)
    assert over == _BC_EXPECTED["推近"], (
        f"intensity=5.0 must clamp to 1.0 (byte-identical to current): {over}"
    )


def test_intensity_clamp_below_0_treated_as_0():
    """intensity < 0 must clamp to 0.0 → 推近 collapses to no motion (cap=1.0, inc=0)."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近", intensity=-2.0)
    zp = _zoompan_str(fg)
    # delta=0 → inc=0.000000, cap=1.0
    assert "zoompan=z='min(zoom+0.000000,1.0)'" in zp, (
        f"intensity<0 must clamp to 0 (cap=1.0, inc=0): {zp}"
    )


def test_intensity_0_collapses_push_in_to_static_amount():
    """intensity=0 → 推近 zoom delta is 0 (cap=1.0): a no-motion push-in."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="推近", intensity=0.0)
    zp = _zoompan_str(fg)
    assert ",1.0)" in zp and "zoom+0.000000" in zp, (
        f"intensity=0 推近 must have cap=1.0 and inc=0: {zp}"
    )


def test_intensity_静止_ignores_intensity():
    """静止 drift (δ=0.02) is hardcoded; intensity param must not change output."""
    a = _still_or_chart_filtergraph(_BC_DUR, camera_move="静止", intensity=1.0)
    b = _still_or_chart_filtergraph(_BC_DUR, camera_move="静止", intensity=0.2)
    assert a == b == _BC_EXPECTED["静止"], (
        f"静止 must ignore intensity: {a} vs {b}"
    )


# ---------------------------------------------------------------------------
# Task 3: 静止 subtle drift — ASCII "static" named tests for `pytest -k static`
#
# 静止 branch changed from z='1.0' (dead-static, 1/240 unique frames) to
# a very slow push-in δ=0.02 (hardcoded, ignoring intensity). The suffix
# `:d={frames}:s=1920x1080:fps=30` is preserved. These tests verify the new
# drift properties and are matched by `pytest lib/tests/test_ffcmd.py -k static`.
# ---------------------------------------------------------------------------

def test_static_camera_move_has_subtle_drift():
    """静止 must now have a non-zero zoom drift (not z='1.0' constant).

    Task 3: _still_or_chart_filtergraph(..., camera_move='静止') must contain
    min(zoom+inc,...) in the zoompan z-expression, proving drift is present.
    """
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止")
    zp = _zoompan_str(fg)
    assert "min(zoom+" in zp, (
        f"静止 must have min(zoom+inc,...) drift, not z='1.0' constant: {zp}"
    )


def test_static_drift_cap_is_small():
    """静止 drift cap must be ≤ 1.05 — well below the full δ=0.1 ceiling (cap=1.1).

    δ=0.02 → cap=1.02; this is less than half the minimum non-static move intensity,
    satisfying 'δ ≤ 现有强度下限一半' (non-goal: must not look like a real camera move).
    """
    import re
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止")
    zp = _zoompan_str(fg)
    # Extract the cap literal from min(zoom+inc,CAP)
    cap_match = re.search(r"min\(zoom\+[0-9.]+,([0-9.]+)\)", zp)
    assert cap_match, f"静止 z-expr must match min(zoom+inc,cap) pattern: {zp}"
    cap = float(cap_match.group(1))
    assert cap <= 1.05, (
        f"静止 drift cap {cap} must be ≤ 1.05 (δ hardcoded at 0.02, not a full camera move): {zp}"
    )
    assert cap > 1.0, (
        f"静止 drift cap {cap} must be > 1.0 (non-zero drift): {zp}"
    )


def test_static_drift_ignores_intensity_param():
    """静止 drift output must be identical for any intensity value (hardcoded δ=0.02)."""
    fg_full = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止", intensity=1.0)
    fg_half = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止", intensity=0.5)
    fg_zero = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止", intensity=0.0)
    assert fg_full == fg_half == fg_zero, (
        f"静止 must produce identical output for all intensity values:\n"
        f"  full: {fg_full}\n  half: {fg_half}\n  zero: {fg_zero}"
    )


def test_static_drift_duration_and_output_size_unchanged():
    """静止 drift must preserve :d={frames}:s=1920x1080:fps=30 (timing unchanged)."""
    fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move="静止")
    zp = _zoompan_str(fg)
    assert f"d={_MOVE_FRAMES}" in zp, (
        f"静止 drift must preserve d={_MOVE_FRAMES} timing: {zp}"
    )
    assert "s=1920x1080:fps=30" in zp, (
        f"静止 drift must preserve s=1920x1080:fps=30 output spec: {zp}"
    )


# ---------------------------------------------------------------------------
# Pre-upscale zoompan assertion (Task 1-tests — TDD red phase)
#
# These tests assert that _still_or_chart_filtergraph inserts a scale= filter
# with width ≥ 3840 BEFORE the zoompan filter. The zoompan filter then operates
# on the upscaled frame, eliminating integer-truncation stairstepping in pan/zoom
# motion (plan-verifier baseline: pre-upscale 201 independent frames,
# post-upscale 239/240 at mpdecimate).
#
# All assertions below will FAIL until Task 1-impl adds scale=3840:-2 to the
# filtergraph prefix. That RED state is the expected outcome for this task.
# ---------------------------------------------------------------------------

import re as _re


def _upscale_width_before_zoompan(fg: str) -> int:
    """Return the largest scale= integer width found strictly before 'zoompan', or 0."""
    zp_idx = fg.find("zoompan")
    pre = fg[:zp_idx] if zp_idx != -1 else fg
    widths = [int(m) for m in _re.findall(r"scale=(\d+)", pre)]
    return max(widths) if widths else 0


def test_filtergraph_has_upscale_before_zoompan_for_all_moves():
    """_still_or_chart_filtergraph must insert scale=≥3840 before zoompan for every move.

    The pre-upscale step ensures zoompan's pan/zoom arithmetic works on a frame
    wide enough that integer truncation doesn't produce visible stairstepping.
    Fails until Task 1-impl inserts scale=3840:-2 into the filtergraph prefix.
    """
    for move in ("推近", "拉远", "横移", "竖摇", "静止"):
        fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move=move)
        w = _upscale_width_before_zoompan(fg)
        assert w >= 3840, (
            f"move={move!r}: no scale=≥3840 before zoompan (got width={w}): {fg}"
        )


def test_still_clip_cmd_vf_has_upscale_before_zoompan():
    """-vf arg in still_clip_cmd must include scale=≥3840 before zoompan.

    still_clip_cmd delegates to _still_or_chart_filtergraph; this test confirms
    the production call path carries the pre-upscale step through to the argv.
    Fails until Task 1-impl adds the upscale to the shared filtergraph helper.
    """
    argv = still_clip_cmd("/t/x.png", 4.0, "/t/c.mp4")
    try:
        vf_idx = argv.index("-vf") + 1
        vf = argv[vf_idx]
    except (ValueError, IndexError):
        raise AssertionError(f"no -vf flag in still_clip_cmd argv: {argv}")
    w = _upscale_width_before_zoompan(vf)
    assert w >= 3840, (
        f"still_clip_cmd -vf must have scale=≥3840 before zoompan (got width={w}): {vf}"
    )


def test_filtergraph_zoompan_present_with_fps30_for_all_moves():
    """All 5 camera moves must produce a non-empty zoompan containing fps=30.

    This is a regression shield: adding the pre-upscale step must not remove or
    corrupt the zoompan filter or its s=1920x1080:fps=30 output spec.
    """
    for move in ("推近", "拉远", "横移", "竖摇", "静止"):
        fg = _still_or_chart_filtergraph(_MOVE_DUR, camera_move=move)
        zp = _zoompan_str(fg)
        assert zp, f"move={move!r}: zoompan must be present (non-empty): {fg}"
        assert "fps=30" in zp, f"move={move!r}: zoompan must contain fps=30: {zp}"


# ---------------------------------------------------------------------------
# Task 5: assembly video>=audio assertion + padding (D-008 — narration must
# NEVER be truncated by mux's `-shortest`; if the xfade'd video track comes up
# short of the narration track, pad the video's tail rather than let `-shortest`
# silently cut the audio).
# ---------------------------------------------------------------------------

def test_assert_video_ge_audio_returns_none_when_video_ge_audio():
    """video_dur >= audio_dur (within tol) → no padding needed → None."""
    assert assert_video_ge_audio(30.0, 30.0) is None
    assert assert_video_ge_audio(30.02, 30.0) is None  # within default tol=0.05
    assert assert_video_ge_audio(31.0, 30.0) is None


def test_assert_video_ge_audio_returns_target_when_video_short():
    """video_dur short of audio_dur beyond tol → returns the target pad
    duration (== audio_dur) so the caller knows how long to extend the tail."""
    target = assert_video_ge_audio(29.7, 30.0)
    assert target == 30.0, (
        f"video short by 0.3s (> default tol=0.05) must return target pad "
        f"duration == audio_dur (30.0), got {target}"
    )


def test_assert_video_ge_audio_respects_tol():
    """A shortfall within tol must NOT trigger padding (avoid over-padding on
    negligible float/frame-rounding drift)."""
    assert assert_video_ge_audio(29.99, 30.0, tol=0.05) is None
    assert assert_video_ge_audio(29.90, 30.0, tol=0.05) == 30.0


def test_pad_video_to_cmd_extends_tail_to_target_dur():
    """pad_video_to_cmd(video_mp4, target_dur, out_mp4): tpad-clone the last
    frame so the output video is target_dur seconds long — never loop/repeat
    the whole clip (that's the Task 4 anti-pattern this guard exists to catch
    if it ever crept back in at the assembly stage)."""
    argv = pad_video_to_cmd("/t/video.mp4", 30.0, "/t/video_padded.mp4")
    joined = _joined(argv)
    assert "/t/video.mp4" in joined
    assert "tpad" in joined
    assert "stop_mode=clone" in joined
    assert "30" in joined
    assert argv[-1] == "/t/video_padded.mp4"
    assert "loop=-1" not in joined
