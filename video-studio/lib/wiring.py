"""Production render wiring — assembles the 13-step `step_fns` dict that
connects `lib.pipeline.load_pipeline()` to the ported render functions in
`lib.steps`, driven by `lib.runner.run_pipeline`.

This is the single source of truth for "how a pipeline step actually renders".
Both the production CLI (`python -m lib.runner`) and the live E2E eval
(`evals/video_render_e2e.py`) import `prepare_render` / `build_step_fns` from
here — the wiring is defined ONCE.

Layering (no cycles): this module imports the render/media/episode helpers but
NOT `lib.runner` — the caller owns the `run_pipeline` call so the executor stays
a leaf. `MediaSeams` makes the three paid calls (gen_still / gen_video / synth)
injectable: live = Doubao + MiniMax; dry = copy cached `.spike/` assets ($0).

Reuse hooks (honoured in both dry and live — "already paid", not dry-fakes):
  SPIKE_REF_PNG + SPIKE_REF_URL   reuse a character_ref (skip gen_character_ref)
  SPIKE_STORYBOARD_JSON           reuse a cached 分镜 plan (skip the slow LLM)
  SPIKE_S2V_CLIP                  reuse an already-paid S2V clip for the video beat
  SPIKE_CARD_TITLE_BG / _END_BG   reuse already-generated card backgrounds
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

# video-studio plugin root (parent of lib/) — needed for the .spike cache and
# the spike_acceptance import (Phase 7 retires this coupling with the spike).
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import cards, ffcmd, grade, media_chart, media_tts
from lib import dispatch as dispatch_mod
from lib import episode as episode_mod
from lib import extract as extract_mod
from lib import steps as steps_mod
from lib.config import VideoStudioConfig
from lib.budget import apply_video_budget
from lib.ledger import LedgerError, validate_ledger, verify_anchors
from lib.provenance import build_provenance, stamp_episode_provenance
from lib.storyboard import parse_shotlist
from lib.subtitle import build_ass, build_srt

SPIKE = PLUGIN_ROOT / ".spike"

BGM_START_VOLUME = 0.12
MAX_CALIB_ROUNDS = 3


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe seams (closures over the config-resolved binaries)
# ---------------------------------------------------------------------------

def make_ff_seams(cfg: VideoStudioConfig) -> tuple[Callable, Callable]:
    """Return (run_ff, probe_ms) bound to the config-or-env ffmpeg binaries.

    Also exports FFMPEG_BIN/FFPROBE_BIN into os.environ, because
    spike_acceptance captures them at module-import time and the BGM-ducking
    measurement (measure_gap) routes through it.
    """
    ffmpeg_bin = os.environ.get("FFMPEG_BIN", cfg.ffmpeg.ffmpeg_bin)
    ffprobe_bin = os.environ.get("FFPROBE_BIN", cfg.ffmpeg.ffprobe_bin)
    os.environ["FFMPEG_BIN"] = ffmpeg_bin
    os.environ["FFPROBE_BIN"] = ffprobe_bin
    ff_prefix = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]

    def run_ff(argv: list[str], label: str) -> None:
        cmd = ff_prefix + argv
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg [{label}] exit={proc.returncode}\n"
                f"CMD: {' '.join(cmd)}\nSTDERR:\n{proc.stderr.strip()}"
            )

    def probe_ms(path: str) -> int:
        out = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(out.stdout.strip()) * 1000)

    return run_ff, probe_ms


# ---------------------------------------------------------------------------
# Media seams — the ONLY difference between dry and live
# ---------------------------------------------------------------------------

class MediaSeams:
    """gen_character_ref / gen_still / gen_video / synth.

    Live: real Doubao + MiniMax. Dry: copy cached `.spike/` assets ($0).
    Both honour the SPIKE_* reuse hooks at the call sites in build_step_fns —
    those bypass these seams entirely for ref/S2V/cards.
    """

    def __init__(self, dry: bool, provider: str, log: Callable[[str], None],
                 style_prefix: str = ""):
        self.dry = dry
        self.provider = provider
        self.log = log
        self.style_prefix = style_prefix
        if not dry:
            if provider == "doubao":
                from lib import media_doubao as m
                self._img = m
                self._vid = m
            else:
                from lib import media_image as m
                from lib import media_video as mv
                self._img = m
                self._vid = mv

    def gen_character_ref(self, prompt: str, out_path: str) -> str:
        if self.dry:
            shutil.copy(SPIKE / "character_ref.jpg", out_path)
            return "dry://character_ref"
        p = f"{self.style_prefix}, {prompt}" if self.style_prefix else prompt
        return self._img.gen_character_ref(p, out_path)

    def gen_still(self, prompt: str, ref_url: str | None, out_path: str) -> str:
        if self.dry:
            # Content is irrelevant for the wiring/ffmpeg test — reuse one cached
            # photographic still so ffmpeg gets a real, decodable image.
            dst = os.path.splitext(out_path)[0] + ".jpg"
            shutil.copy(SPIKE / "b00_shot0.jpg", dst)
            return dst
        p = f"{self.style_prefix}, {prompt}" if self.style_prefix else prompt
        return self._img.gen_still(p, ref_url, out_path)

    def gen_video(self, prompt: str, ref_url: str, out_path: str) -> dict:
        if self.dry:
            shutil.copy(SPIKE / "b04.mp4", out_path)
            return {"ok": True, "path": out_path, "task_id": "dry"}
        p = f"{self.style_prefix}, {prompt}" if self.style_prefix else prompt
        return self._vid.gen_video(p, ref_url, out_path)

    def synth(self, text: str, out_path: str) -> int:
        if self.dry:
            shutil.copy(SPIKE / "b00.raw.mp3", out_path)
            return 5000
        return media_tts.synth(text, out_path)


# ---------------------------------------------------------------------------
# BGM (synthesize a soft placeholder pad if no real bgm.mp3)
# ---------------------------------------------------------------------------

def ensure_bgm(assets_dir: Path, run_ff: Callable) -> str:
    real = assets_dir / "bgm.mp3"
    if real.exists() and real.stat().st_size > 1024:
        return str(real)
    run_ff([
        "-f", "lavfi", "-i", "sine=frequency=220:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
        "-filter_complex", "[0:a][1:a]amix=inputs=2,volume=0.5[a]",
        "-map", "[a]", "-ar", "48000", "-ac", "2", str(real),
    ], "bgm-placeholder")
    return str(real)


# ---------------------------------------------------------------------------
# Chart content selection helper (module-level for testability)
# ---------------------------------------------------------------------------

def _chart_content_for_beat(beat: dict, ctx: dict):
    """Return rendering descriptor for a chart beat. Fixture or spec-driven dispatch.

    Priority:
    1. Fixture (hand-authored): beat has chart_title + chart_rows → verbatim.
    2. Spec-driven: beat["chart"]["type"] dispatches to the authoritative builder.
       - "vdot_pace" → build_pace_chart_from_vdot(ctx["vdot_data"], target_vdot)
       - "zone_boundary" → build_zone_boundary_chart(ctx["zone_data"])
       - "line" → ("__line__", params_dict) sentinel; step_chart calls build_line_chart
       - unknown type → log warning + return None (upstream degrades to still)
    3. No fixture, no spec → RuntimeError (no blanket VDOT-50 fallback anymore).

    Return values:
      (title, rows)          — for vdot_pace / zone_boundary / fixture
      ("__line__", params)   — for line chart (step_chart renders via build_line_chart)
      None                   — for unknown/unsupported types (degrade to still)
    """
    import logging
    bid = beat.get("id", "?")
    # Fixture: hand-authored chart_title/chart_rows take precedence
    if "chart_title" in beat and "chart_rows" in beat:
        return beat["chart_title"], beat["chart_rows"]
    # Spec-driven dispatch via director chart spec
    spec = beat.get("chart")
    if spec:
        t = (spec.get("type") or "").strip()
        params = spec.get("params") or {}
        if t == "vdot_pace":
            target_vdot = params.get("vdot", 50)
            built = media_chart.build_pace_chart_from_vdot(
                ctx.get("vdot_data"), target_vdot=target_vdot
            )
            if built is None:
                raise RuntimeError(
                    f"[chart] beat {bid}: vdot_pace builder returned None — "
                    f"权威表缺失或为空 (ctx['vdot_data'] missing/empty)"
                )
            return built
        elif t == "zone_boundary":
            built = media_chart.build_zone_boundary_chart(ctx.get("zone_data"))
            if built is None:
                raise RuntimeError(
                    f"[chart] beat {bid}: zone_boundary builder returned None — "
                    f"权威表缺失或为空 (ctx['zone_data'] missing/empty)"
                )
            return built
        elif t == "line":
            return ("__line__", params)
        else:
            logging.warning(
                "[chart] beat %s: unknown chart type %r — 降级为静图，跳过渲染", bid, t
            )
            return None
    # No fixture, no spec: no blanket VDOT-50 fallback — fail loud
    raise RuntimeError(
        f"[chart] beat {bid} 是 chart 但导演没给 chart spec，无法填数（不编造）"
    )


# ---------------------------------------------------------------------------
# Card-config discriminator helper (module-level for testability — NH-2 + S1-A5)
# ---------------------------------------------------------------------------

def _card_configs_for(ctx: dict) -> tuple:
    """Return ``(tcfg, ecfg)`` for ``step_assemble``.

    Branches on **fixture presence** (``ctx.get("fixture")`` truthy), NOT on
    ``ctx.get("topic")``:

    - Fixture mode → merge ``fixture.get("cards", {})`` over ``DEFAULT_*``.
      A cards-less fixture keeps the column defaults (NH-2 fix).
    - Doc / PDF mode (no fixture) → episode-derived via ``build_title_card`` /
      ``build_end_card``, never DEFAULT_END's foreign bullets (S1-A5 fix).

    Authored digest fields (``ctx["title_subtitle"]`` / ``ctx["end_card_bullets"]``)
    are forwarded to the builders when present (seeded by ``make_script_dispatch``).
    """
    fixture = ctx.get("fixture")
    beats = ctx.get("beats", [])
    if fixture:
        card_cfg = fixture.get("cards", {})
        tcfg = {**cards.DEFAULT_TITLE, **card_cfg.get("title", {})}
        ecfg = {**cards.DEFAULT_END, **card_cfg.get("end", {})}
    else:
        script_text = " ".join(b.get("text", "") for b in beats)
        tcfg = cards.build_title_card(
            ctx.get("topic", ""),
            ctx.get("learning_goal", ""),
            ctx.get("seq", ""),
            subtitle=ctx.get("title_subtitle", ""),
        )
        ecfg = cards.build_end_card(
            beats[-1].get("text", "") if beats else "",
            ctx.get("learning_goal", ""),
            authored_bullets=ctx.get("end_card_bullets"),
            script_text=script_text,
        )
    return tcfg, ecfg


# ---------------------------------------------------------------------------
# Build the 13-step step_fns dict
# ---------------------------------------------------------------------------

def build_step_fns(
    cfg: VideoStudioConfig,
    *,
    seams: MediaSeams,
    fixture_path: Path,
    seq: str,
    slug: str,
    run_ff: Callable,
    probe_ms: Callable,
    log: Callable[[str], None],
    selection: Any = None,
    dry: bool = False,
) -> dict[str, Callable]:
    """Return {step_name: fn(step, ctx, scratch_dir)} for the Phase-2 pipeline.

    Contract (mirrors test_runner.py): each step writes its real artifacts into
    `scratch_dir` (intermediates) or the episode dir (deliverables), then sets
    ctx[step['artifact']] to a non-empty FILE path so check_artifact passes.
    Heavy data (beats/srt_beats/clip map/start_ms) flows through ctx under
    untracked keys. final.mp4 + subtitles.srt + shotlist.json + provenance.json
    land in the EPISODE dir (survives the runner's end-of-run cleanup_scratch).
    """

    def _sentinel(scratch: Path, key: str, payload: str = "ok") -> str:
        p = scratch / f"{key}.manifest.txt"
        p.write_text(payload, encoding="utf-8")
        return str(p)

    # ---- 0: config (no artifact) ----
    def step_config(step, ctx, scratch):
        ctx["_config"] = cfg
        log(f"[config] provider={cfg.media.provider} "
            f"{cfg.render.width}x{cfg.render.height}@{cfg.render.fps}")

    # ---- 1: cursor-read — seed ctx from syllabus Selection; write cursor_marker ----
    def step_cursor_read(step, ctx, scratch):
        marker_path = str(Path(scratch) / "cursor_marker.txt")
        if selection is not None:
            # Syllabus mode: seed downstream keys from closure-bound Selection.
            ctx["doc_refs"] = selection.doc_refs
            ctx["source"] = selection.source
            ctx["verify_status"] = selection.verify_status
            ctx["topic"] = selection.topic
            ctx["learning_goal"] = selection.learning_goal
            ctx["character_ref"] = selection.character_ref
            ctx["seq"] = selection.seq
            Path(marker_path).write_text(
                f"seq={selection.seq}\nslug={selection.slug}\ntopic={selection.topic}",
                encoding="utf-8",
            )
            log(f"[cursor-read] selection seq={selection.seq} slug={selection.slug}")
        else:
            # Phase-2 no-syllabus mode: write a passing marker so check_artifact gate passes.
            Path(marker_path).write_text("ok", encoding="utf-8")
            log("[cursor-read] Phase-2 placeholder (no syllabus configured)")
        ctx["cursor_marker"] = marker_path

    # ---- 2: scratch (episode dirs + scratch sentinel) ----
    def step_scratch(step, ctx, scratch):
        ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
        ep_dir = ep["final"].parent
        ep_dir.mkdir(parents=True, exist_ok=True)
        ep["assets_dir"].mkdir(parents=True, exist_ok=True)
        ctx["ep"] = ep
        ctx["scratch_dir"] = _sentinel(Path(scratch), "scratch_dir", str(ep_dir))
        log(f"[scratch] episode dir -> {ep_dir}")

    # ---- 3: extract — 3-way dispatch: doc_refs > VIDEO_PDF > fixture ----
    def step_extract(step, ctx, scratch):
        # Resolve art style and color grade independently (two orthogonal dims).
        # Set seams.style_prefix + ctx["grade_vf"] unconditionally so doc / PDF / fixture
        # paths all provision them. Late-bind happens here (before _ensure_ref_url)
        # since gen_character_ref is called from within step_extract itself.
        _art_style = (selection.art_style if selection is not None else "") or ""
        _color_grade = (selection.color_grade if selection is not None else "") or ""
        seams.style_prefix = grade.resolve_art_style(_art_style)
        ctx["grade_vf"] = grade.resolve_color_grade(_color_grade or None)
        log(f"[grade] art_style={_art_style!r} color_grade={_color_grade!r}")

        def _ensure_ref_url(prompt):
            # character_ref → subject_reference URL, shared across all extract
            # modes so ref_url is always provisioned before gen_still/gen_video.
            # Honors the SPIKE_REF_PNG/SPIKE_REF_URL dev reuse hook ($0). NOTE:
            # within-run only — the provider URL is ephemeral, so cross-episode
            # same-face reuse is a separate concern (deferred: needs durable host).
            ep = ctx["ep"]
            cref_out = str(ep["assets_dir"] / "character_ref.png")
            ref_png = os.environ.get("SPIKE_REF_PNG")
            ref_url_env = os.environ.get("SPIKE_REF_URL")
            if ref_png and ref_url_env:
                shutil.copy(ref_png, cref_out)
                ref_url = ref_url_env
                log(f"[ref] reusing provided character_ref {ref_png}")
            else:
                ref_url = seams.gen_character_ref(prompt or "主讲人物", cref_out)
                log(f"[ref] character_ref -> {cref_out} (url={ref_url[:40]})")
            ctx["ref_url"] = ref_url

        # Doc mode (Phase 6, highest priority): cursor-read seeded doc_refs into ctx.
        doc_refs = ctx.get("doc_refs")
        if doc_refs:
            # Snapshot dir: prefer cfg attribute (Task 6); else plugin default under
            # the book's snapshot subdir (MF-2 — snapshot lives one level down at
            # syllabus/source/<book>/, NOT syllabus/source/).
            _book = (getattr(cfg, "book_series", "") or "daniels-uesca")
            snap_dir = Path(
                getattr(cfg, "syllabus_source_dir", None)
                or (PLUGIN_ROOT / "syllabus" / "source" / _book)
            )
            try:
                doc_map = extract_mod.extract_doc_sections(snap_dir, doc_refs)
            except extract_mod.ExtractError as exc:
                raise RuntimeError(
                    f"[extract] doc 模式找不到材料快照 (snap_dir={snap_dir}): {exc}. "
                    f"检查 config.syllabus_source_dir / book_series，或确认快照已落 "
                    f"video-studio/syllabus/source/<book>/"
                ) from exc
            ctx["page_text"] = doc_map  # str-keyed for doc mode
            fulltext = "\n".join(doc_map.values())

            # Append normalized VDOT pace table so 忠实门 can ground pace citations
            # (verifier M5 — without this, any pace-citing beat is false-halted).
            vdot_json_path = snap_dir / "vdot_pace_data.json"
            if vdot_json_path.exists():
                with open(vdot_json_path, encoding="utf-8") as _f:
                    vdot_data = json.load(_f)
                # Carry the authoritative pace data so step_chart can build chart
                # beats from it (doc mode has no hand-authored chart_title/rows).
                ctx["vdot_data"] = vdot_data
                pace_text = extract_mod.render_pace_lines(vdot_data)
                fulltext = fulltext + "\n" + pace_text
                log(f"[extract] doc mode: appended vdot pace table ({len(pace_text)} chars)")

            # Load zone_boundary_data.json so step_chart can render zone charts.
            zone_json_path = snap_dir / "zone_boundary_data.json"
            if zone_json_path.exists():
                with open(zone_json_path, encoding="utf-8") as _f:
                    ctx["zone_data"] = json.load(_f)
                log(f"[extract] doc mode: loaded zone_boundary_data.json")

            ctx["fulltext"] = fulltext
            # Carry source + verify_status from cursor-read into ctx (no-op if absent).
            # (They were already seeded by step_cursor_read; explicit passthrough for clarity.)
            ctx.setdefault("source", ctx.get("source"))
            ctx.setdefault("verify_status", ctx.get("verify_status"))
            log(
                f"[extract] doc mode: {len(doc_refs)} doc(s) → {len(fulltext)} chars "
                f"(source={ctx.get('source')!r})"
            )
            # series character_ref (Phase 6: syllabus.character_ref → ctx via cursor-read)
            _ensure_ref_url(ctx.get("character_ref"))
            return

        # PDF mode: VIDEO_PDF + VIDEO_PAGES set → pdftotext -layout per page.
        # Sets page_text + fulltext (NOT beats — the `script` step produces them).
        pdf = os.environ.get("VIDEO_PDF")
        pages_env = os.environ.get("VIDEO_PAGES")
        if pdf and pages_env:
            pages = [int(p) for p in pages_env.replace(",", " ").split()]
            page_text = extract_mod.extract_pages(pdf, pages)
            ctx["page_text"] = page_text
            ctx["fulltext"] = extract_mod.join_fulltext(page_text)
            log(f"[extract] PDF -layout: pages {pages} -> {len(ctx['fulltext'])} chars")
            _ensure_ref_url(getattr(cfg, "character_ref", ""))
            return
        # Fixture mode (Phase 2, unchanged): load beats + ref + grade + storyboard.
        with open(fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        beats = fixture["beats"]
        ctx["fixture"] = fixture
        ctx["beats"] = beats
        # fixture's character_ref is a dict(.prompt); doc/PDF pass a str (str-vs-dict
        # shape handled at the call site). grade_vf already set at step_extract top.
        _ensure_ref_url(fixture.get("character_ref", {}).get("prompt", "主讲人物"))
        # ctx["sb"] is set by the 分镜 director step (wiring.make_storyboard_dispatch).

    # ---- 4: tts (all beats; drives the timeline) ----
    def step_tts(step, ctx, scratch):
        beats = ctx["beats"]
        scratch_s = str(scratch)
        mp3s, dur_s_list, srt_beats, start_ms = steps_mod.step_tts(
            beats, scratch_s,
            synth_fn=seams.synth, probe_fn=probe_ms, run_ff_fn=run_ff,
        )
        sb = ctx.get("sb")
        if sb is not None:
            for i, sbeat in enumerate(srt_beats):
                sbeat["keywords"] = sb[i].get("keywords", [])
        ctx["mp3s"] = mp3s
        ctx["dur_s_list"] = dur_s_list
        ctx["srt_beats"] = srt_beats
        ctx["start_ms"] = start_ms
        ctx["clip_len_by_index"] = {
            i: steps_mod.compute_clip_len(d) for i, d in enumerate(dur_s_list)
        }
        ctx["clips_by_index"] = {}
        ctx["audio_dir"] = _sentinel(Path(scratch), "audio_dir", "\n".join(mp3s))
        log(f"[tts] {len(mp3s)} beats, start_ms after body = {start_ms}")

    # ---- 5: gen-still (still / multi beats → clips via storyboard shots) ----
    def step_gen_still(step, ctx, scratch):
        beats = ctx["beats"]
        sb = ctx.get("sb")
        ref_url = ctx["ref_url"]
        grade_vf = ctx["grade_vf"]
        scratch_s = str(scratch)
        n = 0
        for i, beat in enumerate(beats):
            if beat["visual_type"] not in ("still", "multi"):
                continue
            bid = beat["id"]
            if sb is not None:
                shots = sb[i]["shots"]
            elif beat["visual_type"] == "multi":
                shots = [{"prompt": p, "with_character": True} for p in beat["shots"]]
            else:
                shots = [{"prompt": beat["image_prompt"], "with_character": True}]
            clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
            steps_mod.step_render_shots_clip(
                shots, ctx["clip_len_by_index"][i], clip, bid, scratch_s,
                ref_url, grade_vf,
                gen_still_fn=seams.gen_still, run_ff_fn=run_ff,
            )
            ctx["clips_by_index"][i] = clip
            n += 1
        ctx["stills_dir"] = _sentinel(Path(scratch), "stills_dir", f"{n} still beats")
        log(f"[gen-still] rendered {n} still/multi beats")

    # ---- 6: gen-video (S2V beats; reuse cached clip, never default-on) ----
    def step_gen_video(step, ctx, scratch):
        beats = ctx["beats"]
        grade_vf = ctx["grade_vf"]
        ref_url = ctx["ref_url"]
        scratch_s = str(scratch)
        reuse_clip = os.environ.get("SPIKE_S2V_CLIP")
        n = 0
        for i, beat in enumerate(beats):
            if beat["visual_type"] != "video":
                continue
            bid = beat["id"]
            clip_len = ctx["clip_len_by_index"][i]
            vmp4 = os.path.join(scratch_s, f"{bid}.mp4")
            clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
            if reuse_clip:
                if os.path.abspath(reuse_clip) != os.path.abspath(vmp4):
                    shutil.copy(reuse_clip, vmp4)
                log(f"[gen-video] {bid} reusing S2V clip {reuse_clip}")
            else:
                res = seams.gen_video(beat["video_prompt"], ref_url, vmp4)
                if not res.get("ok"):
                    fb = beat.get("image_prompt", beat["text"])
                    steps_mod.step_render_shots_clip(
                        [{"prompt": fb, "with_character": True}],
                        clip_len, clip, bid, scratch_s, ref_url, grade_vf,
                        gen_still_fn=seams.gen_still, run_ff_fn=run_ff,
                    )
                    ctx["clips_by_index"][i] = clip
                    log(f"[gen-video] {bid} S2V fallback=still ({res.get('reason')})")
                    n += 1
                    continue
                log(f"[gen-video] {bid} S2V ok -> {vmp4}")
            run_ff(
                ffcmd.video_clip_cmd(vmp4, clip_len, clip, grade_vf,
                                     probe_ms(vmp4) / 1000.0),
                f"clip-{bid}",
            )
            ctx["clips_by_index"][i] = clip
            n += 1
        ctx["clips_dir"] = _sentinel(Path(scratch), "clips_dir", f"{n} video beats")
        log(f"[gen-video] rendered {n} S2V beats")

    # ---- 7: chart (matplotlib code-render; no AI, no grade) ----
    def step_chart(step, ctx, scratch):
        beats = ctx["beats"]
        scratch_s = str(scratch)
        n = 0
        for i, beat in enumerate(beats):
            if beat["visual_type"] != "chart":
                continue
            bid = beat["id"]
            png = os.path.join(scratch_s, f"{bid}.png")
            # Resolve rendering descriptor via spec-driven dispatch or fixture.
            # Returns: (title, rows) | ("__line__", params) | None (degrade to still)
            descriptor = _chart_content_for_beat(beat, ctx)
            if descriptor is None:
                # Unknown/unsupported chart type (DP-002=B) — degrade to a still clip
                # IN-PLACE. _chart_content_for_beat already logged the warning. step 5
                # (gen_still) only handles still/multi, so this chart beat has NO clip
                # yet; rendering a fallback still here + backfilling clips_by_index is
                # mandatory, else step_assemble raises "beats with no clip" AFTER paid
                # S2V/TTS. Mirrors the S2V→still fallback in step_gen_video.
                fb = beat.get("image_prompt", beat.get("text", "")) or "跑步场景"
                clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
                steps_mod.step_render_shots_clip(
                    [{"prompt": fb, "with_character": False}],
                    ctx["clip_len_by_index"][i], clip, bid, scratch_s,
                    ctx["ref_url"], ctx["grade_vf"],
                    gen_still_fn=seams.gen_still, run_ff_fn=run_ff,
                )
                ctx["clips_by_index"][i] = clip
                log(f"[chart] {bid}: unknown chart type → fallback still clip {clip}")
                n += 1
                continue
            if isinstance(descriptor, tuple) and descriptor[0] == "__line__":
                # Line/curve chart — render via build_line_chart from inline params.
                params = descriptor[1] or {}
                title = params.get("title", "")
                x_label = params.get("x_label", params.get("x", ""))
                y_label = params.get("y_label", params.get("y", ""))
                pts = params.get("points", [])
                log(f"[chart] {bid}: line chart → {title!r} ({len(pts)} points)")
                media_chart.build_line_chart(title, x_label, y_label, pts, png)
            else:
                # Table chart (vdot_pace / zone_boundary / fixture).
                chart_title, chart_rows = descriptor
                log(f"[chart] {bid}: resolved → {chart_title!r}")
                media_chart.render_pace_table(
                    chart_title, [tuple(r) for r in chart_rows], png)
            clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
            steps_mod.step_chart_clip(
                png, ctx["clip_len_by_index"][i], clip, run_ff_fn=run_ff)
            ctx["clips_by_index"][i] = clip
            n += 1
        ctx["charts_dir"] = _sentinel(Path(scratch), "charts_dir", f"{n} chart beats")
        log(f"[chart] rendered {n} chart beats (code, no AI)")

    # ---- 8: subtitle (ASS for burn + SRT deliverable) ----
    def step_subtitle(step, ctx, scratch):
        srt_beats = ctx["srt_beats"]
        ass_path = os.path.join(str(scratch), "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(srt_beats))
        ctx["ass_path"] = ass_path
        ep = ctx["ep"]
        ep["subtitles"].write_text(build_srt(srt_beats), encoding="utf-8")
        ctx["subtitles_srt"] = str(ep["subtitles"])
        n_kw = sum(len(b.get("keywords") or []) for b in srt_beats)
        log(f"[subtitle] subs.ass ({n_kw} emphasis kw) + subtitles.srt")

    # ---- 9: assemble (cards + xfade + mux + ASS-burn + BGM ducking → final) ----
    def step_assemble(step, ctx, scratch):
        beats = ctx["beats"]
        fixture = ctx.get("fixture", {})
        grade_vf = ctx["grade_vf"]
        scratch_s = str(scratch)
        ep = ctx["ep"]
        clips_by_index = ctx["clips_by_index"]
        missing = [i for i in range(len(beats)) if i not in clips_by_index]
        if missing:
            raise RuntimeError(f"assemble: beats with no clip: {missing}")
        beat_clips = [clips_by_index[i] for i in range(len(beats))]
        beat_durs = [ctx["clip_len_by_index"][i] for i in range(len(beats))]
        beat_mp3s = ctx["mp3s"]

        # ---- cards: AI/reused bg + code design layer ----
        # Discriminator extracted to _card_configs_for for unit-testability (NH-2 + S1-A5):
        # branches on fixture presence, not topic presence; passes authored digest
        # fields (title_subtitle / end_card_bullets) to the builders.
        tcfg, ecfg = _card_configs_for(ctx)

        def _card_bg(hook: str, prompt: str, out: str) -> str:
            reuse = os.environ.get(hook)
            if reuse:
                if os.path.abspath(reuse) != os.path.abspath(out):
                    shutil.copy(reuse, out)
                log(f"[card] reusing bg {reuse}")
                return out
            actual = seams.gen_still(prompt, None, out)
            log(f"[card] gen bg -> {actual}")
            return actual

        title_bg = _card_bg("SPIKE_CARD_TITLE_BG", tcfg["bg_prompt"],
                            os.path.join(scratch_s, "card_title_bg.png"))
        end_bg = _card_bg("SPIKE_CARD_END_BG", ecfg["bg_prompt"],
                          os.path.join(scratch_s, "card_end_bg.png"))

        title_ass = os.path.join(scratch_s, "card_title.ass")
        Path(title_ass).write_text(
            cards.build_title_ass(tcfg["tag"], tcfg["title"], tcfg["subtitle"]),
            encoding="utf-8")
        end_ass = os.path.join(scratch_s, "card_end.ass")
        Path(end_ass).write_text(
            cards.build_end_ass(ecfg["tag"], ecfg["bullets"], ecfg["footer"]),
            encoding="utf-8")

        title_clip = os.path.join(scratch_s, "clip_title.mp4")
        end_clip = os.path.join(scratch_s, "clip_end.mp4")
        title_len = cards.TITLE_DUR + ffcmd.DISSOLVE_FADE
        end_len = cards.END_DUR + ffcmd.DISSOLVE_FADE
        run_ff(ffcmd.still_card_cmd(title_bg, title_len, title_ass, title_clip, grade_vf), "card-title")
        run_ff(ffcmd.still_card_cmd(end_bg, end_len, end_ass, end_clip, grade_vf), "card-end")

        sil_title = os.path.join(scratch_s, "card_sil_title.mp3")
        sil_end = os.path.join(scratch_s, "card_sil_end.mp3")
        run_ff(ffcmd.silence_mp3_cmd(cards.TITLE_DUR, sil_title), "card-sil-title")
        run_ff(ffcmd.silence_mp3_cmd(cards.END_DUR, sil_end), "card-sil-end")

        vid_clips, clip_durs, mp3s = steps_mod.insert_cards(
            beat_clips, beat_durs, beat_mp3s,
            title_clip, title_len, sil_title, end_clip, end_len, sil_end)

        total_audio_ms = steps_mod.total_audio_ms(ctx["start_ms"])
        ctx["total_audio_ms"] = total_audio_ms

        # ---- assemble base.mp4: xfade video + hard-concat narration ----
        a_list = os.path.join(scratch_s, "narration.txt")
        with open(a_list, "w") as f:
            for m in mp3s:
                f.write(f"file '{m}'\n")
        narration = os.path.join(scratch_s, "narration.m4a")
        run_ff(ffcmd.concat_audio_cmd(a_list, narration), "narration-concat")

        video_track = os.path.join(scratch_s, "video.mp4")
        run_ff(ffcmd.xfade_chain_cmd(vid_clips, clip_durs, video_track), "xfade")

        base_mp4 = os.path.join(scratch_s, "base.mp4")
        run_ff(ffcmd.mux_av_cmd(video_track, narration, base_mp4), "mux-base")
        log(f"[assemble] {len(vid_clips)} clips x-faded, {total_audio_ms}ms narration")

        # ---- BGM + ducking calibration → final.mp4 (EPISODE dir) ----
        # spike_acceptance.measure_gap lives at the plugin root (Phase 7 retires
        # the spike; this is the one remaining production→spike coupling).
        from lib.ffprobe_util import measure_gap
        assets_dir = ep["assets_dir"]
        bgm = ensure_bgm(assets_dir, run_ff)
        final_mp4 = str(ep["final"])
        result = steps_mod.step_bgm_calib(
            base_mp4, ctx["ass_path"], bgm, final_mp4,
            initial_volume=BGM_START_VOLUME, max_rounds=MAX_CALIB_ROUNDS,
            target_db=cfg.audio.bgm_duck_db,
            measure_gap_fn=measure_gap, run_ff_fn=run_ff,
        )
        ctx["calib"] = result
        ctx["bgm"] = bgm
        ctx["final_mp4"] = final_mp4
        (ep["final"].parent / "calib.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        (ep["final"].parent / "meta.json").write_text(
            json.dumps({"total_audio_ms": total_audio_ms,
                        "beats": [{"id": b["id"], "type": b["visual_type"]}
                                  for b in beats]}, indent=2), encoding="utf-8")
        log(f"[assemble] final.mp4 -> {final_mp4} "
            f"(bgm gap {result['final_gap']:.1f}dB, {result['rounds']} rounds)")

    # ---- 10: provenance (stub; Phase 4 wires PDF page mapping) ----
    def step_provenance(step, ctx, scratch):
        ep = ctx["ep"]
        page_text = ctx.get("page_text")
        if page_text:
            # Phase 3: each objective assertion → grounding PDF page (UX-004).
            records = build_provenance(ctx["beats"], page_text)
            # MF-1: stamp the episode's source/verify_status (from cursor-read) onto
            # records — digest-writer beats don't carry them, so without this the doc-
            # mode provenance ships empty attribution (DP-602/603 dead on real path).
            stamp_episode_provenance(
                records, ctx.get("source", ""), ctx.get("verify_status", ""))
            prov = {
                "seq": seq, "slug": slug,
                # Episode-level attribution (UX-004): source + verify_status are
                # episode constants from cursor-read, stamped identically onto
                # every assertion above — surface them here too so the deliverable
                # is attributable at the episode level, not only per-assertion.
                "source": ctx.get("source", ""),
                "verify_status": ctx.get("verify_status", ""),
                "pages": sorted(page_text),
                "assertions": records,
            }
            n_page = sum(1 for r in records if r["page"] is not None)
            log(f"[provenance] {n_page}/{len(records)} assertions → page (UX-004)")
        else:
            # Phase 2 (fixture render): no source PDF — visual-type stub.
            prov = {
                "seq": seq, "slug": slug,
                "provider": cfg.media.provider,
                "total_audio_ms": ctx.get("total_audio_ms"),
                "calib": ctx.get("calib"),
                "beats": [{"id": b["id"], "visual_type": b["visual_type"]}
                          for b in ctx["beats"]],
                "note": "Phase-2 stub provenance (no source PDF).",
            }
            log(f"[provenance] {ep['provenance']}")
        ep["provenance"].write_text(json.dumps(prov, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    # ---- 11: publish (syllabus mode: mark done + advance cursor; Phase 7 wires upload) ----
    def step_publish(step, ctx, scratch):
        if dry:
            log("[publish] dry mode — skipping mark-done + cursor advance")
            return

        if not (getattr(cfg, "syllabus_path", "") and getattr(cfg, "cursor_path", "") and selection is not None):
            # Phase-2 / no-syllabus mode — preserve placeholder behaviour
            log("[publish] placeholder (Phase 7 wires real publish)")
            return

        # Verify final.mp4 exists before advancing cursor
        final_mp4_path = ctx.get("final_mp4", "")
        if not final_mp4_path or not Path(final_mp4_path).exists():
            raise FileNotFoundError(
                f"[publish] final.mp4 not found at {final_mp4_path!r}; "
                "cursor NOT advanced"
            )

        from lib.syllabus import (
            load_syllabus, load_cursor, save_syllabus, save_cursor,
            advance as syllabus_advance, Cursor,
        )

        syllabus = load_syllabus(Path(cfg.syllabus_path))
        cursor = load_cursor(Path(cfg.cursor_path))

        # Locate the episode for this selection by its global 0-based index (from seq)
        target_global = int(selection.seq) - 1
        target_mod_idx: int | None = None
        target_ep_idx: int | None = None
        gidx = 0
        for m_i, module in enumerate(syllabus.modules):
            for e_i in range(len(module.episodes)):
                if gidx == target_global:
                    target_mod_idx = m_i
                    target_ep_idx = e_i
                gidx += 1

        if target_mod_idx is None:
            raise ValueError(
                f"[publish] Episode seq={selection.seq!r} not found in syllabus"
            )

        # Idempotency: if already done, do nothing
        target_episode = syllabus.modules[target_mod_idx].episodes[target_ep_idx]
        if target_episode.status == "done":
            log(f"[publish] Episode seq={selection.seq} already done — skipping advance (idempotent)")
            return

        # Build a temporary cursor pointing at the target episode, then advance
        temp_cursor = Cursor(
            book=cursor.book,
            module_idx=target_mod_idx,
            episode_idx=target_ep_idx,
        )
        new_syllabus, new_cursor = syllabus_advance(syllabus, temp_cursor)

        save_syllabus(new_syllabus, Path(cfg.syllabus_path))
        save_cursor(new_cursor, Path(cfg.cursor_path))
        log(
            f"[publish] seq={selection.seq} marked done; "
            f"cursor → module={new_cursor.module_idx} ep={new_cursor.episode_idx}"
        )

    # ---- 12: cleanup (runner calls cleanup_scratch on clean return) ----
    def step_cleanup(step, ctx, scratch):
        log("[cleanup] deliverables in episode dir; runner clears scratch")

    return {
        "config": step_config,
        "cursor-read": step_cursor_read,
        "scratch": step_scratch,
        "extract": step_extract,
        "tts": step_tts,
        "gen-still": step_gen_still,
        "gen-video": step_gen_video,
        "chart": step_chart,
        "subtitle": step_subtitle,
        "assemble": step_assemble,
        "provenance": step_provenance,
        "publish": step_publish,
        "cleanup": step_cleanup,
    }


# ---------------------------------------------------------------------------
# prepare_render — assemble everything run_pipeline needs (no run_pipeline call)
# ---------------------------------------------------------------------------

def _beats_to_md(beats: list[dict]) -> str:
    """Render digest-writer beats into a reviewable script.md."""
    lines = ["# 脚本（digest-writer）\n"]
    for b in beats:
        lines.append(f"## {b.get('beat_id', '')}\n")
        lines.append(b.get("text", "") + "\n")
        anchor = b.get("anchor")
        if anchor:
            lines.append(f"> 依据: {anchor}\n")
    return "\n".join(lines)


def make_script_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
) -> Callable:
    """The `script` agent step's dispatch closure — host of the 忠实门.

    Runner contract: an agent step routes through this `dispatch(agent, step,
    ctx, scratch_dir)` (NOT step_fns), and it has ctx access (a stock gate fn
    does not — runner.py:73). retry=1: on a faithfulness miss this writes NO
    artifact → check_artifact gate fails → runner re-runs THIS step (re-
    dispatches the writer); a second miss halts (failed_step="script").

    - Fixture / reuse (beats already in ctx) → pass-through, no live dispatch.
    - PDF mode → dispatch digest-writer (claude -p --model sonnet) against the
      SAME ctx["fulltext"] the gate checks, then verify_anchors. Grounded →
      write the script_ledger artifact + script.md; flagged → no artifact.
    """
    def dispatch(agent, step, ctx, scratch_dir):
        if agent != "digest-writer":
            raise RuntimeError(f"unexpected agent step: {agent!r}")
        ledger_art = os.path.join(str(scratch_dir), "script_ledger.json")

        # Fixture / reuse pass-through.
        if ctx.get("beats"):
            with open(ledger_art, "w", encoding="utf-8") as f:
                json.dump({"beats": ctx["beats"]}, f, ensure_ascii=False)
            ctx["script_ledger"] = ledger_art
            log("[script] fixture pass-through (beats already present)")
            return

        # PDF mode: hand the writer the literal fulltext (same object the gate
        # checks), dispatch, then run the 忠实门.
        fulltext = ctx.get("fulltext", "")
        src = os.path.join(str(scratch_dir), "source_fulltext.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write(fulltext)
        # MF-1: tell the writer the episode's source + verify_status so its DP-602
        # "非源直出" disclosure rule has a trigger (⚠️ engineering-estimate content).
        _src_label = ctx.get("source") or ""
        _vstatus = ctx.get("verify_status") or ""
        _disclosure = ""
        if "⚠️" in _vstatus or "🔧" in _vstatus:
            _disclosure = (
                "本期内容含工程估计/产品设定（非源直出）：念稿时明说「这是工程估计、"
                "不是源直出」，不把它当科学定论冒充。"
            )
        user_prompt = (
            f"教材原文（只依据这段原文，不外检索）在: {src}\n"
            f"本期主源: {_src_label or '教材原文'}；溯源状态: {_vstatus or '已溯源'}。{_disclosure}\n"
            f"读它，写一期结构化跑步知识脚本 beats。"
        )
        res = dispatch_persona_fn(
            "digest-writer", user_prompt, scratch_dir, "digest_beats.json",
            plugin_root=plugin_root, model="sonnet",
        )
        if not res.get("ok"):
            log(f"[script] dispatch failed → no artifact (retry): {res.get('reason')}")
            return
        # LLM JSON is not guaranteed well-formed (e.g. unescaped inner quotes).
        # A bad parse is a FAILED ATTEMPT (→ retry), never a pipeline crash.
        try:
            with open(res["artifact_path"], encoding="utf-8") as f:
                beats = json.load(f).get("beats", [])
            # Schema gate: empty beats / missing / empty anchor must NOT clear the
            # 忠实门 (verify_anchors skips past them) — recompute, never trust the
            # persona. A schema miss is a FAILED ATTEMPT (→ retry), like bad JSON.
            validate_ledger({"beats": beats})
        except (json.JSONDecodeError, OSError, AttributeError, ValueError, LedgerError) as exc:
            log(f"[script] digest output invalid (bad JSON / empty / unsourced) → retry: {exc}")
            return

        verdict = verify_anchors({"beats": beats}, fulltext)
        if not verdict["ok"]:
            ctx["_faithfulness_flagged"] = verdict["flagged"]
            log(f"[script] 忠实门 FLAGGED {len(verdict['flagged'])} beat(s) "
                f"→ no artifact → retry/halt")
            return

        # Normalize the render id: the 忠实门/ledger schema uses `beat_id`
        # (digest-writer emits it); fixture beats + every render step (tts /
        # gen_still / gen_video / chart / provenance) read `id`. Bridge the two
        # schemas here (mechanical, index-fallback) so doc-mode beats render.
        # (Phase 7: doc-mode render had never run e2e — this surfaced after the
        # ref_url/grade_vf fix as a KeyError 'id' in step_tts.)
        for _i, _b in enumerate(beats):
            _b.setdefault("id", _b.get("beat_id") or f"b{_i:02d}")

        ctx["beats"] = beats
        with open(ledger_art, "w", encoding="utf-8") as f:
            json.dump({"beats": beats}, f, ensure_ascii=False)
        ctx["script_ledger"] = ledger_art

        # Read optional authored display-content fields from the digest JSON.
        # The digest-writer may emit these as top-level fields in the JSON file.
        try:
            with open(res["artifact_path"], encoding="utf-8") as _f:
                _digest_full = json.load(_f)
            _bullets = _digest_full.get("end_card_bullets")
            if isinstance(_bullets, list) and _bullets:
                ctx["end_card_bullets"] = _bullets
                log(f"[script] end_card_bullets authored ({len(_bullets)} bullets)")
            _subtitle = _digest_full.get("title_subtitle")
            if isinstance(_subtitle, str) and _subtitle.strip():
                ctx["title_subtitle"] = _subtitle.strip()
                log(f"[script] title_subtitle authored: {_subtitle[:40]!r}")
        except (json.JSONDecodeError, OSError, KeyError):
            pass  # authored fields are optional; any read error → ctx stays empty

        ep = ctx.get("ep")
        if ep is not None:
            ep["script"].write_text(_beats_to_md(beats), encoding="utf-8")
        log(f"[script] {len(beats)} beats grounded → 忠实门 PASS")

    return dispatch


def apply_director_color_grade(ctx: dict, beats: list) -> None:
    """Override ctx['grade_vf'] with the director's episode-level color_grade.

    Called after back-prop in make_storyboard_dispatch.  If the director assigned
    a color_grade on the first beat (DP-001=A), resolve it and override the series
    default already stored in ctx['grade_vf'].  No-op when absent.
    """
    cg = beats[0].get("color_grade") if beats else None
    if cg:
        ctx["grade_vf"] = grade.resolve_color_grade(cg)


def make_storyboard_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
) -> Callable:
    """The `分镜` agent step's dispatch closure — host of the 视频预算门.

    Runner contract: an agent step routes through this `dispatch(agent, step,
    ctx, scratch_dir)` (NOT step_fns), and it has ctx access (a stock gate fn
    does not — runner.py:73). retry=1: on bad JSON this writes NO artifact →
    check_artifact gate fails → runner re-runs THIS step (re-dispatches director);
    a second miss halts (failed_step="分镜").

    Three modes:
    - SPIKE_STORYBOARD_JSON set → reuse cached sb (zero live dispatch).
    - beats already carry visual_type (fixture mode) → pass-through (zero live dispatch).
    - beats lack visual_type (live digest) → dispatch director + parse + budget + back-prop.
    """
    def dispatch(agent, step, ctx, scratch_dir):
        if agent != "director":
            raise RuntimeError(f"unexpected agent step: {agent!r}")

        beats = ctx.get("beats", [])
        ep = ctx.get("ep", {})
        shotlist_path = ep.get("shotlist")

        def _write_shotlist_art(data, path=None):
            """Write JSON to shotlist deliverable and return the file path."""
            dest = str(path) if path is not None else os.path.join(str(scratch_dir), "shotlist.json")
            Path(dest).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return dest

        # --- (a2) SPIKE_STORYBOARD_JSON: explicit reuse ---
        reuse_sb = os.environ.get("SPIKE_STORYBOARD_JSON")
        if reuse_sb:
            with open(reuse_sb, encoding="utf-8") as f:
                sb = json.load(f)
            if len(sb) != len(beats):
                raise RuntimeError(
                    f"reused storyboard {reuse_sb} has {len(sb)} entries but "
                    f"beats has {len(beats)} entries"
                )
            log(f"[storyboard] reusing {reuse_sb} "
                f"({sum(len(e.get('shots', [])) for e in sb)} shots / {len(beats)} beats)")
            ctx["sb"] = sb
            if shotlist_path is not None:
                shutil.copy(reuse_sb, str(shotlist_path))
                ctx["shotlist_art"] = str(shotlist_path)
            else:
                art = os.path.join(str(scratch_dir), "shotlist.json")
                shutil.copy(reuse_sb, art)
                ctx["shotlist_art"] = art
            return

        # --- (a) Fixture pass-through: beats already carry visual_type ---
        if beats and all("visual_type" in b for b in beats):
            log("[storyboard] fixture pass-through (beats already have visual_type)")
            # Write a minimal shotlist reflecting the existing beats' visual_type
            # (not overwriting any beat's visual_type — render uses beat-level prompts).
            minimal_sb = [
                {
                    "visual_type": b["visual_type"],
                    "shots": [{"prompt": b.get("image_prompt") or b.get("text", ""),
                               "shot_type": "", "with_character": True, "pace": "normal"}],
                    "keywords": [],
                }
                for b in beats
            ]
            ctx["shotlist_art"] = _write_shotlist_art(minimal_sb, shotlist_path)
            # ctx["sb"] stays as-is (usually None); render uses beat-level prompts.
            return

        # --- (b) Live: beats lack visual_type (digest-writer output) ---
        beats_listing = "\n".join(
            f"{i + 1}. {b.get('text', '')}" for i, b in enumerate(beats)
        )
        user_prompt = (
            f"请为以下 {len(beats)} 段旁白逐段输出分镜方案（JSON数组），"
            f"长度必须等于旁白段数({len(beats)})。\n\n{beats_listing}"
        )
        res = dispatch_persona_fn(
            "director", user_prompt, scratch_dir, "shotlist.json",
            plugin_root=plugin_root, model="sonnet",
        )
        if not res.get("ok"):
            log(f"[storyboard] dispatch failed → no artifact (retry): {res.get('reason')}")
            return

        try:
            with open(res["artifact_path"], encoding="utf-8") as f:
                raw = f.read()
        except OSError as exc:
            log(f"[storyboard] could not read artifact → retry: {exc}")
            return

        result = parse_shotlist(raw, len(beats))
        if isinstance(result, str):
            log(f"[storyboard] parse_shotlist failed → no artifact (retry): {result}")
            return

        # Apply video budget (threshold gate + loose hard cap)
        result = apply_video_budget(
            result,
            cfg.budget.video_shots_per_episode,
            threshold=cfg.budget.video_motion_threshold,
        )

        # Back-propagate visual_type to ctx["beats"]. For video beats also carry
        # a video_prompt (gen_video reads beat["video_prompt"]) from the director's
        # shot — doc-mode beats don't hand-author it like fixtures do.
        # For chart beats also carry the director's chart spec (type + params).
        for i, item in enumerate(result):
            ctx["beats"][i]["visual_type"] = item["visual_type"]
            if item["visual_type"] == "video":
                _shots = item.get("shots") or []
                if _shots:
                    ctx["beats"][i].setdefault("video_prompt", _shots[0].get("prompt", ""))
            if item.get("chart"):
                ctx["beats"][i]["chart"] = item["chart"]

        # Copy episode-level color_grade from director's first beat (DP-001=A).
        if result and result[0].get("color_grade"):
            ctx["beats"][0]["color_grade"] = result[0]["color_grade"]
        apply_director_color_grade(ctx, ctx["beats"])

        # Build ctx["sb"] (shots + keywords, 1:1 with beats)
        ctx["sb"] = [{"shots": item["shots"], "keywords": item["keywords"]}
                     for item in result]

        # Write shotlist deliverable and set the artifact key
        ctx["shotlist_art"] = _write_shotlist_art(result, shotlist_path)

        n_video = sum(1 for b in ctx["beats"] if b.get("visual_type") == "video")
        log(
            f"[storyboard] {len(beats)} beats → {n_video} video "
            f"(cap={cfg.budget.video_shots_per_episode})"
        )

    return dispatch


def prepare_render(
    cfg: VideoStudioConfig,
    *,
    fixture_path: Path,
    seq: str,
    slug: str,
    dry: bool,
    log: Callable[[str], None],
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    selection: Any = None,
) -> dict[str, Any]:
    """Build the {step_fns, gates, dispatch, scratch_dir} bundle the caller
    passes straight into `run_pipeline`. The caller owns the run_pipeline call
    (keeps this module off the runner's import path → no cycle).
    """
    run_ff, probe_ms = make_ff_seams(cfg)
    seams = MediaSeams(dry=dry, provider=cfg.media.provider, log=log)
    step_fns = build_step_fns(
        cfg, seams=seams, fixture_path=fixture_path, seq=seq, slug=slug,
        run_ff=run_ff, probe_ms=probe_ms, log=log, selection=selection, dry=dry,
    )
    gates = {"check_artifact": episode_mod.check_artifact}

    script_dispatch = make_script_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log)
    storyboard_dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log)

    def dispatch(agent, step, ctx, scratch_dir):
        if agent == "digest-writer":
            return script_dispatch(agent, step, ctx, scratch_dir)
        elif agent == "director":
            return storyboard_dispatch(agent, step, ctx, scratch_dir)
        else:
            raise RuntimeError(f"unknown agent for dispatch: {agent!r}")

    scratch = episode_mod.make_scratch(cfg.output_dir, tag=f"{seq}-{slug}")
    return {
        "step_fns": step_fns,
        "gates": gates,
        "dispatch": dispatch,
        "scratch_dir": scratch,
    }
