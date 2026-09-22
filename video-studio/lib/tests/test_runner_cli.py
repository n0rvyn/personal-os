"""Smoke tests for the `python -m lib.runner` CLI entry (Phase-2 Task 5).

Exercises the entry wiring (config → load_pipeline → prepare → run_pipeline →
print product paths) WITHOUT real media/ffmpeg, by injecting a fake prepare_fn
whose step_fns just satisfy each gate with a sentinel file. Also pins the
fail-closed behaviour (missing/bad config) and --help.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


@pytest.fixture(autouse=True)
def _bypass_preflight(monkeypatch):
    """This file tests runner wiring, not preflight.  Bypass the libass check so
    tests with fake ffmpeg bins (/bin/true) are not blocked by preflight."""
    monkeypatch.setattr("lib.preflight.check_ffmpeg_libass", lambda *a, **kw: None)


def _write_config(tmp_path: Path) -> Path:
    """Minimal valid config; output_dir = tmp_path (exists)."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: X\n"
        "audio:\n  voice_id: v\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        "ffmpeg:\n  ffmpeg_bin: /bin/true\n  ffprobe_bin: /bin/true\n"
        f"output_dir: {tmp_path}\n",
        encoding="utf-8",
    )
    return cfg


def _fake_prepare(config, *, fixture_path, seq, slug, dry, log, fresh=False,
                  episode_target=False, script_only=False, selection=None):
    """Bundle whose step_fns satisfy each gate with a sentinel file — no media,
    no ffmpeg. Mirrors run_pipeline's contract (ctx[artifact] = non-empty file)."""
    from lib.pipeline import load_pipeline
    from lib.episode import check_artifact, make_scratch

    def make_fn(_name):
        def fn(step, ctx, scratch_dir):
            art = step.get("artifact")
            if art:
                p = Path(scratch_dir) / f"{art}.txt"
                p.write_text("x", encoding="utf-8")
                ctx[art] = str(p)
        return fn

    step_fns = {s["name"]: make_fn(s["name"]) for s in load_pipeline()}

    def dispatch(agent_name, step, ctx, scratch_dir):
        # agent steps (e.g. `script`) route here — satisfy the gate like a fake.
        art = step.get("artifact")
        if art:
            p = Path(scratch_dir) / f"{art}.txt"
            p.write_text("x", encoding="utf-8")
            ctx[art] = str(p)
        return {"ok": True}

    return {
        "step_fns": step_fns,
        "gates": {"check_artifact": check_artifact},
        "dispatch": dispatch,
        "scratch_dir": make_scratch(config.output_dir, tag=f"{seq}-{slug}"),
    }


def test_cli_help_exits_zero():
    from lib.runner import _cli
    with pytest.raises(SystemExit) as exc:
        _cli(["--help"])
    assert exc.value.code == 0


def test_cli_missing_config_arg_does_not_raise_systemexit2():
    """--config is now optional; omitting it triggers config discovery.
    When no config is found (ConfigNotFound), runner returns 1 (not argparse
    SystemExit(2)).  This test was updated from the old 'required' behaviour
    by Task 2-impl (Phase 3 config-discovery refactor).
    """
    from lib.runner import _cli
    # Without any config files on disk, discovery raises ConfigNotFound → rc=1.
    # Must NOT raise SystemExit(2) (argparse "required argument missing").
    try:
        rc = _cli([])
    except SystemExit as e:
        if e.code == 2:
            raise AssertionError(
                "--config must be optional (required=False); "
                "argparse exited with code 2 (missing required argument)"
            ) from e
        # Any other exit is acceptable (e.g. SystemExit(0) from --help)
    else:
        assert rc == 1, (
            f"Without a config, _cli([]) must return 1 (ConfigNotFound), got {rc}"
        )


def test_cli_bad_config_path_fails_closed(tmp_path):
    """A nonexistent config returns 1 (fail-closed), not a crash."""
    from lib.runner import _cli
    rc = _cli(["--config", str(tmp_path / "nope.yaml")])
    assert rc == 1


def test_cli_missing_fixture_fails_closed(tmp_path):
    from lib.runner import _cli
    cfg = _write_config(tmp_path)
    rc = _cli(["--config", str(cfg), "--fixture", str(tmp_path / "nope.json")])
    assert rc == 1


def test_cli_entry_runs_with_injected_fakes(tmp_path, capsys):
    """End-to-end entry wiring with fake prepare: config → pipeline →
    run_pipeline → prints product paths, returns 0. No media, no ffmpeg."""
    from lib.runner import _cli
    cfg = _write_config(tmp_path)
    fixture = tmp_path / "beats.json"
    fixture.write_text('{"beats": []}', encoding="utf-8")

    rc = _cli(
        ["--config", str(cfg), "--fixture", str(fixture), "--seq", "001", "--slug", "smoke"],
        prepare_fn=_fake_prepare,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "done — episode 001-smoke" in out
    assert "final:" in out


def test_cli_halt_returns_two(tmp_path):
    """If run_pipeline reports a halt, the CLI returns 2."""
    from lib.runner import _cli
    cfg = _write_config(tmp_path)
    fixture = tmp_path / "beats.json"
    fixture.write_text('{"beats": []}', encoding="utf-8")

    def fake_run(config, *, pipeline, step_fns, dispatch, gates, scratch_dir):
        return {"status": "halted", "failed_step": "tts", "reason": "boom"}

    rc = _cli(
        ["--config", str(cfg), "--fixture", str(fixture)],
        prepare_fn=_fake_prepare, run_pipeline_fn=fake_run,
    )
    assert rc == 2
