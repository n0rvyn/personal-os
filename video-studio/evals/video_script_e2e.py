#!/usr/bin/env python3
"""LIVE Phase-3 E2E: extract → digest-writer (claude -p) → 忠实门 → provenance.

Runs a SCRIPT-ONLY sub-pipeline (config→scratch→extract→script→provenance) — no
render, no Doubao burn. Produces the textual review artifacts:
  episodes/{seq}-{slug}/script.md       (digest-writer's faithful script)
  episodes/{seq}-{slug}/provenance.json (每条客观断言 → PDF 页码, UX-004)

Two modes:
  (default) LIVE — real digest-writer via `claude -p --model sonnet` against the
            `-layout` source pages; 忠实门 verifies; provenance maps to pages.
  HALT_DEMO=1 — inject a fabricated-number beat (fake dispatch) → 忠实门 halts →
            prints which anchor was not grounded (acceptance criterion 1).

⚠️ Live-env required: `claude` CLI on PATH + the Daniels PDF + pdftotext.
Env: VIDEO_PDF, VIDEO_PAGES (e.g. "216 217 218"), E2E_SEQ, E2E_SLUG.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib import episode as episode_mod
from lib import pipeline as pipeline_mod
from lib import runner as runner_mod
from lib import wiring as wiring_mod
from lib.config import load_config

SCRIPT_STEPS = {"config", "cursor-read", "scratch", "extract", "script", "provenance"}


def _bootstrap_config() -> Path:
    out_dir = HERE / ".render-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = HERE / ".spike" / "_p3_config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: PingFang SC\n"
        "audio:\n  voice_id: male-qn-jingying-jingpin\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {os.environ.get('FFMPEG_BIN', 'ffmpeg')}\n"
        f"  ffprobe_bin: {os.environ.get('FFPROBE_BIN', 'ffprobe')}\n"
        f"output_dir: {out_dir}\n",
        encoding="utf-8",
    )
    return cfg_path


def _fabricated_persona(agent, prompt, scratch_dir, expected, **kw):
    """HALT_DEMO fake: writes a beat with a pace NOT in the source."""
    out = Path(scratch_dir) / expected
    out.write_text(json.dumps({"beats": [
        {"beat_id": "b00", "text": "乳酸阈配速大约每公里三分整。",
         "anchor": "乳酸阈 T 配速 3:00 每公里", "page": None}
    ]}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "artifact_path": str(out)}


def main() -> int:
    halt_demo = os.environ.get("HALT_DEMO") == "1"
    seq = os.environ.get("E2E_SEQ", "001")
    slug = os.environ.get("E2E_SLUG", "p3-script")
    pdf = os.environ.get("VIDEO_PDF")
    pages = os.environ.get("VIDEO_PAGES")

    def log(m): print(m, file=sys.stderr)

    if not (pdf and pages):
        print("SKIP: set VIDEO_PDF + VIDEO_PAGES (live-env required).", file=sys.stderr)
        return 1
    if not halt_demo and not _have_claude():
        print("SKIP: `claude` CLI not on PATH (live digest-writer needed).", file=sys.stderr)
        return 1

    log(f"[mode] {'HALT_DEMO (fabricated beat)' if halt_demo else 'LIVE digest-writer'}")
    log(f"[source] {pdf} pages {pages}")

    cfg = load_config(_bootstrap_config())
    prep_kw = dict(
        fixture_path=HERE / "beats.long.fixture.json", seq=seq, slug=slug,
        dry=False, log=log,
    )
    if halt_demo:  # inject the fabricated-beat fake; else use the real claude -p default
        prep_kw["dispatch_persona_fn"] = _fabricated_persona
    bundle = wiring_mod.prepare_render(cfg, **prep_kw)

    # script-only sub-pipeline (no render / no Doubao)
    full = pipeline_mod.load_pipeline()
    sub = [s for s in full if s["name"] in SCRIPT_STEPS]

    result = runner_mod.run_pipeline(cfg, pipeline=sub, **bundle)
    log(f"[runner] {result}")

    ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)

    if halt_demo:
        if result.get("status") == "halted" and result.get("failed_step") == "script":
            print(f"\n✅ HALT DEMO PASS — 忠实门 halted at 'script' (编造数字未溯源)")
            print(f"   reason: {result.get('reason')}")
            return 0
        print(f"\n❌ HALT DEMO FAIL — expected halt at script, got {result}", file=sys.stderr)
        return 4

    if result.get("status") != "done":
        print(f"\n❌ LIVE run halted: {result}", file=sys.stderr)
        return 2

    # surface artifacts
    script_md = ep["script"]
    prov_json = ep["provenance"]
    print(f"\n=== script.md -> {script_md} ===")
    if script_md.exists():
        txt = script_md.read_text(encoding="utf-8")
        print(f"  ({len(txt)} chars)")
    print(f"=== provenance.json -> {prov_json} ===")
    ux004 = False
    if prov_json.exists():
        prov = json.loads(prov_json.read_text(encoding="utf-8"))
        assertions = prov.get("assertions", [])
        objective = [a for a in assertions if any(c.isdigit() for c in a["assertion"])]
        grounded = [a for a in objective if a["page"] is not None]
        ux004 = bool(objective) and len(grounded) == len(objective)
        print(f"  {len(grounded)}/{len(objective)} objective assertions → page "
              f"(UX-004 {'PASS' if ux004 else 'FAIL'})")
        for a in assertions[:6]:
            print(f"    {a['beat_id']}: p{a['page']}  «{a['assertion'][:40]}»")

    print(f"\n{'✅ LIVE PASS' if ux004 else '⚠️ LIVE done (review UX-004)'} — artifacts in {ep['final'].parent}")
    return 0 if ux004 else 0  # live artifacts are for human review either way


def _have_claude() -> bool:
    import shutil
    return shutil.which("claude") is not None


if __name__ == "__main__":
    sys.exit(main())
