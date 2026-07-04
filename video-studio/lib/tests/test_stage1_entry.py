"""Tests for Stage-1 entry-level selection and cursor-read marker gate (Phase 6 Task 2).

Pins (per plan Task 2-tests, M1+M2+M3 verifier revisions):

select_next(cfg) -> Selection | None  [lib.syllabus]
  - reads cursor + syllabus → first pending episode from cursor position
  - Selection fields: {episode, seq, slug, doc_refs, source, verify_status,
    learning_goal, topic}
  - all done → None
  - seq = 1-indexed global episode number zero-padded to 3 digits ("001", "002", ...)
  - slug derived from episode title (kebab/ASCII-safe)

Stage-1 entry (driver):
  - syllabus configured + pending → seq/slug come from Selection BEFORE prepare_render
    (seq/slug are closure-bound in build_step_fns at wiring.py:173-174 and must be
    set before prepare_render is called; a mid-pipeline step injecting them is too late)
  - no-pending → entry returns None (clean halt, not a step raise — runner.py:110-117
    does NOT wrap step_fn try/except, so a raise would traceback, M2)

cursor-read step = marker gate (M3):
  - step_cursor_read writes cursor_marker artifact into ctx + a file on disk
  - Phase-2 no-syllabus mode also writes a passing cursor_marker (so fixture render
    E2E doesn't halt at the new gate)

TDD contract: ALL tests must fail (red) on first run because lib.syllabus does not
exist yet (select_next / Selection → ImportError/AttributeError).  The red IS success.
Task 2-impl resolves this.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Fixtures: tiny in-memory YAML syllabus + cursor written to tmp_path
# ---------------------------------------------------------------------------

SYLLABUS_YAML = """\
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
"""

CURSOR_YAML = """\
book: running-science
module_idx: 0
episode_idx: 1
"""

ALL_DONE_SYLLABUS_YAML = """\
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
"""

CURSOR_AT_END_YAML = """\
book: running-science
module_idx: 1
episode_idx: 0
"""


@pytest.fixture()
def tmp_syllabus(tmp_path: Path) -> Path:
    p = tmp_path / "syllabus.yaml"
    p.write_text(SYLLABUS_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def tmp_cursor(tmp_path: Path) -> Path:
    p = tmp_path / "cursor.yaml"
    p.write_text(CURSOR_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def tmp_all_done_syllabus(tmp_path: Path) -> Path:
    p = tmp_path / "syllabus_done.yaml"
    p.write_text(ALL_DONE_SYLLABUS_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def tmp_cursor_at_end(tmp_path: Path) -> Path:
    p = tmp_path / "cursor_end.yaml"
    p.write_text(CURSOR_AT_END_YAML, encoding="utf-8")
    return p


def _make_cfg_with_syllabus(syllabus_path: Path, cursor_path: Path, tmp_path: Path) -> Any:
    """Return a minimal VideoStudioConfig-like object with syllabus configured."""
    from lib.config import load_config
    cfg_yaml = tmp_path / "cfg.yaml"
    cfg_yaml.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: X\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        "ffmpeg:\n  ffmpeg_bin: /bin/true\n  ffprobe_bin: /bin/true\n"
        f"output_dir: {tmp_path}\n"
        f"syllabus_path: {syllabus_path}\n"
        f"cursor_path: {cursor_path}\n",
        encoding="utf-8",
    )
    return load_config(str(cfg_yaml))


def _make_cfg_no_syllabus(tmp_path: Path) -> Any:
    """Return a minimal VideoStudioConfig-like object WITHOUT syllabus (Phase-2 mode)."""
    from lib.config import load_config
    cfg_yaml = tmp_path / "cfg_nosyl.yaml"
    cfg_yaml.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: X\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        "ffmpeg:\n  ffmpeg_bin: /bin/true\n  ffprobe_bin: /bin/true\n"
        f"output_dir: {tmp_path}\n",
        encoding="utf-8",
    )
    return load_config(str(cfg_yaml))


# ---------------------------------------------------------------------------
# Tests: select_next → Selection with correct fields
# ---------------------------------------------------------------------------

class TestSelectNext:
    """select_next(cfg) reads cursor + syllabus → first pending episode."""

    def test_returns_selection_when_pending(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Cursor at module 0 ep 1 (pending) → Selection with expected fields."""
        from lib.syllabus import select_next, Selection  # noqa: F401 — import pins the contract

        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        result = select_next(cfg)

        assert result is not None, "expected Selection, got None"
        assert isinstance(result, Selection)

    def test_selection_has_episode_field(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.episode is the raw episode mapping/dict from the syllabus."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.episode is not None

    def test_selection_seq_is_global_index(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.seq = 1-indexed global position, zero-padded to 3 digits.

        Syllabus: ep[0][0]=done, ep[0][1]=pending(idx=2 global) → seq="002".
        """
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.seq == "002", f"expected seq='002', got {sel.seq!r}"

    def test_selection_slug_derived_from_title(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.slug is a filesystem-safe version of the episode title that
        preserves the original wording and case (acronyms like VDOT stay upper)."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        # "VDOT Pace Tables" → "VDOT-Pace-Tables" (spaces → hyphens, case kept)
        assert sel.slug == "VDOT-Pace-Tables"
        assert " " not in sel.slug, "slug must not contain spaces"
        assert "/" not in sel.slug and "\\" not in sel.slug

    def test_selection_doc_refs(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.doc_refs matches the episode's doc_refs list."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.doc_refs == ["02-vdot-system.md"], (
            f"expected doc_refs=['02-vdot-system.md'], got {sel.doc_refs!r}"
        )

    def test_selection_source(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.source matches the episode's source field."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.source == "Daniels"

    def test_selection_verify_status(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.verify_status matches the episode's verify_status field."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.verify_status == "✅"

    def test_selection_learning_goal(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.learning_goal matches the episode's learning_goal field."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert "VDOT pace tables" in sel.learning_goal

    def test_selection_topic(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection.topic is derived from the episode title."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.topic, "topic must be non-empty"
        # topic should reflect the episode title
        assert "VDOT" in sel.topic or "Pace" in sel.topic or "vdot" in sel.topic.lower()

    def test_returns_none_when_all_done(self, tmp_all_done_syllabus, tmp_cursor_at_end, tmp_path):
        """All episodes done → select_next returns None (clean entry halt)."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_all_done_syllabus, tmp_cursor_at_end, tmp_path)
        result = select_next(cfg)
        assert result is None, f"expected None (all done), got {result!r}"

    def test_returns_none_is_not_a_raise(self, tmp_all_done_syllabus, tmp_cursor_at_end, tmp_path):
        """No-pending must NOT raise — runner step_fns are not wrapped in try/except.

        The entry must return None so the driver can cleanly report 'no pending'
        and exit without running the pipeline.  A raise would produce a traceback
        because runner.py:110-117 calls fn(step, ctx, scratch_dir) bare.
        """
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_all_done_syllabus, tmp_cursor_at_end, tmp_path)
        # Must not raise any exception
        try:
            result = select_next(cfg)
        except Exception as exc:
            pytest.fail(f"select_next raised when it should return None: {exc!r}")
        assert result is None

    def test_returns_none_when_no_syllabus_configured(self, tmp_path):
        """No syllabus config (Phase-2 mode) → select_next returns None (fixture seq/slug used)."""
        from lib.syllabus import select_next
        cfg = _make_cfg_no_syllabus(tmp_path)
        result = select_next(cfg)
        assert result is None, (
            f"expected None when no syllabus configured, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Tests: seq/slug come from Selection (M1 — closure bound BEFORE prepare_render)
# ---------------------------------------------------------------------------

class TestSeqSlugFromSelection:
    """seq and slug must come from select_next(cfg) BEFORE prepare_render is called.

    These are closure-bound in build_step_fns (wiring.py:173-174); injecting them
    mid-pipeline via ctx would fall back to the default '001-ep', which is wrong (M1).
    """

    def test_seq_from_selection_not_default(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Driver must pass sel.seq (not '001') to prepare_render when syllabus active."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        # The pending episode is the 2nd globally → seq != '001'
        assert sel.seq != "001", (
            f"seq should NOT be the default '001' when cursor points to 2nd episode; "
            f"got {sel.seq!r}"
        )

    def test_slug_from_selection_not_default(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Driver must pass sel.slug (not 'ep') to prepare_render when syllabus active."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        assert sel.slug != "ep", (
            f"slug should NOT be the default 'ep' when syllabus is active; "
            f"got {sel.slug!r}"
        )

    def test_selection_dataclass_has_all_required_fields(self, tmp_syllabus, tmp_cursor, tmp_path):
        """Selection must expose all fields needed to seed prepare_render + ctx."""
        from lib.syllabus import select_next, Selection
        import dataclasses

        # Verify Selection is a proper dataclass (or named-tuple) with required fields
        field_names = {f.name for f in dataclasses.fields(Selection)}
        required = {"episode", "seq", "slug", "doc_refs", "source",
                    "verify_status", "learning_goal", "topic"}
        missing = required - field_names
        assert not missing, f"Selection is missing fields: {missing}"


# ---------------------------------------------------------------------------
# Tests: cursor-read step writes cursor_marker artifact (M3)
# ---------------------------------------------------------------------------

class TestCursorReadMarker:
    """step_cursor_read must write ctx['cursor_marker'] pointing to a real file.

    This is the artifact the pipeline gate (check_artifact) verifies.
    Both syllabus-mode and Phase-2 no-syllabus mode must write a passing marker
    so the gate doesn't halt the fixture render E2E (M3).
    """

    def _run_cursor_read(self, cfg, tmp_path, selection=None):
        """Build wiring step_fns for the given cfg and invoke step_cursor_read."""
        from lib.wiring import build_step_fns, MediaSeams, make_ff_seams

        run_ff, probe_ms = make_ff_seams(cfg)
        seams = MediaSeams(dry=True, provider=cfg.media.provider, log=lambda _: None)

        import os
        fixture_path = tmp_path / "beats.json"
        fixture_path.write_text("[]", encoding="utf-8")

        step_fns = build_step_fns(
            cfg,
            seams=seams,
            fixture_path=fixture_path,
            seq="001",
            slug="ep",
            run_ff=run_ff,
            probe_ms=probe_ms,
            log=lambda _: None,
            selection=selection,  # M1: selection injected at build time
        )
        scratch = tmp_path / "scratch"
        scratch.mkdir(exist_ok=True)
        ctx: dict[str, Any] = {}
        step = {"name": "cursor-read", "artifact": "cursor_marker",
                "gate": [{"fn": "check_artifact"}]}
        step_fns["cursor-read"](step, ctx, scratch)
        return ctx

    def test_cursor_read_writes_marker_key_into_ctx(self, tmp_syllabus, tmp_cursor, tmp_path):
        """syllabus mode: step_cursor_read sets ctx['cursor_marker']."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        ctx = self._run_cursor_read(cfg, tmp_path, selection=sel)
        assert "cursor_marker" in ctx, (
            "step_cursor_read must set ctx['cursor_marker'] — "
            "the gate uses this key to find the artifact file"
        )

    def test_cursor_read_marker_file_exists(self, tmp_syllabus, tmp_cursor, tmp_path):
        """syllabus mode: the cursor_marker path points to a real non-empty file."""
        from lib.syllabus import select_next
        from lib.episode import check_artifact
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        ctx = self._run_cursor_read(cfg, tmp_path, selection=sel)
        marker_path = ctx.get("cursor_marker", "")
        gate_result = check_artifact(marker_path)
        assert gate_result["ok"], (
            f"check_artifact failed on cursor_marker={marker_path!r}: "
            f"{gate_result.get('reason')}"
        )

    def test_cursor_read_seeds_ctx_from_selection(self, tmp_syllabus, tmp_cursor, tmp_path):
        """syllabus mode: step_cursor_read seeds doc_refs/source/verify_status/topic/learning_goal into ctx."""
        from lib.syllabus import select_next
        cfg = _make_cfg_with_syllabus(tmp_syllabus, tmp_cursor, tmp_path)
        sel = select_next(cfg)
        assert sel is not None
        ctx = self._run_cursor_read(cfg, tmp_path, selection=sel)
        for field in ("doc_refs", "source", "verify_status", "topic", "learning_goal"):
            assert field in ctx, (
                f"step_cursor_read must seed ctx['{field}'] from selection "
                f"so downstream steps (extract, provenance) can consume it"
            )

    def test_cursor_read_phase2_no_syllabus_also_writes_marker(self, tmp_path):
        """Phase-2 (no syllabus): step_cursor_read still writes a passing cursor_marker.

        Without this, adding check_artifact gate to cursor-read would halt
        fixture render E2E (regression, M3).
        """
        from lib.episode import check_artifact
        cfg = _make_cfg_no_syllabus(tmp_path)
        ctx = self._run_cursor_read(cfg, tmp_path, selection=None)
        assert "cursor_marker" in ctx, (
            "Phase-2 no-syllabus mode: step_cursor_read must ALSO write cursor_marker "
            "so the gate doesn't halt fixture render E2E (M3)"
        )
        gate_result = check_artifact(ctx["cursor_marker"])
        assert gate_result["ok"], (
            f"Phase-2 cursor_marker gate failed: {gate_result.get('reason')}"
        )
