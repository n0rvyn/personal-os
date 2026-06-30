#!/usr/bin/env python3
"""Phase-4 E2E: 分镜判官（director）→ shotlist.json + DRY render demo.

Two independent segments:

  (A) LIVE 分镜 — digest-form beats (beat_id/text/anchor only) through the
      director agent step (claude -p --model sonnet) → parse_shotlist →
      apply_video_budget → writes episodes/{seq}-{slug}/shotlist.json and
      STOPS THERE.  Does NOT feed digest beats into render (digest beats
      lack id/video_prompt; schema normalize is out of Phase-4 scope).
      Mirrors the script-only sub-pipeline pattern in evals/video_script_e2e.py.

      Three acceptance states verified:
        A-① (LIVE)    real director (claude -p) → valid shotlist; all
                      visual_type values in {still,video,chart,multi};
                      video count ≤ cfg.budget.video_shots_per_episode (UX-005).
        A-② (INJECTED) fake director returns N>cap video candidates → budget
                      gate downgrades to cap, rest → still, NO halt.
        A-③ (INJECTED) bad JSON from director (missing trailing ]/extra comma /
                      completely broken) → parse_shotlist tolerates or returns
                      clean error string; no crash, no halt.

  (B) DRY render demo — fixture beats (beats.long.fixture.json, with
      id/visual_type/prompts) through director pass-through → Phase-2 render
      with cached .spike/ media, proving the 分镜 step insertion did NOT
      break the render chain.

⚠️  Live-env required:
    - Segment A-①: `claude` CLI on PATH + VIDEO_PDF + VIDEO_PAGES
    - Segment B:   libass ffmpeg (FFMPEG_BIN / FFPROBE_BIN) + .spike/ assets.
    - Without claude CLI and without BUDGET_DEMO=1 → SKIP + non-zero exit
      (no fake green).

Modes:
  (default)      LIVE — runs A-①②③ + segment B; needs claude + PDF + ffmpeg
  BUDGET_DEMO=1  injected only — runs A-②③ without claude; proves budget
                 gate + bad-JSON tolerance; no live dispatch, no render

Env vars:
  VIDEO_PDF, VIDEO_PAGES   — PDF source for live A-①
  E2E_SEQ, E2E_SLUG        — episode id (default 001 / sb-e2e)
  FFMPEG_BIN, FFPROBE_BIN  — libass-enabled ffmpeg/ffprobe (segment B)
  BUDGET_DEMO              — 1 = injected-only mode (no claude needed)
  SPIKE_FIXTURE            — override fixture beats path (segment B)
"""
from __future__ import annotations

import copy
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
from lib.budget import apply_video_budget  # noqa: F401 (verify import works)
from lib.config import load_config
from lib.storyboard import parse_shotlist

# ---------------------------------------------------------------------------
# 分镜-only sub-pipeline: stop at shotlist.json, do NOT proceed to render
# ---------------------------------------------------------------------------
STORYBOARD_STEPS = {"config", "cursor-read", "scratch", "extract", "script", "分镜"}

# Minimal digest-form beats (beat_id/text/anchor only — same schema as
# digest-writer output; intentionally NO id/visual_type/image_prompt).
_DIGEST_BEATS = [
    {"beat_id": "b00",
     "text": "今天聊乳酸阈配速，搞懂这一个概念训练效率能上台阶。",
     "anchor": "乳酸阈 threshold", "page": 1},
    {"beat_id": "b01",
     "text": "乳酸阈是乳酸产生速度刚好超过清除速度的临界点。",
     "anchor": "lactate production clearance threshold", "page": 2},
    {"beat_id": "b02",
     "text": "说人话：还能咬牙撑住，但再快一点就会迅速垮掉的配速。",
     "anchor": "pace tolerable collapse", "page": 2},
    {"beat_id": "b03",
     "text": "用心率确认阈值：180减年龄，或跑步时还能说短句。",
     "anchor": "heart rate age formula talk test", "page": 3},
    {"beat_id": "b04",
     "text": "每周安排一到两次阈值训练，持续八周见效。",
     "anchor": "weekly threshold sessions 8 weeks", "page": 3},
    {"beat_id": "b05",
     "text": "配速跑到阈值区间里保持二十到四十分钟是最有效的方式。",
     "anchor": "threshold pace 20-40 minutes effective", "page": 4},
]


# ---------------------------------------------------------------------------
# Config bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_config() -> Path:
    out_dir = HERE / ".render-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = HERE / ".spike" / "_sb_e2e_config.yaml"
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


def _have_claude() -> bool:
    import shutil
    return shutil.which("claude") is not None


# ---------------------------------------------------------------------------
# Fake dispatch personas for injected states A-② and A-③
# ---------------------------------------------------------------------------

def _over_cap_persona(n_video: int):
    """Fake director that returns n_video video beats; remaining beats are still.

    motion_value decreases with beat index so the first n_video beats have the
    highest scores — budget gate should keep the first `cap` and downgrade the
    rest.
    """
    n_beats = len(_DIGEST_BEATS)

    def _persona(agent, prompt, scratch_dir, expected, **kw):
        data = []
        for i in range(n_beats):
            vt = "video" if i < n_video else "still"
            item: dict = {
                "visual_type": vt,
                "shots": [{"prompt": f"场景{i}", "shot_type": "中景",
                           "with_character": True, "pace": "normal"}],
                "keywords": [],
            }
            if vt == "video":
                item["motion_value"] = float(n_beats - i)  # descending
            data.append(item)
        out_path = Path(scratch_dir) / expected
        out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out_path)}

    return _persona


# ---------------------------------------------------------------------------
# Inject extract: bypass gen_character_ref + PDF parse for non-render tests
# ---------------------------------------------------------------------------

def _make_inject_extract(beats: list[dict], log):
    """Return an extract step override that injects digest beats into ctx.

    Replaces the real extract step to avoid gen_character_ref API calls and
    PDF parsing when we only want to test the director dispatch.  Sets the
    minimum ctx required for script pass-through + 分镜 dispatch to run.
    """
    def inject_extract(step, ctx, scratch_dir):
        ctx["beats"] = copy.deepcopy(beats)
        ctx["ref_url"] = "injected://ref"
        ctx["grade_vf"] = "eq=saturation=1"
        log("[extract] injected digest-form beats (beat_id/text/anchor only)")

    return inject_extract


# ---------------------------------------------------------------------------
# Shared sub-pipeline runner helper
# ---------------------------------------------------------------------------

def _run_storyboard_sub(
    cfg,
    seq: str,
    slug: str,
    log,
    *,
    dispatch_persona_fn=None,
    inject_beats: list[dict] | None = None,
) -> dict:
    """Run the 分镜-only sub-pipeline and return the runner result dict.

    Args:
        dispatch_persona_fn: if set, injected into prepare_render (for fakes);
            if None, defaults to the real dispatch_mod.dispatch_persona.
        inject_beats: if set, replace the extract step with an override that
            injects these beats (no API calls, no PDF parsing).
    """
    kw: dict = {}
    if dispatch_persona_fn is not None:
        kw["dispatch_persona_fn"] = dispatch_persona_fn

    bundle = wiring_mod.prepare_render(
        cfg,
        fixture_path=HERE / "beats.long.fixture.json",
        seq=seq,
        slug=slug,
        dry=False,
        log=log,
        **kw,
    )

    if inject_beats is not None:
        bundle["step_fns"]["extract"] = _make_inject_extract(inject_beats, log)

    full = pipeline_mod.load_pipeline()
    sub = [s for s in full if s["name"] in STORYBOARD_STEPS]
    return runner_mod.run_pipeline(cfg, pipeline=sub, **bundle)


# ---------------------------------------------------------------------------
# Segment A — A-② budget gate demo (injected, no live dispatch)
# ---------------------------------------------------------------------------

def _demo_budget_gate(cfg, seq: str, slug: str, log) -> bool:
    """A-②: inject N>cap video candidates → verify budget gate downgrades to cap.

    The fake director returns `n_video = cap + 2` video beats.  After the budget
    gate inside make_storyboard_dispatch, the shotlist.json must contain ≤ cap
    video entries.  The runner must NOT halt (UX-005).
    """
    cap = cfg.budget.video_shots_per_episode
    n_beats = len(_DIGEST_BEATS)
    n_video = min(cap + 2, n_beats)  # over cap, but can't exceed total beats

    log(f"\n[A-②] Budget gate demo: {n_video} video candidates → cap={cap}")

    result = _run_storyboard_sub(
        cfg,
        seq=seq + "-budget",
        slug=slug + "-budget",
        log=log,
        dispatch_persona_fn=_over_cap_persona(n_video),
        inject_beats=_DIGEST_BEATS,
    )

    if result.get("status") != "done":
        print(
            f"\n❌ A-② FAIL — runner halted (expected 'done'): {result}",
            file=sys.stderr,
        )
        return False

    ep = episode_mod.episode_paths(cfg.output_dir, seq + "-budget", slug + "-budget")
    shotlist_path = ep["shotlist"]
    if not shotlist_path.exists():
        print(f"\n❌ A-② FAIL — shotlist.json missing: {shotlist_path}", file=sys.stderr)
        return False

    shotlist = json.loads(shotlist_path.read_text(encoding="utf-8"))
    n_after = sum(1 for e in shotlist if e.get("visual_type") == "video")
    downgraded = n_video - n_after

    if n_after > cap:
        print(
            f"\n❌ A-② FAIL — {n_after} video beats remain after budget gate "
            f"(expected ≤ {cap})",
            file=sys.stderr,
        )
        return False

    print(
        f"\n✅ A-② PASS — budget gate: {n_video} video → {n_after} video "
        f"({downgraded} downgraded to still, cap={cap}, NO halt)"
    )
    return True


# ---------------------------------------------------------------------------
# Segment A — A-③ bad JSON tolerance demo (direct parse_shotlist calls)
# ---------------------------------------------------------------------------

def _demo_bad_json(log) -> bool:
    """A-③: parse_shotlist must handle bad JSON without raising exceptions.

    Tests three bad-input patterns that match observed LLM failure modes (POC6):
      - missing trailing ]
      - extra trailing comma before ]
      - completely non-JSON input

    Acceptable outcomes: returns a list (tolerant parse) OR returns a str
    (clean error string for retry).  Any raised exception is a FAIL.
    """
    log("\n[A-③] Bad JSON tolerance demo")
    n_beats = len(_DIGEST_BEATS)

    def _valid_item() -> str:
        return (
            '{"visual_type":"still","shots":[{"prompt":"清晨跑道空镜",'
            '"shot_type":"远景","with_character":false,"pace":"normal"}],'
            '"keywords":[]}'
        )

    cases = {
        "missing trailing ]": (
            "[" + ",".join(_valid_item() for _ in range(n_beats))
            # intentionally omit the closing ]
        ),
        "extra trailing comma": (
            "[" + ",".join(_valid_item() for _ in range(n_beats)) + ",]"
        ),
        "completely broken": "not a json array at all",
    }

    ok = True
    for label, raw in cases.items():
        try:
            result = parse_shotlist(raw, n_beats)
        except Exception as exc:
            print(
                f"\n❌ A-③ FAIL [{label}] — parse_shotlist raised {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            ok = False
            continue

        if isinstance(result, list):
            log(f"  [{label}] tolerantly parsed → {len(result)} entries")
        elif isinstance(result, str):
            log(f"  [{label}] clean error string (no crash): {result[:80]!r}")
        else:
            print(
                f"\n❌ A-③ FAIL [{label}] — unexpected return type "
                f"{type(result).__name__!r}",
                file=sys.stderr,
            )
            ok = False

    if ok:
        print("\n✅ A-③ PASS — parse_shotlist handles all bad-JSON cases without crash")
    return ok


# ---------------------------------------------------------------------------
# Segment A — A-① live director (needs claude CLI + VIDEO_PDF/PAGES)
# ---------------------------------------------------------------------------

def _live_storyboard(cfg, seq: str, slug: str, log) -> bool:
    """A-①: real director via claude -p; assert valid shotlist + video≤cap (UX-005)."""
    log(
        f"\n[A-①] LIVE director: {os.environ.get('VIDEO_PDF')} "
        f"pages={os.environ.get('VIDEO_PAGES')}"
    )

    # No inject_beats: let extract run in PDF mode (page_text/fulltext), then
    # script dispatches the real digest-writer, then director runs live.
    result = _run_storyboard_sub(cfg, seq=seq, slug=slug, log=log)
    log(f"[runner] A-① result: {result}")

    if result.get("status") != "done":
        print(
            f"\n❌ A-① FAIL — runner halted at {result.get('failed_step')!r}: "
            f"{result.get('reason')}",
            file=sys.stderr,
        )
        return False

    ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
    shotlist_path = ep["shotlist"]
    if not shotlist_path.exists():
        print(f"\n❌ A-① FAIL — shotlist.json missing: {shotlist_path}", file=sys.stderr)
        return False

    shotlist = json.loads(shotlist_path.read_text(encoding="utf-8"))
    cap = cfg.budget.video_shots_per_episode
    n_video = sum(1 for e in shotlist if e.get("visual_type") == "video")
    vtypes = [e.get("visual_type") for e in shotlist]
    valid_types = {"still", "video", "chart", "multi"}
    all_valid = all(vt in valid_types for vt in vtypes)
    ux005 = n_video <= cap

    print(f"\n=== shotlist.json → {shotlist_path} ===")
    print(f"  {len(shotlist)} beats | visual_types: {vtypes}")
    print(
        f"  UX-005  video count: {n_video} / cap: {cap} "
        f"→ {'PASS' if ux005 else 'FAIL'}"
    )
    print(
        f"  visual_type all sensible (∈ {sorted(valid_types)}): "
        f"{'PASS' if all_valid else 'FAIL'}"
    )
    # Print a sample of shots for manual review
    for i, entry in enumerate(shotlist[:4]):
        shots = entry.get("shots", [])
        kw = entry.get("keywords", [])
        print(
            f"  beat[{i}] {entry.get('visual_type')} "
            f"({len(shots)} shots, keywords={kw})"
        )

    if not (ux005 and all_valid):
        print("\n❌ A-① FAIL — shotlist quality checks failed", file=sys.stderr)
        return False

    print(
        f"\n✅ A-① PASS — live director shotlist: {len(shotlist)} beats, "
        f"video={n_video}≤cap={cap} (UX-005)"
    )
    return True


# ---------------------------------------------------------------------------
# Segment B — DRY render demo (fixture beats → director pass-through → final.mp4)
# ---------------------------------------------------------------------------

def _dry_render(cfg_path: Path, seq: str, slug: str, log) -> bool:
    """B: fixture beats (with visual_type) → director pass-through → DRY render.

    Uses cached .spike/ assets ($0 media spend).  Proves that inserting the
    分镜 step into the pipeline did NOT break the Phase-2 render chain.
    """
    log("\n[B] DRY render demo: fixture beats → director pass-through → final.mp4")
    cfg = load_config(cfg_path)
    fixture_path = Path(
        os.environ.get("SPIKE_FIXTURE", str(HERE / "beats.long.fixture.json"))
    )
    if not fixture_path.is_absolute():
        fixture_path = HERE / fixture_path

    pipeline = pipeline_mod.load_pipeline()
    pipeline_mod.validate_pipeline(pipeline)

    bundle = wiring_mod.prepare_render(
        cfg,
        fixture_path=fixture_path,
        seq=seq,
        slug=slug,
        dry=True,
        log=log,
    )

    # Segment B is the FIXTURE-beats DRY render demo: step_extract must load the
    # fixture (beats carry `id`/`visual_type`/prompts), NOT run PDF mode. When the
    # full live E2E sets VIDEO_PDF/VIDEO_PAGES (for A-①), those env vars leak into
    # this run and force step_extract into PDF mode → digest beats (beat_id, no id)
    # → render KeyError. Isolate segment B from them (restore after).
    _saved_env = {k: os.environ.pop(k) for k in ("VIDEO_PDF", "VIDEO_PAGES")
                  if k in os.environ}
    try:
        result = runner_mod.run_pipeline(cfg, pipeline=pipeline, **bundle)
    finally:
        os.environ.update(_saved_env)
    log(f"[B-render] {result}")

    if result.get("status") != "done":
        print(
            f"\n❌ B FAIL — DRY render halted at {result.get('failed_step')!r}: "
            f"{result.get('reason')}",
            file=sys.stderr,
        )
        return False

    ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
    final = ep["final"]
    if not (final.exists() and final.stat().st_size > 0):
        print(f"\n❌ B FAIL — final.mp4 missing after clean run: {final}", file=sys.stderr)
        return False

    print(
        f"\n✅ B PASS — DRY render: fixture beats → director pass-through → "
        f"{final} ({final.stat().st_size // 1024} KB); 分镜 step did not break render chain"
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    budget_demo = os.environ.get("BUDGET_DEMO") == "1"
    seq = os.environ.get("E2E_SEQ", "001")
    slug = os.environ.get("E2E_SLUG", "sb-e2e")
    pdf = os.environ.get("VIDEO_PDF")
    pages = os.environ.get("VIDEO_PAGES")

    def log(m: str) -> None:
        print(m, file=sys.stderr)

    # --- live-env gate ---
    # Without claude CLI and without BUDGET_DEMO=1, skip to avoid fake green.
    if not budget_demo and not _have_claude():
        print(
            "SKIP: `claude` CLI not on PATH (live director needed for A-①).\n"
            "      Set BUDGET_DEMO=1 to run injected states A-② + A-③ only "
            "(no claude needed).",
            file=sys.stderr,
        )
        return 1

    cfg_path = _bootstrap_config()
    cfg = load_config(cfg_path)

    overall_ok = True

    # --- A-②: budget gate demo (injected, always runs in both modes) ---
    if not _demo_budget_gate(cfg, seq, slug, log):
        overall_ok = False

    # --- A-③: bad JSON tolerance demo (always runs) ---
    if not _demo_bad_json(log):
        overall_ok = False

    # In BUDGET_DEMO mode, stop here (no live dispatch, no render).
    if budget_demo:
        log("\n[mode] BUDGET_DEMO — skipping live A-① and segment B")
        return 0 if overall_ok else 5

    # --- A-①: live director (needs claude + PDF source) ---
    if not (pdf and pages):
        print(
            "\nSKIP A-①: VIDEO_PDF + VIDEO_PAGES not set.\n"
            "           Set them to run the live director and segment B.\n"
            "           (Injected states A-② + A-③ completed above.)",
            file=sys.stderr,
        )
        # Non-zero: the full live pass was not completed.
        return 1

    log(f"[mode] LIVE (claude -p --model sonnet) + DRY render demo")
    log(f"[source] {pdf} pages={pages}")

    if not _live_storyboard(cfg, seq=seq, slug=slug, log=log):
        overall_ok = False

    # --- Segment B: DRY render demo (fixture beats, not digest beats) ---
    if not _dry_render(cfg_path, seq=seq + "-dry", slug=slug + "-dry", log=log):
        overall_ok = False

    print(
        f"\n{'✅ ALL PASS' if overall_ok else '❌ SOME FAILURES'} — "
        f"shotlist.json at episodes/{seq}-{slug}/shotlist.json"
    )
    return 0 if overall_ok else 4


if __name__ == "__main__":
    sys.exit(main())
