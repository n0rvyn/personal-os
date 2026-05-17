#!/usr/bin/env bash
# tts-toolkit entry point. Routes to a provider script by voice-id prefix.
#
# Modes:
#   Single:  --text <s> --voice <id> --output <path> [--speed --rate]
#   Batch:   --input <md> --voice <id> --output <mp3> [--speed --rate --max-chars]
#
# Voice prefix routing:
#   volc-* → providers/volcengine.sh
#   mm-*   → providers/minimax.sh   (skeleton; returns 2 until implemented)

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

text=""
input=""
voice=""
output=""
speed="1.0"
rate="24000"
max_chars="280"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --text) text="$2"; shift 2;;
        --input) input="$2"; shift 2;;
        --voice) voice="$2"; shift 2;;
        --output) output="$2"; shift 2;;
        --speed) speed="$2"; shift 2;;
        --rate) rate="$2"; shift 2;;
        --max-chars) max_chars="$2"; shift 2;;
        -h|--help)
            cat <<'EOF'
Usage:
  synth.sh --text <s>   --voice <id> --output <path> [--speed 1.0 --rate 24000]
  synth.sh --input <md> --voice <id> --output <path> [--speed 1.0 --rate 24000 --max-chars 280]

Voice prefix: volc-* (Volcengine), mm-* (MiniMax — skeleton)
EOF
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done

if [[ -z "$voice" || -z "$output" ]]; then
    echo "missing --voice or --output" >&2; exit 1
fi
if [[ -z "$text" && -z "$input" ]]; then
    echo "must pass either --text or --input" >&2; exit 1
fi
if [[ -n "$text" && -n "$input" ]]; then
    echo "--text and --input are mutually exclusive" >&2; exit 1
fi

# Voice-prefix → provider routing
case "$voice" in
    volc-*) provider="$HERE/providers/volcengine.sh"; voice_inner="${voice#volc-}";;
    mm-*)   provider="$HERE/providers/minimax.sh";    voice_inner="${voice#mm-}";;
    *)
        echo "unsupported voice prefix: $voice" >&2
        echo "supported: volc-* (Volcengine), mm-* (MiniMax)" >&2
        exit 1;;
esac

# --- Single mode ----------------------------------------------------------
if [[ -n "$text" ]]; then
    bash "$provider" "$text" "$voice_inner" "$output" "$speed" "$rate"
    exit 0
fi

# --- Batch mode -----------------------------------------------------------
[[ -f "$input" ]] || { echo "input not found: $input" >&2; exit 1; }

# Inline markdown strip + chunker. Mirrors /tmp/doubao_tts_podcast.py logic so the
# 2026-05-17 proven path stays reproducible. When personal-os/text-to-segments
# (#238) ships, swap this block out for a shell-out to that skill.
staging="$(mktemp -d -t tts-batch-XXXXXX)"
chunks_dir="$staging/chunks"
mkdir -p "$chunks_dir"

python3 - "$input" "$chunks_dir" "$max_chars" <<'PYEOF'
import os, re, sys
src, chunks_dir, max_chars = sys.argv[1], sys.argv[2], int(sys.argv[3])

with open(src) as f:
    s = f.read()

# Strip markdown to natural speech
s = re.sub(r"```[\s\S]*?```", "", s)
s = re.sub(r"`([^`]+)`", r"\1", s)
s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
s = re.sub(r"\*(.+?)\*", r"\1", s)
s = re.sub(r"__(.+?)__", r"\1", s)
s = re.sub(r"^[-—]{2,}\s*$", "", s, flags=re.MULTILINE)
s = re.sub(r"<!--[\s\S]*?-->", "", s)
s = re.sub(r"^[\s]*[-*•]\s+", "", s, flags=re.MULTILINE)
s = re.sub(r"^[\s]*\d+\.\s+", "", s, flags=re.MULTILINE)
s = re.sub(r"\n{3,}", "\n\n", s)
s = s.strip()

# Chunk on paragraph/sentence boundary
paragraphs = [p.strip() for p in s.split("\n") if p.strip()]
chunks, cur = [], ""
for p in paragraphs:
    if len(cur) + len(p) + 1 <= max_chars:
        cur = (cur + "\n" + p) if cur else p
    else:
        if cur: chunks.append(cur)
        if len(p) > max_chars:
            sentences = re.split(r"(?<=[。！？!?])\s*", p)
            buf = ""
            for sent in sentences:
                if not sent: continue
                if len(buf) + len(sent) <= max_chars:
                    buf += sent
                else:
                    if buf: chunks.append(buf)
                    buf = sent
            if buf: chunks.append(buf)
            cur = ""
        else:
            cur = p
if cur: chunks.append(cur)

for i, c in enumerate(chunks, 1):
    with open(os.path.join(chunks_dir, f"chunk_{i:03d}.txt"), "w") as f:
        f.write(c)
print(f"chunks={len(chunks)}")
PYEOF

list_file="$staging/concat.txt"
: > "$list_file"

idx=0
for chunk_file in "$chunks_dir"/chunk_*.txt; do
    idx=$((idx + 1))
    chunk_text="$(cat "$chunk_file")"
    chunk_mp3="$chunks_dir/chunk_$(printf "%03d" "$idx").mp3"
    bash "$provider" "$chunk_text" "$voice_inner" "$chunk_mp3" "$speed" "$rate"
    echo "file '$chunk_mp3'" >> "$list_file"
    sleep 0.5
done

bash "$HERE/merge.sh" "$list_file" "$output"
rm -rf "$staging"
