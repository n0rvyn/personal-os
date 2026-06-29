"""Unit tests for storyboard JSON extraction/repair (the real LLM failure modes)."""
import json

from lib import storyboard


def _ok(out, n):
    res = storyboard._parse_storyboard(out, n)
    assert isinstance(res, list), res
    return res


def test_wellformed_array_parses():
    out = '[{"shots":[{"prompt":"a","shot_type":"特写","with_character":true,"pace":"hold"}],"keywords":[]}]'
    res = _ok(out, 1)
    assert res[0]["shots"][0]["pace"] == "hold"


def test_dropped_trailing_bracket_is_repaired():
    # the exact observed failure: model omits the final ']'
    out = '[{"shots":[{"prompt":"a","with_character":true}],"keywords":["x"]}'
    res = _ok(out, 1)
    assert res[0]["keywords"] == ["x"]


def test_trailing_comma_before_close_is_repaired():
    out = '[{"shots":[{"prompt":"a"}],"keywords":[]},]'
    res = _ok(out, 1)
    assert len(res) == 1


def test_prose_wrapper_is_stripped():
    out = '好的，分镜如下：\n[{"shots":[{"prompt":"a"}],"keywords":[]}]\n希望有用'
    res = _ok(out, 1)
    assert res[0]["shots"][0]["prompt"] == "a"


def test_bracket_inside_string_does_not_confuse_scan():
    # a ']' inside a prompt value must not be treated as the array close
    out = '[{"shots":[{"prompt":"区间[T]配速"}],"keywords":[]}]'
    res = _ok(out, 1)
    assert res[0]["shots"][0]["prompt"] == "区间[T]配速"


def test_no_array_returns_error_string():
    assert isinstance(storyboard._parse_storyboard("nothing here", 1), str)


def test_wrong_length_returns_error_string():
    out = '[{"shots":[{"prompt":"a"}],"keywords":[]}]'
    assert isinstance(storyboard._parse_storyboard(out, 3), str)


def test_captured_raw_llm_output_now_parses(tmp_path):
    # regression: the real captured output that failed rfind(']') must parse now.
    import os
    raw_path = os.path.join(os.path.dirname(__file__), "fixtures", "storyboard_truncated.json")
    if not os.path.exists(raw_path):
        return  # fixture optional; skip if not present
    out = open(raw_path, encoding="utf-8").read()
    res = storyboard._parse_storyboard(out, 12)
    assert isinstance(res, list) and len(res) == 12
