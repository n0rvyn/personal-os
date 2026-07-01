"""Tests for lib/ffprobe_util.py — measure_gap + probe helpers (spike_acceptance migrated here).

Written BEFORE lib/ffprobe_util.py exists; collection must fail at this point
(`No module named 'lib.ffprobe_util'`). Task 1-impl resolves this.

Nailed-down contracts:
  1. Module imports cleanly and exports measure_gap, probe_dimensions, probe_duration_s.
  2. Function signatures match spike_acceptance originals exactly.
  3. probe_duration_s / probe_dimensions parse ffprobe output correctly (monkeypatch subprocess).
  4. measure_gap computes narration_mean − bgm_mean via _volumedetect_mean (monkeypatch).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# 1. Import + public names (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """lib.ffprobe_util must import cleanly with all required public names."""
    import lib.ffprobe_util as m  # noqa: F401
    for name in ("measure_gap", "probe_dimensions", "probe_duration_s"):
        assert hasattr(m, name), f"lib.ffprobe_util missing: {name}"


# ---------------------------------------------------------------------------
# 2. Function signatures
# ---------------------------------------------------------------------------

def test_measure_gap_signature():
    """measure_gap must accept (base_mp4, bgm, bgm_volume, duck_threshold=0.03)."""
    import inspect
    from lib.ffprobe_util import measure_gap
    sig = inspect.signature(measure_gap)
    params = list(sig.parameters)
    assert params == ["base_mp4", "bgm", "bgm_volume", "duck_threshold"], (
        f"measure_gap signature mismatch: {params}"
    )
    assert sig.parameters["duck_threshold"].default == 0.03, (
        "measure_gap duck_threshold default must be 0.03"
    )


def test_probe_dimensions_signature():
    """probe_dimensions must accept (path) -> (int, int)."""
    import inspect
    from lib.ffprobe_util import probe_dimensions
    sig = inspect.signature(probe_dimensions)
    params = list(sig.parameters)
    assert params == ["path"], f"probe_dimensions signature mismatch: {params}"


def test_probe_duration_s_signature():
    """probe_duration_s must accept (path) -> float."""
    import inspect
    from lib.ffprobe_util import probe_duration_s
    sig = inspect.signature(probe_duration_s)
    params = list(sig.parameters)
    assert params == ["path"], f"probe_duration_s signature mismatch: {params}"


# ---------------------------------------------------------------------------
# 3. probe_duration_s and probe_dimensions parse ffprobe output
# ---------------------------------------------------------------------------

def _make_completed_process(stdout: str, returncode: int = 0):
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


def test_probe_duration_s_parses_output():
    """probe_duration_s must convert ffprobe plain-value output to float."""
    from lib.ffprobe_util import probe_duration_s

    with patch("subprocess.run", return_value=_make_completed_process("123.456\n")):
        result = probe_duration_s("/fake/video.mp4")

    assert isinstance(result, float), "probe_duration_s must return float"
    assert abs(result - 123.456) < 1e-6, f"Expected 123.456, got {result}"


def test_probe_dimensions_parses_output():
    """probe_dimensions must parse '1920x1080' into (1920, 1080)."""
    from lib.ffprobe_util import probe_dimensions

    with patch("subprocess.run", return_value=_make_completed_process("1920x1080\n")):
        w, h = probe_dimensions("/fake/video.mp4")

    assert (w, h) == (1920, 1080), f"Expected (1920, 1080), got ({w}, {h})"


def test_probe_dimensions_returns_ints():
    """probe_dimensions return values must be int, not str."""
    from lib.ffprobe_util import probe_dimensions

    with patch("subprocess.run", return_value=_make_completed_process("640x480")):
        w, h = probe_dimensions("/fake/video.mp4")

    assert isinstance(w, int) and isinstance(h, int), (
        f"probe_dimensions must return (int, int), got ({type(w)}, {type(h)})"
    )


# ---------------------------------------------------------------------------
# 4. measure_gap computes narration_mean − bgm_mean
# ---------------------------------------------------------------------------

def test_measure_gap_returns_difference():
    """measure_gap must return narration_mean - bgm_ducked_mean."""
    from lib.ffprobe_util import measure_gap

    # _volumedetect_mean is called twice: first narration (-12 dB), then BGM (-30 dB).
    call_results = [-12.0, -30.0]

    def fake_volumedetect(ff_input_args, timeout=120):
        return call_results.pop(0)

    with patch("lib.ffprobe_util._volumedetect_mean", side_effect=fake_volumedetect), \
         patch("lib.ffprobe_util.probe_duration_s", return_value=60.0):
        gap = measure_gap("/fake/base.mp4", "/fake/bgm.mp3", "-12dB")

    assert abs(gap - 18.0) < 1e-6, f"Expected gap=18.0 dB, got {gap}"


def test_measure_gap_custom_duck_threshold():
    """measure_gap must pass duck_threshold through to _volumedetect_mean graph."""
    from lib.ffprobe_util import measure_gap

    captured_args = []

    def capture_volumedetect(ff_input_args, timeout=120):
        captured_args.append(ff_input_args)
        return -15.0

    with patch("lib.ffprobe_util._volumedetect_mean", side_effect=capture_volumedetect), \
         patch("lib.ffprobe_util.probe_duration_s", return_value=30.0):
        measure_gap("/fake/base.mp4", "/fake/bgm.mp3", "-10dB", duck_threshold=0.05)

    # Second call (BGM stem) must contain the custom threshold.
    bgm_args = captured_args[1]
    bgm_args_str = " ".join(str(a) for a in bgm_args)
    assert "0.05" in bgm_args_str, (
        f"duck_threshold=0.05 not found in BGM volumedetect args: {bgm_args_str}"
    )
