#!/usr/bin/env python3
"""Fold finished episodes' covered.json into the shared covered-ground store.

Why this exists as a separate step: covered-ground is sequential by construction
(avoid-memo reads the store → writer avoids what's in it → distiller extracts this
episode's apparatus → covered-update folds it back so the NEXT episode sees it).
Writing episodes in parallel breaks that in two ways:

  1. Parallel writers all read the SAME store snapshot, so they are blind to each
     other and can converge on the same fresh analogy.
  2. `covered-update` does read-modify-write on one shared YAML. Concurrent runs
     lose each other's anchors (os.replace is atomic per write, the RMW is not).

The fix is to stop each parallel episode at `distill` — which writes covered.json
into that episode's OWN directory, race-free — and then fold a whole wave into the
store here, serially, between waves. Episodes inside a wave stay blind to each
other (accepted, and why waves are striped across modules rather than run down one
module); every LATER wave sees every EARLIER wave.

Usage:
    python3 scripts/fold_covered_ground.py --output-dir <dir> --date YYYY-MM-DD 001 002 007
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from lib.coveredground import load_store, update_covered_ground  # noqa: E402

_SEQ_PREFIX = re.compile(r"^(\d+)")


def episode_dir_for(episodes_dir: Path, seq: str) -> Path | None:
    """Find the episode directory whose name starts with `seq` (zero-padded)."""
    want = int(seq)
    for child in sorted(episodes_dir.iterdir()):
        if not child.is_dir():
            continue
        m = _SEQ_PREFIX.match(child.name)
        if m and int(m.group(1)) == want:
            return child
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD stamped on the anchors")
    ap.add_argument("seqs", nargs="+", help="episode seqs to fold, e.g. 001 002 007")
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    episodes_dir = output_dir / "episodes"
    if not episodes_dir.is_dir():
        print(f"error: no episodes dir at {episodes_dir}", file=sys.stderr)
        return 1

    before = len(load_store(output_dir).get("anchors", {}))
    folded, skipped = 0, []

    # Ascending seq: an earlier episode's anchors must already be in the store when
    # a later one folds, so the reskin detector merges paraphrases into the FIRST
    # use rather than creating a second key.
    for seq in sorted(args.seqs, key=int):
        ep_dir = episode_dir_for(episodes_dir, seq)
        if ep_dir is None:
            skipped.append(f"{seq}: no episode dir")
            continue
        covered = ep_dir / "covered.json"
        if not covered.is_file():
            skipped.append(f"{seq}: no covered.json (distiller did not run?)")
            continue
        try:
            anchors = json.loads(covered.read_text(encoding="utf-8")).get("anchors") or []
        except (json.JSONDecodeError, OSError) as exc:
            skipped.append(f"{seq}: unreadable covered.json ({exc})")
            continue

        update_covered_ground(output_dir, covered, args.date, int(seq))
        folded += 1
        print(f"  [{seq}] {ep_dir.name} — {len(anchors)} anchors")

    after = len(load_store(output_dir).get("anchors", {}))
    print(f"\nfolded {folded}/{len(args.seqs)} episodes; "
          f"store {before} → {after} distinct anchors")

    if skipped:
        # Loud, not silent: a skipped episode means the next wave will NOT avoid its
        # apparatus, which is exactly the homogenization this machinery prevents.
        print("\nSKIPPED (their apparatus will NOT be avoided downstream):",
              file=sys.stderr)
        for s in skipped:
            print(f"  - {s}", file=sys.stderr)

    return 0 if folded else 1


if __name__ == "__main__":
    raise SystemExit(main())
