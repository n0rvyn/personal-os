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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# video-studio plugin root (parent of lib/) — needed for the .spike cache and
# the spike_acceptance import (Phase 7 retires this coupling with the spike).
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib import cards, coveredground, ffcmd, grade, media_chart, media_tts
from lib import dispatch as dispatch_mod
from lib import episode as episode_mod
from lib import extract as extract_mod
from lib import shotplan as shotplan_mod
from lib import steps as steps_mod
from lib.config import HostConfig, VideoStudioConfig
from lib.media_source import GenerateSource, MediaRouter
from lib.budget import apply_video_budget, apply_video_floor
from lib.ledger import LedgerError, validate_ledger, verify_anchors, verify_style
from lib.provenance import build_provenance, stamp_episode_provenance
from lib.query_filter import sanitize_query
from lib import render_stamp
from lib.visual_ledger import VisualLedger, sig as visual_sig, _done_episode_dirs
from lib.storyboard import parse_shotlist
from lib.subtitle import build_ass, build_srt

SPIKE = PLUGIN_ROOT / ".spike"

BGM_START_VOLUME = 0.12
MAX_CALIB_ROUNDS = 3
# ffmpeg per-call timeout. The product targets 6–10 min videos; the final xfade chain
# and the BGM-calibration muxes re-encode the WHOLE video, which at 1080p takes well
# over the old 300s on a full-length episode (that cap failed the ep005 assembly). Sized
# to comfortably cover a 10-min full-length encode with margin; per-clip encodes finish
# in seconds and never approach it.
FFMPEG_TIMEOUT_S = 1200

# digest-writer's `claude -p` budget. dispatch_persona defaults to 600s, which the
# pilot burned through three times over — every attempt died on the clock, not on a
# gate, and the truncated draft it left behind was indistinguishable from a real
# length-gate rejection. One attempt = read a ~26 KB chapter, write ~3000 characters
# of narration carrying a verbatim source anchor per beat, then self-check it.
WRITER_TIMEOUT_S = 1500
# ⑧ (2026-07-03): a chart held fully static for a whole long-narration beat reads as
# frozen (ep002 had one chart on screen ~50s). Cap the chart's on-screen time; the rest
# of the beat's narration plays over a content-relevant still (no camera on the chart —
# user's call). Only splits when the beat is meaningfully longer than the cap.
CHART_MAX_SECONDS = 12.0


def _beat_shots(beat: dict) -> list[dict]:
    """Fixture/no-director-mode shot list for one still/multi beat, in the
    same shot-dict shape director-sourced shots already carry (prompt +
    with_character) so `shotplan.plan_windows` can treat both sources
    uniformly. No regression path for `step_shotplan` when ctx has no "sb"
    (director not run)."""
    if beat["visual_type"] == "multi":
        return [{"prompt": p, "with_character": True} for p in beat.get("shots", [])]
    return [{"prompt": beat.get("image_prompt", ""), "with_character": True}]


# ---------------------------------------------------------------------------
# Character reference resolution (cross-episode persistence via base64)
# ---------------------------------------------------------------------------

# Image magic-byte signatures — used to reject a corrupt/non-image file stored as
# character_ref before we base64-encode it as an image-to-image reference.
_IMAGE_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"BM": "bmp",
}


def _sniff_image_format(data: bytes) -> str | None:
    """Return the image format ('png'/'jpeg'/...) from magic bytes, or None if the
    bytes are not a recognized image (empty / corrupt / non-image file)."""
    if not data:
        return None
    for magic, fmt in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            return fmt
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _looks_like_path(s: str) -> str:
    """True if *s* is meant as a file path (not a natural-language description).
    A stored character_ref path has a slash / ~ / a known image extension; a
    prompt description ("一位动漫女教练…") has none of these."""
    s = s.strip()
    if s.startswith(("/", "~", "./", "../")) or ("/" in s) or ("\\" in s):
        return True
    return s.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


def resolve_character_ref_url(
    character_ref: str,
    cref_out: str,
    gen_character_ref_fn: Callable[[str, str], str],
    log: Callable[[str], None],
    provider: str = "doubao",
) -> str:
    """Resolve the image-to-image reference URL for the episode's主讲人物.

    Priority (fixes the character_ref path-vs-prompt contract + 跨集固定):
      1. SPIKE_REF_PNG + SPIKE_REF_URL env → dev reuse hook (highest).
      2. ``character_ref`` is an existing, readable IMAGE file:
         - provider == "doubao" → return a base64 ``data:image/...`` URI (Doubao/Ark
           accepts base64 as image-to-image ref; verified live for BOTH Seedream
           (image) and Seedance (S2V video)), so the SAME png locks the face every
           episode — no external hosting needed. gen is NOT called.
         - other provider (e.g. MiniMax) → its subject_reference wants a REMOTE URL,
           not a data URI, so base64 reuse is unsupported: log a warning and fall
           back to gen_character_ref (returns a real URL; loses exact-face reuse but
           never silently ships a broken ref). Durable cross-provider reuse needs a
           hosted URL — deferred.
      3. ``character_ref`` is a description → use it as the generation prompt.
         Empty, or a path that doesn't exist / is corrupt → generate with the default
         prompt "主讲人物" (never feed a raw path string in as a prompt; never crash).
    """
    import base64

    ref_png = os.environ.get("SPIKE_REF_PNG")
    ref_url_env = os.environ.get("SPIKE_REF_URL")
    if ref_png and ref_url_env:
        try:
            shutil.copy(ref_png, cref_out)
        except OSError:
            pass
        log(f"[ref] reusing SPIKE hook {ref_png}")
        return ref_url_env

    cref = character_ref.strip() if isinstance(character_ref, str) else ""
    if cref:
        p = os.path.expanduser(cref)
        if os.path.isfile(p):
            # A stored image path. Only Doubao accepts a base64 data-URI ref.
            if provider != "doubao":
                log(f"[ref] 持久人物图 base64 复用仅 doubao 支持，provider={provider} 回退生成"
                    f"（失去精确同脸；跨 provider 复用需托管 URL）")
                return gen_character_ref_fn("主讲人物", cref_out)
            try:
                data = Path(p).read_bytes()
                fmt = _sniff_image_format(data)
                if fmt is None:
                    raise ValueError("not a decodable image")
                data_uri = f"data:image/{fmt};base64," + base64.b64encode(data).decode("ascii")
                try:
                    shutil.copy(p, cref_out)
                except OSError:
                    pass
                log(f"[ref] reusing persistent character_ref {p} (base64 {fmt}, {len(data)} bytes)")
                return data_uri
            except (OSError, ValueError) as exc:
                # Path exists but unreadable/corrupt → graceful fallback to gen.
                log(f"[ref] persistent character_ref 不可读，回退生成: {exc}")
                return gen_character_ref_fn("主讲人物", cref_out)
        if _looks_like_path(cref):
            # Meant as a path but the file is missing → do NOT use the path string
            # as a generation prompt (garbage); fall back to the default prompt.
            log(f"[ref] character_ref 路径不存在，回退默认 prompt: {cref}")
            return gen_character_ref_fn("主讲人物", cref_out)
        # Natural-language description → use it as the generation prompt.
        return gen_character_ref_fn(cref, cref_out)
    # Empty → default prompt.
    return gen_character_ref_fn("主讲人物", cref_out)


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

    def run_ff(argv: list[str], label: str, timeout: int = FFMPEG_TIMEOUT_S) -> None:
        cmd = ff_prefix + argv
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
                 style_prefix: str = "", voice_id: str = ""):
        self.dry = dry
        self.provider = provider
        self.log = log
        self.style_prefix = style_prefix
        self.voice_id = voice_id   # host TTS voice from cfg.audio.voice_id (empty → media_tts default)
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
        # Config-driven host voice (cfg.audio.voice_id). Empty → media_tts default.
        return media_tts.synth(text, out_path, voice_id=self.voice_id or None)


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
        elif t == "bar":
            return ("__bar__", params)
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
            channel=ctx.get("channel", ""),
            module_name=ctx.get("module_name", ""),
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
    router: Any = None,
    fixture_path: Path,
    seq: str,
    slug: str,
    run_ff: Callable,
    probe_ms: Callable,
    log: Callable[[str], None],
    selection: Any = None,
    dry: bool = False,
    resume: bool = False,
    episode_target: bool = False,
    script_only: bool = False,
) -> dict[str, Callable]:
    """Return {step_name: fn(step, ctx, scratch_dir)} for the Phase-2 pipeline.

    Contract (mirrors test_runner.py): each step writes its real artifacts into
    `scratch_dir` (intermediates) or the episode dir (deliverables), then sets
    ctx[step['artifact']] to a non-empty FILE path so check_artifact passes.
    Heavy data (beats/srt_beats/clip map/start_ms) flows through ctx under
    untracked keys. The video ({seq}-{slug}.mp4) + subtitles.srt + shotlist.json
    + provenance.json land in the EPISODE dir (survives the runner's end-of-run
    cleanup_scratch).
    """
    # router is a deterministic function of (seams, cfg); callers that don't
    # route (most tests) get a transparent default (library-less → generate),
    # so it need not be a required kwarg.
    if router is None:
        router = MediaRouter(GenerateSource(seams), cfg=cfg, log=log)

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
            # R2: brand tag inputs — module name (from selection) + channel (from
            # configured host). _card_configs_for reads these for the title tag.
            ctx["module_name"] = getattr(selection, "module_name", "")
            _host = getattr(cfg, "host", None)
            ctx["channel"] = getattr(_host, "channel", "") if _host is not None else ""
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
        ctx["_resume"] = resume
        ctx["scratch_dir"] = _sentinel(Path(scratch), "scratch_dir", str(ep_dir))
        log(f"[scratch] episode dir -> {ep_dir}")

    # ---- 3: extract — 3-way dispatch: doc_refs > VIDEO_PDF > fixture ----
    def step_extract(step, ctx, scratch):
        # Resolve art style and color grade independently (two orthogonal dims).
        # Set seams.style_prefix + ctx["grade_vf"] unconditionally so doc / PDF / fixture
        # paths all provision them. Late-bind happens here (before _ensure_ref_url)
        # since gen_character_ref is called from within step_extract itself.
        _art_style = (selection.art_style if selection is not None else "") or ""
        # Resolve the style NAME once: empty module art_style → plugin default
        # (DEFAULT_ART_STYLE=写实电影). style_prefix already maps ''→default; the
        # library routing (set_style) and per-style host ref (character_refs)
        # MUST use the same resolved NAME, else an empty-art_style episode (all
        # of 季1) silently bypasses its 写实电影 library(Pexels)+host bindings and
        # falls to paid generation. (Explicit styles like 简笔日漫 are unchanged.)
        _style_name = _art_style or grade.DEFAULT_ART_STYLE
        _color_grade = (selection.color_grade if selection is not None else "") or ""
        seams.style_prefix = grade.resolve_art_style(_art_style)
        router.set_style(_style_name)
        # Store the resolved art_style so the shotlist stamp (_shotlist_config_fields
        # reads ctx["art_style"], design §Pillar A) actually invalidates on an
        # art_style change — previously the field was unpopulated (always "").
        ctx["art_style"] = _art_style
        ctx["grade_vf"] = grade.resolve_color_grade(_color_grade or None)
        log(f"[grade] art_style={_art_style!r}→{_style_name!r} color_grade={_color_grade!r}")

        def _ensure_ref_url(prompt):
            # Resolve the主讲人物 image-to-image ref. `prompt` is the character_ref
            # value (may be a stored image PATH → base64 reuse for 跨集同一张脸, a
            # DESCRIPTION → generate, or empty → default). SPIKE_REF hook still wins.
            # See resolve_character_ref_url for the full priority + corrupt-file
            # fallback. This replaces the old within-run-only ephemeral-URL path.
            #
            # A script-only run stops before any media step, so nothing will ever
            # read ref_url — and resolving it is not free: a missing/unreadable
            # character_ref file falls through to gen_character_ref, a PAID image
            # generation. Skip it outright rather than rely on the ref happening
            # to be on disk.
            if script_only:
                log("[ref] script-only：跳过人物参考图解析（不生成、不计费）")
                return
            ep = ctx["ep"]
            cref_out = str(ep["assets_dir"] / "character_ref.png")
            ctx["ref_url"] = resolve_character_ref_url(
                prompt or "", cref_out, seams.gen_character_ref, log,
                provider=getattr(seams, "provider", "doubao"),
            )

        # Phase 5 (DP-P5-002): per-style character ref takes priority over the
        # existing per-module/series character_ref. `ps` is None when this
        # art_style has no binding in cfg.character_refs — each mode below
        # falls back to its own pre-existing source (per-mode, NOT a unified
        # formula — the fixture mode's fallback keeps its '.prompt' tail /
        # "主讲人物" default; MR#3).
        ps = getattr(cfg, "character_refs", {}).get(_style_name)

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
            # Phase 5: per-style ref (ps) takes priority (DP-P5-002).
            _ensure_ref_url(ps or ctx.get("character_ref"))
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
            # Phase 5: per-style ref (ps) takes priority (DP-P5-002).
            _ensure_ref_url(ps or getattr(cfg, "character_ref", ""))
            return
        # Fixture mode (Phase 2, unchanged): load beats + ref + grade + storyboard.
        with open(fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        beats = fixture["beats"]
        ctx["fixture"] = fixture
        ctx["beats"] = beats
        # fixture's character_ref is a dict(.prompt); doc/PDF pass a str (str-vs-dict
        # shape handled at the call site). grade_vf already set at step_extract top.
        # Phase 5: per-style ref (ps) takes priority (DP-P5-002); fallback keeps
        # the fixture's own '.prompt' tail / "主讲人物" default (MR#3).
        _ensure_ref_url(ps or fixture.get("character_ref", {}).get("prompt", "主讲人物"))
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

    # ---- 4b: 镜头计划 (Phase-1 shot-timeline) — post-tts, pre-gen-still.
    # Turns each still/multi beat's real clip_len + director shots into N
    # distinct content windows (lib.shotplan.plan_windows). Pure code, no
    # gate. Writes ctx["windows"][i] = list[window dict] for consumption by
    # step_gen_still below. video/chart beats are untouched here (video →
    # Task 4; chart keeps its existing CHART_MAX windowing).
    def step_shotplan(step, ctx, scratch):
        beats = ctx["beats"]
        sb = ctx.get("sb")
        ep_dir = ctx["ep"]["final"].parent
        ledger = VisualLedger(
            ep_dir,
            prior_dirs=_done_episode_dirs(cfg, ctx),
            load_own=not ctx.get("_resume", False),
        )
        windows: dict[int, list] = {}
        for i, beat in enumerate(beats):
            if beat["visual_type"] not in ("still", "multi"):
                continue
            if sb is not None:
                director_shots = sb[i]["shots"]
                keywords = sb[i].get("keywords", [])
            else:
                director_shots = _beat_shots(beat)
                keywords = beat.get("keywords", [])
            clip_len = ctx["clip_len_by_index"][i]
            beat_windows = shotplan_mod.plan_windows(
                beat, clip_len, director_shots, keywords, ledger,
            )
            for w in beat_windows:
                ledger.record(visual_sig(w["prompt"]))
            windows[i] = beat_windows
        ledger.save()
        ctx["windows"] = windows
        # Keep the ledger in ctx so step_gen_video can window a video beat's
        # relay tail (rem = clip_len - s2v_dur) against the same dedup set —
        # rem is only known after S2V is probed (post-shotplan), so the relay
        # windowing must happen there, not here.
        ctx["visual_ledger"] = ledger
        log(f"[镜头计划] windowed {len(windows)} still/multi beats")

    # ---- 5: gen-still (still / multi beats → clips via N shot-plan windows) ----
    def step_gen_still(step, ctx, scratch):
        beats = ctx["beats"]
        windows = ctx["windows"]
        ref_url = ctx["ref_url"]
        grade_vf = ctx["grade_vf"]
        scratch_s = str(scratch)
        n = 0
        for i, beat in enumerate(beats):
            if beat["visual_type"] not in ("still", "multi"):
                continue
            bid = beat["id"]
            shots = windows[i]
            durs = [w["dur"] for w in shots]
            clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
            ledger = ctx.get("visual_ledger")
            host_on = getattr(cfg, "host_enabled", True)
            fetch_lib_clip_fn = lambda w, d, o: (
                None
                if (host_on and w.get("with_character"))
                else router.fetch_library_clip(w, d, o, run_ff, grade_vf, ledger)
            )
            steps_mod.step_render_shots_clip(
                shots, ctx["clip_len_by_index"][i], clip, bid, scratch_s,
                ref_url, grade_vf,
                gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                durs=durs,
                fetch_lib_clip_fn=fetch_lib_clip_fn,
            )
            ctx["clips_by_index"][i] = clip
            n += 1
        # MF-1: persist Pexels ids recorded during fetch_clip (via
        # fetch_lib_clip_fn) so cross-episode dedup works. step_gen_still is the
        # ONLY save site for a pure still/multi 写实 episode (no S2V/chart save
        # runs); without this the on-disk ledger keeps "ids":[] and the next
        # episode re-picks the same clips.
        _ledger = ctx.get("visual_ledger")
        if _ledger is not None:
            _ledger.save()
        ctx["stills_dir"] = _sentinel(Path(scratch), "stills_dir", f"{n} still beats")
        log(f"[gen-still] rendered {n} still/multi beats")

    # ---- 6: gen-video (S2V beats; reuse cached clip, never default-on) ----
    #
    # Task 4 "once + relay" contract: the S2V source plays ONCE, capped at
    # lib.shotplan.WINDOW_MAX — never looped/tpad-cloned to fill the whole
    # beat (the root of the POC-era hold/loop bugs). The beat's remaining
    # duration (rem = clip_len - s2v_dur) is covered by relay still window(s)
    # (interim prompt = the beat's own image_prompt via _beat_shots), rendered
    # via step_render_shots_clip and hard-concatenated with the S2V window
    # into the single beat clip. ctx["clips_by_index"][i] stays a str path
    # (contract unchanged).
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
                res = router.gen_video(beat["video_prompt"], ref_url, vmp4)
                if not res.get("ok"):
                    # S2V failed → fall back to a WINDOWED still sequence over the
                    # whole beat (same as still/multi + the relay), NOT one image
                    # held for the full clip_len (that reintroduces the ⑤ hold on
                    # the failure path — the cached-S2V reuse branch never
                    # exercises this, so it slips past structural renders).
                    sb = ctx.get("sb")
                    if sb is not None:
                        fb_shots = sb[i]["shots"]
                        fb_keywords = sb[i].get("keywords", [])
                    else:
                        fb_shots = _beat_shots(beat)
                        fb_keywords = beat.get("keywords", [])
                    ledger = ctx.get("visual_ledger")
                    fb_windows = shotplan_mod.plan_windows(
                        beat, clip_len, fb_shots, fb_keywords, ledger,
                    )
                    if ledger is not None:
                        for w in fb_windows:
                            ledger.record(visual_sig(w["prompt"]))
                        ledger.save()
                    steps_mod.step_render_shots_clip(
                        fb_windows, clip_len, clip, bid, scratch_s, ref_url,
                        grade_vf, gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                        durs=[w["dur"] for w in fb_windows],
                    )
                    ctx["clips_by_index"][i] = clip
                    log(f"[gen-video] {bid} S2V fallback=windowed-still ({res.get('reason')})")
                    n += 1
                    continue
                log(f"[gen-video] {bid} S2V ok -> {vmp4}")

            src_dur = probe_ms(vmp4) / 1000.0
            s2v_dur = min(src_dur, shotplan_mod.WINDOW_MAX)
            s2v_sub = os.path.join(scratch_s, f"clip_{i}_{bid}_s2v.mp4")
            run_ff(
                ffcmd.video_clip_cmd(vmp4, clip_len, s2v_sub, grade_vf, src_dur),
                f"clip-{bid}",
            )
            rem = clip_len - s2v_dur
            if rem > 1e-6:
                relay_sub = os.path.join(scratch_s, f"clip_{i}_{bid}_relay.mp4")
                # Window the relay tail into N≈rem/TARGET_WINDOW distinct content
                # windows (same as still/multi beats) instead of one still held
                # for the whole remainder — otherwise a long video beat re-holds
                # a single image after the S2V window (the ⑤ hold, relay edition).
                sb = ctx.get("sb")
                if sb is not None:
                    r_shots = sb[i]["shots"]
                    r_keywords = sb[i].get("keywords", [])
                else:
                    r_shots = _beat_shots(beat)
                    r_keywords = beat.get("keywords", [])
                ledger = ctx.get("visual_ledger")
                relay_windows = shotplan_mod.plan_windows(
                    beat, rem, r_shots, r_keywords, ledger,
                )
                if ledger is not None:
                    for w in relay_windows:
                        ledger.record(visual_sig(w["prompt"]))
                    ledger.save()
                steps_mod.step_render_shots_clip(
                    relay_windows, rem, relay_sub, bid, scratch_s,
                    ref_url, grade_vf,
                    gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                    durs=[w["dur"] for w in relay_windows],
                )
                listf = os.path.join(scratch_s, f"{bid}_beat_concat.txt")
                with open(listf, "w") as f:
                    f.write(f"file '{s2v_sub}'\n")
                    f.write(f"file '{relay_sub}'\n")
                run_ff(ffcmd.concat_cmd(listf, clip), f"{bid}-beat-concat")
            else:
                shutil.copy(s2v_sub, clip)
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
                # Windowed degrade (not one held still over the whole beat) — same
                # rule as still/multi + the S2V fallback.
                _clen = ctx["clip_len_by_index"][i]
                fb_windows = shotplan_mod.plan_windows(
                    beat, _clen,
                    [{"prompt": fb, "with_character": False}],
                    beat.get("keywords", []), ctx.get("visual_ledger"),
                )
                _ledger = ctx.get("visual_ledger")
                if _ledger is not None:
                    for w in fb_windows:
                        _ledger.record(visual_sig(w["prompt"]))
                    _ledger.save()
                steps_mod.step_render_shots_clip(
                    fb_windows, _clen, clip, bid, scratch_s,
                    ctx["ref_url"], ctx["grade_vf"],
                    gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                    durs=[w["dur"] for w in fb_windows],
                )
                ctx["clips_by_index"][i] = clip
                log(f"[chart] {bid}: unknown chart type → windowed fallback still {clip}")
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
            elif isinstance(descriptor, tuple) and descriptor[0] == "__bar__":
                # Bar chart (0-baseline) — discrete/equivalence comparison. params is
                # a dict (storyboard-parsed): access via .get(), NOT attribute access.
                params = descriptor[1] or {}
                title = params.get("title", "")
                x_label = params.get("x_label", params.get("x", ""))
                y_label = params.get("y_label", params.get("y", ""))
                cats = params.get("categories", [])
                vals = params.get("values", [])
                log(f"[chart] {bid}: bar chart → {title!r} ({len(cats)} bars)")
                media_chart.build_bar_chart(title, x_label, y_label, cats, vals, png)
            else:
                # Table chart (vdot_pace / zone_boundary / fixture).
                chart_title, chart_rows = descriptor
                log(f"[chart] {bid}: resolved → {chart_title!r}")
                media_chart.render_pace_table(
                    chart_title, [tuple(r) for r in chart_rows], png)
            clip = os.path.join(scratch_s, f"clip_{i}_{bid}.mp4")
            clip_len = ctx["clip_len_by_index"][i]
            if clip_len > CHART_MAX_SECONDS + 1.5:
                # ⑧ Long-narration chart: hold the chart for CHART_MAX_SECONDS, then cut
                # to a content-relevant still for the remainder (keeps audio synced).
                chart_part = os.path.join(scratch_s, f"clip_{i}_{bid}_chart.mp4")
                steps_mod.step_chart_clip(png, CHART_MAX_SECONDS, chart_part, run_ff_fn=run_ff)
                fu_prompt = (
                    beat.get("chart_followup_prompt")
                    or beat.get("image_prompt")
                    or "跑者在真实环境中从容奔跑的动态空镜"
                )
                after_part = os.path.join(scratch_s, f"clip_{i}_{bid}_after.mp4")
                # ⑧ "其余窗口切内容": window the post-chart remainder into distinct
                # ~5s content windows, NOT one still held for (clip_len - 12s)
                # — a long chart beat otherwise re-holds a single image after the
                # 12s chart (the ⑤ hold, chart-followup edition).
                fu_rem = clip_len - CHART_MAX_SECONDS
                fu_windows = shotplan_mod.plan_windows(
                    beat, fu_rem,
                    [{"prompt": fu_prompt, "with_character": False}],
                    beat.get("keywords", []), ctx.get("visual_ledger"),
                )
                _ledger = ctx.get("visual_ledger")
                if _ledger is not None:
                    for w in fu_windows:
                        _ledger.record(visual_sig(w["prompt"]))
                    _ledger.save()
                steps_mod.step_render_shots_clip(
                    fu_windows, fu_rem, after_part, bid, scratch_s,
                    ctx["ref_url"], ctx["grade_vf"],
                    gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                    durs=[w["dur"] for w in fu_windows],
                )
                listf = os.path.join(scratch_s, f"clip_{i}_{bid}_list.txt")
                with open(listf, "w") as lf:
                    lf.write(f"file '{chart_part}'\nfile '{after_part}'\n")
                run_ff(ffcmd.concat_cmd(listf, clip), f"{bid}-chart-concat")
                log(f"[chart] {bid}: narration {clip_len:.0f}s → chart {CHART_MAX_SECONDS:.0f}s "
                    f"+ 相关静图 {clip_len - CHART_MAX_SECONDS:.0f}s")
            else:
                steps_mod.step_chart_clip(png, clip_len, clip, run_ff_fn=run_ff)
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
            # ③ lock the presenter: pass the same character ref the shots use so the
            # title/end card shows the actual host (not a generic runner). ref set
            # upstream by _ensure_ref_url; None-safe fallback keeps old no-lock behavior.
            card_ref = ctx.get("ref_url")
            try:
                actual = seams.gen_still(prompt, card_ref, out)
            except Exception as exc:  # noqa: BLE001 — a provider error (account overdue /
                # content moderation / transient) on the card bg must NOT crash a fully
                # rendered episode at the final assemble step. Fall back to the same neutral
                # brand-color placeholder the per-shot gen-still path uses (steps.py:281-286)
                # so the card still renders and the episode still produces final.mp4.
                run_ff(
                    ["-f", "lavfi", "-i", "color=c=0x1B2A38:s=1920x1080",
                     "-frames:v", "1", out],
                    "card-bg-placeholder",
                )
                log(f"[card] gen bg 失败({exc}) — 用中性占位图兜底，不中断整期")
                return out
            log(f"[card] gen bg -> {actual} (ref={'locked' if card_ref else 'none'})")
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
        # Title card is STATIC (intensity=0.0): its background is a portrait of
        # the host, and a full push-in (the default intensity=1.0) pans the face/
        # body so the figure appears to "drift" inside the right panel — jarring.
        # User: 起始画面不运镜. End card keeps the gentle default push-in.
        run_ff(ffcmd.still_card_cmd(title_bg, title_len, title_ass, title_clip, grade_vf, intensity=0.0), "card-title")
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

        # Task 5 (D-008): assert video_track >= narration; pad the video's
        # tail (never loop) if the xfade chain came up short — `-shortest` in
        # mux_av_cmd must only ever trim the padded track's tiny overshoot,
        # never silently truncate the narration.
        video_track_dur = probe_ms(video_track) / 1000.0
        narration_dur = probe_ms(narration) / 1000.0
        pad_target = ffcmd.assert_video_ge_audio(video_track_dur, narration_dur)
        video_padded = pad_target is not None
        if video_padded:
            padded_track = os.path.join(scratch_s, "video_padded.mp4")
            run_ff(ffcmd.pad_video_to_cmd(video_track, pad_target, padded_track), "pad-video-tail")
            log(f"[assemble] video_track short ({video_track_dur:.2f}s < narration "
                f"{narration_dur:.2f}s) — padded tail to {pad_target:.2f}s")
            video_track = padded_track

        base_mp4 = os.path.join(scratch_s, "base.mp4")
        run_ff(ffcmd.mux_av_cmd(video_track, narration, base_mp4), "mux-base")
        log(f"[assemble] {len(vid_clips)} clips x-faded, {total_audio_ms}ms narration")

        # ---- BGM + ducking calibration → output video (EPISODE dir) ----
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
                        "video_track_dur": video_track_dur,
                        "narration_dur": narration_dur,
                        "video_padded": video_padded,
                        "beats": [{"id": b["id"], "type": b["visual_type"]}
                                  for b in beats]}, indent=2), encoding="utf-8")
        log(f"[assemble] video -> {final_mp4} "
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

        if episode_target:
            log("[publish] --episode 定向重渲 — cursor/status 不变")
            return

        if not (getattr(cfg, "syllabus_path", "") and getattr(cfg, "cursor_path", "") and selection is not None):
            # Phase-2 / no-syllabus mode — preserve placeholder behaviour
            log("[publish] placeholder (Phase 7 wires real publish)")
            return

        # Verify the output video exists before advancing cursor
        final_mp4_path = ctx.get("final_mp4", "")
        if not final_mp4_path or not Path(final_mp4_path).exists():
            raise FileNotFoundError(
                f"[publish] output video not found at {final_mp4_path!r}; "
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

    # ---- avoid-memo (B5): read the cross-episode covered-ground store and seed
    # ctx['avoid_memo'] with the avoidance brief BEFORE the writer runs. Fail-
    # soft: any store error → memo='' (the run must never halt on the memo).
    # int(ctx['seq']) — seq is a zero-padded STRING (syllabus.derive_seq_slug),
    # is_stale/render_memo want an int.
    def _legacy_avoid() -> str:
        """The back catalogue's already-used apparatus — a permanent avoid list.

        Kept separate from the covered-ground store on purpose. That store decays
        (an anchor goes cold once it is older than `recent_n` episodes), which is
        correct for de-duplicating WITHIN a season and wrong for this: "do not
        re-use an analogy from the 100 episodes already published" does not expire
        after 20 episodes. Fail-soft — an unreadable file must not halt the run
        (config already fail-closed on a path that doesn't exist).
        """
        path = getattr(cfg, "legacy_avoid_path", "") or ""
        if not path:
            return ""
        try:
            return Path(os.path.expanduser(path)).read_text(encoding="utf-8").strip()
        except OSError as exc:
            log(f"[avoid-memo] legacy avoid list unreadable (fail-soft): {exc}")
            return ""

    def step_avoid_memo(step, ctx, scratch):
        legacy = _legacy_avoid()

        # No seq = fixture / no-cursor render: no episode context, so no
        # covered-ground memo. The legacy list still applies — it is not
        # seq-relative.
        seq = ctx.get("seq")
        if seq is None:
            ctx["avoid_memo"] = legacy
            log("[avoid-memo] no seq (fixture/no-cursor) — legacy list only"
                if legacy else "[avoid-memo] no seq — empty avoid-memo")
            return
        try:
            recent = coveredground.render_memo(
                coveredground.load_store(cfg.output_dir), int(seq)
            )
        except Exception:  # noqa: BLE001 — fail-soft: never halt on the memo
            recent = ""

        ctx["avoid_memo"] = "\n\n".join(p for p in (legacy, recent) if p)
        log(f"[avoid-memo] legacy={'yes' if legacy else 'no'} "
            f"covered-ground={'yes' if recent else 'no hot anchors'}")

    # ---- covered-update (B6): fold this episode's distilled covered.json into
    # the cross-episode store, so the NEXT episode's avoid-memo sees it.
    # update_covered_ground is fully fail-soft; the closure only computes the
    # covered.json path (from the distiller's ctx['covered_art'], else the
    # episode dir) + date/seq. Wrapped defensively so a missing ep/seq can't
    # bubble either.
    def step_covered_update(step, ctx, scratch):
        # No seq = fixture / no-cursor render: no episode to attribute anchors
        # to. Skip explicitly so the fail-soft try/except only ever swallows a
        # GENUINE update failure (not the expected no-seq case).
        seq = ctx.get("seq")
        if seq is None:
            log("[covered-update] no seq (fixture/no-cursor) — skip")
            return
        try:
            covered_json = ctx.get("covered_art")
            if not covered_json:
                ep = ctx.get("ep")
                if ep is None:
                    log("[covered-update] no ep / covered_art — nothing to merge")
                    return
                covered_json = str(ep["final"].parent / "covered.json")
            date = ctx.get("date") or datetime.now().strftime("%Y-%m-%d")
            coveredground.update_covered_ground(
                cfg.output_dir, covered_json, date, int(seq)
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft; never halt the run
            log(f"[covered-update] swallowed error (fail-soft): {exc}")
            return

    return {
        "config": step_config,
        "cursor-read": step_cursor_read,
        "scratch": step_scratch,
        "extract": step_extract,
        "avoid-memo": step_avoid_memo,
        "covered-update": step_covered_update,
        "tts": step_tts,
        "镜头计划": step_shotplan,
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

def script_chars(beats: list[dict]) -> int:
    """Length of the narration, counted the way the length gate counts it.

    Every character of every beat's `text` — the exact string TTS speaks, so the
    count maps to runtime (~4-5 chars/sec of Mandarin). `anchor` is excluded: it
    is the source citation, not narration, and never reaches the audio.
    """
    return sum(len((b.get("text") or "")) for b in beats if isinstance(b, dict))


def _retry_brief(ctx: dict) -> str:
    """Turn the previous attempt's gate failures into a corrective brief.

    Every gate reports back, so a retry is a targeted fix rather than a re-roll.
    Empty string on the first attempt (nothing to report).
    """
    parts: list[str] = []

    schema_err = ctx.get("_schema_error")
    if schema_err:
        parts.append(
            "## 上一稿的 JSON 不合格（整期作废，必须修）\n\n"
            f"报错：`{schema_err}`\n\n"
            "常见原因：\n"
            "- **某个 beat 没写 `anchor`**（每一个 beat 都必须有；纯过渡段就摘该段引用的术语原词）\n"
            "- `text` 里用了英文直引号 `\"`，把 JSON 字符串提前截断了 → 改用中文「」\n"
            "- 输出带了 ```json 围栏或解释文字 → 只输出那一个 JSON，别的都不要\n"
        )

    flagged = ctx.get("_faithfulness_flagged") or []
    if flagged:
        lines = [
            "## 上一稿被忠实门拦下了（必须修，否则本期作废）\n",
            "以下 beat 的 anchor 在教材原文里找不到依据——要么数字是编的，"
            "要么 anchor 被你改写/概括了。anchor 必须是原文里**原样存在**的英文句子。\n",
        ]
        for f in flagged[:8]:
            lines.append(f"- `{f.get('beat_id', '?')}`：anchor = {f.get('anchor', '')!r}")
        lines.append(
            "\n改法：把 anchor 换成原文里真实存在的那一行原话；如果原文根本没有这个数据，"
            "**删掉这个说法**，不要换个数字硬凑。"
        )
        parts.append("\n".join(lines))

    style = ctx.get("_style_violations") or []
    if style:
        lines = ["## 上一稿违反了开场/独立成篇/引用腔规则（必须修）\n"]
        for v in style[:8]:
            if v.get("rule") == "冷开场":
                lines.append(
                    f"- `{v.get('beat_id')}`：**第一个 beat 出现了「{v.get('found')}」**。"
                    f"第一个 beat 必须是冷钩子——一个具体画面 / 一个反差事实 / 一个具体的人。"
                    f"问候和自我介绍挪到钩子之后。\n"
                    f"  你写的是：{v.get('excerpt', '')}…"
                )
            elif v.get("rule") == "引用腔":
                lines.append(
                    f"- `{v.get('beat_id')}`：出现了「{v.get('found')}」。"
                    f"全篇以「教材/原文」作主语的框架句超过两三句了——把知识内化成你自己要讲的话，"
                    f"直接讲结论和数据，不必每句都点出处。\n"
                    f"  上下文：…{v.get('excerpt', '')}…"
                )
            else:
                lines.append(
                    f"- `{v.get('beat_id')}`：出现了「{v.get('found')}」。每一期都是单独推给"
                    f"陌生人的，观众没看过上一期，也不知道有下一期——提到它们只会让人划走。删掉。\n"
                    f"  上下文：…{v.get('excerpt', '')}…"
                )
        parts.append("\n".join(lines))

    short = ctx.get("_length_short")
    if short:
        got, need = short
        parts.append(
            f"## 上一稿字数不够（{got} 字，下限 {need} 字，还差 {need - got} 字）\n\n"
            "补法只有一种：**加实料**——多讲一个教材里的具体例子/实验/人物，"
            "多把一个要点讲透（为什么、怎么用、常见错在哪），或补一个书里真有的相关角度。\n"
            "**禁止**：车轱辘话、重复上一句、空泛铺垫、把一句话抻长。宁可多讲一个真实要点。"
        )

    long_ = ctx.get("_length_long")
    if long_:
        got, cap = long_
        parts.append(
            f"## 上一稿字数超上限（{got} 字，上限 {cap} 字，多了 {got - cap} 字）\n\n"
            "砍法只有一种：**砍实料**——合并重复的实验/人物、删最弱的一个例子、把两个相邻 beat 合并成一个。"
            "**禁止**：直接砍末尾的 beat（破坏结尾领取物结构）；把长句拆短充数（不会减字）；删证据/数字让忠实门不通过。"
            "**目标**：把字数砍到 ≤ {cap}。宁可少一个真实要点，不可超过上限。"
        )

    if not parts:
        return ""

    # Observed on the pilot: attempt 1 greeted and was long enough; attempt 2 fixed
    # the opening and came back 759 characters short. The writer fixes the gate it
    # is told about and quietly drops one it already satisfied. So every retry brief
    # restates the full set — a fix is only a fix if it holds everything else.
    parts.append(
        "## 修的时候别把已经对的地方改回去\n\n"
        "下面几条是**同时成立**的硬约束，不是可以互相交换的：\n"
        "- 第一个 beat 是冷钩子（没有「大家好」「欢迎来到」「今天我们讲」）\n"
        "- 字数达到下限\n"
        "- 每个 beat 都有 anchor，且是教材原文原样摘抄\n"
        "- 全片不出现「上期」「下期」「我们下次见」\n\n"
        "只修被点名的那一条，其余原样保留。"
    )
    return "\n\n---\n\n" + "\n\n".join(parts) + "\n\n---\n\n"


def _beats_to_md(
    beats: list[dict],
    *,
    title: str = "",
    title_subtitle: str = "",
    end_card_bullets: list[str] | None = None,
) -> str:
    """Render the episode's deliverables into a reviewable script.md.

    The publish title and the end-card lines are part of what gets handed over,
    not just the narration — so they lead the file rather than living only inside
    beats.json where a human would never look for them.
    """
    lines: list[str] = [f"# {title}\n" if title else "# 脚本（digest-writer）\n"]

    meta: list[str] = []
    if title:
        meta.append(f"- **视频号标题**：{title}")
    if title_subtitle:
        meta.append(f"- **显示副标**：{title_subtitle}")
    if end_card_bullets:
        meta.append("- **尾卡文案**：")
        meta.extend(f"  {i}. {b}" for i, b in enumerate(end_card_bullets, 1))
    if meta:
        lines.append("\n".join(meta) + "\n")

    lines.append(f"> {len(beats)} beats · {script_chars(beats)} 字（配音正文）\n")
    lines.append("---\n")

    for b in beats:
        lines.append(f"## {b.get('beat_id', '')}\n")
        lines.append(b.get("text", "") + "\n")
        anchor = b.get("anchor")
        if anchor:
            lines.append(f"> 依据: {anchor}\n")
    return "\n".join(lines)


def _stampable(value: Any) -> Any:
    """Coerce a config/ctx field to a JSON-serializable stamp component.

    Test doubles (bare `MagicMock()` configs used by unrelated existing
    tests) auto-vivify unconfigured attributes as further MagicMock objects
    rather than raising AttributeError, so a plain `getattr(cfg, "x", "")`
    can still yield a non-JSON-serializable mock. Only primitives / None pass
    through unchanged; anything else degrades to "" (treated like an unset
    field — safe because a real VideoStudioConfig only ever carries
    primitives here).
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return ""


def _read_persona(plugin_root: Path, name: str) -> str:
    """Read an agent persona file's text (for stamp fingerprinting). Missing
    file → "" (treated as an empty/unstamped input, never a crash)."""
    path = Path(plugin_root) / "agents" / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _script_config_fields(cfg: VideoStudioConfig, ctx: dict) -> dict:
    """Config/ctx fields the beats.json stamp must cover (Task 2/4 § Pillar A).

    The length target is part of the stamp: without it, raising `target_chars`
    would leave every existing (shorter) beats.json stamp-valid, and the durable-
    reuse branch would silently hand back the old under-length script instead of
    re-dispatching the writer.
    """
    host = getattr(cfg, "host", None)
    script = getattr(cfg, "script", None)
    return {
        "host_name": _stampable(getattr(host, "name", "")) or "",
        "host_channel": _stampable(getattr(host, "channel", "")) or "",
        "host_tone": _stampable(getattr(host, "tone", "")) or "",
        "source": _stampable(ctx.get("source")) or "",
        "verify_status": _stampable(ctx.get("verify_status")) or "",
        "target_chars": _stampable(getattr(script, "target_chars", None)),
        "min_chars": _stampable(getattr(script, "min_chars", None)),
    }


def _shotlist_config_fields(cfg: VideoStudioConfig, ctx: dict) -> dict:
    """Config/ctx fields the shotlist.json stamp must cover (Task 2/4 § Pillar A)."""
    host = getattr(cfg, "host", None)
    budget = getattr(cfg, "budget", None)
    return {
        "host_gender": _stampable(getattr(host, "gender", "")) or "",
        "art_style": _stampable(ctx.get("art_style")) or "",
        "video_shots_per_episode": _stampable(getattr(budget, "video_shots_per_episode", None)),
        "video_motion_threshold": _stampable(getattr(budget, "video_motion_threshold", None)),
        "video_floor": _stampable(getattr(budget, "video_floor", None)),
    }


def make_script_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
    fresh: bool = False,
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

        # Durable-reuse: beats.json already on disk + stamp matches current
        # upstream inputs (fulltext / persona text / config fields) → load it,
        # skip the live digest-writer dispatch entirely (Task 4, A-layer).
        ep = ctx.get("ep")
        if not fresh and ep is not None and Path(ep["beats"]).exists():
            persona_text = _read_persona(plugin_root, "digest-writer")
            cfgfields = _script_config_fields(cfg, ctx)
            want = render_stamp.beats_stamp(ctx.get("fulltext", ""), persona_text, cfgfields)
            stamp_path = Path(str(ep["beats"]) + ".stamp")
            if render_stamp.stamp_matches(stamp_path, want):
                _data = json.loads(Path(ep["beats"]).read_text(encoding="utf-8"))
                beats = _data["beats"]
                ctx["beats"] = beats
                # MF#2: restore authored card fields the live path would have set
                # (persisted into beats.json above) so resumed cards match pass 1.
                if _data.get("title"):
                    ctx["title"] = _data["title"]
                if _data.get("title_subtitle"):
                    ctx["title_subtitle"] = _data["title_subtitle"]
                if _data.get("end_card_bullets"):
                    ctx["end_card_bullets"] = _data["end_card_bullets"]
                with open(ledger_art, "w", encoding="utf-8") as f:
                    json.dump({"beats": beats}, f, ensure_ascii=False)
                ctx["script_ledger"] = ledger_art
                log("[script] durable 复用 beats.json（stamp 匹配，跳过 digest-writer）")
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
        # R2: fixed host persona (configurable). When cfg.host.name is set, tell the
        # writer to open with a greeting-as-hook + sign off, in the given tone.
        _host = getattr(cfg, "host", None)
        _host_line = ""
        if _host is not None and getattr(_host, "name", ""):
            _ch = getattr(_host, "channel", "")
            _tone = getattr(_host, "tone", "")
            # The self-intro goes AFTER the hook, not before it. This line used to
            # order the opposite ("开场第一个 beat 用「大家好，我是X，欢迎来到Y」"),
            # which overrode the persona from the user prompt — the pilot opened with
            # the greeting despite the persona forbidding it. The channel's own data
            # settles it: viewers average 55.5 seconds, so a greeting spends the
            # product's opening on nothing.
            _intro = f"我是{_host.name}" + (f"，{_ch}" if _ch else "")
            _host_line = (
                f"本期主播：{_host.name}"
                + (f"（频道：{_ch}）" if _ch else "")
                + (f"，口吻基调：{_tone}" if _tone else "")
                + "。\n"
                "**开场硬规则**：第一个 beat 是冷钩子——一个具体画面 / 一个反差事实 / "
                "一个具体的人。**禁止**出现「大家好」「欢迎来到」「今天我们讲」。\n"
                f"自我介绍放在钩子之后（约第 2 个 beat 末尾），只用极短一句：「{_intro}。」"
                "然后立刻回到内容，不许展开寒暄。\n"
                "结尾用暖收束 + 一个可执行动作 + 一句升华收口；不要照搬开头的自我介绍，"
                "不要预告下期。整期贯穿该口吻。\n"
            )
        # Length target is config-driven. A cfg that carries no ScriptConfig (the
        # bare stand-ins the wiring tests inject) yields 0 → the gate is inert and
        # the prompt says nothing about length, i.e. exactly the pre-existing
        # behaviour. A real VideoStudioConfig always carries one (dataclass
        # default), so the production path is always gated.
        _sc = getattr(cfg, "script", None)

        def _cfg_int(name: str) -> int:
            v = getattr(_sc, name, None)
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        _target, _minc, _maxc = (
            _cfg_int("target_chars"), _cfg_int("min_chars"), _cfg_int("max_chars"),
        )
        user_prompt = (
            f"教材原文（只依据这段原文，不外检索）在: {src}\n"
            f"本期主源: {_src_label or '教材原文'}；溯源状态: {_vstatus or '已溯源'}。{_disclosure}\n"
            f"{_host_line}"
            f"读它，写一期结构化跑步知识脚本 beats。"
        )
        if _minc:
            user_prompt += (
                f"\n**本期字数：目标 {_target} 字，硬下限 {_minc} 字"
                + (f"，不要超过 {_maxc} 字" if _maxc else "")
                + "。**（按 beats 的 text 累计字符数算——就是配音要念的那些字。）"
                "不到下限会被字数门打回重写。字数靠加实料达到，绝不注水。"
            )
        # Retry feedback: on attempt 2+ the previous gate failures are named, so
        # the writer fixes THOSE rather than re-rolling the whole episode.
        user_prompt += _retry_brief(ctx)
        # B5: covered-ground avoidance section — recently-overused 类比/例子/钩子
        # the writer should avoid/replace this episode. DATA, not a fact source
        # (温度盾: only packaging, never opinions; the 忠实门 owns the numbers).
        # Seeded by the pre-script avoid-memo code step (step_avoid_memo).
        user_prompt += (
            "\n## 反复用过的类比/例子/钩子——本期避让(covered-ground)\n"
            f"{ctx.get('avoid_memo', '') or '(无 covered-ground 避让约束)'}\n"
        )

        # Clear last attempt's output first. A timed-out or crashed attempt leaves a
        # half-written digest_beats.json behind; if the NEXT `claude -p` exits 0
        # without rewriting it, that stale draft would sail through the gates as
        # though the writer had just produced it.
        _stale = Path(scratch_dir) / "digest_beats.json"
        if _stale.exists():
            try:
                _stale.unlink()
            except OSError:
                pass

        res = dispatch_persona_fn(
            "digest-writer", user_prompt, scratch_dir, "digest_beats.json",
            plugin_root=plugin_root, model="sonnet", timeout=WRITER_TIMEOUT_S,
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
            # Report it back. A schema miss used to retry blind: the writer was told
            # nothing, so it had no idea which beat was missing an anchor or where
            # its JSON broke. (Live: "beat 2 missing or empty 'anchor'".)
            ctx["_schema_error"] = str(exc)
            log(f"[script] digest output invalid (bad JSON / empty / unsourced) → retry: {exc}")
            return

        # Three gates, evaluated together so ONE retry can fix all of them.
        # Evaluating in series would spend an attempt per gate and halt an episode
        # that was a single draft away from passing everything.
        verdict = verify_anchors({"beats": beats}, fulltext)
        style = verify_style({"beats": beats})
        n_chars = script_chars(beats)
        too_short = bool(_minc) and n_chars < _minc
        too_long = bool(_maxc) and n_chars > _maxc

        # Clear last attempt's findings first: a stale flag would otherwise be
        # replayed into the NEXT retry's brief as though it were still failing.
        for k in ("_faithfulness_flagged", "_length_short", "_length_long",
                  "_style_violations", "_schema_error"):
            ctx.pop(k, None)

        if not verdict["ok"]:
            ctx["_faithfulness_flagged"] = verdict["flagged"]
        if too_short:
            ctx["_length_short"] = (n_chars, _minc)
        if too_long:
            ctx["_length_long"] = (n_chars, _maxc)
        if not style["ok"]:
            ctx["_style_violations"] = style["violations"]

        if not verdict["ok"] or too_short or too_long or not style["ok"]:
            why = []
            if not verdict["ok"]:
                why.append(f"忠实门拦下 {len(verdict['flagged'])} beat")
            if too_short:
                why.append(f"字数 {n_chars} < 下限 {_minc}")
            if too_long:
                why.append(f"字数 {n_chars} > 上限 {_maxc}")
            if not style["ok"]:
                why.append(f"开场/独立成篇/引用腔违规 {len(style['violations'])} 处")
            log(f"[script] {' + '.join(why)} → no artifact → retry/halt")
            return

        log(f"[script] {len(beats)} beats · {n_chars} 字（目标 {_target}，下限 {_minc}）· 三门通过")

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
            # The 视频号 publish title — the one line a viewer sees in the feed
            # before deciding to tap. Persisted like the other authored fields;
            # without this it would be written by the persona and then dropped.
            _title = _digest_full.get("title")
            if isinstance(_title, str) and _title.strip():
                ctx["title"] = _title.strip()
                log(f"[script] 视频号标题: {_title[:40]!r}")
        except (json.JSONDecodeError, OSError, KeyError):
            pass  # authored fields are optional; any read error → ctx stays empty

        if ep is not None:
            ep["script"].write_text(
                _beats_to_md(
                    beats,
                    title=ctx.get("title", ""),
                    title_subtitle=ctx.get("title_subtitle", ""),
                    end_card_bullets=ctx.get("end_card_bullets"),
                ),
                encoding="utf-8",
            )
            # MF#2: persist the authored card fields alongside beats so a durable
            # resume restores them (they otherwise live only in the scratch digest
            # JSON → resumed title/end cards would silently revert to defaults).
            Path(ep["beats"]).write_text(
                json.dumps({"beats": beats,
                            "title": ctx.get("title", ""),
                            "title_subtitle": ctx.get("title_subtitle", ""),
                            "end_card_bullets": ctx.get("end_card_bullets")},
                           ensure_ascii=False), encoding="utf-8"
            )
            persona_text = _read_persona(plugin_root, "digest-writer")
            cfgfields = _script_config_fields(cfg, ctx)
            want = render_stamp.beats_stamp(fulltext, persona_text, cfgfields)
            render_stamp.write_stamp(Path(str(ep["beats"]) + ".stamp"), want)
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


def _backprop_visual_fields(ctx: dict, items: list) -> None:
    """Mirror the director's per-beat visual fields onto ``ctx["beats"]``.

    The live storyboard path mutates each beat with visual_type / video_prompt /
    chart / episode color_grade so downstream steps (step_shotplan reads
    ``beat["visual_type"]``, step_gen_video reads ``beat["video_prompt"]``,
    step_chart reads ``beat["chart"]``) find what they need on doc-mode beats
    (which, unlike fixtures, don't hand-author these). The durable-reuse and
    SPIKE reuse branches MUST perform the SAME back-prop from the persisted
    shotlist ``items`` — otherwise a resumed run leaves beats without
    visual_type and step_shotplan raises ``KeyError: 'visual_type'`` (the exact
    doc-mode 崩溃续跑 path this feature exists for). Extracted here so the live
    path and both reuse branches stay in lockstep.
    """
    beats = ctx["beats"]
    for i, item in enumerate(items):
        if i >= len(beats):
            break
        if "visual_type" in item:
            beats[i]["visual_type"] = item["visual_type"]
        if item.get("visual_type") == "video":
            _shots = item.get("shots") or []
            _vp = item.get("video_prompt") or (_shots[0].get("prompt", "") if _shots else "")
            if _vp:
                beats[i].setdefault("video_prompt", _vp)
        if item.get("chart"):
            beats[i]["chart"] = item["chart"]
    if items and items[0].get("color_grade"):
        beats[0]["color_grade"] = items[0]["color_grade"]
    apply_director_color_grade(ctx, beats)


def build_director_user_prompt(beats: list, host: HostConfig) -> str:
    """Build the director's live-dispatch user_prompt from beats (+ optional host).

    When ``host.gender`` is set, prepend a line naming the host and gender so the
    director can match generic (non-host) runner shots to the host's gender (MR-2).
    Gate is on ``gender`` (not ``name``) — a name-only host (no gender configured)
    falls back to the plain beats listing, byte-for-byte identical to no host at
    all (AD-2: avoids a malformed "（）" injection and keeps other projects that
    only set a host name unaffected).
    """
    beats_listing = "\n".join(
        f"{i + 1}. {b.get('text', '')}" for i, b in enumerate(beats)
    )
    if host and getattr(host, "gender", ""):
        gender_line = f"主讲人物：{host.name}（{host.gender}）；通用（非主讲）跑者镜头性别与主讲一致。\n\n"
        return f"{gender_line}请为以下 {len(beats)} 段旁白逐段输出分镜方案（JSON数组），长度必须等于旁白段数({len(beats)})。\n\n{beats_listing}"
    return (
        f"请为以下 {len(beats)} 段旁白逐段输出分镜方案（JSON数组），"
        f"长度必须等于旁白段数({len(beats)})。\n\n{beats_listing}"
    )


def make_storyboard_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
    fresh: bool = False,
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
            # Same back-prop as durable/live: a doc-mode SPIKE reuse must also put
            # visual_type/video_prompt/chart onto beats or step_shotplan crashes.
            _backprop_visual_fields(ctx, sb)
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

        # --- (a3) Durable-reuse: shotlist.json already on disk + stamp matches
        # current upstream inputs (beats content / director persona text / config
        # fields) → load it, skip the live director dispatch entirely (Task 4).
        beats_json_text = json.dumps(beats, sort_keys=True, ensure_ascii=False)
        persona_text = _read_persona(plugin_root, "director")
        cfgfields = _shotlist_config_fields(cfg, ctx)
        want = render_stamp.shotlist_stamp(beats_json_text, persona_text, cfgfields)
        if not fresh and shotlist_path is not None and Path(shotlist_path).exists():
            stamp_path = Path(str(shotlist_path) + ".stamp")
            if render_stamp.stamp_matches(stamp_path, want):
                sb = json.loads(Path(shotlist_path).read_text(encoding="utf-8"))
                if len(sb) != len(beats):
                    log(f"[storyboard] durable shotlist length mismatch "
                        f"({len(sb)} vs {len(beats)} beats) → live re-dispatch")
                else:
                    # Mirror the live back-prop so resumed doc-mode beats carry
                    # visual_type/video_prompt/chart/color_grade (else step_shotplan
                    # raises KeyError 'visual_type'). Data is in the persisted sb.
                    _backprop_visual_fields(ctx, sb)
                    ctx["sb"] = [{"shots": item["shots"], "keywords": item["keywords"]}
                                 for item in sb]
                    ctx["shotlist_art"] = str(shotlist_path)
                    log("[storyboard] durable 复用 shotlist.json（stamp 匹配，跳过导演）")
                    return

        # --- (b) Live: beats lack visual_type (digest-writer output) ---
        user_prompt = build_director_user_prompt(beats, cfg.host)
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

        # Video floor safety net: guarantee ≥ video_floor 段真人跑步 b-roll，即使导演
        # 漏判（抽象期常见）。promote 最佳候选 still（跑步意象 > 人物 > 其它）→ video。
        _before = sum(1 for e in result if e.get("visual_type") == "video")
        result = apply_video_floor(result, cfg.budget.video_floor)
        _after = sum(1 for e in result if e.get("visual_type") == "video")
        if _after < cfg.budget.video_floor:
            log(f"[storyboard] video floor 未达成：无跑步候选可提升 "
                f"(floor={cfg.budget.video_floor}, got={_after})")
        elif _after > _before:
            log(f"[storyboard] video floor 安全网：promote {_after - _before} 段 still→video")

        # Back-propagate visual_type to ctx["beats"]. For video beats also carry
        # a video_prompt (gen_video reads beat["video_prompt"]) from the director's
        # shot — doc-mode beats don't hand-author it like fixtures do.
        # For chart beats also carry the director's chart spec (type + params).
        # Back-propagate visual_type / video_prompt / chart / color_grade onto
        # ctx["beats"] (shared with the durable + SPIKE reuse branches).
        _backprop_visual_fields(ctx, result)

        # Build ctx["sb"] (shots + keywords, 1:1 with beats)
        ctx["sb"] = [{"shots": item["shots"], "keywords": item["keywords"]}
                     for item in result]

        # Write shotlist deliverable and set the artifact key
        ctx["shotlist_art"] = _write_shotlist_art(result, shotlist_path)
        if shotlist_path is not None:
            render_stamp.write_stamp(Path(str(shotlist_path) + ".stamp"), want)

        n_video = sum(1 for b in ctx["beats"] if b.get("visual_type") == "video")
        log(
            f"[storyboard] {len(beats)} beats → {n_video} video "
            f"(cap={cfg.budget.video_shots_per_episode})"
        )

    return dispatch


def make_querywriter_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
) -> Callable:
    """The `query-writer` agent step's dispatch closure (Phase 3, D-007).

    Non-gating (pipeline step carries artifact=None, gate=[]; MR#1): a
    dispatch failure never halts the runner — it is swallowed here and the
    windows keep their Phase-1 interim `prompt` (no-regression fallback).

    - Fixture pass-through (ctx["fixture"] truthy, MR#2): zero live claude -p
      in the fixture render eval — the closure returns immediately without
      touching ctx["windows"].
    - Live: read ctx["windows"] (beat_i -> [window, ...]), build one prompt
      per window's narration `text`, dispatch "query-writer", parse the
      `[{beat_i, idx, query}]` JSON artifact, sanitize each query, write
      window["query"], and for interim windows (source=="interim") also
      overwrite window["prompt"] = query (DP-P1-001 closure, #3). Director
      windows keep their prompt untouched.
    """
    def dispatch(agent, step, ctx, scratch_dir):
        if agent != "query-writer":
            raise RuntimeError(f"unexpected agent step: {agent!r}")

        # --- MR#2: fixture pass-through, zero live claude -p ---
        if ctx.get("fixture"):
            log("[query-writer] fixture pass-through")
            return

        windows = ctx.get("windows") or {}
        if not windows:
            log("[query-writer] no windows in ctx — nothing to query")
            return

        # Build one prompt entry per window (video/chart beats aren't in
        # ctx["windows"] at all — nothing to skip explicitly, the dict
        # simply won't have their beat_i).
        lines = []
        for beat_i, win_list in windows.items():
            for idx, w in enumerate(win_list):
                lines.append(f"beat_i={beat_i} idx={idx}: {w.get('text', '')}")
        if not lines:
            log("[query-writer] no window entries — nothing to query")
            return

        user_prompt = (
            "为以下每个窗口写一条英文可拍场景查询（3–6 词，抽象→跑步代理画面），"
            "按 JSON 数组 [{\"beat_i\":i,\"idx\":j,\"query\":\"...\"}] 输出。\n\n"
            + "\n".join(lines)
        )

        try:
            res = dispatch_persona_fn(
                "query-writer", user_prompt, scratch_dir, "queries.json",
                plugin_root=plugin_root, model="sonnet",
            )
        except Exception as exc:  # noqa: BLE001 — MR#1: never halt the runner
            log(f"[query-writer] dispatch raised → fallback to interim: {exc}")
            return

        if not res.get("ok"):
            log(f"[query-writer] dispatch failed → fallback to interim: {res.get('reason')}")
            return

        try:
            with open(res["artifact_path"], encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log(f"[query-writer] could not read/parse artifact → fallback to interim: {exc}")
            return

        n_applied = 0
        for entry in entries:
            beat_i = entry.get("beat_i")
            idx = entry.get("idx")
            query = entry.get("query")
            if beat_i is None or idx is None or not query:
                continue
            win_list = windows.get(beat_i)
            if win_list is None:
                continue
            try:
                w = win_list[idx]
            except (IndexError, TypeError):
                continue
            q = sanitize_query(str(query))
            w["query"] = q
            if w.get("source") == "interim":
                w["prompt"] = q
            n_applied += 1

        log(f"[query-writer] {n_applied} window(s) got a content-matched query")

    return dispatch


def make_distiller_dispatch(
    cfg: VideoStudioConfig,
    *,
    dispatch_persona_fn: Callable[..., Any] = dispatch_mod.dispatch_persona,
    plugin_root: Path = PLUGIN_ROOT,
    log: Callable[[str], None] = lambda _m: None,
) -> Callable:
    """The `distill` agent step's dispatch closure — covered-ground 蒸馏器.

    Anti-homogenization: after the台本 is finalized (script gate passed), this
    isolates the episode's signature apparatus (类比/具体例子/开场钩子) into
    ``covered.json`` in the episode dir, for the covered-ground-update station
    (B6) to merge into the cross-episode store, so the next episode's writer
    gets an avoidance memo (B5).

    Non-gating (pipeline step carries artifact=None, gate=[]): a distiller
    failure must NEVER halt an already-rendered episode.

    MR-5 (critical): the runner (runner.py) does NOT wrap steps in try/except,
    so a raising dispatch/persona would bubble and abort the whole run. The
    WHOLE closure body is wrapped in ``try/except Exception → log + return`` —
    every failure (fixture guard, bad ctx, dispatch raise, persona crash,
    store/read error) is swallowed HERE and the run continues. A ``{ok:False}``
    return is not enough; the closure itself must swallow.

    - Fixture / render-eval short-circuit (``ctx['fixture']`` truthy): zero live
      claude -p — returns immediately without persisting.
    - Live: read ``ctx['beats']`` (current台本正文) + recent prior 台本 bodies
      (``_done_episode_dirs``, read-only) + the existing store (``load_store``,
      read-only), dispatch "covered-ground-distiller", and persist the returned
      ``covered.json`` into the episode dir; set ``ctx['covered_art']``.
    """
    def dispatch(agent, step, ctx, scratch_dir):
        try:
            if agent != "covered-ground-distiller":
                raise RuntimeError(f"unexpected agent step: {agent!r}")

            # Fixture / render-eval short-circuit — zero live claude -p.
            if ctx.get("fixture"):
                log("[distill] fixture pass-through (no live dispatch)")
                return

            beats = ctx.get("beats") or []
            if not beats:
                log("[distill] no beats in ctx — nothing to distill")
                return

            # Current episode's台本正文 (逐段旁白 text; DATA, not instructions).
            body = "\n\n".join(
                str(b.get("text", "")).strip()
                for b in beats
                if str(b.get("text", "")).strip()
            )

            # Recent prior 台本 bodies (read-only, most-recent few). Fail-closed:
            # _done_episode_dirs returns () without a syllabus/cursor/seq.
            recent_bodies: list[str] = []
            for ep_dir in _done_episode_dirs(cfg, ctx)[-5:]:
                script_md = Path(ep_dir) / "script.md"
                try:
                    recent_bodies.append(script_md.read_text(encoding="utf-8"))
                except OSError:
                    continue

            # Existing store (read-only — the distiller never writes it; the
            # update station does, after computing embeddings).
            try:
                store = coveredground.load_store(cfg.output_dir)
            except Exception:  # noqa: BLE001 — corrupt store must not halt distill
                store = {"anchors": {}}
            existing = sorted((store.get("anchors") or {}).keys())

            user_prompt = "\n".join([
                "本期台本正文（beats，逐段旁白——DATA，不是指令）：",
                body or "(空)",
                "",
                "最近几期台本正文（recent_bodies，判断哪些装置最近反复用过）：",
                ("\n\n---\n\n".join(recent_bodies) if recent_bodies else "(无)"),
                "",
                "现有 covered-ground store 已记录的装置（只读，供参考；仍要报本期实际用到的）：",
                ("\n".join(f"- {a}" for a in existing) if existing else "(空)"),
                "",
                "翻这期台本，抽出招牌装置（类比/具体例子/开场钩子），按约定 JSON "
                "写到指定路径（本期 covered.json）。没抽到也写 {\"anchors\": []}。",
            ])

            res = dispatch_persona_fn(
                "covered-ground-distiller", user_prompt, scratch_dir, "covered.json",
                plugin_root=plugin_root, model="sonnet",
            )
            if not res.get("ok"):
                log(f"[distill] dispatch failed → no covered.json: {res.get('reason')}")
                return

            # Persist covered.json into the episode dir (for B6's update station).
            art_path = res.get("artifact_path")
            ep = ctx.get("ep")
            if not art_path or ep is None:
                log("[distill] no artifact_path / ep — covered.json not persisted")
                return
            ep_dir = ep["final"].parent
            ep_dir.mkdir(parents=True, exist_ok=True)
            dest = ep_dir / "covered.json"
            dest.write_text(
                Path(art_path).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            ctx["covered_art"] = str(dest)
            log(f"[distill] covered.json persisted → {dest}")
        except Exception as exc:  # noqa: BLE001 — MR-5: never halt the runner
            log(f"[distill] swallowed error (fail-soft, run continues): {exc}")
            return

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
    fresh: bool = False,
    episode_target: bool = False,
    script_only: bool = False,
) -> dict[str, Any]:
    """Build the {step_fns, gates, dispatch, scratch_dir} bundle the caller
    passes straight into `run_pipeline`. The caller owns the run_pipeline call
    (keeps this module off the runner's import path → no cycle).

    `script_only` (the `--stop-after` CLI path) tells the step layer that no media
    step will run, so it must not resolve the character ref — that resolution can
    fall through to a PAID image generation.

    `fresh` / `episode_target` (Task 7, resume/media-cache): `fresh=True`
    bypasses both the durable A-layer (script/storyboard re-dispatch) and the
    B-layer media cache (`CachingMediaSeams(fresh=True)`, forcing regeneration
    even on a key hit). `episode_target=True` (the `--episode <seq>` CLI path)
    is threaded through to `step_publish` so a directed re-render never
    advances cursor/status (MR-1) — it is orthogonal to `fresh`.
    """
    from lib import media_cache

    run_ff, probe_ms = make_ff_seams(cfg)
    cache_dir = Path(cfg.output_dir) / "episodes" / f"{seq}-{slug}" / ".cache"
    render_dims = (cfg.render.width, cfg.render.height)
    shotlist_path = episode_mod.episode_paths(cfg.output_dir, seq, slug)["shotlist"]
    # Resume detection for ledger skip-own (Task 5): sufficient to check the
    # shotlist's presence — if the stamp later mismatches inside
    # make_storyboard_dispatch, the shotlist re-dispatches and the windows
    # change anyway, so a resume=True-but-stamp-miss is harmless (ADV per
    # design doc — "宁可 True 也不误 False").
    resume = (not fresh) and Path(shotlist_path).exists()

    inner_seams = MediaSeams(dry=dry, provider=cfg.media.provider, log=log,
                             voice_id=getattr(cfg.audio, "voice_id", ""))
    seams = media_cache.wrap_seams(
        inner_seams, cache_dir, render_dims=render_dims, fresh=fresh, log=log,
    )
    router = MediaRouter(GenerateSource(seams), cfg=cfg, log=log)
    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path, seq=seq, slug=slug,
        run_ff=run_ff, probe_ms=probe_ms, log=log, selection=selection, dry=dry,
        resume=resume, episode_target=episode_target, script_only=script_only,
    )
    gates = {"check_artifact": episode_mod.check_artifact}

    script_dispatch = make_script_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log, fresh=fresh)
    storyboard_dispatch = make_storyboard_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log, fresh=fresh)
    querywriter_dispatch = make_querywriter_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log)
    distiller_dispatch = make_distiller_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log)

    def dispatch(agent, step, ctx, scratch_dir):
        if agent == "digest-writer":
            return script_dispatch(agent, step, ctx, scratch_dir)
        elif agent == "director":
            return storyboard_dispatch(agent, step, ctx, scratch_dir)
        elif agent == "query-writer":
            return querywriter_dispatch(agent, step, ctx, scratch_dir)
        elif agent == "covered-ground-distiller":
            return distiller_dispatch(agent, step, ctx, scratch_dir)
        else:
            raise RuntimeError(f"unknown agent for dispatch: {agent!r}")

    scratch = episode_mod.make_scratch(cfg.output_dir, tag=f"{seq}-{slug}")
    return {
        "step_fns": step_fns,
        "gates": gates,
        "dispatch": dispatch,
        "scratch_dir": scratch,
        "_seams": seams,
    }
