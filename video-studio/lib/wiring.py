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
from lib.ledger import LedgerError, validate_ledger, verify_anchors
from lib.provenance import build_provenance
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

    def __init__(self, dry: bool, provider: str, log: Callable[[str], None]):
        self.dry = dry
        self.provider = provider
        self.log = log
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
        return self._img.gen_character_ref(prompt, out_path)

    def gen_still(self, prompt: str, ref_url: str | None, out_path: str) -> str:
        if self.dry:
            # Content is irrelevant for the wiring/ffmpeg test — reuse one cached
            # photographic still so ffmpeg gets a real, decodable image.
            dst = os.path.splitext(out_path)[0] + ".jpg"
            shutil.copy(SPIKE / "b00_shot0.jpg", dst)
            return dst
        return self._img.gen_still(prompt, ref_url, out_path)

    def gen_video(self, prompt: str, ref_url: str, out_path: str) -> dict:
        if self.dry:
            shutil.copy(SPIKE / "b04.mp4", out_path)
            return {"ok": True, "path": out_path, "task_id": "dry"}
        return self._vid.gen_video(prompt, ref_url, out_path)

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

    # ---- 1: cursor-read (placeholder; Phase 3) ----
    def step_cursor_read(step, ctx, scratch):
        log("[cursor-read] placeholder (Phase 3 wires real cursor agent)")

    # ---- 2: scratch (episode dirs + scratch sentinel) ----
    def step_scratch(step, ctx, scratch):
        ep = episode_mod.episode_paths(cfg.output_dir, seq, slug)
        ep_dir = ep["final"].parent
        ep_dir.mkdir(parents=True, exist_ok=True)
        ep["assets_dir"].mkdir(parents=True, exist_ok=True)
        ctx["ep"] = ep
        ctx["scratch_dir"] = _sentinel(Path(scratch), "scratch_dir", str(ep_dir))
        log(f"[scratch] episode dir -> {ep_dir}")

    # ---- 3: extract — PDF-text mode (Phase 3) OR fixture-beats mode (Phase 2) ----
    def step_extract(step, ctx, scratch):
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
            return
        # Fixture mode (Phase 2, unchanged): load beats + ref + grade + storyboard.
        with open(fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        beats = fixture["beats"]
        ctx["fixture"] = fixture
        ctx["beats"] = beats

        # character_ref → subject_reference URL (reuse hook or generate fresh)
        ep = ctx["ep"]
        cref_out = str(ep["assets_dir"] / "character_ref.png")
        ref_png = os.environ.get("SPIKE_REF_PNG")
        ref_url_env = os.environ.get("SPIKE_REF_URL")
        if ref_png and ref_url_env:
            shutil.copy(ref_png, cref_out)
            ref_url = ref_url_env
            log(f"[ref] reusing provided character_ref {ref_png}")
        else:
            cref = fixture.get("character_ref", {})
            ref_url = seams.gen_character_ref(cref.get("prompt", "主讲人物"), cref_out)
            log(f"[ref] character_ref -> {cref_out} (url={ref_url[:40]})")
        ctx["ref_url"] = ref_url

        # locked series grade (every photographic shot; charts excluded)
        ctx["grade_vf"] = grade.grade_filter()
        log(f"[grade] series look = {grade.SERIES_LOOK}")

        # storyboard (reuse cached plan; must match beat count)
        sb = None
        reuse_sb = os.environ.get("SPIKE_STORYBOARD_JSON")
        if reuse_sb:
            with open(reuse_sb, encoding="utf-8") as f:
                sb = json.load(f)
            if len(sb) != len(beats):
                raise RuntimeError(
                    f"reused storyboard {reuse_sb} has {len(sb)} entries but "
                    f"fixture has {len(beats)} beats")
            log(f"[storyboard] reusing {reuse_sb} "
                f"({sum(len(e['shots']) for e in sb)} shots / {len(beats)} beats)")
            shutil.copy(reuse_sb, str(ep["shotlist"]))  # shotlist.json deliverable
        ctx["sb"] = sb

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
            media_chart.render_pace_table(
                beat["chart_title"], [tuple(r) for r in beat["chart_rows"]], png)
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
        fixture = ctx["fixture"]
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
        card_cfg = fixture.get("cards", {})
        tcfg = {**cards.DEFAULT_TITLE, **card_cfg.get("title", {})}
        ecfg = {**cards.DEFAULT_END, **card_cfg.get("end", {})}

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
        from spike_acceptance import measure_gap
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
            prov = {
                "seq": seq, "slug": slug,
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

    # ---- 11: publish (placeholder; Phase 7) ----
    def step_publish(step, ctx, scratch):
        log("[publish] placeholder (Phase 7 wires real publish)")

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
        user_prompt = (
            f"教材原文（只依据这段原文，不外检索）在: {src}\n"
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

        ctx["beats"] = beats
        with open(ledger_art, "w", encoding="utf-8") as f:
            json.dump({"beats": beats}, f, ensure_ascii=False)
        ctx["script_ledger"] = ledger_art
        ep = ctx.get("ep")
        if ep is not None:
            ep["script"].write_text(_beats_to_md(beats), encoding="utf-8")
        log(f"[script] {len(beats)} beats grounded → 忠实门 PASS")

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
) -> dict[str, Any]:
    """Build the {step_fns, gates, dispatch, scratch_dir} bundle the caller
    passes straight into `run_pipeline`. The caller owns the run_pipeline call
    (keeps this module off the runner's import path → no cycle).
    """
    run_ff, probe_ms = make_ff_seams(cfg)
    seams = MediaSeams(dry=dry, provider=cfg.media.provider, log=log)
    step_fns = build_step_fns(
        cfg, seams=seams, fixture_path=fixture_path, seq=seq, slug=slug,
        run_ff=run_ff, probe_ms=probe_ms, log=log,
    )
    gates = {"check_artifact": episode_mod.check_artifact}

    dispatch = make_script_dispatch(
        cfg, dispatch_persona_fn=dispatch_persona_fn, log=log)

    scratch = episode_mod.make_scratch(cfg.output_dir, tag=f"{seq}-{slug}")
    return {
        "step_fns": step_fns,
        "gates": gates,
        "dispatch": dispatch,
        "scratch_dir": scratch,
    }
