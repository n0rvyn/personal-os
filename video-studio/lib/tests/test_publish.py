"""Tests for step_publish — mark current episode done + advance cursor (Phase 6 Task 4).

TDD fail-first contract: step_publish is currently a no-op placeholder
(wiring.py line ~567: logs "[publish] placeholder (Phase 7 wires real publish)").
ALL tests below MUST fail (red) until Task 4-impl replaces the placeholder.

Pins (per plan Task 4-tests):

  syllabus mode (cfg.syllabus_path configured):
    - step_publish marks current seq/slug episode status→done
    - step_publish advances cursor to next pending
    - save_syllabus + save_cursor written back to disk
    - final.mp4 missing → raise / error, cursor NOT advanced
    - idempotent: calling step_publish again on an already-done episode does
      NOT double-advance the cursor

  Phase-2 mode (no syllabus):
    - step_publish is a no-op (placeholder preserved); no raise

Fixtures: tmp syllabus + cursor YAML + final.mp4 sentinel written to tmp_path.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.syllabus import (  # noqa: E402
    load_syllabus,
    load_cursor,
    Selection,
)

# ---------------------------------------------------------------------------
# YAML fixture content
# ---------------------------------------------------------------------------

# Syllabus with 2 modules x 2 episodes each; ep[0][0] is done, ep[0][1] is pending,
# ep[1][0] and ep[1][1] are pending.  Cursor sits at module_idx=0, episode_idx=1
# (pointing to the first pending episode, seq="002").
SYLLABUS_YAML = textwrap.dedent("""\
    book: running-science
    character_ref: ""
    modules:
      - name: VDOT System
        style: educational
        episodes:
          - title: What is VDOT
            doc_refs: ["01-daniels-overview.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Understand the VDOT concept
            status: done
          - title: VDOT Pace Tables
            doc_refs: ["02-vdot-system.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Read and use VDOT pace tables
            status: locked
      - name: Training Zones
        style: educational
        episodes:
          - title: Seven Training Zones
            doc_refs: ["03-training-zones.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Understand the seven training zones
            status: locked
          - title: Easy Run Zone
            doc_refs: ["03-training-zones.md#easy"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Run in the easy zone
            status: locked
""")

# Cursor pointing at module_idx=0, episode_idx=1 (the first pending ep, "VDOT Pace Tables")
CURSOR_YAML = textwrap.dedent("""\
    book: running-science
    module_idx: 0
    episode_idx: 1
""")

# All-done syllabus (for idempotency test)
ALL_DONE_SYLLABUS_YAML = textwrap.dedent("""\
    book: running-science
    character_ref: ""
    modules:
      - name: VDOT System
        style: educational
        episodes:
          - title: What is VDOT
            doc_refs: ["01-daniels-overview.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Understand the VDOT concept
            status: done
          - title: VDOT Pace Tables
            doc_refs: ["02-vdot-system.md"]
            source: Daniels
            verify_status: "✅"
            learning_goal: Read and use VDOT pace tables
            status: done
""")

# Cursor at start (module_idx=0, episode_idx=0) used for the all-done scenario
ALL_DONE_CURSOR_YAML = textwrap.dedent("""\
    book: running-science
    module_idx: 0
    episode_idx: 0
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, syllabus_path: Path | None, cursor_path: Path | None) -> Any:
    """Return a VideoStudioConfig loaded from a tmp YAML."""
    from lib.config import load_config

    lines = [
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: Arial\n",
        "audio:\n  voice_id: voice-abc\n  bgm_duck_db: 18.0\n",
        "media:\n  provider: doubao\n",
        "budget:\n  video_shots_per_episode: 2\n",
        "ffmpeg:\n  ffmpeg_bin: /bin/true\n  ffprobe_bin: /bin/true\n",
        f"output_dir: {tmp_path}\n",
    ]
    if syllabus_path:
        lines.append(f"syllabus_path: {syllabus_path}\n")
    if cursor_path:
        lines.append(f"cursor_path: {cursor_path}\n")

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("".join(lines), encoding="utf-8")
    return load_config(str(cfg_path))


def _build_publish_fn(
    tmp_path: Path,
    *,
    syllabus_path: Path | None = None,
    cursor_path: Path | None = None,
    seq: str = "002",
    slug: str = "vdot-pace-tables",
    selection: Any = None,
):
    """Return the step_publish callable extracted from build_step_fns."""
    from lib.wiring import build_step_fns

    cfg = _make_cfg(tmp_path, syllabus_path, cursor_path)

    def _noop(*args, **kwargs):
        pass

    step_fns = build_step_fns(
        cfg,
        seams=_DummySeams(),
        fixture_path=tmp_path / "fixture.json",
        seq=seq,
        slug=slug,
        run_ff=_noop,
        probe_ms=lambda p: 1000,
        log=lambda msg: None,
        selection=selection,
    )
    return step_fns["publish"]


class _DummySeams:
    """Minimal stub for MediaSeams — step_publish doesn't use media methods."""
    dry = True
    provider = "doubao"

    def gen_still(self, *a, **kw):
        raise NotImplementedError

    def gen_video(self, *a, **kw):
        raise NotImplementedError

    def synth(self, *a, **kw):
        raise NotImplementedError


def _make_selection(syllabus_path: Path, cursor_path: Path) -> Any:
    """Build a Selection from existing syllabus/cursor files."""
    from lib.syllabus import select_next

    # Use a simple namespace to avoid Python class-body scoping issues
    class _FakeCfg:
        pass

    obj = _FakeCfg()
    obj.syllabus_path = str(syllabus_path)
    obj.cursor_path = str(cursor_path)
    return select_next(obj)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def syl_path(tmp_path: Path) -> Path:
    p = tmp_path / "syllabus.yaml"
    p.write_text(SYLLABUS_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def cur_path(tmp_path: Path) -> Path:
    p = tmp_path / "cursor.yaml"
    p.write_text(CURSOR_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def final_mp4(tmp_path: Path) -> Path:
    """A zero-byte sentinel standing in for the real final.mp4."""
    ep_dir = tmp_path / "episodes" / "002-vdot-pace-tables"
    ep_dir.mkdir(parents=True)
    p = ep_dir / "final.mp4"
    p.write_bytes(b"")
    return p


@pytest.fixture()
def all_done_syl_path(tmp_path: Path) -> Path:
    p = tmp_path / "syllabus_done.yaml"
    p.write_text(ALL_DONE_SYLLABUS_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def all_done_cur_path(tmp_path: Path) -> Path:
    p = tmp_path / "cursor_done.yaml"
    p.write_text(ALL_DONE_CURSOR_YAML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests: syllabus mode — happy path
# ---------------------------------------------------------------------------


class TestPublishSyllabusMode:
    """step_publish with syllabus configured and final.mp4 present."""

    def test_marks_current_episode_done(
        self, tmp_path: Path, syl_path: Path, cur_path: Path, final_mp4: Path
    ):
        """After step_publish the episode at cursor (module=0, ep=1) is done."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None, "test setup: selection must not be None"

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        ctx: dict = {"final_mp4": str(final_mp4)}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        step_publish({}, ctx, scratch)

        # Reload syllabus from disk — the episode at cursor position must now be "done"
        syl = load_syllabus(syl_path)
        assert syl.modules[0].episodes[1].status == "done", (
            "step_publish must persist status=done for the current episode"
        )

    def test_advances_cursor_to_next_pending(
        self, tmp_path: Path, syl_path: Path, cur_path: Path, final_mp4: Path
    ):
        """After step_publish the cursor points at the next pending episode."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        ctx: dict = {"final_mp4": str(final_mp4)}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        step_publish({}, ctx, scratch)

        # Reload cursor — must now point at module_idx=1, episode_idx=0
        # ("Seven Training Zones", seq=003)
        cur = load_cursor(cur_path)
        assert (cur.module_idx, cur.episode_idx) == (1, 0), (
            f"cursor should advance to (1, 0) after publish; got ({cur.module_idx}, {cur.episode_idx})"
        )

    def test_saves_syllabus_back_to_disk(
        self, tmp_path: Path, syl_path: Path, cur_path: Path, final_mp4: Path
    ):
        """step_publish must write the updated syllabus back to disk."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        import os
        mtime_before = os.path.getmtime(syl_path)

        ctx: dict = {"final_mp4": str(final_mp4)}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        step_publish({}, ctx, scratch)

        mtime_after = os.path.getmtime(syl_path)
        assert mtime_after > mtime_before, (
            "step_publish must update the syllabus file on disk"
        )

    def test_saves_cursor_back_to_disk(
        self, tmp_path: Path, syl_path: Path, cur_path: Path, final_mp4: Path
    ):
        """step_publish must write the updated cursor back to disk."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        import os
        mtime_before = os.path.getmtime(cur_path)

        ctx: dict = {"final_mp4": str(final_mp4)}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        step_publish({}, ctx, scratch)

        mtime_after = os.path.getmtime(cur_path)
        assert mtime_after > mtime_before, (
            "step_publish must update the cursor file on disk"
        )


# ---------------------------------------------------------------------------
# Tests: final.mp4 missing → error, cursor NOT advanced
# ---------------------------------------------------------------------------


class TestPublishMissingFinalMp4:
    """step_publish with final.mp4 absent must error and must NOT advance cursor."""

    def test_raises_when_final_mp4_missing(
        self, tmp_path: Path, syl_path: Path, cur_path: Path
    ):
        """If ctx['final_mp4'] does not exist on disk, step_publish must raise."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        ctx: dict = {"final_mp4": str(tmp_path / "nonexistent" / "final.mp4")}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        with pytest.raises((FileNotFoundError, RuntimeError, ValueError, OSError)):
            step_publish({}, ctx, scratch)

    def test_cursor_not_advanced_when_final_mp4_missing(
        self, tmp_path: Path, syl_path: Path, cur_path: Path
    ):
        """Cursor must remain unchanged if step_publish errors due to missing final.mp4."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        ctx: dict = {"final_mp4": str(tmp_path / "nonexistent" / "final.mp4")}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        try:
            step_publish({}, ctx, scratch)
        except Exception:
            pass  # expected error

        # Cursor must still point at (0, 1)
        cur = load_cursor(cur_path)
        assert (cur.module_idx, cur.episode_idx) == (0, 1), (
            "cursor must NOT advance if final.mp4 is missing"
        )


# ---------------------------------------------------------------------------
# Tests: Phase-2 mode (no syllabus) → placeholder preserved
# ---------------------------------------------------------------------------


class TestPublishPhase2NoSyllabus:
    """step_publish with no syllabus configured is a no-op (Phase-2 backward-compat)."""

    def test_no_error_when_no_syllabus(self, tmp_path: Path):
        """step_publish must not raise when cfg.syllabus_path is empty."""
        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=None,
            cursor_path=None,
            seq="001",
            slug="ep",
            selection=None,
        )

        ctx: dict = {}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        # Must not raise
        step_publish({}, ctx, scratch)

    def test_no_syllabus_no_cursor_written(self, tmp_path: Path):
        """In Phase-2 mode, no cursor or syllabus file should be written."""
        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=None,
            cursor_path=None,
            seq="001",
            slug="ep",
            selection=None,
        )

        ctx: dict = {}
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        step_publish({}, ctx, scratch)

        # No syllabus or cursor files should have been created by publish
        syl_file = tmp_path / "syllabus.yaml"
        cur_file = tmp_path / "cursor.yaml"
        assert not syl_file.exists(), "Phase-2 publish must not create syllabus.yaml"
        assert not cur_file.exists(), "Phase-2 publish must not create cursor.yaml"


# ---------------------------------------------------------------------------
# Tests: Idempotency — repeat publish of same episode
# ---------------------------------------------------------------------------


class TestPublishIdempotent:
    """Calling step_publish twice for the same episode must not double-advance."""

    def test_idempotent_repeat_publish(
        self, tmp_path: Path, syl_path: Path, cur_path: Path, final_mp4: Path
    ):
        """A second call to step_publish after the episode is already done must not
        advance the cursor a second time."""
        selection = _make_selection(syl_path, cur_path)
        assert selection is not None

        step_publish = _build_publish_fn(
            tmp_path,
            syllabus_path=syl_path,
            cursor_path=cur_path,
            seq=selection.seq,
            slug=selection.slug,
            selection=selection,
        )

        scratch = tmp_path / "scratch"
        scratch.mkdir()

        # First publish — advances to (1, 0)
        ctx: dict = {"final_mp4": str(final_mp4)}
        step_publish({}, ctx, scratch)

        cur_after_first = load_cursor(cur_path)
        first_pos = (cur_after_first.module_idx, cur_after_first.episode_idx)

        # Second publish with same selection/ctx — must NOT advance further
        ctx2: dict = {"final_mp4": str(final_mp4)}
        try:
            step_publish({}, ctx2, scratch)
        except Exception:
            pass  # a raise is acceptable; what matters is cursor didn't double-advance

        cur_after_second = load_cursor(cur_path)
        second_pos = (cur_after_second.module_idx, cur_after_second.episode_idx)

        assert first_pos == second_pos, (
            f"cursor advanced twice: first={first_pos}, second={second_pos}; "
            "repeat publish of same episode must be idempotent"
        )
