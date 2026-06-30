"""Unit tests for parse_shotlist — shotlist parse+validate gate (TDD: tests first).

parse_shotlist(raw_text, n_beats) -> list[dict] | str
  - list  = success (validated shotlist)
  - str   = error reason (→ caller logs + triggers runner retry, no raise)

Contract mirrors _parse_storyboard but adds visual_type / shots / prompt validation.
"""
import json
import pytest

from lib import storyboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _good_shot(prompt="跑者晨光中快速向前", shot_type="全景", with_character=True, pace="normal"):
    return {"prompt": prompt, "shot_type": shot_type, "with_character": with_character, "pace": pace}


def _beat(visual_type="still", shots=None, keywords=None, motion_value=None):
    item = {
        "visual_type": visual_type,
        "shots": shots if shots is not None else [_good_shot()],
        "keywords": keywords if keywords is not None else [],
    }
    if motion_value is not None:
        item["motion_value"] = motion_value
    return item


def _raw(items):
    """Serialize items to a JSON string (well-formed)."""
    return json.dumps(items, ensure_ascii=False)


def _ok(raw, n_beats):
    res = storyboard.parse_shotlist(raw, n_beats)
    assert isinstance(res, list), f"expected list, got str: {res}"
    return res


def _err(raw, n_beats):
    res = storyboard.parse_shotlist(raw, n_beats)
    assert isinstance(res, str), f"expected error str, got list"
    return res


# ---------------------------------------------------------------------------
# Happy path — well-formed JSON
# ---------------------------------------------------------------------------

def test_wellformed_still_parses():
    items = [_beat("still")]
    res = _ok(_raw(items), 1)
    assert len(res) == 1
    assert res[0]["visual_type"] == "still"


def test_all_visual_types_accepted():
    items = [_beat("still"), _beat("video", motion_value=0.8), _beat("chart"), _beat("multi")]
    res = _ok(_raw(items), 4)
    assert [r["visual_type"] for r in res] == ["still", "video", "chart", "multi"]


def test_video_without_motion_value_is_tolerated():
    """motion_value missing on a video item is fine — budget gate defaults to 0."""
    item = _beat("video")  # no motion_value
    res = _ok(_raw([item]), 1)
    assert res[0]["visual_type"] == "video"
    assert "motion_value" not in res[0] or res[0].get("motion_value") is None  # either absent or None is fine


def test_prose_wrapper_stripped():
    """LLM often wraps the array in explanation text."""
    items = [_beat("still")]
    raw = "好的，以下是分镜方案：\n" + _raw(items) + "\n希望有用"
    res = _ok(raw, 1)
    assert res[0]["visual_type"] == "still"


# ---------------------------------------------------------------------------
# Tolerant JSON repair (POC6 real failure modes via _extract_json_array)
# ---------------------------------------------------------------------------

def test_dropped_trailing_bracket_repaired():
    """Most common LLM failure: omits the final ']'."""
    item = _beat("still")
    well = _raw([item])
    truncated = well[:-1]  # drop the trailing ']'
    res = _ok(truncated, 1)
    assert res[0]["visual_type"] == "still"


def test_trailing_comma_before_close_repaired():
    """JSON like [...,] is invalid but observed in the wild."""
    items = [_beat("still")]
    raw = _raw(items)[:-1] + ",]"  # inject trailing comma
    res = _ok(raw, 1)
    assert len(res) == 1


def test_chinese_bracket_inside_string_not_confused():
    """A ']' or '[' inside a Chinese prompt must not terminate the array scan."""
    item = _beat("still", shots=[_good_shot(prompt="区间[T]强度的乳酸阈训练")])
    raw = _raw([item])
    res = _ok(raw, 1)
    assert res[0]["shots"][0]["prompt"] == "区间[T]强度的乳酸阈训练"


def test_multiple_items_dropped_bracket():
    items = [_beat("still"), _beat("video", motion_value=0.9)]
    well = _raw(items)
    truncated = well[:-1]  # drop trailing ']'
    res = _ok(truncated, 2)
    assert len(res) == 2


# ---------------------------------------------------------------------------
# Unrecoverable bad JSON → error string (not raise)
# ---------------------------------------------------------------------------

def test_no_array_returns_error_string():
    _err("这里没有任何JSON数组，只有散文描述。", 1)


def test_mid_truncation_returns_error_string():
    """A truncation mid-string leaves the JSON unparseable even after repair."""
    raw = '[{"visual_type":"still","shots":[{"prompt":"跑者在山'  # cut mid-string
    result = storyboard.parse_shotlist(raw, 1)
    assert isinstance(result, str)  # either error str or (if repair miraculous) list — no raise


def test_completely_empty_input_returns_error_string():
    _err("", 1)


def test_empty_array_wrong_length_returns_error_string():
    _err("[]", 1)


# ---------------------------------------------------------------------------
# Length validation
# ---------------------------------------------------------------------------

def test_wrong_length_returns_error_string():
    items = [_beat("still")]
    _err(_raw(items), 3)


def test_correct_length_passes():
    items = [_beat("still"), _beat("video", motion_value=0.5), _beat("chart")]
    res = _ok(_raw(items), 3)
    assert len(res) == 3


def test_zero_beats_empty_array_ok():
    _ok("[]", 0)


# ---------------------------------------------------------------------------
# visual_type validation
# ---------------------------------------------------------------------------

def test_invalid_visual_type_returns_error_string():
    item = _beat("still")
    item["visual_type"] = "animation"  # not in {still, video, chart, multi}
    _err(_raw([item]), 1)


def test_missing_visual_type_returns_error_string():
    item = _beat("still")
    del item["visual_type"]
    _err(_raw([item]), 1)


def test_visual_type_empty_string_returns_error_string():
    item = _beat("still")
    item["visual_type"] = ""
    _err(_raw([item]), 1)


# ---------------------------------------------------------------------------
# shots field validation
# ---------------------------------------------------------------------------

def test_missing_shots_field_returns_error_string():
    item = _beat("still")
    del item["shots"]
    _err(_raw([item]), 1)


def test_empty_shots_list_returns_error_string():
    item = _beat("still", shots=[])
    _err(_raw([item]), 1)


def test_shot_missing_prompt_returns_error_string():
    shot = {"shot_type": "全景", "with_character": False, "pace": "normal"}  # no prompt
    item = _beat("still", shots=[shot])
    _err(_raw([item]), 1)


def test_shot_empty_prompt_returns_error_string():
    shot = _good_shot(prompt="")
    item = _beat("still", shots=[shot])
    _err(_raw([item]), 1)


def test_multiple_shots_per_beat_accepted():
    shots = [_good_shot("山路弯曲"), _good_shot("运动员近景")]
    item = _beat("multi", shots=shots)
    res = _ok(_raw([item]), 1)
    assert len(res[0]["shots"]) == 2


# ---------------------------------------------------------------------------
# No raise guarantee — parse_shotlist must never raise, only return str on errors
# ---------------------------------------------------------------------------

def test_parse_shotlist_never_raises_on_garbage():
    garbage_inputs = [
        None if False else "not-json",
        "<<<malformed>>>",
        "[{broken",
        '{"not": "an array"}',
        "null",
        "true",
        "42",
    ]
    for bad in garbage_inputs:
        result = storyboard.parse_shotlist(bad, 1)
        assert isinstance(result, str), f"raised or returned list for: {bad!r}"


def test_parse_shotlist_never_raises_on_wrong_types_in_items():
    # items that are not dicts
    raw = json.dumps(["string_item", 42, None])
    result = storyboard.parse_shotlist(raw, 3)
    assert isinstance(result, str)
