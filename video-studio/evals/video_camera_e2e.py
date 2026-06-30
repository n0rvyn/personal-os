#!/usr/bin/env python3
"""Phase-5 E2E: camera-move variety + DP-003=A quick=静止 safeguard.

Two independent segments:

  (C) DETERMINISTIC camera-move variety assertion (no claude needed):
      Construct 5 test shots — one per CAMERA_MOVES entry — run each through
      step_render_shots_clip using the cached .spike/b00_shot0.jpg still (DRY
      render, $0 media spend), capture every ffmpeg argv, parse zoompan=
      expressions, and assert:
        C-① ≥3 DISTINCT zoompan expressions appear across the beats.
        C-② per-beat clip drift < 0.1s  (|actual_dur - clip_len| < 0.1).

      AR-1 framing: every beat is rendered from THE SAME cached still
      (b00_shot0.jpg).  This is intentional — the render proves MOTION
      variety and restraint, NOT content matching (content matching requires
      distinct images and is deferred to N-2 with live media budget).

  (D) LIVE DP-003=A quick=静止 safeguard (needs claude CLI):
      Real director (claude -p --model sonnet) for the Daniels PDF page
      produces a shotlist; parse it and assert all pace=quick shots carry
      camera_move="静止".  Gate behind live-env — without claude CLI,
      SKIP and exit non-zero (no fake green).

⚠️  AR-1 note (logged at runtime):
    DRY render shows camera moves on a single cached image (b00_shot0.jpg
    reused for all beats).  That is intentional for judging MOTION variety
    and restraint, NOT content-matching (which needs distinct images,
    deferred N-2).

Env:
  FFMPEG_BIN / FFPROBE_BIN  — libass-enabled ffmpeg/ffprobe (required for C).
                               Fallback: ~/.local/ffmpeg-libass/ffmpeg|ffprobe
  VIDEO_PDF, VIDEO_PAGES    — PDF source for live segment D.
  E2E_SEQ, E2E_SLUG         — episode id (default 001 / cam-e2e).
  CAMERA_DEMO               — 1 = run segment C only (no claude needed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # video-studio/
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SPIKE = HERE / ".spike"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _have_claude() -> bool:
    return shutil.which("claude") is not None


def _ffmpeg_bin() -> str:
    return os.environ.get(
        "FFMPEG_BIN",
        str(Path.home() / ".local/ffmpeg-libass/ffmpeg"),
    )


def _ffprobe_bin() -> str:
    return os.environ.get(
        "FFPROBE_BIN",
        str(Path.home() / ".local/ffmpeg-libass/ffprobe"),
    )


def _probe_dur_s(ffprobe: str, path: str) -> float:
    """Return duration in seconds via ffprobe."""
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, timeout=30,
    )
    return float(out.stdout.strip())


def _run_ff_real(ffmpeg: str, argv: list[str], label: str) -> None:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + argv
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg [{label}] exit={proc.returncode}  "
            f"CMD: {' '.join(cmd[:6])}…\nSTDERR: {proc.stderr.strip()[:400]}"
        )


def _extract_zoompan_expr(argv: list[str]) -> str | None:
    """Return the zoompan= expression from a -vf arg, or None."""
    for arg in argv:
        m = re.search(r"zoompan=(z=[^:]+(?::[^,]+)*)", arg)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Segment C — deterministic camera-move variety assertion
# ---------------------------------------------------------------------------

# AR-1: intentionally reuse one cached still for ALL beats — the render proves
# MOTION variety and restraint, not content matching (deferred to N-2).
_AR1_STILL = SPIKE / "b00_shot0.jpg"

# 5 test shots — one per CAMERA_MOVES entry
_CAMERA_TEST_SHOTS = [
    {"camera_move": "推近", "shot_type": "中景",  "with_character": False,
     "prompt": "跑步教练站在清晨跑道上", "pace": "normal"},
    {"camera_move": "拉远", "shot_type": "全景",  "with_character": False,
     "prompt": "城市晨景俯视全景", "pace": "hold"},
    {"camera_move": "横移", "shot_type": "远景",  "with_character": False,
     "prompt": "沿着跑道横向展开的空镜", "pace": "normal"},
    {"camera_move": "竖摇", "shot_type": "特写",  "with_character": False,
     "prompt": "跑鞋与地面接触的脚步特写", "pace": "normal"},
    {"camera_move": "静止", "shot_type": "中景",  "with_character": False,
     "prompt": "金句留白背景静止镜头", "pace": "quick"},
]

_CLIP_LEN = 3.0  # narration+fade test value (seconds)


def _segment_c(log) -> bool:
    """Deterministic camera-move variety assertion (no claude needed).

    Steps:
      1. For each of the 5 CAMERA_MOVES entries, call step_render_shots_clip
         with a fake gen_still (returns cached b00_shot0.jpg) and a real run_ff
         wrapped to also record argv.
      2. Parse zoompan expressions from captured argv.
      3. Assert ≥3 DISTINCT expressions.
      4. Assert per-beat drift < 0.1s.
    """
    from lib.steps import step_render_shots_clip

    ffmpeg = _ffmpeg_bin()
    ffprobe = _ffprobe_bin()

    if not Path(ffmpeg).exists():
        print(
            f"SKIP C: FFMPEG_BIN not found: {ffmpeg}\n"
            "       Set FFMPEG_BIN to the libass-enabled ffmpeg binary.",
            file=sys.stderr,
        )
        return False

    if not _AR1_STILL.exists():
        print(
            f"SKIP C: cached still not found: {_AR1_STILL}\n"
            "       Run the DRY render E2E once to populate .spike/ assets.",
            file=sys.stderr,
        )
        return False

    log("\n[C] Deterministic camera-move variety assertion")
    log(
        "[C] AR-1: DRY render uses b00_shot0.jpg for ALL beats — intentional: "
        "judge MOTION variety + restraint, NOT content matching (deferred N-2)."
    )

    captured: list[tuple[str, list[str]]] = []  # (label, argv)

    def fake_gen_still(prompt: str, ref_url: str | None, out_path: str) -> str:
        # AR-1: reuse single cached still; content is irrelevant for motion test.
        dst = os.path.splitext(out_path)[0] + ".jpg"
        shutil.copy(str(_AR1_STILL), dst)
        return dst

    def capture_and_run(argv: list[str], label: str) -> None:
        captured.append((label, list(argv)))
        _run_ff_real(ffmpeg, argv, label)

    ok = True
    drift_results: list[tuple[str, float]] = []

    with tempfile.TemporaryDirectory(prefix="cam_e2e_") as tmp:
        scratch = Path(tmp)

        for i, shot in enumerate(_CAMERA_TEST_SHOTS):
            move = shot["camera_move"]
            bid = f"cam_{i}_{move}"
            out_clip = str(scratch / f"{bid}.mp4")

            log(f"  [C] beat {i}: camera_move={move}")
            step_render_shots_clip(
                shots=[shot],
                clip_len=_CLIP_LEN,
                out_clip=out_clip,
                bid=bid,
                scratch=str(scratch),
                ref_url=None,
                grade_vf=None,
                gen_still_fn=fake_gen_still,
                run_ff_fn=capture_and_run,
            )

            # C-②: drift check
            actual_dur = _probe_dur_s(ffprobe, out_clip)
            drift = abs(actual_dur - _CLIP_LEN)
            drift_results.append((move, drift))
            drift_ok = drift < 0.1
            log(f"    clip_len={_CLIP_LEN}s  actual={actual_dur:.3f}s  "
                f"drift={drift:.3f}s  {'OK' if drift_ok else 'FAIL'}")
            if not drift_ok:
                print(
                    f"\n❌ C-② FAIL  beat {i} ({move}): "
                    f"drift={drift:.3f}s ≥ 0.1s",
                    file=sys.stderr,
                )
                ok = False

    # C-①: parse zoompan expressions from captured argv
    zoompan_set: set[str] = set()
    for label, argv in captured:
        expr = _extract_zoompan_expr(argv)
        if expr:
            zoompan_set.add(expr)
            log(f"  [C] {label}: zoompan expr detected: {expr[:80]!r}")

    n_distinct = len(zoompan_set)
    c1_ok = n_distinct >= 3
    if c1_ok:
        print(
            f"\n✅ C-① PASS — {n_distinct} distinct zoompan expressions "
            f"across {len(_CAMERA_TEST_SHOTS)} beats (≥3 required)"
        )
    else:
        print(
            f"\n❌ C-① FAIL — only {n_distinct} distinct zoompan expression(s) "
            f"found (need ≥3).\n  Expressions seen: {sorted(zoompan_set)!r}",
            file=sys.stderr,
        )
        ok = False

    if ok:
        print(
            f"✅ C-② PASS — per-beat drift < 0.1s "
            f"(max={max(d for _, d in drift_results):.3f}s)"
        )

    return ok


# ---------------------------------------------------------------------------
# Segment D — live DP-003=A quick=静止 safeguard
# ---------------------------------------------------------------------------

def _bootstrap_config() -> Path:
    out_dir = HERE / ".render-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = HERE / ".spike" / "_cam_e2e_config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        "render:\n  width: 1920\n  height: 1080\n  fps: 30\n  subtitle_font: PingFang SC\n"
        "audio:\n  voice_id: male-qn-jingying-jingpin\n  bgm_duck_db: 18.0\n"
        "media:\n  provider: doubao\n"
        "budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {_ffmpeg_bin()}\n"
        f"  ffprobe_bin: {_ffprobe_bin()}\n"
        f"output_dir: {out_dir}\n",
        encoding="utf-8",
    )
    return cfg_path


# Minimal digest-form beats for the live director dispatch
_LIVE_BEATS = [
    {"beat_id": "b00", "text": "今天聊乳酸阈配速，搞懂这一个概念训练效率能上台阶。",
     "anchor": "乳酸阈 threshold", "page": 1},
    {"beat_id": "b01", "text": "乳酸阈是乳酸产生速度刚好超过清除速度的临界点。",
     "anchor": "lactate production clearance threshold", "page": 2},
    {"beat_id": "b02", "text": "说人话：还能咬牙撑住，但再快一点就会迅速垮掉的配速。",
     "anchor": "pace tolerable collapse", "page": 2},
    {"beat_id": "b03", "text": "用心率确认阈值：180减年龄，或跑步时还能说短句。",
     "anchor": "heart rate age formula talk test", "page": 3},
    {"beat_id": "b04", "text": "每周安排一到两次阈值训练，持续八周见效。",
     "anchor": "weekly threshold sessions 8 weeks", "page": 3},
    {"beat_id": "b05", "text": "配速跑到阈值区间里保持二十到四十分钟是最有效的方式。",
     "anchor": "threshold pace 20-40 minutes effective", "page": 4},
]

# Sub-pipeline steps that produce a shotlist (stop before render)
_STORYBOARD_STEPS = {"config", "cursor-read", "scratch", "extract", "script", "分镜"}


def _segment_d(log) -> bool:
    """Live DP-003=A safeguard: director → shotlist; assert quick shots = 静止."""
    from lib import episode as episode_mod
    from lib import pipeline as pipeline_mod
    from lib import runner as runner_mod
    from lib import wiring as wiring_mod
    from lib.config import load_config
    from lib.ffcmd import CAMERA_MOVES
    import copy

    seq = os.environ.get("E2E_SEQ", "001")
    slug = os.environ.get("E2E_SLUG", "cam-e2e")

    log(f"\n[D] LIVE DP-003=A quick=静止 safeguard: director dispatch")
    log(f"    PDF={os.environ.get('VIDEO_PDF')} pages={os.environ.get('VIDEO_PAGES')}")

    cfg_path = _bootstrap_config()
    cfg = load_config(cfg_path)

    # Inject digest beats to bypass PDF parse + gen_character_ref
    def inject_extract(step, ctx, scratch_dir):
        ctx["beats"] = copy.deepcopy(_LIVE_BEATS)
        ctx["ref_url"] = "injected://ref"
        ctx["grade_vf"] = "eq=saturation=1"
        log("[D/extract] injected digest-form beats")

    bundle = wiring_mod.prepare_render(
        cfg,
        fixture_path=HERE / "beats.long.fixture.json",
        seq=seq,
        slug=slug,
        dry=False,
        log=log,
    )
    bundle["step_fns"]["extract"] = inject_extract

    full_pipeline = pipeline_mod.load_pipeline()
    sub = [s for s in full_pipeline if s["name"] in _STORYBOARD_STEPS]
    result = runner_mod.run_pipeline(cfg, pipeline=sub, **bundle)

    if result.get("status") != "done":
        print(
            f"\n❌ D FAIL — director runner halted at {result.get('failed_step')!r}: "
            f"{result.get('reason')}",
            file=sys.stderr,
        )
        return False

    ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
    shotlist_path = ep["shotlist"]
    if not shotlist_path.exists():
        print(f"\n❌ D FAIL — shotlist.json missing: {shotlist_path}", file=sys.stderr)
        return False

    shotlist = json.loads(shotlist_path.read_text(encoding="utf-8"))

    # DP-003=A: assert all pace=quick shots have camera_move=静止
    violations: list[str] = []
    quick_total = 0
    for b_idx, beat_entry in enumerate(shotlist):
        shots = beat_entry.get("shots", [])
        for s_idx, shot in enumerate(shots):
            if shot.get("pace") == "quick":
                quick_total += 1
                move = shot.get("camera_move", "(missing)")
                if move != "静止":
                    violations.append(
                        f"beat[{b_idx}].shot[{s_idx}] pace=quick "
                        f"camera_move={move!r} (expected 静止)"
                    )

    log(f"  quick shots found: {quick_total}")
    for b_idx, beat_entry in enumerate(shotlist):
        for s_idx, shot in enumerate(beat_entry.get("shots", [])):
            move = shot.get("camera_move", "(missing)")
            log(f"    beat[{b_idx}].shot[{s_idx}] pace={shot.get('pace')} "
                f"camera_move={move}")

    if violations:
        print(
            f"\n❌ D FAIL — DP-003=A violated: {len(violations)} quick shots "
            f"not camera_move=静止:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"     {v}", file=sys.stderr)
        return False

    print(
        f"\n✅ D PASS — DP-003=A: all {quick_total} pace=quick shot(s) carry "
        f"camera_move=静止 (director followed HARD rule)"
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    camera_demo = os.environ.get("CAMERA_DEMO") == "1"

    def log(m: str) -> None:
        print(m, file=sys.stderr)

    overall_ok = True

    # --- Segment C: deterministic (always runs, needs FFMPEG_BIN) ---
    if not _segment_c(log):
        overall_ok = False

    # In CAMERA_DEMO mode, stop here (no live dispatch).
    if camera_demo:
        log("\n[mode] CAMERA_DEMO — skipping live segment D")
        return 0 if overall_ok else 5

    # --- Live-env gate for Segment D ---
    if not _have_claude():
        print(
            "\nSKIP D: `claude` CLI not on PATH (live director needed).\n"
            "        Set CAMERA_DEMO=1 to run Segment C only (no claude).\n"
            "        Segment C completed above.",
            file=sys.stderr,
        )
        # Non-zero: full live pass not completed.
        return 1 if overall_ok else 5

    # --- Segment D: live DP-003=A safeguard ---
    if not _segment_d(log):
        overall_ok = False

    print(
        f"\n{'✅ ALL PASS' if overall_ok else '❌ SOME FAILURES'} — "
        f"camera-move E2E"
    )
    return 0 if overall_ok else 4


if __name__ == "__main__":
    sys.exit(main())
