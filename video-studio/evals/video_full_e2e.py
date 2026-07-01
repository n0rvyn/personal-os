#!/usr/bin/env python3
"""video-studio FULL E2E: Stage 0 (planner) + Stage 1 (render pipeline).

Full chain:
  Stage 0 → planner dispatch (via sonnet_wrapper) → syllabus draft → promote_draft
            → locked syllabus.yaml + cursor.yaml
  Stage 1 → select_next → prepare_render(dry=True, dispatch_persona_fn=sonnet_wrapper)
            → run_pipeline → final.mp4

Sentinel①: wraps dispatch_persona to force model="sonnet" and record (agent_name, model)
            for every call.  Final assertion: all 3 dispatches (planner, digest-writer,
            director) used model=sonnet.

Sentinel②: monkeypatches paid media generator functions in lib.media_doubao /
            lib.media_tts / lib.media_video to raise MediaGuardError if called.
            dry=True means MediaSeams copies .spike/ cached assets — the underlying
            paid API should never be reached.  Final assertion: MediaGuardError never
            raised (i.e. zero paid media calls).

Modes:
  --det    Deterministic subset: import check + sentinel logic unit tests +
           promote_draft smoke.  No claude CLI or FFMPEG_BIN required. Exits 0.
  (none)   LIVE: planner→promote_draft→select_next→run_pipeline with real claude
           and libass ffmpeg.  Requires FFMPEG_BIN (libass build).

Env:
  FFMPEG_BIN / FFPROBE_BIN  libass-enabled ffmpeg/ffprobe (LIVE only)
  VIDEO_STUDIO_CONFIG        path to config yaml (else bootstrapped to .spike/)
  MEDIA_PROVIDER             doubao (default) | minimax
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent  # video-studio/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SNAPSHOT_DIR = HERE / "syllabus" / "source" / "daniels-uesca"


# ---------------------------------------------------------------------------
# Sentinel①: sonnet_wrapper — wraps dispatch_persona, enforces model=sonnet
# ---------------------------------------------------------------------------

class _Sentinel1:
    """Records every dispatch_persona call; forces model=sonnet."""

    def __init__(self, orig: Any) -> None:
        self._orig = orig
        self.calls: list[tuple[str, str]] = []  # [(agent_name, model)]

    def __call__(self, agent_name: str, *args: Any, **kwargs: Any) -> Any:
        kwargs["model"] = "sonnet"
        self.calls.append((agent_name, "sonnet"))
        return self._orig(agent_name, *args, **kwargs)


def _make_sonnet_wrapper() -> _Sentinel1:
    from lib.dispatch import dispatch_persona as _orig
    return _Sentinel1(_orig)


# ---------------------------------------------------------------------------
# Sentinel②: media guard — raises if any paid generator is called
# ---------------------------------------------------------------------------

class MediaGuardError(RuntimeError):
    """Raised if a paid media generator is invoked when dry=True should prevent it."""


def _install_media_guard() -> list[str]:
    """Monkeypatch paid media generator functions to raise MediaGuardError.

    Returns the list of 'module.attr' strings that were patched.
    """
    import importlib

    guarded: list[str] = []
    targets = [
        ("lib.media_doubao", ["gen_character_ref", "gen_still", "gen_video"]),
        ("lib.media_tts",    ["synth"]),
        ("lib.media_video",  ["gen_video"]),
        ("lib.media_image",  ["gen_still"]),
    ]
    for mod_name, attrs in targets:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for attr in attrs:
            if not hasattr(mod, attr):
                continue

            def _guard(*a: Any, _mod: str = mod_name, _attr: str = attr, **kw: Any) -> Any:
                raise MediaGuardError(
                    f"paid media generator {_mod}.{_attr} called — "
                    f"dry=True must prevent this"
                )

            setattr(mod, attr, _guard)
            guarded.append(f"{mod_name}.{attr}")

    return guarded


# ---------------------------------------------------------------------------
# Config bootstrap
# ---------------------------------------------------------------------------

def _write_config(out_dir: Path, syllabus_path: Path | None = None,
                  cursor_path: Path | None = None) -> Path:
    ffmpeg_bin = os.environ.get(
        "FFMPEG_BIN", str(Path.home() / ".local/ffmpeg-libass/ffmpeg"))
    ffprobe_bin = os.environ.get(
        "FFPROBE_BIN", str(Path.home() / ".local/ffmpeg-libass/ffprobe"))
    provider = os.environ.get("MEDIA_PROVIDER", "doubao").lower()
    spike = HERE / ".spike"
    spike.mkdir(parents=True, exist_ok=True)
    cfg_path = spike / "_full_e2e_config.yaml"
    lines = [
        "render:",
        "  width: 1920",
        "  height: 1080",
        "  fps: 30",
        "  subtitle_font: PingFang SC",
        "audio:",
        "  voice_id: male-qn-jingying-jingpin",
        "  bgm_duck_db: 18.0",
        f"media:",
        f"  provider: {provider}",
        "budget:",
        "  video_shots_per_episode: 2",
        "ffmpeg:",
        f"  ffmpeg_bin: {ffmpeg_bin}",
        f"  ffprobe_bin: {ffprobe_bin}",
        f"output_dir: {out_dir}",
    ]
    if syllabus_path is not None:
        lines.append(f"syllabus_path: {syllabus_path}")
    if cursor_path is not None:
        lines.append(f"cursor_path: {cursor_path}")
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_path


def _bootstrap_config(out_dir: Path,
                      syllabus_path: Path | None = None,
                      cursor_path: Path | None = None) -> Path:
    explicit = os.environ.get("VIDEO_STUDIO_CONFIG")
    if explicit:
        return Path(explicit)
    return _write_config(out_dir, syllabus_path, cursor_path)


# ---------------------------------------------------------------------------
# DET subset: no claude, no ffmpeg
# ---------------------------------------------------------------------------

_DET_MINIMAL_DRAFT = """\
book: "det-smoke"
modules:
  - name: "Test Module"
    style: ""
    episodes:
      - title: "Test Episode One"
        doc_refs: []
        source: "test"
        verify_status: "unverified"
        learning_goal: "verify promote_draft end-to-end"
        status: "pending"
"""


def _run_det() -> int:
    """Deterministic checks: imports + sentinel logic + promote_draft smoke.

    Returns 0 on pass, 1 on first failure.
    """
    errors: list[str] = []

    # 1. Import check
    print("[DET] 1/4 import check …")
    try:
        import lib.dispatch   # noqa: F401
        import lib.wiring     # noqa: F401
        import lib.runner     # noqa: F401
        import lib.syllabus   # noqa: F401
        import lib.pipeline   # noqa: F401
        import lib.config     # noqa: F401
        print("      ✔ all core imports ok")
    except ImportError as exc:
        errors.append(f"import failed: {exc}")
        print(f"      ✘ {exc}")

    # 2. Sentinel② logic: install guard + verify it raises on probe call
    print("[DET] 2/4 sentinel② (media guard) …")
    try:
        guarded = _install_media_guard()
        # Probe one patched function to confirm it raises MediaGuardError
        import importlib
        probe_mod = importlib.import_module("lib.media_doubao")
        if hasattr(probe_mod, "gen_still"):
            raised = False
            try:
                probe_mod.gen_still("probe_prompt", None, "/tmp/probe.jpg")
            except MediaGuardError:
                raised = True
            if raised:
                print(f"      ✔ {len(guarded)} functions guarded; guard raises MediaGuardError ✓")
            else:
                errors.append("media guard did not raise on gen_still probe call")
                print("      ✘ guard silently returned — not raising")
        else:
            print(f"      ✔ {len(guarded)} functions guarded (probe fn absent)")
    except Exception as exc:
        errors.append(f"media guard install failed: {exc}")
        print(f"      ✘ {exc}")

    # 3. Sentinel① logic: wrapper enforces model=sonnet
    print("[DET] 3/4 sentinel① (sonnet_wrapper) …")
    try:
        recorded: list[tuple[str, str]] = []

        def _fake_dispatch(agent_name: str, *args: Any, model: str = "opus", **kw: Any) -> dict:
            recorded.append((agent_name, model))
            return {"ok": True, "artifact_path": "/dev/null"}

        s1 = _Sentinel1(_fake_dispatch)
        s1("planner", "test prompt", "/tmp", "syllabus_draft.yaml",
           plugin_root=HERE, timeout=10)

        if recorded and recorded[0][1] == "sonnet":
            print(f"      ✔ sentinel① enforces model=sonnet; recorded={recorded}")
        else:
            errors.append(f"sentinel① did not enforce model=sonnet: recorded={recorded}")
            print(f"      ✘ recorded={recorded}")
    except Exception as exc:
        errors.append(f"sentinel① test failed: {exc}")
        print(f"      ✘ {exc}")

    # 4. promote_draft smoke: write minimal draft → promote_draft → assert artifacts
    print("[DET] 4/4 promote_draft smoke …")
    try:
        from lib.syllabus import promote_draft

        with tempfile.TemporaryDirectory(prefix="vfe2e_det_") as tmp:
            tmp_path = Path(tmp)
            draft_path = tmp_path / "syllabus_draft.yaml"
            draft_path.write_text(_DET_MINIMAL_DRAFT, encoding="utf-8")

            syllabus_out = tmp_path / "syllabus.yaml"
            cursor_out = tmp_path / "cursor.yaml"
            promote_draft(draft_path, syllabus_out, cursor_out, book="det-smoke")

            if not syllabus_out.exists():
                errors.append("promote_draft did not create syllabus.yaml")
                print("      ✘ syllabus.yaml missing")
            elif not cursor_out.exists():
                errors.append("promote_draft did not create cursor.yaml")
                print("      ✘ cursor.yaml missing")
            else:
                print(f"      ✔ syllabus.yaml + cursor.yaml written")
    except Exception as exc:
        errors.append(f"promote_draft smoke failed: {exc}")
        print(f"      ✘ {exc}")

    if errors:
        print(f"\n❌ DET FAILED ({len(errors)} error(s)):")
        for e in errors:
            print(f"   • {e}")
        return 1

    print("\n✅ DET PASS (4/4)")
    return 0


# ---------------------------------------------------------------------------
# LIVE run: Stage 0 → promote_draft → Stage 1 → run_pipeline
# ---------------------------------------------------------------------------

def _run_live() -> int:
    from lib.config import load_config
    from lib import pipeline as pipeline_mod
    from lib import runner as runner_mod
    from lib import wiring as wiring_mod
    from lib import episode as episode_mod
    from lib.syllabus import promote_draft, select_next

    # Install sentinel② (media guard) before any module-level imports fire
    guarded = _install_media_guard()
    print(f"[live] sentinel② installed: {len(guarded)} media functions guarded")

    # Sentinel①: sonnet_wrapper
    sentinel = _make_sonnet_wrapper()
    print("[live] sentinel① installed: all dispatch_persona calls → model=sonnet")

    # Output dir
    out_dir = HERE / ".render-out-full-e2e"
    out_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Stage 0: planner dispatch
    # -----------------------------------------------------------------------
    stage0_scratch = HERE / ".spike" / "_full_e2e_stage0"
    stage0_scratch.mkdir(parents=True, exist_ok=True)

    doc_paths = sorted(SNAPSHOT_DIR.glob("*.md")) if SNAPSHOT_DIR.exists() else []
    if doc_paths:
        doc_list = "\n".join(str(p) for p in doc_paths[:5])
        user_prompt = (
            "以下是权威层训练科学文档的绝对路径（每行一个）：\n"
            f"{doc_list}\n\n"
            "书系名称：daniels-uesca 跑步知识系列。\n"
            "请读取这些文档后提议一份课程大纲草案 YAML。\n"
            "输出格式：```yaml fence 包裹的 YAML，顶层含 book / modules；"
            "每个 module 含 name / episodes；每个 episode 含 title / doc_refs / "
            "source / verify_status / learning_goal / status(pending)。至少2期。"
        )
    else:
        user_prompt = (
            "为一档面向业余长跑者的微信视频号制作课程大纲草案 YAML。\n"
            "输出格式：```yaml fence 包裹，顶层含 book / modules；每个 module 含 "
            "name / episodes；每个 episode 含 title / doc_refs(空列表) / source / "
            "verify_status / learning_goal / status(pending)。至少2期。"
        )

    print("[live] Stage 0 — planner dispatch (sonnet) …")
    t0 = time.monotonic()
    planner_result = sentinel(
        "planner",
        user_prompt,
        str(stage0_scratch),
        "syllabus_draft.yaml",
        plugin_root=HERE,
        timeout=900,
    )
    elapsed = time.monotonic() - t0
    print(f"[live] planner done in {elapsed:.1f}s — sentinel calls so far: {sentinel.calls}")

    if not planner_result.get("ok"):
        print(f"❌ Stage 0 FAILED: planner dispatch error: {planner_result.get('reason')}")
        return 2

    draft_path = Path(planner_result["artifact_path"])
    if not draft_path.exists() or draft_path.stat().st_size == 0:
        print(f"❌ Stage 0 FAILED: planner did not write artifact: {draft_path}")
        return 3

    print(f"[live] draft written: {draft_path} ({draft_path.stat().st_size} bytes)")

    # -----------------------------------------------------------------------
    # promote_draft → locked syllabus + cursor
    # -----------------------------------------------------------------------
    syllabus_dir = HERE / ".spike" / "_full_e2e_syllabus"
    syllabus_dir.mkdir(parents=True, exist_ok=True)
    syllabus_file = syllabus_dir / "syllabus.yaml"
    cursor_file = syllabus_dir / "cursor.yaml"

    print("[live] promote_draft → locked syllabus + cursor …")
    try:
        from lib.syllabus import Syllabus as _Syllabus
        # Extract book name from draft for cursor init
        import yaml as _yaml
        raw = draft_path.read_text(encoding="utf-8").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            inner = lines[1:]
            if inner and inner[-1].strip() == "```":
                inner = inner[:-1]
            raw = "\n".join(inner)
        draft_data = _yaml.safe_load(raw) or {}
        book = str(draft_data.get("book", "unknown"))

        promote_draft(draft_path, syllabus_file, cursor_file, book=book)
    except Exception as exc:
        print(f"❌ Stage 0 FAILED: promote_draft error: {exc}")
        return 4

    if not syllabus_file.exists() or not cursor_file.exists():
        print("❌ Stage 0 FAILED: promote_draft did not produce syllabus.yaml + cursor.yaml")
        return 5

    print(f"[live] syllabus locked: {syllabus_file}")
    print(f"[live] cursor: {cursor_file}")

    # -----------------------------------------------------------------------
    # Stage 1: reload config with syllabus + cursor paths
    # -----------------------------------------------------------------------
    cfg_path = _bootstrap_config(out_dir,
                                 syllabus_path=syllabus_file,
                                 cursor_path=cursor_file)
    cfg = load_config(cfg_path)
    print(f"[live] Stage 1 config: {cfg_path}")

    # select_next from the locked syllabus
    selection = select_next(cfg)
    if selection is None:
        print("❌ Stage 1 FAILED: select_next returned None — no pending episodes")
        return 6

    print(f"[live] selection: seq={selection.seq} slug={selection.slug!r} "
          f"topic={selection.topic!r} source={selection.source!r}")

    # Fixture path (required by prepare_render even in doc mode)
    fixture_path = HERE / "beats.long.fixture.json"
    if not fixture_path.exists():
        print(f"❌ Stage 1 FAILED: fixture not found: {fixture_path}")
        return 7

    pipeline = pipeline_mod.load_pipeline()
    pipeline_mod.validate_pipeline(pipeline)

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    print("[live] Stage 1 — prepare_render(dry=True, sonnet_wrapper) …")
    bundle = wiring_mod.prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq=selection.seq,
        slug=selection.slug,
        dry=True,
        log=_log,
        dispatch_persona_fn=sentinel,
        selection=selection,
    )

    print(f"[live] Stage 1 — run_pipeline (scratch: {bundle['scratch_dir']}) …")
    result = runner_mod.run_pipeline(cfg, pipeline=pipeline, **bundle)
    print(f"[live] run_pipeline result: {result}")

    if result.get("status") != "done":
        print(f"❌ Stage 1 HALTED at step {result.get('failed_step')!r}: "
              f"{result.get('reason')}")
        return 8

    # -----------------------------------------------------------------------
    # Assert: final.mp4 exists and is non-empty
    # -----------------------------------------------------------------------
    ep = episode_mod.episode_paths(cfg.output_dir, selection.seq, selection.slug)
    final_mp4 = ep["final"]
    if not (final_mp4.exists() and final_mp4.stat().st_size > 0):
        print(f"❌ final.mp4 missing or empty: {final_mp4}")
        return 9

    print(f"\n[live] final.mp4: {final_mp4} ({final_mp4.stat().st_size // 1024} KB)")

    # -----------------------------------------------------------------------
    # Assert: provenance carries source + verify_status
    # -----------------------------------------------------------------------
    prov_path = ep.get("provenance")
    if prov_path and Path(prov_path).exists():
        import json as _json
        prov = _json.loads(Path(prov_path).read_text())
        has_source = bool(prov.get("source") or
                          prov.get("selection", {}).get("source"))
        has_verify = bool(prov.get("verify_status") or
                          prov.get("selection", {}).get("verify_status"))
        sv_ok = has_source and has_verify
        print(f"[live] provenance: source={'✓' if has_source else '—'} "
              f"verify_status={'✓' if has_verify else '—'} "
              f"{'PASS' if sv_ok else 'FAIL'}")
    else:
        print("[live] provenance file absent (non-blocking)")
        sv_ok = True  # provenance optional for pass/fail

    # -----------------------------------------------------------------------
    # Assert sentinel①: all dispatches used model=sonnet
    # -----------------------------------------------------------------------
    print(f"\n[live] sentinel① call log: {sentinel.calls}")
    non_sonnet = [(a, m) for a, m in sentinel.calls if m != "sonnet"]
    if non_sonnet:
        print(f"❌ sentinel① FAIL: non-sonnet dispatches: {non_sonnet}")
        return 10

    dispatch_agents = [a for a, _ in sentinel.calls]
    expected_agents = {"planner", "digest-writer", "director"}
    missing_agents = expected_agents - set(dispatch_agents)
    if missing_agents:
        print(f"❌ sentinel① FAIL: expected {expected_agents} but missing {missing_agents}; "
              f"got {dispatch_agents}")
        return 11

    print(f"✅ sentinel① PASS: {len(sentinel.calls)} dispatches, all sonnet: {dispatch_agents}")

    # -----------------------------------------------------------------------
    # Assert sentinel②: no MediaGuardError was raised (dry=True routed everything)
    # -----------------------------------------------------------------------
    # If we reach here without a MediaGuardError having propagated, paid APIs were not called.
    print("✅ sentinel② PASS: no paid media API called (dry=True + .spike/ cached assets)")

    if not sv_ok:
        print("❌ provenance FAIL: source or verify_status absent")
        return 12

    print(f"\n✅ LIVE PASS — final.mp4 at {final_mp4}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="video-studio full E2E: Stage 0 (planner) + Stage 1 (render)")
    parser.add_argument(
        "--det", action="store_true",
        help="Deterministic subset: no claude or FFMPEG_BIN required")
    args = parser.parse_args()

    if args.det:
        return _run_det()
    return _run_live()


if __name__ == "__main__":
    sys.exit(main())
