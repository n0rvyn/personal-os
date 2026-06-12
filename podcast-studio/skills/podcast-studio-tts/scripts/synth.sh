#!/usr/bin/env bash
# tts-toolkit entry point. Routes to a provider script by voice-id prefix.
#
# Modes:
#   Single:   --text <s>       --voice <id> --output <path> [--speed --rate]
#   Batch:    --input <md>     --voice <id> --output <path> [--speed --rate --max-chars --concurrency]
#   Segments: --segments <json> --voice <id> --output <path> [--speed --rate --concurrency]
#
# Voice prefix routing:
#   volc-* → providers/volcengine.sh
#   mm-*   → providers/minimax.sh
#
# Env overrides (for tests / CI):
#   TTS_PROVIDER_OVERRIDE  — path to a stub provider script (bypasses prefix routing)
#   TTS_CHUNKER_PATH       — override path to chunker.py (bypasses auto-detection)

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# chunker.py is vendored into this skill's own scripts/ dir — a Claude plugin
# cannot reference files outside its directory once installed, so the chunker
# is a sibling of this script, not a cross-plugin path.
TTS_CHUNKER="${TTS_CHUNKER_PATH:-$HERE/chunker.py}"
if [[ ! -f "$TTS_CHUNKER" ]]; then
    echo "synth: chunker.py not found at $TTS_CHUNKER (set TTS_CHUNKER_PATH to override)" >&2
    exit 1
fi

text=""
input=""
segments=""
voice=""
output=""
speed="1.0"
rate="24000"
max_chars="280"
concurrency=2
model=""
provider_override="${TTS_PROVIDER_OVERRIDE:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --text)              text="$2";              shift 2;;
        --input)             input="$2";             shift 2;;
        --segments)          segments="$2";          shift 2;;
        --voice)             voice="$2";             shift 2;;
        --output)            output="$2";            shift 2;;
        --speed)             speed="$2";             shift 2;;
        --rate)              rate="$2";              shift 2;;
        --max-chars)         max_chars="$2";         shift 2;;
        --concurrency)       concurrency="$2";       shift 2;;
        --model)             model="$2";             shift 2;;
        --provider-override) provider_override="$2"; shift 2;;
        -h|--help)
            cat <<'EOF'
Usage:
  synth.sh --text <s>        --voice <id> --output <path> [--speed 1.0] [--rate 24000]
  synth.sh --input <md>      --voice <id> --output <path> [--speed 1.0] [--rate 24000] [--max-chars 280] [--concurrency 2]
  synth.sh --segments <json> --voice <id> --output <path> [--speed 1.0] [--rate 24000] [--concurrency 2]

Voice prefix routing:
  volc-*  Volcengine (Doubao) TTS
  mm-*    MiniMax TTS

Flags:
  --model <name>       MiniMax model name (default: speech-2.8-hd); env MINIMAX_MODEL also works
  --concurrency <N>    Max parallel provider calls for batch/segments mode (default: 2)
  --max-chars <N>      Max chars per chunk when using --input mode (default: 280)

Env:
  TTS_PROVIDER_OVERRIDE  Override provider script path (for tests)
  TTS_CHUNKER_PATH       Override path to text-to-segments chunker.py
EOF
            exit 0;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done

if [[ -z "$voice" || -z "$output" ]]; then
    echo "missing --voice or --output" >&2; exit 1
fi

# Exactly one input mode required
mode_count=0
[[ -n "$text" ]]     && mode_count=$(( mode_count + 1 ))
[[ -n "$input" ]]    && mode_count=$(( mode_count + 1 ))
[[ -n "$segments" ]] && mode_count=$(( mode_count + 1 ))
if [[ "$mode_count" -eq 0 ]]; then
    echo "must pass exactly one of --text, --input, or --segments" >&2; exit 1
fi
if [[ "$mode_count" -gt 1 ]]; then
    echo "--text, --input, and --segments are mutually exclusive" >&2; exit 1
fi

# --- Boundary guard (harness, issue #279) ---------------------------------
# Unattended long-form batch MUST go through synth-auto.sh, which does
# quota-aware vendor selection + cross-vendor fallback. A direct batch call
# bypasses vendor selection — that is exactly how run-to-run vendor drift
# happened (an executor hand-rolled `synth.sh --segments --voice <volc>` and
# pinned one vendor, so MiniMax never got picked). Refuse direct batch unless
# we were launched by synth-auto (TTS_VIA_SYNTH_AUTO=1) or a human/test
# explicitly opts in (TTS_ALLOW_DIRECT_BATCH=1). Single --text is unaffected.
if [[ ( -n "$input" || -n "$segments" ) \
      && -z "${TTS_VIA_SYNTH_AUTO:-}" \
      && -z "${TTS_ALLOW_DIRECT_BATCH:-}" ]]; then
    echo "synth.sh: direct batch (--input/--segments) is disabled — long-form must run through synth-auto.sh for quota-aware vendor selection + fallback." >&2
    echo "synth.sh: re-run:  synth-auto.sh --input <file> --output <mp3>" >&2
    echo "synth.sh: (advanced single-vendor batch, bypassing fallback: set TTS_ALLOW_DIRECT_BATCH=1)" >&2
    exit 1
fi

# Voice-prefix routing: always validate prefix; provider_override only replaces the
# binary, not the validation. voice_inner strips the known prefix for the provider call.
case "$voice" in
    volc-*) provider="$HERE/providers/volcengine.sh"; voice_inner="${voice#volc-}";;
    mm-*)   provider="$HERE/providers/minimax.sh";    voice_inner="${voice#mm-}";;
    *)
        echo "unsupported voice prefix: $voice" >&2
        echo "supported: volc-* (Volcengine), mm-* (MiniMax)" >&2
        exit 1;;
esac
# Provider override: replace binary but keep voice_inner from prefix strip above.
if [[ -n "$provider_override" ]]; then
    provider="$provider_override"
    voice_inner="$voice"
fi

# Portable sha256 (macOS shasum / Linux sha256sum).
_sha256() { if command -v shasum >/dev/null 2>&1; then shasum -a 256; else sha256sum; fi; }

# Retryable transient rate-limit signatures across providers (MiniMax 1002/1039,
# generic English/Chinese phrasing). A 1002 is a passing RPM blip — it must not
# escalate into a whole-run abort that an outer step-retry would re-bill.
RATELIMIT_RE='code=1002|code=1039|rate.?limit|限流|too many request'

# Helper: invoke the provider for one chunk, with bounded retry on transient
# rate-limit. Synthesizes to a .partial path and renames on success, so a
# resumed run never sees (and never skips) a half-written chunk.
call_provider() {
    local chunk_text="$1" chunk_out="$2"
    # Keep a real .mp3 extension on the temp path — some providers/encoders
    # (e.g. ffmpeg) infer the output muxer from the extension.
    local tmp_out="${chunk_out}.partial.mp3"
    local attempt=1 max_attempts="${TTS_RATELIMIT_RETRIES:-3}" rc errf
    errf="$(mktemp -t tts-chunk-err-XXXXXX)"
    while :; do
        rc=0
        if [[ -z "$provider_override" && "$voice" == mm-* ]]; then
            MINIMAX_MODEL="${model:-${MINIMAX_MODEL:-speech-2.8-hd}}" \
                bash "$provider" "$chunk_text" "$voice_inner" "$tmp_out" "$speed" "$rate" 2>"$errf" || rc=$?
        else
            bash "$provider" "$chunk_text" "$voice_inner" "$tmp_out" "$speed" "$rate" 2>"$errf" || rc=$?
        fi
        cat "$errf" >&2
        if [[ "$rc" -eq 0 ]]; then
            mv -f "$tmp_out" "$chunk_out"
            rm -f "$errf"
            return 0
        fi
        if grep -qiE "$RATELIMIT_RE" "$errf" && [[ "$attempt" -lt "$max_attempts" ]]; then
            local wait_s=$(( 30 * attempt ))
            echo "synth.sh: chunk rate-limited (attempt ${attempt}/${max_attempts}), waiting ${wait_s}s" >&2
            rm -f "$tmp_out"
            sleep "$wait_s"
            attempt=$(( attempt + 1 ))
            continue
        fi
        rm -f "$errf" "$tmp_out"
        return "$rc"
    done
}

# Bash ≥ 4.3 check for wait -n (parallel concurrency)
bash_supports_wait_n() {
    [[ "${BASH_VERSINFO[0]:-0}" -gt 4 ]] && return 0
    [[ "${BASH_VERSINFO[0]:-0}" -eq 4 && "${BASH_VERSINFO[1]:-0}" -ge 3 ]] && return 0
    return 1
}
if ! bash_supports_wait_n; then
    if [[ "$concurrency" -gt 1 ]]; then
        echo "synth.sh: bash ${BASH_VERSION} lacks 'wait -n'; falling back to --concurrency 1. For parallel synthesis, brew install bash and prepend its bin to PATH." >&2
    fi
    concurrency=1
fi

# --- Single mode ----------------------------------------------------------
if [[ -n "$text" ]]; then
    call_provider "$text" "$output"
    exit 0
fi

# --- Batch/Segments mode --------------------------------------------------
# Deterministic staging dir keyed by input content + voice + params. An
# interrupted batch (or an outer step-retry) reuses this dir and resumes —
# skipping already-synthesized chunks — instead of re-billing from chunk 1.
_batch_src="${input:-$segments}"
if [[ -n "$_batch_src" && -f "$_batch_src" ]]; then
    _batch_key="$(_sha256 < "$_batch_src" | cut -d' ' -f1)|${voice}|${speed}|${rate}|${max_chars}"
else
    _batch_key="$(date +%s%N)|${voice}|${speed}|${rate}|${max_chars}"
fi
_batch_hash="$(printf '%s' "$_batch_key" | _sha256 | cut -c1-16)"
staging="${TMPDIR:-/tmp}/tts-batch-${_batch_hash}"
chunks_dir="$staging/chunks"
mkdir -p "$chunks_dir"

if [[ -n "$input" ]]; then
    # --input mode: call external chunker, produce segments.json, then treat as --segments
    [[ -f "$input" ]] || { echo "input not found: $input" >&2; rm -rf "$staging"; exit 1; }
    python3 "$TTS_CHUNKER" \
        --input "$input" \
        --output "$staging/segments.json" \
        --max-chars "$max_chars" \
        --vendor-format generic
    chunk_count="$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))['segments']))" "$staging/segments.json")"
    echo "chunks=$chunk_count" >&2
    segments="$staging/segments.json"
fi

# --segments mode (also reached from --input after chunker runs):
# Read .segments[].text — segment .id is informational; synth.sh assigns its own
# chunk_NNN ordering for staging. This is intentional: decouples synth ordering
# from the chunker's ID scheme, preventing future drift.
[[ -f "$segments" ]] || { echo "segments file not found: $segments" >&2; rm -rf "$staging"; exit 1; }

python3 - "$segments" "$chunks_dir" <<'PYEOF'
import json, sys, os
seg_file, chunks_dir = sys.argv[1], sys.argv[2]
with open(seg_file) as f:
    data = json.load(f)
# Accept both {"segments":[...]} and a bare [...] array
segs = data if isinstance(data, list) else data.get("segments", [])
for i, seg in enumerate(segs, 1):
    text = seg["text"]
    with open(os.path.join(chunks_dir, f"chunk_{i:03d}.txt"), "w") as out:
        out.write(text)
PYEOF

list_file="$staging/concat.txt"
: > "$list_file"

# Worker pool with optional parallelism (bash 4.3+ wait -n)
running=0
idx=0
for chunk_file in "$chunks_dir"/chunk_*.txt; do
    idx=$(( idx + 1 ))
    chunk_text="$(cat "$chunk_file")"
    chunk_mp3="$chunks_dir/chunk_$(printf "%03d" "$idx").mp3"
    echo "file '$chunk_mp3'" >> "$list_file"

    # Resume: a chunk mp3 exists only if call_provider fully succeeded (atomic
    # .partial→rename), so presence == done. Skip → no re-bill on retry.
    if [[ -s "$chunk_mp3" ]]; then
        echo "synth.sh: resume — chunk $(printf '%03d' "$idx") already done, skip" >&2
        continue
    fi

    if [[ "$concurrency" -gt 1 ]]; then
        call_provider "$chunk_text" "$chunk_mp3" &
        running=$(( running + 1 ))
        if [[ "$running" -ge "$concurrency" ]]; then
            wait -n
            running=$(( running - 1 ))
            # 1.0 s inter-batch sleep to stay under MiniMax RPM
            # (per 周杰伦 Role's learned rule; widened 0.5→1.0 after a
            #  2026-06-03 speech-2.8-hd rpm pre-warning email)
            sleep 1.0
        fi
    else
        call_provider "$chunk_text" "$chunk_mp3"
        sleep 1.0
    fi
done

# Wait for remaining parallel workers
if [[ "$concurrency" -gt 1 && "$running" -gt 0 ]]; then
    wait
fi

bash "$HERE/merge.sh" "$list_file" "$output"

# Intentional: no EXIT/ERR trap. Threat model guarantees staging dir is kept on
# error/SIGINT for forensic inspection. Adding a defensive trap would silently
# break that contract. If you must add cleanup, gate it behind a successful merge.
rm -rf "$staging"
