#!/usr/bin/env python3
"""Stage 0 + Stage 1 full-loop E2E harness.

Two segments:

  (DET) Deterministic subset — no live claude needed.
        Exercises the full Stage-0/Stage-1 data path using a fixture syllabus
        and cursor written to a tmp directory:
          select_next (cursor → pending episode) →
          step_cursor_read (seeds ctx: doc_refs/source/verify_status/topic) →
          step_extract (doc mode: reads syllabus/source/daniels-uesca/ docs +
                        appends VDOT pace table) →
          assert fulltext non-empty AND normalized pace number in fulltext (M5) →
          step_publish (marks ep done + advances cursor) →
          assert episode marked done + cursor advanced on disk →
          no-pending → select_next → None (clean entry halt, not a raise).

        ⚠️ AR-2 deterministic net:
          (a) step_cursor_read seeds ctx["verify_status"]="⚠️" for a ⚠️ episode.
          (b) build_provenance on a ⚠️ beat preserves verify_status="⚠️" in the
              provenance record — "⚠️ never dressed as science" is a real
              regression net, not a claim.
          (c) ⚠️ episode's learning_goal carries "工程估计" label (planner rule).

  (LIVE) Live segment — `claude` CLI on PATH required.
        Dispatches the planner persona (claude -p --model sonnet) reading the
        snapshot docs → produces a syllabus draft YAML → asserts:
          - draft has at least one module with at least one episode
          - every episode has `source`, `verify_status`, and `doc_refs` fields
          - at least one episode has verify_status starting with "⚠️"
          - ⚠️ episodes carry a learning_goal with "工程估计" or "非源直出"
            (AR-2 live check: planner follows DP-602 labeling rule)

⚠️ Live-env required: `claude` CLI on PATH for live segment.  Without it:
  - DET_ONLY=1 → run deterministic subset only, exit 0 on pass, nonzero on fail.
  - default  → run deterministic subset, then skip live + exit nonzero (no
               fake green).

Modes:
  (default)  DET + LIVE: runs deterministic subset then live planner.
             Needs claude CLI; without it → DET passes, live SKIP + exit 1.
  DET_ONLY=1 Deterministic-only: run DET, exit 0 on pass.

Env vars:
  DET_ONLY    1 = skip live segment, exit 0 on DET pass
  E2E_SEQ     override episode seq label (default "002")
  E2E_SLUG    override episode slug (derived from episode title if unset)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent  # video-studio root
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lib import wiring as wiring_mod
from lib.config import load_config
from lib.dispatch import dispatch_persona
from lib.extract import extract_doc_sections, render_pace_lines
from lib.provenance import build_provenance
from lib.syllabus import (
    Cursor,
    Episode,
    Module,
    Selection,
    Syllabus,
    advance,
    derive_seq_slug,
    load_cursor,
    load_syllabus,
    save_cursor,
    save_syllabus,
    select_next,
)

# ---------------------------------------------------------------------------
# Snapshot directory (DP-601: training-science snapshot is self-contained)
# ---------------------------------------------------------------------------
SNAPSHOT_DIR = HERE / "syllabus" / "source" / "daniels-uesca"

# M5 regression: VDOT 60 threshold pace = 3.67 decimal min/km → "3:40"
EXPECTED_PACE_IN_FULLTEXT = "3:40"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _have_claude() -> bool:
    return shutil.which("claude") is not None


def _make_fixture_syllabus() -> Syllabus:
    """Return a fixture Syllabus with 2 modules × 2 episodes each.

    Layout (global indices):
      ep1 (0,0) done   ✅  module 0 — doc: 01-intensity-zones.md
      ep2 (0,1) locked ✅  module 0 — doc: 02-vdot-system.md   ← cursor starts here
      ep3 (1,0) locked ⚠️  module 1 — doc: 07-athlete-intake.md  ← AR-2 fixture
      ep4 (1,1) locked ✅  module 1 — doc: 03-training-load-points.md

    Episodes are `locked` (not `pending`): the consume gate (next_pending /
    select_next / advance) only picks `locked`; `pending` is an unlocked draft.
    """
    return Syllabus(
        book="test-book",
        character_ref="",
        modules=[
            Module(
                name="强度系统",
                style="教学",
                episodes=[
                    Episode(
                        title="强度区间总览",
                        doc_refs=["01-intensity-zones.md"],
                        source="Daniels",
                        verify_status="✅",
                        learning_goal="了解跑步七区和各区强度意义",
                        status="done",
                    ),
                    Episode(
                        title="VDOT系统详解",
                        doc_refs=["02-vdot-system.md"],
                        source="Daniels",
                        verify_status="✅",
                        learning_goal="理解VDOT配速系统和训练配速推导",
                        status="locked",
                    ),
                ],
            ),
            Module(
                name="运动员评估",
                style="教学",
                episodes=[
                    Episode(
                        title="冷启动VDOT先验估计",
                        doc_refs=["07-athlete-intake.md"],
                        source="UESCA",
                        verify_status="⚠️",
                        learning_goal=(
                            "工程估计/产品设定，非源直出："
                            "基于常见人口先验的冷启动VDOT估值，不是丹尼尔斯原话"
                        ),
                        status="locked",
                    ),
                    Episode(
                        title="训练负荷积分",
                        doc_refs=["03-training-load-points.md"],
                        source="Daniels",
                        verify_status="✅",
                        learning_goal="了解训练积分(E/Q/L点)的计算和用途",
                        status="locked",
                    ),
                ],
            ),
        ],
    )


def _make_fixture_cursor(book: str = "test-book") -> Cursor:
    """Cursor at (module_idx=0, episode_idx=0) — ep1 is done; next_pending → ep2."""
    return Cursor(book=book, module_idx=0, episode_idx=0)


def _make_all_done_syllabus() -> Syllabus:
    """Same shape as fixture but all episodes done."""
    s = _make_fixture_syllabus()
    for mod in s.modules:
        for ep in mod.episodes:
            ep.status = "done"
    return s


def _bootstrap_config(tmp_dir: Path) -> Path:
    """Write a minimal config.yaml; includes syllabus + cursor paths."""
    out_dir = tmp_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    syllabus_yaml = tmp_dir / "syllabus.yaml"
    cursor_yaml = tmp_dir / "cursor.yaml"

    cfg_path = tmp_dir / "config.yaml"
    cfg_path.write_text(
        f"render:\n  width: 1920\n  height: 1080\n  fps: 30\n"
        f"  subtitle_font: PingFang SC\n"
        f"audio:\n  voice_id: male-qn-jingying-jingpin\n  bgm_duck_db: 18.0\n"
        f"media:\n  provider: doubao\n"
        f"budget:\n  video_shots_per_episode: 2\n"
        f"ffmpeg:\n  ffmpeg_bin: {os.environ.get('FFMPEG_BIN', 'ffmpeg')}\n"
        f"  ffprobe_bin: {os.environ.get('FFPROBE_BIN', 'ffprobe')}\n"
        f"output_dir: {out_dir}\n"
        f"syllabus_path: {syllabus_yaml}\n"
        f"cursor_path: {cursor_yaml}\n"
        # Critical: must point to daniels-uesca subdir, not just source/
        f"syllabus_source_dir: {SNAPSHOT_DIR}\n",
        encoding="utf-8",
    )
    return cfg_path


# ---------------------------------------------------------------------------
# Deterministic subset
# ---------------------------------------------------------------------------

def _run_deterministic(log) -> bool:
    """Run the deterministic subset.  Returns True iff all assertions pass."""
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"\n[DET] FAIL — {msg}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="vs_stage0_e2e_") as _td:
        tmp_dir = Path(_td)

        # ------------------------------------------------------------------ Write fixtures
        syllabus_path = tmp_dir / "syllabus.yaml"
        cursor_path = tmp_dir / "cursor.yaml"
        save_syllabus(_make_fixture_syllabus(), syllabus_path)
        save_cursor(_make_fixture_cursor(), cursor_path)

        cfg_path = _bootstrap_config(tmp_dir)
        cfg = load_config(cfg_path)

        scratch = tmp_dir / "scratch"
        scratch.mkdir()

        # stub step dict (step_fns only consume the name; the dict shape suffices)
        stub_step: dict[str, Any] = {"name": "test", "kind": "code"}

        # ================================================================== DET-1: select_next
        log("[DET-1] select_next: cursor at (0,0)→ep1(done) → should return ep2 seq=002")
        sel = select_next(cfg)
        if sel is None:
            fail("select_next returned None; expected ep2 (first pending)")
        elif sel.seq != "002":
            fail(f"select_next seq={sel.seq!r}; expected '002'")
        elif "02-vdot-system.md" not in sel.doc_refs:
            fail(f"select_next doc_refs={sel.doc_refs}; expected '02-vdot-system.md'")
        elif sel.verify_status != "✅":
            fail(f"select_next verify_status={sel.verify_status!r}; expected '✅'")
        else:
            print("[DET-1] select_next: seq=002, ✅, doc_refs ok  ✔")

        if sel is None:
            # Can't proceed without a selection
            return False

        # ================================================================== DET-2: cursor-read ctx seed
        log("[DET-2] cursor-read: checking ctx seeding via step_fns")
        bundle = wiring_mod.prepare_render(
            cfg,
            fixture_path=HERE / "beats.long.fixture.json",
            seq=sel.seq,
            slug=sel.slug,
            dry=True,
            log=log,
            selection=sel,
        )
        step_fns = bundle["step_fns"]

        ctx: dict[str, Any] = {}
        step_fns["config"](stub_step, ctx, str(scratch))
        step_fns["cursor-read"](stub_step, ctx, str(scratch))

        if ctx.get("doc_refs") != sel.doc_refs:
            fail(f"cursor-read ctx['doc_refs']={ctx.get('doc_refs')!r}; expected {sel.doc_refs!r}")
        elif ctx.get("source") != sel.source:
            fail(f"cursor-read ctx['source']={ctx.get('source')!r}; expected {sel.source!r}")
        elif ctx.get("verify_status") != sel.verify_status:
            fail(
                f"cursor-read ctx['verify_status']={ctx.get('verify_status')!r}; "
                f"expected {sel.verify_status!r}"
            )
        elif ctx.get("topic") != sel.topic:
            fail(f"cursor-read ctx['topic']={ctx.get('topic')!r}; expected {sel.topic!r}")
        else:
            print("[DET-2] cursor-read ctx seed: doc_refs/source/verify_status/topic  ✔")

        # ================================================================== DET-3: extract doc-mode
        log("[DET-3] extract doc-mode: reads snapshot docs + appends VDOT pace table")
        step_fns["scratch"](stub_step, ctx, str(scratch))
        step_fns["extract"](stub_step, ctx, str(scratch))

        fulltext = ctx.get("fulltext", "")
        if not fulltext:
            fail("extract doc-mode: fulltext is empty")
        elif EXPECTED_PACE_IN_FULLTEXT not in fulltext:
            snippet = fulltext[-300:] if len(fulltext) > 300 else fulltext
            fail(
                f"extract doc-mode: M5 pace '{EXPECTED_PACE_IN_FULLTEXT}' "
                f"not in fulltext ({len(fulltext)} chars). "
                f"Last 300 chars: {snippet!r}"
            )
        else:
            print(
                f"[DET-3] extract doc-mode: {len(fulltext)} chars, "
                f"'{EXPECTED_PACE_IN_FULLTEXT}' in fulltext (M5)  ✔"
            )

        # ================================================================== DET-4: publish (step_publish)
        log("[DET-4] publish: step_publish marks ep2 done + advances cursor")
        # step_publish requires final.mp4 to exist; create a 4-byte stub
        ep_paths = ctx.get("ep", {})
        if ep_paths:
            final_path: Path = ep_paths["final"]
            final_path.parent.mkdir(parents=True, exist_ok=True)
            final_path.write_bytes(b"FAKE")
            ctx["final_mp4"] = str(final_path)
        else:
            # ep not yet set (scratch not reached); create a temp path.
            # Video name matches the episode dir (see episode.episode_paths).
            ep_name = f"{sel.seq}-{sel.slug}"
            tmp_final = tmp_dir / "output" / "episodes" / ep_name / f"{ep_name}.mp4"
            tmp_final.parent.mkdir(parents=True, exist_ok=True)
            tmp_final.write_bytes(b"FAKE")
            ctx["final_mp4"] = str(tmp_final)

        try:
            step_fns["publish"](stub_step, ctx, str(scratch))
        except Exception as exc:
            fail(f"step_publish raised: {exc}")
        else:
            # Reload from disk and verify
            post_syllabus = load_syllabus(syllabus_path)
            post_cursor = load_cursor(cursor_path)

            ep2_status = post_syllabus.modules[0].episodes[1].status
            if ep2_status != "done":
                fail(f"publish: ep2.status={ep2_status!r}; expected 'done'")
            elif post_cursor.module_idx != 1 or post_cursor.episode_idx != 0:
                fail(
                    f"publish: cursor=({post_cursor.module_idx},{post_cursor.episode_idx}); "
                    f"expected (1,0)"
                )
            else:
                print(
                    "[DET-4] publish: ep2 marked done, "
                    f"cursor→(1,0)=ep3(⚠️)  ✔"
                )

        # ================================================================== DET-5: no-pending halt
        log("[DET-5] no-pending halt: select_next returns None when all episodes done")
        all_done_path = tmp_dir / "all_done_syllabus.yaml"
        all_done_cursor_path = tmp_dir / "all_done_cursor.yaml"
        save_syllabus(_make_all_done_syllabus(), all_done_path)
        save_cursor(_make_fixture_cursor(), all_done_cursor_path)

        class _NoPendingCfg:
            syllabus_path = str(all_done_path)
            cursor_path = str(all_done_cursor_path)
            # select_next only reads these two attrs
            def __getattr__(self, _: str):  # noqa: ANN001
                return ""

        np_sel = select_next(_NoPendingCfg())  # type: ignore[arg-type]
        if np_sel is not None:
            fail(f"no-pending halt: select_next returned {np_sel!r}; expected None")
        else:
            print("[DET-5] no-pending halt: select_next → None (clean entry halt)  ✔")

        # ================================================================== DET-6: AR-2 ⚠️ net
        log("[DET-6] AR-2 ⚠️ net: verify_status propagation + provenance labeling")

        # (a) ctx seeding: ⚠️ episode's verify_status propagates through cursor-read
        post_syllabus2 = load_syllabus(syllabus_path)  # has ep2=done now
        ep3_ep = post_syllabus2.modules[1].episodes[0]  # ⚠️, pending
        ep3_seq, ep3_slug = derive_seq_slug(post_syllabus2, 1, 0)  # should be "003"

        warn_sel = Selection(
            episode=ep3_ep,
            seq=ep3_seq,
            slug=ep3_slug,
            doc_refs=ep3_ep.doc_refs,
            source=ep3_ep.source,
            verify_status=ep3_ep.verify_status,
            learning_goal=ep3_ep.learning_goal,
            topic=ep3_ep.title,
            character_ref=post_syllabus2.character_ref,
        )

        warn_scratch = tmp_dir / "scratch_warn"
        warn_scratch.mkdir(exist_ok=True)
        warn_bundle = wiring_mod.prepare_render(
            cfg,
            fixture_path=HERE / "beats.long.fixture.json",
            seq=ep3_seq,
            slug=ep3_slug,
            dry=True,
            log=log,
            selection=warn_sel,
        )
        warn_step_fns = warn_bundle["step_fns"]
        warn_ctx: dict[str, Any] = {}
        warn_step_fns["config"](stub_step, warn_ctx, str(warn_scratch))
        warn_step_fns["cursor-read"](stub_step, warn_ctx, str(warn_scratch))

        if warn_ctx.get("verify_status") != "⚠️":
            fail(
                f"AR-2(a): ctx['verify_status']={warn_ctx.get('verify_status')!r}; "
                f"expected '⚠️' for ⚠️ episode"
            )
        else:
            print("[DET-6a] AR-2 ctx propagation: verify_status='⚠️'  ✔")

        # (b) build_provenance: ⚠️ beat preserves verify_status in provenance record
        warn_doc_map = extract_doc_sections(SNAPSHOT_DIR, ep3_ep.doc_refs)
        test_beats = [
            {
                "beat_id": "b00",
                "anchor": "VDOT 先验估计",
                "source": ep3_ep.source,
                "verify_status": ep3_ep.verify_status,   # "⚠️"
            },
            {
                "beat_id": "b01",
                "anchor": "强度区间",
                "source": "Daniels",
                "verify_status": "✅",
            },
        ]
        prov = build_provenance(test_beats, warn_doc_map)
        warn_prov = [p for p in prov if p.get("verify_status") == "⚠️"]
        ok_prov = [p for p in prov if p.get("verify_status") == "✅"]

        if not warn_prov:
            fail("AR-2(b): no ⚠️ beat found in provenance output (expected 1)")
        elif warn_prov[0]["verify_status"] != "⚠️":
            fail(
                f"AR-2(b): provenance ⚠️ beat has verify_status={warn_prov[0]['verify_status']!r}; "
                f"expected '⚠️'"
            )
        elif not ok_prov:
            fail("AR-2(b): no ✅ beat in provenance (needed to distinguish from ⚠️)")
        else:
            print(
                f"[DET-6b] AR-2 provenance: ⚠️ beat records verify_status='⚠️', "
                f"✅ beat records '✅'  ✔"
            )

        # (c) ⚠️ episode's learning_goal carries the label (planner contract)
        if "工程估计" not in ep3_ep.learning_goal:
            fail(
                f"AR-2(c): ⚠️ episode learning_goal missing '工程估计': "
                f"{ep3_ep.learning_goal!r}"
            )
        else:
            print(f"[DET-6c] AR-2 learning_goal: contains '工程估计'  ✔")

    return ok


# ---------------------------------------------------------------------------
# Live planner segment
# ---------------------------------------------------------------------------

def _run_live_planner(log) -> bool:
    """Dispatch planner persona, assert draft structure."""
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"\n[LIVE] FAIL — {msg}", file=sys.stderr)

    doc_paths = sorted(SNAPSHOT_DIR.glob("*.md"))
    if not doc_paths:
        fail(f"No *.md docs found in snapshot dir: {SNAPSHOT_DIR}")
        return False

    doc_list_str = "\n".join(str(p) for p in doc_paths)
    user_prompt = (
        f"以下是权威层训练科学文档的绝对路径（每行一个）：\n{doc_list_str}\n\n"
        f"书系名称：daniels-uesca 跑步知识系列。\n"
        f"请读取这些文档后提议一份课程大纲草案 YAML。"
    )

    with tempfile.TemporaryDirectory(prefix="vs_stage0_planner_") as _td:
        scratch = Path(_td)
        log(f"[LIVE] dispatching planner → scratch: {scratch}")
        result = dispatch_persona(
            "planner",
            user_prompt,
            str(scratch),
            "syllabus_draft.yaml",
            plugin_root=HERE,
            model="sonnet",
            timeout=900,
        )

        if not result.get("ok"):
            fail(f"planner dispatch failed: {result.get('reason')}")
            return False

        draft_path = Path(result["artifact_path"])
        if not draft_path.exists() or draft_path.stat().st_size == 0:
            fail(f"planner did not write artifact: {draft_path}")
            return False

        raw_text = draft_path.read_text(encoding="utf-8")
        log(f"[LIVE] draft YAML: {len(raw_text)} chars from {draft_path}")

        # Strip possible ```yaml fences from the draft
        import re as _re
        stripped = _re.sub(r"^```[a-zA-Z]*\n", "", raw_text, flags=_re.MULTILINE)
        stripped = _re.sub(r"\n```$", "", stripped.rstrip())

        import yaml as _yaml
        try:
            draft = _yaml.safe_load(stripped)
        except Exception as exc:
            fail(f"YAML parse error on draft: {exc}\nraw[:200]={raw_text[:200]!r}")
            return False

        if not isinstance(draft, dict):
            fail(f"draft is not a YAML mapping: {type(draft).__name__}")
            return False

        modules = draft.get("modules", [])
        if not modules:
            fail("draft has no 'modules' key or modules list is empty")
            return False

        # Collect all episodes for structural assertions
        all_episodes: list[dict] = []
        for m in modules:
            all_episodes.extend(m.get("episodes", []))

        if not all_episodes:
            fail("draft modules have no episodes")
            return False

        # Assert every episode has source + verify_status + doc_refs
        missing_fields: list[str] = []
        for ep in all_episodes:
            for field in ("source", "verify_status", "doc_refs"):
                if field not in ep:
                    missing_fields.append(f"episode {ep.get('title','?')!r} missing {field!r}")
        if missing_fields:
            fail("some episodes missing required fields:\n  " + "\n  ".join(missing_fields[:10]))
        else:
            print(
                f"[LIVE-1] draft structure: {len(modules)} modules, "
                f"{len(all_episodes)} episodes, all have source/verify_status/doc_refs  ✔"
            )

        # Assert at least one ⚠️ episode exists
        warn_eps = [e for e in all_episodes if str(e.get("verify_status", "")).startswith("⚠️")]
        if not warn_eps:
            fail(
                f"draft has no ⚠️ episodes out of {len(all_episodes)}; "
                f"statuses seen: {list({str(e.get('verify_status','')) for e in all_episodes})}"
            )
        else:
            print(f"[LIVE-2] draft has {len(warn_eps)} ⚠️ episode(s)  ✔")

        # AR-2 live: ⚠️ episodes must carry "工程估计" or "非源直出" in learning_goal
        ar2_failures: list[str] = []
        for ep in warn_eps:
            lg = str(ep.get("learning_goal", ""))
            if "工程估计" not in lg and "非源直出" not in lg:
                ar2_failures.append(
                    f"{ep.get('title','?')!r}: learning_goal={lg!r}"
                )
        if ar2_failures:
            fail(
                "AR-2 live: ⚠️ episodes missing '工程估计'/'非源直出' in learning_goal:\n  "
                + "\n  ".join(ar2_failures)
            )
        else:
            print(
                f"[LIVE-3] AR-2 live: all {len(warn_eps)} ⚠️ episode(s) carry "
                f"'工程估计'/'非源直出' in learning_goal  ✔"
            )

        print(f"\n=== planner draft → {draft_path} ===")
        print(f"  modules: {len(modules)}, episodes: {len(all_episodes)}")
        for m in modules[:4]:
            eps = m.get("episodes", [])
            print(
                f"  [{m.get('name','?')}] {len(eps)} episode(s): "
                + ", ".join(e.get("title", "?")[:20] for e in eps[:4])
            )

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    det_only = os.environ.get("DET_ONLY") == "1"

    def log(m: str) -> None:
        print(m, file=sys.stderr)

    # ---- Deterministic subset (always runs) ----
    print("=" * 60)
    print("Deterministic subset (no claude CLI needed)")
    print("=" * 60)

    det_ok = _run_deterministic(log)

    if det_ok:
        print("\n✅ DET PASS — deterministic subset all assertions passed")
    else:
        print("\n❌ DET FAIL — see FAIL lines above", file=sys.stderr)

    if det_only:
        return 0 if det_ok else 3

    # ---- Live segment (requires claude CLI) ----
    print("\n" + "=" * 60)
    print("Live segment (planner dispatch — requires claude CLI)")
    print("=" * 60)

    if not _have_claude():
        print(
            "\nSKIP: `claude` CLI not on PATH (live planner needed).\n"
            "      Set DET_ONLY=1 to run deterministic subset only "
            "(exits 0 on DET pass).",
            file=sys.stderr,
        )
        # DET may have passed; live is incomplete — report non-zero (no fake green)
        return 1

    live_ok = _run_live_planner(log)

    if live_ok:
        print("\n✅ LIVE PASS — planner draft structure + AR-2 labeling verified")
    else:
        print("\n❌ LIVE FAIL — see FAIL lines above", file=sys.stderr)

    overall_ok = det_ok and live_ok
    print(
        f"\n{'✅ ALL PASS' if overall_ok else '❌ SOME FAILURES'} — "
        f"Stage-0 + Stage-1 E2E"
    )
    return 0 if overall_ok else 4


if __name__ == "__main__":
    sys.exit(main())
