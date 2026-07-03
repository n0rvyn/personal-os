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
from lib import shotplan as shotplan_mod
from lib import steps as steps_mod
from lib.config import VideoStudioConfig
from lib.media_source import GenerateSource, MediaRouter
from lib.budget import apply_video_budget, apply_video_floor
from lib.ledger import LedgerError, validate_ledger, verify_anchors
from lib.provenance import build_provenance, stamp_episode_provenance
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
        router.set_style(_art_style)
        ctx["grade_vf"] = grade.resolve_color_grade(_color_grade or None)
        log(f"[grade] art_style={_art_style!r} color_grade={_color_grade!r}")

        def _ensure_ref_url(prompt):
            # Resolve the主讲人物 image-to-image ref. `prompt` is the character_ref
            # value (may be a stored image PATH → base64 reuse for 跨集同一张脸, a
            # DESCRIPTION → generate, or empty → default). SPIKE_REF hook still wins.
            # See resolve_character_ref_url for the full priority + corrupt-file
            # fallback. This replaces the old within-run-only ephemeral-URL path.
            ep = ctx["ep"]
            cref_out = str(ep["assets_dir"] / "character_ref.png")
            ctx["ref_url"] = resolve_character_ref_url(
                prompt or "", cref_out, seams.gen_character_ref, log,
                provider=getattr(seams, "provider", "doubao"),
            )

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
        ledger = VisualLedger(ep_dir, prior_dirs=_done_episode_dirs(cfg, ctx))
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
            steps_mod.step_render_shots_clip(
                shots, ctx["clip_len_by_index"][i], clip, bid, scratch_s,
                ref_url, grade_vf,
                gen_still_fn=router.gen_still, run_ff_fn=run_ff,
                durs=durs,
            )
            ctx["clips_by_index"][i] = clip
            n += 1
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
            actual = seams.gen_still(prompt, card_ref, out)
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

    return {
        "config": step_config,
        "cursor-read": step_cursor_read,
        "scratch": step_scratch,
        "extract": step_extract,
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
        # R2: fixed host persona (configurable). When cfg.host.name is set, tell the
        # writer to open with a greeting-as-hook + sign off, in the given tone.
        _host = getattr(cfg, "host", None)
        _host_line = ""
        if _host is not None and getattr(_host, "name", ""):
            _ch = getattr(_host, "channel", "")
            _tone = getattr(_host, "tone", "")
            _host_line = (
                f"本期主播：{_host.name}"
                + (f"（频道：{_ch}）" if _ch else "")
                + (f"，口吻基调：{_tone}" if _tone else "")
                + f"。开场第一个 beat 用「大家好，我是{_host.name}"
                + (f"，欢迎来到{_ch}" if _ch else "")
                + "」式问候，且问候必须和钩子合一（问候后立刻抛反差/悬念，不许干念"
                  "「今天我们讲 X」）；结尾最后一个 beat 用主播签名收尾（暖收束+一句留客），"
                  "整期贯穿该口吻。\n"
            )
        user_prompt = (
            f"教材原文（只依据这段原文，不外检索）在: {src}\n"
            f"本期主源: {_src_label or '教材原文'}；溯源状态: {_vstatus or '已溯源'}。{_disclosure}\n"
            f"{_host_line}"
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
        for i, item in enumerate(result):
            ctx["beats"][i]["visual_type"] = item["visual_type"]
            if item["visual_type"] == "video":
                _shots = item.get("shots") or []
                # Prefer an explicit video_prompt (e.g. the running shot apply_video_floor
                # picked via _shot_score) over shots[0] — for a multi-shot beat the running
                # shot may not be first, and gen_video reads beat["video_prompt"].
                _vp = item.get("video_prompt") or (_shots[0].get("prompt", "") if _shots else "")
                if _vp:
                    ctx["beats"][i].setdefault("video_prompt", _vp)
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
    seams = MediaSeams(dry=dry, provider=cfg.media.provider, log=log,
                       voice_id=getattr(cfg.audio, "voice_id", ""))
    router = MediaRouter(GenerateSource(seams), cfg=cfg, log=log)
    step_fns = build_step_fns(
        cfg, seams=seams, router=router, fixture_path=fixture_path, seq=seq, slug=slug,
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
