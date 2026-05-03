"""pytest UTs for full-session-review helper scripts.

Each test must include the pytest_main_guard so direct execution (python file.py)
does not silently pass without running tests.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = PLUGIN_ROOT / "scripts" / "check_session_report_installed.sh"
DETECT_SCRIPT = PLUGIN_ROOT / "scripts" / "detect_session_report_output.sh"


def test_check_session_report_installed_when_plugin_present():
    """Real-environment test — session-report IS installed in this dev env."""
    result = subprocess.run(
        ["bash", str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "SKILL.md" in result.stdout


def test_check_session_report_installed_when_plugin_missing(tmp_path, monkeypatch):
    """Point HOME at an empty dir -> script must fail with install hint."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = subprocess.run(
        ["bash", str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not installed" in result.stderr
    assert "/plugin install" in result.stderr


def test_detect_session_report_output_finds_newest(tmp_path):
    (tmp_path / "session-report-20260101.html").write_text("old")
    time.sleep(0.05)
    (tmp_path / "session-report-20260202.html").write_text("new")
    result = subprocess.run(
        ["bash", str(DETECT_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip().endswith("session-report-20260202.html")


def test_detect_session_report_output_empty_dir(tmp_path):
    result = subprocess.run(
        ["bash", str(DETECT_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "no session-report-*.html found" in result.stderr


def test_detect_session_report_output_ignores_non_matching(tmp_path):
    (tmp_path / "session-report-20260101.html").write_text("match")
    (tmp_path / "other-file.html").write_text("nope")
    (tmp_path / "session-report.html").write_text("missing date suffix")
    result = subprocess.run(
        ["bash", str(DETECT_SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # session-report.html (no -date) doesn't match the glob session-report-*.html
    assert result.stdout.strip().endswith("session-report-20260101.html")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
