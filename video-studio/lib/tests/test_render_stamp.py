"""Unit tests for lib.render_stamp (test-FAIL-first contract — written before
lib/render_stamp.py exists; collection must fail at import time).

Covers (Task 2-tests):
  - beats_stamp(fulltext, persona_text, config_fields): pure function,
    reproducible; fulltext / persona_text / config_fields (host name/channel/
    tone, source, verify_status) each change the stamp.
  - shotlist_stamp(beats_json_text, persona_text, config_fields): beats
    content / director.md text / host.gender / art_style / budget override
    each change the stamp.
  - write_stamp / read_stamp / stamp_matches: write-then-read round trip;
    missing file -> stamp_matches False (treated as upstream changed, must
    re-roll); mismatched content -> False.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import render_stamp  # noqa: E402
from lib.render_stamp import (  # noqa: E402
    beats_stamp,
    read_stamp,
    shotlist_stamp,
    stamp_matches,
    write_stamp,
)


# ---------------------------------------------------------------------------
# beats_stamp
# ---------------------------------------------------------------------------

def _base_beats_cfg(**overrides):
    cfg = dict(
        host_name="小跑",
        channel="跑步知识",
        tone="温暖专业",
        source="textbook.pdf",
        verify_status="verified",
    )
    cfg.update(overrides)
    return cfg


def test_beats_stamp_reproducible():
    s1 = beats_stamp("原文内容", "persona 文案", _base_beats_cfg())
    s2 = beats_stamp("原文内容", "persona 文案", _base_beats_cfg())
    assert s1 == s2
    assert isinstance(s1, str) and len(s1) >= 32


def test_beats_stamp_fulltext_changes_stamp():
    s1 = beats_stamp("原文内容 A", "persona 文案", _base_beats_cfg())
    s2 = beats_stamp("原文内容 B", "persona 文案", _base_beats_cfg())
    assert s1 != s2


def test_beats_stamp_persona_text_changes_stamp():
    s1 = beats_stamp("原文内容", "persona 文案 A", _base_beats_cfg())
    s2 = beats_stamp("原文内容", "persona 文案 B", _base_beats_cfg())
    assert s1 != s2


def test_beats_stamp_config_fields_change_stamp():
    base = beats_stamp("原文内容", "persona 文案", _base_beats_cfg())

    for key, changed_value in [
        ("host_name", "阿健"),
        ("channel", "健康跑步频道"),
        ("tone", "轻松幽默"),
        ("source", "textbook_v2.pdf"),
        ("verify_status", "unverified"),
    ]:
        other = beats_stamp(
            "原文内容", "persona 文案", _base_beats_cfg(**{key: changed_value})
        )
        assert other != base, f"changing config field {key!r} did not change beats_stamp"


# ---------------------------------------------------------------------------
# shotlist_stamp
# ---------------------------------------------------------------------------

def _base_shotlist_cfg(**overrides):
    cfg = dict(
        host_gender="female",
        art_style="写实电影",
        budget_override=None,
    )
    cfg.update(overrides)
    return cfg


def test_shotlist_stamp_reproducible():
    s1 = shotlist_stamp('{"beats": []}', "director.md 文案", _base_shotlist_cfg())
    s2 = shotlist_stamp('{"beats": []}', "director.md 文案", _base_shotlist_cfg())
    assert s1 == s2
    assert isinstance(s1, str) and len(s1) >= 32


def test_shotlist_stamp_beats_json_text_changes_stamp():
    s1 = shotlist_stamp('{"beats": [1]}', "director.md 文案", _base_shotlist_cfg())
    s2 = shotlist_stamp('{"beats": [2]}', "director.md 文案", _base_shotlist_cfg())
    assert s1 != s2


def test_shotlist_stamp_director_text_changes_stamp():
    s1 = shotlist_stamp('{"beats": []}', "director.md 文案 A", _base_shotlist_cfg())
    s2 = shotlist_stamp('{"beats": []}', "director.md 文案 B", _base_shotlist_cfg())
    assert s1 != s2


def test_shotlist_stamp_config_fields_change_stamp():
    base = shotlist_stamp('{"beats": []}', "director.md 文案", _base_shotlist_cfg())

    for key, changed_value in [
        ("host_gender", "male"),
        ("art_style", "简笔日漫"),
        ("budget_override", 42),
    ]:
        other = shotlist_stamp(
            '{"beats": []}', "director.md 文案", _base_shotlist_cfg(**{key: changed_value})
        )
        assert other != base, f"changing config field {key!r} did not change shotlist_stamp"


# ---------------------------------------------------------------------------
# write_stamp / read_stamp / stamp_matches
# ---------------------------------------------------------------------------

def test_write_then_read_round_trip(tmp_path):
    p = tmp_path / "beats.json.stamp"
    write_stamp(p, "abc123")
    assert read_stamp(p) == "abc123"


def test_read_stamp_missing_file_returns_none(tmp_path):
    p = tmp_path / "does_not_exist.stamp"
    assert read_stamp(p) is None


def test_stamp_matches_missing_file_returns_false(tmp_path):
    p = tmp_path / "missing.stamp"
    assert stamp_matches(p, "abc123") is False


def test_stamp_matches_content_mismatch_returns_false(tmp_path):
    p = tmp_path / "beats.json.stamp"
    write_stamp(p, "abc123")
    assert stamp_matches(p, "different-value") is False


def test_stamp_matches_content_match_returns_true(tmp_path):
    p = tmp_path / "beats.json.stamp"
    value = beats_stamp("原文内容", "persona 文案", _base_beats_cfg())
    write_stamp(p, value)
    assert stamp_matches(p, value) is True
