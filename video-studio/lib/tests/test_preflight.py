"""Tests for lib/preflight.py — ffmpeg libass preflight check.

Written before lib/preflight.py exists; collection must fail at this point
(`No module named 'lib.preflight'`).

Pins (per Phase 1 plan Task 4-tests):
- check_ffmpeg_libass(ffmpeg_bin, *, runner=subprocess.run) returns None on
  success (ffmpeg -version stdout contains '--enable-libass').
- Raises PreflightError when libass is absent; error message contains 'libass'.
- Raises PreflightError when ffmpeg binary is not found (FileNotFoundError);
  error message contains actionable hints ('FFMPEG_BIN' or 'libass').
- Uses injected fake runner — no real ffmpeg dependency.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.preflight import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 4-impl will resolve this."""
    from lib import preflight  # noqa: F401
    assert hasattr(preflight, "check_ffmpeg_libass")
    assert hasattr(preflight, "PreflightError")


# ---------------------------------------------------------------------------
# Fake runner helpers
# ---------------------------------------------------------------------------

_FFMPEG_VERSION_WITH_LIBASS = """\
ffmpeg version 6.0 Copyright (c) 2000-2023 the FFmpeg developers
built with gcc 11 (GCC)
configuration: --prefix=/usr --enable-libass --enable-libfreetype --enable-libfribidi
libavutil      58.  2.100 / 58.  2.100
"""

_FFMPEG_VERSION_WITHOUT_LIBASS = """\
ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021 the FFmpeg developers
built with gcc 11 (Ubuntu 11.3.0-1ubuntu1~22.04)
configuration: --prefix=/usr --enable-libfreetype --enable-libfribidi
libavutil      56. 70.100 / 56. 70.100
"""


class _FakeRunner:
    """Minimal fake for subprocess.run that returns a controlled CompletedProcess.

    Knobs:
      - stdout_text: text returned as CompletedProcess.stdout
      - raise_file_not_found: if True, raises FileNotFoundError instead of returning
    """

    def __init__(
        self,
        stdout_text: str = "",
        raise_file_not_found: bool = False,
    ):
        self.stdout_text = stdout_text
        self.raise_file_not_found = raise_file_not_found
        self.call_count = 0
        self.last_argv = None

    def __call__(self, argv, **kwargs):
        self.call_count += 1
        self.last_argv = argv

        if self.raise_file_not_found:
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        result = subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=self.stdout_text,
            stderr="",
        )
        return result


# ---------------------------------------------------------------------------
# Success path: ffmpeg with libass → no exception
# ---------------------------------------------------------------------------

def test_check_ffmpeg_libass_passes_when_libass_present():
    """check_ffmpeg_libass must not raise when ffmpeg -version reports
    '--enable-libass' in its configuration line."""
    from lib.preflight import check_ffmpeg_libass

    fake = _FakeRunner(stdout_text=_FFMPEG_VERSION_WITH_LIBASS)
    # Must return without raising
    result = check_ffmpeg_libass("/usr/local/bin/ffmpeg", runner=fake)
    # Return value is None (or falsy) — the function signals success by not raising
    assert result is None or result is True, (
        f"check_ffmpeg_libass must return None or True on success, got {result!r}"
    )
    assert fake.call_count == 1, "runner must be called exactly once"


def test_check_ffmpeg_libass_invokes_ffmpeg_version():
    """check_ffmpeg_libass must call ffmpeg with the -version flag."""
    from lib.preflight import check_ffmpeg_libass

    fake = _FakeRunner(stdout_text=_FFMPEG_VERSION_WITH_LIBASS)
    check_ffmpeg_libass("/fake/ffmpeg", runner=fake)

    assert fake.last_argv is not None, "runner was never called"
    argv = fake.last_argv
    assert isinstance(argv, list), f"argv must be a list, got {type(argv)}"
    assert "/fake/ffmpeg" in argv or argv[0] == "/fake/ffmpeg", (
        f"argv must include the ffmpeg_bin path, got {argv!r}"
    )
    assert "-version" in argv, (
        f"argv must include '-version', got {argv!r}"
    )


# ---------------------------------------------------------------------------
# Failure path: libass absent → PreflightError with 'libass' in message
# ---------------------------------------------------------------------------

def test_check_ffmpeg_libass_raises_when_libass_absent():
    """check_ffmpeg_libass must raise PreflightError when '--enable-libass'
    is absent from ffmpeg -version output."""
    from lib.preflight import check_ffmpeg_libass, PreflightError

    fake = _FakeRunner(stdout_text=_FFMPEG_VERSION_WITHOUT_LIBASS)

    with pytest.raises(PreflightError) as exc_info:
        check_ffmpeg_libass("/usr/bin/ffmpeg", runner=fake)

    error_msg = str(exc_info.value).lower()
    assert "libass" in error_msg, (
        f"PreflightError must mention 'libass', got: {exc_info.value!r}"
    )


def test_check_ffmpeg_libass_error_contains_actionable_hint_when_libass_absent():
    """When libass is absent, the error message must contain actionable hints
    so the user knows what to do ('FFMPEG_BIN' or 'libass' static build)."""
    from lib.preflight import check_ffmpeg_libass, PreflightError

    fake = _FakeRunner(stdout_text=_FFMPEG_VERSION_WITHOUT_LIBASS)

    with pytest.raises(PreflightError) as exc_info:
        check_ffmpeg_libass("/usr/bin/ffmpeg", runner=fake)

    error_msg = str(exc_info.value)
    has_actionable = any(
        tok in error_msg
        for tok in ("FFMPEG_BIN", "libass", "static", "~/.local")
    )
    assert has_actionable, (
        f"Error must contain actionable hint (FFMPEG_BIN/libass/static), "
        f"got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# Failure path: ffmpeg binary not found → PreflightError with actionable hint
# ---------------------------------------------------------------------------

def test_check_ffmpeg_libass_raises_when_binary_not_found():
    """check_ffmpeg_libass must raise PreflightError (not raw FileNotFoundError)
    when the ffmpeg binary does not exist."""
    from lib.preflight import check_ffmpeg_libass, PreflightError

    fake = _FakeRunner(raise_file_not_found=True)

    with pytest.raises(PreflightError) as exc_info:
        check_ffmpeg_libass("/nonexistent/ffmpeg", runner=fake)

    # Must be PreflightError, not the raw FileNotFoundError
    assert isinstance(exc_info.value, PreflightError), (
        f"Must raise PreflightError, got {type(exc_info.value)}"
    )


def test_check_ffmpeg_libass_not_found_error_contains_actionable_hint():
    """When ffmpeg binary is not found, the error message must mention
    FFMPEG_BIN or libass static build so the user knows how to fix it."""
    from lib.preflight import check_ffmpeg_libass, PreflightError

    fake = _FakeRunner(raise_file_not_found=True)

    with pytest.raises(PreflightError) as exc_info:
        check_ffmpeg_libass("/nonexistent/ffmpeg", runner=fake)

    error_msg = str(exc_info.value)
    has_actionable = any(
        tok in error_msg
        for tok in ("FFMPEG_BIN", "libass", "static", "~/.local")
    )
    assert has_actionable, (
        f"Not-found error must contain actionable hint, got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# PreflightError is a subclass of RuntimeError
# ---------------------------------------------------------------------------

def test_preflight_error_is_runtime_error():
    """PreflightError must subclass RuntimeError (per plan spec)."""
    from lib.preflight import PreflightError

    assert issubclass(PreflightError, RuntimeError), (
        f"PreflightError must be a subclass of RuntimeError, "
        f"got bases={PreflightError.__bases__!r}"
    )


# ---------------------------------------------------------------------------
# ffmpeg_bin is passed through correctly to the runner argv
# ---------------------------------------------------------------------------

def test_ffmpeg_bin_is_used_in_argv():
    """The ffmpeg_bin argument must be the first element of the argv passed
    to runner — the binary path must not be hardcoded."""
    from lib.preflight import check_ffmpeg_libass

    custom_bin = "/custom/path/to/ffmpeg-libass"
    fake = _FakeRunner(stdout_text=_FFMPEG_VERSION_WITH_LIBASS)
    check_ffmpeg_libass(custom_bin, runner=fake)

    assert fake.last_argv is not None
    assert fake.last_argv[0] == custom_bin, (
        f"argv[0] must be the ffmpeg_bin ({custom_bin!r}), "
        f"got {fake.last_argv[0]!r}"
    )
