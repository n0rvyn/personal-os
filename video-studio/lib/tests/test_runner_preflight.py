"""Tests for preflight wired into lib/runner._cli (Phase-7 Task 3-tests).

Written BEFORE Task 3-impl; all tests that check preflight behaviour must be
RED at this point (runner._cli does not yet call check_ffmpeg_libass).

Nailed-down contracts:
  1. _cli calls check_ffmpeg_libass (before select_next / prepare).
  2. PreflightError → rc=1, message to stderr, prepare_fn NOT called.
  3. Check passes → _cli proceeds (check recorded, prepare IS called).
  4. ffmpeg_bin resolution: FFMPEG_BIN env first, then config.ffmpeg.ffmpeg_bin (AD-4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, ffmpeg_bin: str = "/config/ffmpeg") -> Path:
    """Minimal valid config YAML. ffmpeg_bin is overridable for AD-4 tests."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: X\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {ffmpeg_bin}\n  ffprobe_bin: /bin/true\n"
        f"output_dir: {tmp_path}\n",
        encoding="utf-8",
    )
    return cfg


def _write_fixture(tmp_path: Path) -> Path:
    fx = tmp_path / "beats.json"
    fx.write_text('{"beats": []}', encoding="utf-8")
    return fx


def _make_raising_check(message: str = "缺 libass 测试"):
    """Return a fake check_ffmpeg_libass that raises PreflightError."""
    from lib.preflight import PreflightError
    def _check(ffmpeg_bin):
        raise PreflightError(message)
    return _check


def _make_recording_check(record_list: list, *, succeed: bool = True):
    """Return a fake check_ffmpeg_libass that records the ffmpeg_bin it received."""
    def _check(ffmpeg_bin):
        record_list.append(ffmpeg_bin)
        return None  # success
    return _check


def _make_tracking_prepare(marker: list):
    """Return a prepare_fn that records it was called; returns minimal bundle."""
    def _prepare(config, *, fixture_path, seq, slug, dry, log, **kwargs):
        marker.append("called")
        from lib.episode import make_scratch
        scratch = make_scratch(config.output_dir, tag=f"{seq}-{slug}")
        # Return a bundle that run_pipeline will accept: empty step list via
        # a run_pipeline_fn override (injected separately in each test).
        return {
            "step_fns": {},
            "gates": {},
            "dispatch": lambda *a, **k: {"ok": True},
            "scratch_dir": scratch,
        }
    return _prepare


def _fake_run_pipeline(config, *, pipeline, step_fns, dispatch, gates, scratch_dir):
    """No-op run_pipeline that always reports success."""
    return {"status": "done"}


# ---------------------------------------------------------------------------
# 1. check is called — FAIL-first (runner doesn't call it yet)
# ---------------------------------------------------------------------------

def test_check_is_called_by_cli(tmp_path, monkeypatch):
    """_cli must invoke check_ffmpeg_libass at least once.

    FAIL-first: _cli currently has no call to check_ffmpeg_libass → record list
    stays empty → assertion fails.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path)
    fx = _write_fixture(tmp_path)

    record = []
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_recording_check(record))

    _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare([]),
        run_pipeline_fn=_fake_run_pipeline,
    )

    assert record, (
        "check_ffmpeg_libass must be called by _cli — got zero calls "
        "(preflight not wired into runner)"
    )


# ---------------------------------------------------------------------------
# 2. PreflightError → rc=1 — FAIL-first
# ---------------------------------------------------------------------------

def test_preflight_error_returns_1(tmp_path, monkeypatch):
    """When check_ffmpeg_libass raises PreflightError, _cli must return 1.

    FAIL-first: _cli never calls check → no PreflightError → proceeds normally
    → rc=0, not 1.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path)
    fx = _write_fixture(tmp_path)

    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_raising_check())

    rc = _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare([]),
        run_pipeline_fn=_fake_run_pipeline,
    )
    assert rc == 1, (
        f"_cli must return 1 on PreflightError, got {rc!r}"
    )


# ---------------------------------------------------------------------------
# 3. PreflightError → message to stderr — FAIL-first
# ---------------------------------------------------------------------------

def test_preflight_error_prints_to_stderr(tmp_path, monkeypatch, capsys):
    """PreflightError message must appear in stderr.

    FAIL-first: check is never called → no message printed.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path)
    fx = _write_fixture(tmp_path)

    error_msg = "缺 libass — 请用静态构建 FFMPEG_BIN"
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_raising_check(error_msg))

    _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare([]),
        run_pipeline_fn=_fake_run_pipeline,
    )

    captured = capsys.readouterr()
    assert "libass" in captured.err or error_msg in captured.err, (
        f"PreflightError message must appear in stderr. Got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# 4. PreflightError → prepare NOT called (render not entered) — FAIL-first
# ---------------------------------------------------------------------------

def test_preflight_error_does_not_call_prepare(tmp_path, monkeypatch):
    """When PreflightError fires, prepare_fn must NOT be called.

    FAIL-first: without preflight, _cli proceeds normally → prepare IS called
    → assert not called → fails.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path)
    fx = _write_fixture(tmp_path)

    called = []
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_raising_check())

    rc = _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare(called),
        run_pipeline_fn=_fake_run_pipeline,
    )

    assert rc == 1
    assert not called, (
        "prepare_fn must NOT be called when PreflightError fires "
        f"(render must not be entered); got calls: {called}"
    )


# ---------------------------------------------------------------------------
# 5. Check passes → prepare IS called — sanity / regression guard
# ---------------------------------------------------------------------------

def test_preflight_passes_proceeds_to_prepare(tmp_path, monkeypatch):
    """When check_ffmpeg_libass succeeds, _cli must proceed and call prepare_fn.

    FAIL-first: check is never called (monkeypatch has no effect) — this test
    actually passes before impl too, but the pairing test (test_check_is_called_by_cli)
    ensures the call is wired.  Kept as a green-flag regression guard after impl.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path)
    fx = _write_fixture(tmp_path)

    record = []
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_recording_check(record))

    called = []
    rc = _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare(called),
        run_pipeline_fn=_fake_run_pipeline,
    )

    assert rc == 0, f"_cli must return 0 when preflight passes, got {rc!r}"
    assert called, "prepare_fn must be called when check passes"


# ---------------------------------------------------------------------------
# 6. ffmpeg_bin: FFMPEG_BIN env takes priority (AD-4) — FAIL-first
# ---------------------------------------------------------------------------

def test_env_ffmpeg_bin_passed_to_check(tmp_path, monkeypatch):
    """FFMPEG_BIN env must be passed to check_ffmpeg_libass (env > config, AD-4).

    FAIL-first: check never called → record empty → assertion fails.
    """
    from lib.runner import _cli

    cfg = _write_config(tmp_path, ffmpeg_bin="/config/ffmpeg")
    fx = _write_fixture(tmp_path)

    env_bin = "/env/ffmpeg-libass"
    monkeypatch.setenv("FFMPEG_BIN", env_bin)

    record = []
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_recording_check(record))

    _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare([]),
        run_pipeline_fn=_fake_run_pipeline,
    )

    assert record, "check_ffmpeg_libass must be called"
    assert record[0] == env_bin, (
        f"FFMPEG_BIN env must take priority over config. "
        f"Expected {env_bin!r}, got {record[0]!r}"
    )


# ---------------------------------------------------------------------------
# 7. ffmpeg_bin: falls back to config.ffmpeg.ffmpeg_bin (AD-4) — FAIL-first
# ---------------------------------------------------------------------------

def test_config_ffmpeg_bin_used_when_env_absent(tmp_path, monkeypatch):
    """config.ffmpeg.ffmpeg_bin must be passed when FFMPEG_BIN env is absent (AD-4).

    FAIL-first: check never called → record empty → assertion fails.
    """
    from lib.runner import _cli

    config_bin = "/config/ffmpeg"
    cfg = _write_config(tmp_path, ffmpeg_bin=config_bin)
    fx = _write_fixture(tmp_path)

    monkeypatch.delenv("FFMPEG_BIN", raising=False)

    record = []
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", _make_recording_check(record))

    _cli(
        ["--config", str(cfg), "--fixture", str(fx)],
        prepare_fn=_make_tracking_prepare([]),
        run_pipeline_fn=_fake_run_pipeline,
    )

    assert record, "check_ffmpeg_libass must be called"
    assert record[0] == config_bin, (
        f"config.ffmpeg.ffmpeg_bin must be used when FFMPEG_BIN env absent. "
        f"Expected {config_bin!r}, got {record[0]!r}"
    )
