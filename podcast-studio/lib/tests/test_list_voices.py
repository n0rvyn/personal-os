"""Task 12: tools/list_voices.py parses the voice catalog by provider.

Contract: volc IDs are BARE (no `volc-` prefix, matching how config stores them)
and INCLUDE the "NOT verified" sub-table — notably BV001_streaming, the
config.example.yaml default. minimax IDs keep the `mm-` prefix. The cross-vendor
equivalents table (whose IDs carry prefixes) is excluded.
"""
import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(provider: str) -> list:
    proc = subprocess.run(
        [sys.executable, "tools/list_voices.py", "--provider", provider],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_volc_voices_bare_and_include_default():
    voices = _run("volc")
    # the example-config default lives in the "NOT verified" sub-table
    assert "BV001_streaming" in voices
    # a verified-table id is present
    assert "zh_male_yuanboxiaoshu_uranus_bigtts" in voices
    # bare ids only — no prefixes, no minimax leakage
    assert all(not v.startswith("volc-") for v in voices)
    assert all(not v.startswith("mm-") for v in voices)


def test_minimax_voices_prefixed():
    voices = _run("minimax")
    assert any(v.startswith("mm-") for v in voices)
    assert "mm-Chinese (Mandarin)_Radio_Host" in voices
    # no volc ids leaked in
    assert "BV001_streaming" not in voices


def test_unknown_provider_empty_or_error():
    proc = subprocess.run(
        [sys.executable, "tools/list_voices.py", "--provider", "nope"],
        cwd=str(PLUGIN_ROOT),
        capture_output=True,
        text=True,
    )
    # unknown provider: empty list is acceptable (no crash)
    if proc.returncode == 0:
        assert json.loads(proc.stdout) == []
