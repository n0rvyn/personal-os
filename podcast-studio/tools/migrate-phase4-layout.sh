#!/usr/bin/env bash
# tools/migrate-phase4-layout.sh — Phase 4 one-shot, reversible layout migration.
#
# Moves the flat `output_dir` artifacts into the derived subdirs introduced in
# Phase 4 (episodes/ state/ reports/) and relocates an in-vault `config.yaml`
# to the documented default `~/.podcast-studio/config.yaml`.
#
#   episodes/  ← {date}-{title}.md  {date}-{title}.mp3  {date}-{show}.stance.yaml
#   state/     ← character-bible.md  covered-ground.yaml  throughline.yaml
#   reports/   ← {date}-{show}.scorecard.md
#   (root)     ← topic_log.yaml  source_log.jsonl  x_banner.png  .scratch-*  (UNCHANGED)
#
# Idempotent: only root-level files are globbed, so files already inside a
# subdir are never re-moved; existing destination files are NOT overwritten.
# Non-destructive: nothing is deleted. Prints every move + an undo hint.
#
# Usage: bash tools/migrate-phase4-layout.sh <output_dir>
#        (output_dir defaults to the value in the active config if omitted)
set -euo pipefail

OUT="${1:-}"
if [[ -z "$OUT" ]]; then
  OUT="$(python3 -c 'from lib.config import load_config; print(load_config().vault.output_dir)' 2>/dev/null || true)"
fi
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
  echo "ERROR: output_dir not found (arg or config): '${OUT}'" >&2
  exit 1
fi
OUT="${OUT%/}"
echo "migrate-phase4-layout: output_dir = $OUT"

mkdir -p "$OUT/episodes" "$OUT/state" "$OUT/reports"

MOVED=0
# move_one <src-file> <dest-subdir-name>: move a single root-level file into a
# subdir, skipping if absent or if a same-named file already sits in the dest.
move_one() {
  local src="$1" sub="$2" base dest
  [[ -e "$src" ]] || return 0
  base="$(basename "$src")"
  dest="$OUT/$sub/$base"
  if [[ -e "$dest" ]]; then
    echo "  skip (exists): $sub/$base"
    return 0
  fi
  mv "$src" "$dest"
  echo "  moved: $base -> $sub/"
  MOVED=$((MOVED + 1))
}

# ORDER MATTERS: scorecard.md (*.scorecard.md ⊂ *.md) and character-bible.md
# (*.md) must route BEFORE the generic *.md -> episodes sweep.
shopt -s nullglob

# 1) reports/: scorecards
for f in "$OUT"/*.scorecard.md; do move_one "$f" reports; done

# 2) state/: continuity (explicit names — NOT a *.yaml glob, which would also
#    catch topic_log.yaml which must stay at root).
for f in "$OUT"/character-bible.md "$OUT"/covered-ground.yaml "$OUT"/throughline.yaml; do
  move_one "$f" state
done

# 3) episodes/: listener artifacts. *.md now excludes scorecards (→reports) and
#    character-bible.md (→state, already moved).
for f in "$OUT"/*.md "$OUT"/*.mp3 "$OUT"/*.stance.yaml; do move_one "$f" episodes; done

shopt -u nullglob

# 4) config out of the artifacts dir → documented default (only if absent there).
CFG_SRC="$OUT/config.yaml"
CFG_DST="$HOME/.podcast-studio/config.yaml"
if [[ -e "$CFG_SRC" ]]; then
  if [[ -e "$CFG_DST" ]]; then
    echo "  skip config move: $CFG_DST already exists (leaving $CFG_SRC in place)"
  else
    mkdir -p "$(dirname "$CFG_DST")"
    mv "$CFG_SRC" "$CFG_DST"
    echo "  moved: config.yaml -> $CFG_DST"
    MOVED=$((MOVED + 1))
  fi
fi

echo "migrate-phase4-layout: $MOVED file(s) moved."
echo "undo: move files back from $OUT/{episodes,state,reports}/ to $OUT/ and config.yaml back if relocated."
