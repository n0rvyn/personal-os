#!/usr/bin/env python3
"""Backfill the covered-ground store from the existing running-class episodes.

One-shot backfill (Task B7): walk the already-published running-class episodes
(002 → 005) in seq order, run the isolated `covered-ground-distiller` persona
over each 台本 (script.md) to extract that episode's 招牌装置 (类比 / 例子 /
钩子), and fold the anchors into running-class's cross-episode store
(`<output_dir>/covered-ground.yaml`). Earliest-first so a later episode's
distillation sees the store accumulated by the earlier ones.

Why: E6 (the next episode) should not start from zero — the writing brief's
avoidance memo needs a baseline of what the first 5 episodes already reached
for, so digest-writer avoids recycling the same apparatus.

Distillation goes STRICTLY through `claude -p` (dispatch_persona) — never a
direct API. This is the first real end-to-end exercise of the distiller chain.

Usage:
    # No-LLM smoke: stubs the distiller with {"anchors": ["test-anchor"]},
    # exercises the store round-trip on disk without any claude -p call.
    python3 scripts/backfill_covered_ground.py --dry

    # LIVE: real claude -p distiller over the 5 episodes (LLM cost small).
    python3 scripts/backfill_covered_ground.py

Fail-soft: a distiller failure on one episode logs + continues; it never
aborts the whole backfill.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixed paths (this script is a one-shot backfill against known locations).
# ---------------------------------------------------------------------------

# video-studio plugin root: <root>/scripts/backfill_covered_ground.py
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# The running-class节目 lives beside video-studio in the personal-os tree.
RUNNING_CLASS_OUTPUT = (PLUGIN_ROOT.parent / "running-class" / "output").resolve()
EPISODES_DIR = RUNNING_CLASS_OUTPUT / "episodes"

# No real publish dates exist on these episodes (provenance.json carries only
# `seq`). is_stale is seq-based (date only drives memo display + tiebreak),
# so a single honest placeholder — the backfill date — is correct for all.
BACKFILL_DATE = "2026-07-04"

# `python3 scripts/backfill_covered_ground.py` puts `scripts/` on sys.path[0],
# NOT the plugin root — so `from lib.coveredground import ...` needs this.
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.coveredground import load_store, update_covered_ground  # noqa: E402
from lib.dispatch import dispatch_persona  # noqa: E402


_SEQ_PREFIX = re.compile(r"^(\d+)")


def discover_episodes() -> list[tuple[int, Path]]:
    """Return [(seq, episode_dir), ...] sorted EARLIEST-first (002 → 005).

    An episode dir is a child of EPISODES_DIR whose name starts with digits
    (the global seq). Non-conforming entries are skipped.
    """
    out: list[tuple[int, Path]] = []
    if not EPISODES_DIR.is_dir():
        return out
    for child in EPISODES_DIR.iterdir():
        if not child.is_dir():
            continue
        m = _SEQ_PREFIX.match(child.name)
        if not m:
            continue
        out.append((int(m.group(1)), child))
    out.sort(key=lambda t: t[0])
    return out


def read_taiben(episode_dir: Path) -> str:
    """Read the episode's 台本 body.

    Preference order:
      1. script.md  — the digest-writer 定稿旁白正文 (the real narration).
      2. shotlist.json beats — derive text from each beat's visual prompts
         (fallback only; visual prompts are coarser signal than narration).

    Returns "" when neither is usable (caller treats as skip).
    """
    script_md = episode_dir / "script.md"
    if script_md.is_file():
        try:
            text = script_md.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return text

    shotlist = episode_dir / "shotlist.json"
    if shotlist.is_file():
        try:
            data = json.loads(shotlist.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
        if isinstance(data, list):
            parts: list[str] = []
            for beat in data:
                if not isinstance(beat, dict):
                    continue
                vp = beat.get("video_prompt")
                if isinstance(vp, str) and vp.strip():
                    parts.append(vp.strip())
                for shot in beat.get("shots", []) or []:
                    if isinstance(shot, dict):
                        sp = shot.get("prompt")
                        if isinstance(sp, str) and sp.strip():
                            parts.append(sp.strip())
            if parts:
                return "\n".join(parts)

    return ""


def build_distiller_prompt(taiben: str, store: dict) -> str:
    """Compose the distiller user prompt: 本期已发布正文 + 现有 store (只读).

    Deliberate scope (Task B7): the distiller agent md lists three inputs
    (本期正文 / recent_bodies / store), but this backfill feeds only body +
    store. The accumulating store subsumes recent_bodies here — earlier
    episodes are already folded into the store the distiller reads, and the
    agent still reports anchors already present (it does not self-dedup).
    """
    store_yaml = json.dumps(store, ensure_ascii=False, indent=2)
    return (
        "## 本期已发布正文（台本 beats — 脚本定稿后的逐段旁白文本）\n\n"
        f"{taiben}\n\n"
        "---\n\n"
        "## 现有 covered-ground store（只读，勿改写；写回由 code 站算 embedding 后落盘）\n\n"
        "```json\n"
        f"{store_yaml}\n"
        "```\n\n"
        "---\n\n"
        "按你的隔离纪律，从**本期正文**里抽出招牌装置（类比 / 具体例子 / 钩子），"
        "写成 `{\"anchors\": [...]}` 的 JSON。store 里已有的装置若本期也出现，仍然要报。"
        "抽不到就写 `{\"anchors\": []}`。"
    )


def distill_episode(
    seq: int,
    episode_dir: Path,
    taiben: str,
    *,
    dry: bool,
) -> list[str] | None:
    """Run the distiller for one episode and fold its anchors into the store.

    Returns the list of anchors the distiller extracted (for reporting), or
    None if the episode was skipped / the distiller failed (fail-soft — the
    caller continues to the next episode either way).
    """
    # Fresh scratch dir per episode: avoids the stale-artifact trap where a
    # claude -p that exits 0 without rewriting covered.json would let the
    # dispatch gate pass on the PREVIOUS episode's leftover file.
    scratch_dir = Path(tempfile.mkdtemp(prefix=f"backfill_cg_{seq:03d}_"))
    covered_json = scratch_dir / "covered.json"

    if dry:
        # No-LLM stub: write the fake artifact directly, skip claude -p.
        covered_json.write_text(
            json.dumps({"anchors": ["test-anchor"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        covered_path = str(covered_json)
    else:
        store = load_store(RUNNING_CLASS_OUTPUT)
        prompt = build_distiller_prompt(taiben, store)
        result = dispatch_persona(
            "covered-ground-distiller",
            prompt,
            str(scratch_dir),
            "covered.json",
            plugin_root=str(PLUGIN_ROOT),
            model="sonnet",
        )
        if not result.get("ok"):
            print(
                f"  [seq {seq:03d}] distiller FAILED (fail-soft, continuing): "
                f"{result.get('reason')}",
                file=sys.stderr,
            )
            return None
        covered_path = result.get("artifact_path") or str(covered_json)

    # Read back the anchors this episode contributed (for the report).
    anchors: list[str] = []
    try:
        data = json.loads(Path(covered_path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("anchors"), list):
            anchors = [a for a in data["anchors"] if isinstance(a, str)]
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"  [seq {seq:03d}] could not read covered.json ({exc}); "
            f"store update still attempted",
            file=sys.stderr,
        )

    # Fold into the running-class store (internally fail-soft).
    update_covered_ground(
        RUNNING_CLASS_OUTPUT, covered_path, date=BACKFILL_DATE, seq=seq
    )

    print(f"  [seq {seq:03d}] {episode_dir.name}")
    print(f"           anchors ({len(anchors)}): {anchors}")
    return anchors


def print_store_summary() -> int:
    """Print the final store summary (anchor count + a sample). Returns the
    anchor count."""
    store = load_store(RUNNING_CLASS_OUTPUT)
    anchors = store.get("anchors", {})
    count = len(anchors) if isinstance(anchors, dict) else 0
    print("\n" + "=" * 60)
    print(f"Store summary: {RUNNING_CLASS_OUTPUT / 'covered-ground.yaml'}")
    print(f"  total distinct anchors: {count}")
    if isinstance(anchors, dict) and anchors:
        print("  sample:")
        for key in list(anchors.keys())[:8]:
            entry = anchors[key]
            c = entry.get("count") if isinstance(entry, dict) else "?"
            eps = entry.get("episodes") if isinstance(entry, dict) else None
            seqs = (
                [e.get("seq") for e in eps if isinstance(e, dict)]
                if isinstance(eps, list)
                else []
            )
            print(f"    - {key}  (count={c}, seqs={seqs})")
    print("=" * 60)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Stub the distiller (no claude -p) for a no-LLM store round-trip smoke.",
    )
    args = parser.parse_args()

    mode = "DRY (stubbed distiller)" if args.dry else "LIVE (claude -p distiller)"
    print(f"Backfill covered-ground store — mode: {mode}")
    print(f"  plugin_root: {PLUGIN_ROOT}")
    print(f"  output_dir:  {RUNNING_CLASS_OUTPUT}")

    episodes = discover_episodes()
    if not episodes:
        print(f"No episodes found under {EPISODES_DIR}", file=sys.stderr)
        return 1
    print(f"  episodes (earliest-first): {[s for s, _ in episodes]}\n")

    processed = 0
    for seq, ep_dir in episodes:
        taiben = read_taiben(ep_dir)
        if not taiben:
            print(
                f"  [seq {seq:03d}] no usable 台本 (script.md / shotlist.json); "
                f"skipping",
                file=sys.stderr,
            )
            continue
        try:
            anchors = distill_episode(seq, ep_dir, taiben, dry=args.dry)
        except Exception as exc:  # noqa: BLE001 — per-episode fail-soft
            print(
                f"  [seq {seq:03d}] unexpected error (fail-soft, continuing): {exc}",
                file=sys.stderr,
            )
            continue
        if anchors is not None:
            processed += 1

    count = print_store_summary()

    # Smoke assertion (Task B7 automated verify): the store must be non-empty
    # on disk after a run. update_covered_ground is internally fail-soft, so
    # this assertion is what actually catches a silent store-write failure.
    if count == 0:
        print(
            "\nFAIL: store is empty after backfill "
            f"(processed {processed}/{len(episodes)} episodes)",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nOK: backfill complete — {processed}/{len(episodes)} episodes folded, "
        f"{count} distinct anchors in store."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
