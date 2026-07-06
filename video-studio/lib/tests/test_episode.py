"""Tests for lib/episode.py — episode directory structure, scratch lifecycle,
artifact gate, and path-traversal guard.

Written before lib/episode.py exists; collection must fail at this point
(`No module named 'lib.episode'`). Task 1-impl resolves this.
"""
from __future__ import annotations

import os
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
    contract — Task 1-impl will resolve this."""
    from lib import episode  # noqa: F401
    assert hasattr(episode, "episode_paths")
    assert hasattr(episode, "check_artifact")
    assert hasattr(episode, "make_scratch")
    assert hasattr(episode, "EpisodeError")


# ---------- episode_paths: directory structure ----------

def test_episode_paths_returns_all_keys(tmp_path):
    """episode_paths(output_dir, seq, slug) must return all 7 video artifact
    keys covering the full per-episode product set."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="intro-to-python")
    required_keys = {
        "script",
        "beats",
        "shotlist",
        "assets_dir",
        "audio",
        "subtitles",
        "final",
        "provenance",
    }
    assert required_keys == set(paths.keys()), (
        f"Missing keys: {required_keys - set(paths.keys())}; "
        f"Extra keys: {set(paths.keys()) - required_keys}"
    )


def test_episode_paths_inside_seq_slug_dir(tmp_path):
    """All artifact paths must live inside `{output_dir}/episodes/{seq}-{slug}/`."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="intro-to-python")
    expected_episode_dir = Path(tmp_path) / "episodes" / "001-intro-to-python"
    for key, p in paths.items():
        assert str(p).startswith(str(expected_episode_dir)), (
            f"Key '{key}' path {p} is not inside {expected_episode_dir}"
        )


def test_episode_paths_script_is_md(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["script"].suffix == ".md"


def test_episode_paths_shotlist_is_json(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["shotlist"].suffix == ".json"


def test_episode_paths_audio_is_mp3(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["audio"].suffix == ".mp3"


def test_episode_paths_subtitles_is_srt(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["subtitles"].suffix == ".srt"


def test_episode_paths_final_is_mp4(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["final"].suffix == ".mp4"


def test_episode_paths_provenance_is_json(tmp_path):
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="test-slug")
    assert paths["provenance"].suffix == ".json"


def test_episode_paths_seq_slug_in_dir_name(tmp_path):
    """Directory name must embed seq and slug so episodes don't collide."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="007", slug="quicksort")
    for key, p in paths.items():
        # The episode directory name must contain seq and slug.
        parts = Path(str(p)).parts
        episode_dir = next(
            (part for part in parts if "007" in part and "quicksort" in part), None
        )
        assert episode_dir is not None, (
            f"Key '{key}': no parent dir containing '007' and 'quicksort' in {p}"
        )


def test_episode_paths_final_matches_dir_name(tmp_path):
    """The output video's basename (stem) must equal its episode directory name,
    so the two always carry the same formatted name — e.g.
    episodes/002-别信.../002-别信....mp4."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="002", slug="别信-每周只能加10")
    final = paths["final"]
    ep_dir = final.parent
    assert final.suffix == ".mp4"
    assert final.stem == ep_dir.name == "002-别信-每周只能加10"


def test_episode_paths_returns_path_objects(tmp_path):
    """All returned values must be Path objects (not plain strings)."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="001", slug="slug")
    for key, p in paths.items():
        assert isinstance(p, Path), f"Key '{key}' is not a Path: {type(p)}"


# ---------- beats.json durable path (media-cache resume) ----------

def test_episode_paths_beats_json_same_dir_as_shotlist(tmp_path):
    """episode_paths must expose ep['beats'] pointing at
    episodes/{seq}-{slug}/beats.json, alongside shotlist.json, so the
    script step can persist beats durably for resume/reuse."""
    from lib.episode import episode_paths
    paths = episode_paths(str(tmp_path), seq="002", slug="x")
    expected = tmp_path / "episodes" / "002-x" / "beats.json"
    assert paths["beats"] == expected
    assert paths["beats"].parent == paths["shotlist"].parent


# ---------- path-traversal guard ----------

def test_episode_paths_traversal_guard_rejects_dotdot_in_slug(tmp_path):
    """A slug containing '..' must either be sanitized or raise EpisodeError —
    the artifact path must not escape the episode/scratch directory."""
    from lib.episode import episode_paths, EpisodeError
    try:
        paths = episode_paths(str(tmp_path), seq="001", slug="../../etc/passwd")
    except (EpisodeError, ValueError):
        return  # explicit rejection is fine
    # If accepted, every returned path must still live inside output_dir.
    real_base = os.path.realpath(str(tmp_path))
    for key, p in paths.items():
        real_p = os.path.realpath(str(p))
        assert real_p.startswith(real_base), (
            f"Path traversal not blocked for key '{key}': {p} escapes {tmp_path}"
        )


def test_episode_paths_traversal_guard_rejects_dotdot_in_seq(tmp_path):
    """A seq containing '..' must be rejected or sanitized."""
    from lib.episode import episode_paths, EpisodeError
    try:
        paths = episode_paths(str(tmp_path), seq="../evil", slug="normal")
    except (EpisodeError, ValueError):
        return
    real_base = os.path.realpath(str(tmp_path))
    for key, p in paths.items():
        real_p = os.path.realpath(str(p))
        assert real_p.startswith(real_base), (
            f"Path traversal not blocked for key '{key}': {p} escapes {tmp_path}"
        )


# ---------- check_artifact ----------

def test_artifact_gate_missing(tmp_path):
    from lib.episode import check_artifact
    result = check_artifact(tmp_path / "no-such.mp4")
    assert result["ok"] is False
    assert "no-such" in result.get("reason", "")


def test_artifact_gate_present(tmp_path):
    from lib.episode import check_artifact
    p = tmp_path / "final.mp4"
    p.write_bytes(b"\x00" * 100)
    result = check_artifact(p)
    assert result["ok"] is True


def test_artifact_gate_empty_file_fails(tmp_path):
    """A zero-byte file is treated as missing (not present)."""
    from lib.episode import check_artifact
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    result = check_artifact(p)
    assert result["ok"] is False


def test_artifact_gate_returns_reason(tmp_path):
    """check_artifact always returns a dict with 'ok' and 'reason' keys."""
    from lib.episode import check_artifact
    result_miss = check_artifact(tmp_path / "missing.srt")
    assert "ok" in result_miss and "reason" in result_miss
    p = tmp_path / "ok.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    result_ok = check_artifact(p)
    assert "ok" in result_ok and "reason" in result_ok


# ---------- make_scratch / cleanup ----------

def test_make_scratch_creates_dir(tmp_path):
    from lib.episode import make_scratch
    s = make_scratch(str(tmp_path), tag="e001-slug")
    assert s.exists()
    assert s.is_dir()


def test_make_scratch_dir_is_empty(tmp_path):
    """A freshly created scratch must start empty."""
    from lib.episode import make_scratch
    s = make_scratch(str(tmp_path), tag="e001")
    assert list(s.iterdir()) == []


def test_make_scratch_unique_per_call(tmp_path):
    """Two calls with the same tag must produce different directories."""
    from lib.episode import make_scratch
    a = make_scratch(str(tmp_path), tag="e001-slug")
    (a / "intermediate.mp4").write_bytes(b"\x00")
    b = make_scratch(str(tmp_path), tag="e001-slug")
    assert a != b, "Two make_scratch calls must not return the same dir"
    assert b.exists() and b.is_dir()
    # Second scratch starts empty — cannot inherit first scratch's content.
    assert not (b / "intermediate.mp4").exists()


def test_make_scratch_inside_output_dir(tmp_path):
    """The scratch dir must live inside output_dir (or a sub-dir of it)."""
    from lib.episode import make_scratch
    s = make_scratch(str(tmp_path), tag="e001")
    real_output = os.path.realpath(str(tmp_path))
    real_scratch = os.path.realpath(str(s))
    assert real_scratch.startswith(real_output), (
        f"Scratch dir {s} is not inside output_dir {tmp_path}"
    )


def test_make_scratch_invalid_tag_raises(tmp_path):
    """An empty tag must raise (fail closed — never adopt an existing dir
    silently)."""
    from lib.episode import make_scratch, EpisodeError
    with pytest.raises((EpisodeError, ValueError)):
        make_scratch(str(tmp_path), tag="")


# ---------- EpisodeError ----------

def test_episode_error_is_exception():
    from lib.episode import EpisodeError
    assert issubclass(EpisodeError, Exception)


# ---------- archive_episode_output ----------

def test_archive_moves_episode_dir(tmp_path):
    """Episode dir is moved to .archive/{seq}-{slug}-1/ and original is gone."""
    from lib.episode import archive_episode_output
    ep_dir = tmp_path / "episodes" / "001-x"
    ep_dir.mkdir(parents=True)
    (ep_dir / "final.mp4").write_bytes(b"\x00" * 100)

    result = archive_episode_output(tmp_path, seq="001", slug="x")

    assert result is not None
    archive_dir = tmp_path / "episodes" / ".archive" / "001-x-1"
    assert result == archive_dir
    assert archive_dir.exists()
    assert (archive_dir / "final.mp4").exists()
    assert not ep_dir.exists()


def test_archive_no_overwrite_increments(tmp_path):
    """Second archive of the same seq-slug gets -2 (no clobber of -1)."""
    from lib.episode import archive_episode_output
    ep_dir = tmp_path / "episodes" / "001-x"

    # First archive
    ep_dir.mkdir(parents=True)
    (ep_dir / "final.mp4").write_bytes(b"\x00" * 10)
    r1 = archive_episode_output(tmp_path, seq="001", slug="x")
    assert r1 == tmp_path / "episodes" / ".archive" / "001-x-1"

    # Rebuild episode dir and archive again
    ep_dir.mkdir(parents=True)
    (ep_dir / "final.mp4").write_bytes(b"\x00" * 20)
    r2 = archive_episode_output(tmp_path, seq="001", slug="x")
    assert r2 == tmp_path / "episodes" / ".archive" / "001-x-2"

    # Both archives must coexist
    assert r1.exists()
    assert r2.exists()


def test_archive_nonexistent_returns_none(tmp_path):
    """Archiving an episode directory that doesn't exist returns None without raising."""
    from lib.episode import archive_episode_output
    result = archive_episode_output(tmp_path, seq="999", slug="missing")
    assert result is None
