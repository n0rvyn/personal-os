"""Test-first contract for lib.visual_ledger.VisualLedger (Phase 1 shot-timeline).

VisualLedger is the cross-episode visual-dedup account: it persists a set of
normalized prompt signatures per episode (`{episode_dir}/visual_ledger.json`) so
shotplan.plan_windows can avoid re-emitting a visual (within this episode AND
across prior episodes) when deriving interim windows.

Contract:
  - sig(prompt): normalize a prompt string to a stable signature (strip
    whitespace/punctuation, case-fold). Purely literal for Phase 1 — no semantic
    near-match.
  - VisualLedger(episode_dir, prior_dirs=()): seen(sig) / record(sig) / save().
    Loads this episode's own already-recorded signatures (if any) plus every
    prior episode dir's visual_ledger.json into the in-memory `seen` set.
  - _done_episode_dirs(cfg, ctx) (MF-4): pure helper that enumerates already-
    completed episode directories for cross-episode dedup. In fixture/CLI mode
    (no cfg.syllabus_path/cursor_path or no ctx["seq"]) it must return an empty
    tuple WITHOUT raising — this is the no-syllabus Phase-2 path and must not
    crash the pipeline.
"""
import json

import pytest

from lib.visual_ledger import VisualLedger, sig, _done_episode_dirs


# ---------------------------------------------------------------------------
# sig() normalization
# ---------------------------------------------------------------------------


def test_sig_normalizes_whitespace_and_punctuation():
    assert sig("远景，晨跑者剪影！") == sig("远景 晨跑者剪影")


def test_sig_is_case_insensitive():
    assert sig("Runner Silhouette") == sig("runner silhouette")


def test_sig_is_stable_across_repeated_calls():
    prompt = "远景晨跑者剪影，城市天际线"
    assert sig(prompt) == sig(prompt)


def test_sig_differs_for_different_content():
    assert sig("远景晨跑者剪影") != sig("运动手表特写镜头")


# ---------------------------------------------------------------------------
# seen / record / save / load round-trip
# ---------------------------------------------------------------------------


def test_seen_false_initially_true_after_record(tmp_path):
    ep_dir = tmp_path / "ep001"
    ep_dir.mkdir()
    ledger = VisualLedger(ep_dir)

    s = sig("远景晨跑者剪影")
    assert ledger.seen(s) is False
    ledger.record(s)
    assert ledger.seen(s) is True


def test_save_writes_visual_ledger_json(tmp_path):
    ep_dir = tmp_path / "ep002"
    ep_dir.mkdir()
    ledger = VisualLedger(ep_dir)
    s = sig("城市天际线全景")
    ledger.record(s)
    ledger.save()

    out_path = ep_dir / "visual_ledger.json"
    assert out_path.exists()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert s in data.get("signatures", data if isinstance(data, list) else [])


def test_save_then_reload_round_trip(tmp_path):
    ep_dir = tmp_path / "ep003"
    ep_dir.mkdir()
    s = sig("运动手表特写镜头")

    ledger = VisualLedger(ep_dir)
    ledger.record(s)
    ledger.save()

    # A fresh VisualLedger pointed at the same episode dir must pick up the
    # already-recorded signature (own-episode persistence across pipeline runs).
    reloaded = VisualLedger(ep_dir)
    assert reloaded.seen(s) is True


# ---------------------------------------------------------------------------
# Cross-episode dedup via prior_dirs
# ---------------------------------------------------------------------------


def test_prior_dirs_signatures_are_visible_to_seen(tmp_path):
    old_dir = tmp_path / "ep-old"
    old_dir.mkdir()
    s_old = sig("远景晨跑者剪影：历史集已用过")

    old_ledger = VisualLedger(old_dir)
    old_ledger.record(s_old)
    old_ledger.save()

    new_dir = tmp_path / "ep-new"
    new_dir.mkdir()
    new_ledger = VisualLedger(new_dir, prior_dirs=[old_dir])

    assert new_ledger.seen(s_old) is True
    # A signature never recorded anywhere must still read as unseen.
    assert new_ledger.seen(sig("从未用过的全新镜头")) is False


def test_prior_dirs_missing_ledger_file_does_not_raise(tmp_path):
    # A prior episode dir that exists but has no visual_ledger.json (e.g. an
    # old episode predating this feature) must not crash construction.
    old_dir = tmp_path / "ep-old-no-ledger"
    old_dir.mkdir()

    new_dir = tmp_path / "ep-new-2"
    new_dir.mkdir()
    ledger = VisualLedger(new_dir, prior_dirs=[old_dir])
    assert ledger.seen(sig("任意")) is False


# ---------------------------------------------------------------------------
# MF-4: _done_episode_dirs fail-closed in fixture/CLI mode
# ---------------------------------------------------------------------------


class _FakeCfg:
    """Minimal stand-in for lib.config's Config in fixture/CLI (no-syllabus) mode."""

    syllabus_path = ""
    cursor_path = ""


def test_done_episode_dirs_returns_empty_tuple_without_syllabus_or_seq():
    cfg = _FakeCfg()
    ctx = {}  # no "seq" key — fixture/CLI mode
    result = _done_episode_dirs(cfg, ctx)
    assert result == ()


def test_done_episode_dirs_returns_empty_tuple_when_seq_missing_even_with_paths(tmp_path):
    class _CfgWithPaths:
        syllabus_path = str(tmp_path / "syllabus.yaml")
        cursor_path = str(tmp_path / "cursor.yaml")

    ctx = {}  # still no "seq"
    result = _done_episode_dirs(_CfgWithPaths(), ctx)
    assert result == ()
