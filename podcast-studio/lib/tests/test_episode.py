"""Tests for lib/episode.py — naming, sanitization, artifact gate,
select_draft, and scratch cleanup.

Written before lib/episode.py exists; collection must fail at this
point (`No module named 'lib.episode'`).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.episode import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
    from lib import episode  # noqa: F401
    assert hasattr(episode, "episode_paths")
    assert hasattr(episode, "sanitize_title")
    assert hasattr(episode, "check_artifact")
    assert hasattr(episode, "check_stance_card")
    assert hasattr(episode, "check_min_chars")
    assert hasattr(episode, "floor_chars_for_show")
    assert hasattr(episode, "select_draft")
    assert hasattr(episode, "make_scratch")
    assert hasattr(episode, "cleanup_scratch")


# ---------- check_stance_card (continuity gate) ----------

def test_check_stance_card_present(tmp_path):
    """A valid stance card written by the real writer passes the gate."""
    from lib.episode import check_stance_card
    from lib.stance import write_card
    card = {
        "episode": {"date": "2026-06-10", "show": "morning"},
        "bets": [],
        "open_questions": [],
        "named_concept": [],
        "topics": [],
    }
    write_card(str(tmp_path), "2026-06-10", "morning", card)
    result = check_stance_card(str(tmp_path), "2026-06-10", "morning")
    assert result["ok"] is True
    assert "present" in result["reason"]


def test_check_stance_card_absent(tmp_path):
    """No card written → gate fails closed, naming the missing path."""
    from lib.episode import check_stance_card
    result = check_stance_card(str(tmp_path), "2026-06-10", "morning")
    assert result["ok"] is False
    assert "2026-06-10-morning.stance.yaml" in result["reason"]


def test_check_stance_card_malformed(tmp_path):
    """A file present at the canonical path but not a loadable card → fail
    closed, never an uncaught raise."""
    from lib.episode import check_stance_card, stance_path
    p = stance_path(str(tmp_path), "2026-06-10", "morning")
    # Valid YAML, but not a stance card (no `episode` block) — also covers
    # the 'parseable but wrong shape' branch.
    p.write_text("just a string, not a card mapping\n", encoding="utf-8")
    result = check_stance_card(str(tmp_path), "2026-06-10", "morning")
    assert result["ok"] is False


# ---------- sanitize_title ----------

def test_sanitize_title_basic():
    from lib.episode import sanitize_title
    assert sanitize_title("Hello World") == "Hello World"


def test_sanitize_title_replaces_path_separators():
    """Slashes and `..` are replaced with `-`; the slug itself is safe."""
    from lib.episode import sanitize_title
    s = sanitize_title("a/b/../c")
    assert "/" not in s
    assert ".." not in s


def test_sanitize_title_replaces_newlines_and_tabs():
    from lib.episode import sanitize_title
    s = sanitize_title("a\nb\tc")
    assert "\n" not in s
    assert "\t" not in s


def test_sanitize_title_collapses_runs():
    from lib.episode import sanitize_title
    s = sanitize_title("a    b")
    # Multiple spaces collapsed (no triple spaces in output)
    assert "   " not in s


def test_sanitize_title_strips_and_caps_length():
    from lib.episode import sanitize_title
    s = sanitize_title("  hello  ")
    assert s == "hello"


def test_sanitize_title_empty_returns_empty():
    from lib.episode import sanitize_title
    assert sanitize_title("") == ""
    assert sanitize_title("   ") == ""
    # Title with only stripped chars
    assert sanitize_title("///..") == ""


# ---------- episode_paths ----------

def test_script_path_naming(tmp_path):
    """episode_paths(output_dir, date, title) returns script .md + audio .mp3
    both named `{date}-{title}`; stance path is `{date}-{show}.stance.yaml`."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), "2026-06-08", "AI 大事", "morning")
    assert paths["script"].name == "2026-06-08-AI 大事.md"
    assert paths["audio"].name == "2026-06-08-AI 大事.mp3"
    assert paths["stance"].name == "2026-06-08-morning.stance.yaml"
    # All paths must be inside output_dir
    for p in paths.values():
        assert str(p).startswith(str(tmp_path.resolve()))


def test_title_sanitization_no_traversal(tmp_path):
    """A title with /, .., newline becomes a safe slug; the joined path
    stays inside output_dir (no traversal)."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), "2026-06-08", "../../../etc/passwd\n", "morning")
    real = os.path.realpath(paths["script"])
    assert real.startswith(os.path.realpath(str(tmp_path)) + os.sep)


def test_empty_title_fallback(tmp_path):
    """Empty / whitespace-only / all-stripped title → `{date}-{show}` fallback
    naming (date + show)."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), "2026-06-08", "   ", "morning")
    assert paths["script"].name == "2026-06-08-morning.md"
    assert paths["audio"].name == "2026-06-08-morning.mp3"


# ---------- check_artifact ----------

def test_artifact_gate_missing(tmp_path):
    from lib.episode import check_artifact
    result = check_artifact(tmp_path / "no-such.md")
    assert result["ok"] is False
    assert "no-such" in result.get("reason", "")


def test_artifact_gate_present(tmp_path):
    from lib.episode import check_artifact
    p = tmp_path / "ok.md"
    p.write_text("hello", encoding="utf-8")
    result = check_artifact(p)
    assert result["ok"] is True


def test_artifact_gate_empty_file_fails(tmp_path):
    """A zero-byte file is treated as missing (not present)."""
    from lib.episode import check_artifact
    p = tmp_path / "empty.md"
    p.write_text("", encoding="utf-8")
    result = check_artifact(p)
    assert result["ok"] is False


# ---------- check_min_chars (length gate) ----------

def test_floor_chars_for_show_known():
    """Both shows have a coded floor of 6500 字 (product min ~18 min; target ~7000)."""
    from lib.episode import floor_chars_for_show
    assert floor_chars_for_show("morning") == 6500
    assert floor_chars_for_show("evening") == 6500


def test_floor_chars_for_show_unknown_raises():
    """An unknown show fails closed (never a permissive 0 that disables the
    floor)."""
    from lib.episode import floor_chars_for_show
    with pytest.raises(ValueError):
        floor_chars_for_show("midday")


def test_check_min_chars_too_short_fails(tmp_path):
    """A present-but-short draft fails the length gate, naming the counts —
    this is the exact hole the ~1500 字 evening run slipped through."""
    from lib.episode import check_min_chars
    p = tmp_path / "draft-A.md"
    p.write_text("字" * 1500, encoding="utf-8")
    result = check_min_chars(p, 4000)
    assert result["ok"] is False
    assert "1500" in result["reason"]
    assert "4000" in result["reason"]


def test_check_min_chars_at_floor_passes(tmp_path):
    """Exactly at the floor passes (>= , not >)."""
    from lib.episode import check_min_chars
    p = tmp_path / "draft-A.md"
    p.write_text("字" * 4000, encoding="utf-8")
    result = check_min_chars(p, 4000)
    assert result["ok"] is True


def test_check_min_chars_ignores_whitespace(tmp_path):
    """Whitespace / blank lines between 段 must NOT inflate the count: a body
    that is short on real characters but padded with newlines still fails."""
    from lib.episode import check_min_chars
    p = tmp_path / "padded.md"
    # 1000 real chars + 5000 newlines — non-whitespace count is 1000.
    p.write_text("字" * 1000 + "\n" * 5000, encoding="utf-8")
    result = check_min_chars(p, 4000)
    assert result["ok"] is False
    assert "1000" in result["reason"]


def test_check_min_chars_missing_delegates_to_check_artifact(tmp_path):
    """A missing file fails via the composed check_artifact (presence first)."""
    from lib.episode import check_min_chars
    result = check_min_chars(tmp_path / "no-such.md", 4000)
    assert result["ok"] is False
    assert "missing" in result["reason"]


def test_check_min_chars_json_field_short_body_fails(tmp_path):
    """Gating the step-12 finalize body: a short `body` inside
    finalize-result.json fails BEFORE the expensive broadcast+TTS, even though
    the JSON file itself is non-empty and valid."""
    from lib.episode import check_min_chars
    p = tmp_path / "finalize-result.json"
    p.write_text(
        json.dumps({"title": "测试", "body": "字" * 1500}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = check_min_chars(p, 4000, json_field="body")
    assert result["ok"] is False
    assert "1500" in result["reason"]


def test_check_min_chars_json_field_long_body_passes(tmp_path):
    """A long enough `body` passes; the JSON keys / title do not count toward
    the body length."""
    from lib.episode import check_min_chars
    p = tmp_path / "finalize-result.json"
    p.write_text(
        json.dumps({"title": "测试", "body": "字" * 4200}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = check_min_chars(p, 4000, json_field="body")
    assert result["ok"] is True


def test_check_min_chars_json_field_missing_field_fails(tmp_path):
    """A finalize JSON with no `body` field fails closed (not an uncaught
    raise)."""
    from lib.episode import check_min_chars
    p = tmp_path / "finalize-result.json"
    p.write_text(json.dumps({"title": "测试"}), encoding="utf-8")
    result = check_min_chars(p, 4000, json_field="body")
    assert result["ok"] is False


def test_check_min_chars_json_field_unparseable_fails(tmp_path):
    """Garbage that is non-empty but not JSON fails closed when a json_field is
    requested."""
    from lib.episode import check_min_chars
    p = tmp_path / "finalize-result.json"
    p.write_text("not json at all {", encoding="utf-8")
    result = check_min_chars(p, 4000, json_field="body")
    assert result["ok"] is False


# ---------- scratch lifecycle ----------

def test_make_scratch_creates_dir(tmp_path):
    from lib.episode import make_scratch, cleanup_scratch
    s = make_scratch(str(tmp_path), run_id="run-1")
    assert s.exists()
    assert s.is_dir()
    cleanup_scratch(s)


def test_scratch_cleanup_on_success(tmp_path):
    from lib.episode import make_scratch, cleanup_scratch
    s = make_scratch(str(tmp_path), run_id="run-2")
    (s / "draft.md").write_text("x", encoding="utf-8")
    cleanup_scratch(s)
    assert not s.exists()


def test_scratch_cleanup_on_error_path(tmp_path):
    """cleanup_scratch must be safe even if the dir was partially populated
    or doesn't exist (caller is in an except/finally block)."""
    from lib.episode import cleanup_scratch
    # Non-existent path: must not raise
    cleanup_scratch(tmp_path / "never-existed")
    # Existing path with subdirs: cleaned
    s = tmp_path / "scratch-with-sub"
    (s / "sub").mkdir(parents=True)
    (s / "sub" / "f").write_text("x")
    cleanup_scratch(s)
    assert not s.exists()


def test_scratch_cleanup_tolerates_oserror(tmp_path, monkeypatch, capsys):
    """A host/sandbox uid split makes the remove fail with EPERM. cleanup is
    best-effort by contract: it logs to stderr and swallows OSError, never
    raises (so it cannot block pipeline finalize), and leaves the dir in place."""
    from lib.episode import cleanup_scratch
    s = tmp_path / "scratch-uid-locked"
    s.mkdir()
    (s / "f").write_text("x")

    def _boom(*_a, **_k):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(shutil, "rmtree", _boom)
    cleanup_scratch(s)  # must NOT raise
    assert s.exists()  # left in place on failure (harmless residue)
    assert "best-effort skip" in capsys.readouterr().err


# ---------- select_draft ----------

def _verdict(scores):
    """Build a candidates verdict JSON where each candidate has the given
    scores. Helper for the select_draft tests."""
    return {
        "candidates": [
            {"candidate_id": cid, "scores": {**s, "total": sum(s[k] for k in ("洞察", "命名", "跨域", "思考问句"))}, "selected": sel}
            for cid, s, sel in scores
        ]
    }


def test_select_draft_picks_max_total(tmp_path):
    """select_draft picks the candidate with the highest scores.total."""
    from lib.episode import select_draft
    verdict = _verdict([
        ("稿-A", {"洞察": 4, "命名": 3, "跨域": 3, "思考问句": 3}, False),
        ("稿-B", {"洞察": 5, "命名": 5, "跨域": 5, "思考问句": 5}, False),  # total=20
        ("稿-C", {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3}, False),
    ])
    candidates = {"稿-A": "A.md", "稿-B": "B.md", "稿-C": "C.md"}
    chosen_id, chosen_path = select_draft(verdict, candidates)
    assert chosen_id == "稿-B"
    assert chosen_path == "B.md"


def test_select_draft_tiebreak_higher_insight(tmp_path):
    """When totals tie, the candidate with higher 洞察 wins."""
    from lib.episode import select_draft
    verdict = _verdict([
        ("稿-A", {"洞察": 3, "命名": 4, "跨域": 4, "思考问句": 4}, False),  # total=15, 洞察=3
        ("稿-B", {"洞察": 5, "命名": 3, "跨域": 3, "思考问句": 4}, False),  # total=15, 洞察=5
    ])
    candidates = {"稿-A": "A.md", "稿-B": "B.md"}
    chosen_id, _ = select_draft(verdict, candidates)
    assert chosen_id == "稿-B"


def test_select_draft_tiebreak_candidate_order(tmp_path):
    """When total AND 洞察 tie, candidate order decides (稿-A < 稿-B < 稿-C)."""
    from lib.episode import select_draft
    verdict = _verdict([
        ("稿-A", {"洞察": 4, "命名": 4, "跨域": 4, "思考问句": 4}, False),  # total=16, 洞察=4
        ("稿-B", {"洞察": 4, "命名": 4, "跨域": 4, "思考问句": 4}, False),  # total=16, 洞察=4
        ("稿-C", {"洞察": 4, "命名": 4, "跨域": 4, "思考问句": 4}, False),  # total=16, 洞察=4
    ])
    candidates = {"稿-A": "A.md", "稿-B": "B.md", "稿-C": "C.md"}
    chosen_id, _ = select_draft(verdict, candidates)
    # First in the candidates dict order = 稿-A
    assert chosen_id == "稿-A"


def test_select_draft_ignores_mislabeled_selected(tmp_path):
    """Pins the exact bug from the export ref (ref:666-667): even if the
    scoring LLM puts `selected: true` on a NON-top-total candidate,
    `select_draft` still returns the max-total winner (NOT the
    mislabeled-selected candidate)."""
    from lib.episode import select_draft
    verdict = _verdict([
        ("稿-A", {"洞察": 5, "命名": 5, "跨域": 5, "思考问句": 5}, False),  # total=20 (real winner)
        ("稿-B", {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3}, True),  # mislabeled selected
        ("稿-C", {"洞察": 4, "命名": 4, "跨域": 4, "思考问句": 4}, False),  # total=16
    ])
    candidates = {"稿-A": "A.md", "稿-B": "B.md", "稿-C": "C.md"}
    chosen_id, chosen_path = select_draft(verdict, candidates)
    assert chosen_id == "稿-A"
    assert chosen_path == "A.md"


def test_select_draft_malformed_raises(tmp_path):
    """Malformed / empty verdict → explicit error (never silently pick A)."""
    from lib.episode import select_draft
    with pytest.raises(Exception):
        select_draft({}, {"稿-A": "A.md"})
    with pytest.raises(Exception):
        select_draft({"candidates": []}, {"稿-A": "A.md"})
