"""lib/preflight.py — ffmpeg libass pre-flight check for video-studio.

Usage:
    from lib.preflight import check_ffmpeg_libass, PreflightError
    check_ffmpeg_libass(ffmpeg_bin)   # raises PreflightError if not ok
"""
from __future__ import annotations

import subprocess
from typing import Callable


class PreflightError(RuntimeError):
    """Raised when a pre-flight environment check fails."""


def check_ffmpeg_libass(
    ffmpeg_bin: str,
    *,
    runner: Callable = subprocess.run,
) -> None:
    """Check that ffmpeg_bin was built with libass support.

    Runs `ffmpeg -version` and inspects the configuration line for
    `--enable-libass`.  Raises PreflightError with an actionable message
    if libass is absent or the binary is missing.

    Args:
        ffmpeg_bin: Absolute path to the ffmpeg binary.
        runner:     Callable with the same signature as subprocess.run
                    (injectable for testing).

    Returns:
        None on success.

    Raises:
        PreflightError: when libass is absent or the binary cannot be found.
    """
    try:
        result = runner(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise PreflightError(
            f"ffmpeg 二进制不存在或不可执行：{ffmpeg_bin!r}。"
            " 请安装带 libass 的静态构建（如 ~/.local/ffmpeg-libass/ffmpeg）"
            " 并经 FFMPEG_BIN 环境变量指过去。"
        ) from None

    stdout = result.stdout or ""
    if "--enable-libass" not in stdout:
        raise PreflightError(
            f"ffmpeg 缺 libass，烧不了硬字幕（{ffmpeg_bin!r} 的 -version 里"
            " 没有 --enable-libass）。"
            " 请用 libass 静态构建（如 ~/.local/ffmpeg-libass/ffmpeg）"
            " 并经 FFMPEG_BIN 环境变量指过去。"
        )
