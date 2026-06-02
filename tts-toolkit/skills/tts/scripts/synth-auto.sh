#!/usr/bin/env bash
# synth-auto.sh — quota-aware TTS orchestration on top of synth.sh.
#
# Pre-flight: estimate the WHOLE job's character count, then walk a vendor pool
# in priority order and pick the FIRST vendor that has enough quota for the
# entire job. Synthesize the whole job on that one vendor. If no vendor has
# enough, abort BEFORE synthesizing a single character — no half-spent budget,
# no half-made podcast.
#
# synth.sh stays single-vendor / no-fallback by design; this script is the
# fallback layer above it.
#
# Usage:
#   synth-auto.sh --input <md>      --output <mp3> [--reserve-pct 25] [--concurrency 2] [--max-chars 280] [--vendor-pool a,b]
#   synth-auto.sh --segments <json> --output <mp3> [--reserve-pct 25] [--concurrency 2] [--vendor-pool a,b]
#
# Default vendor pool (priority order): minimax → volc-2.0 → volc-1.0
#
# Exit codes:
#   0  success (mp3 written to --output; path echoed to stdout)
#   1  argument error
#   4  no vendor has enough quota / is usable — decided before any synthesis
#   other: propagated from synth.sh
#
# Env (read indirectly by quota_check.sh / providers):
#   MINIMAX_API_KEY                                  — MiniMax quota + synth
#   VOLC_TTS_APPID / VOLC_TTS_TOKEN                  — Volcengine synth
#   VOLC_TTS_DAILY_BUDGET_V1 / _V2                   — per-tier self-set ceilings
#   VOLC_IAM_ACCESS_KEY_ID / VOLC_IAM_SECRET_ACCESS_KEY — (optional) UsageMonitoring
#   TTS_CHUNKER_PATH, TTS_LEDGER_DIR                 — overrides for tests

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SYNTH="$HERE/synth.sh"
QUOTA="$HERE/quota_check.sh"
# chunker.py is vendored as a sibling — tts-toolkit is self-contained, no
# cross-plugin path (installed plugins cannot reach outside their own dir).
CHUNKER="${TTS_CHUNKER_PATH:-$HERE/chunker.py}"

input="" segments="" output=""
reserve_pct=25 concurrency=2 max_chars=280 vendor_pool=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)        input="$2";        shift 2;;
        --segments)     segments="$2";     shift 2;;
        --output)       output="$2";       shift 2;;
        --reserve-pct)  reserve_pct="$2";  shift 2;;
        --concurrency)  concurrency="$2";  shift 2;;
        --max-chars)    max_chars="$2";    shift 2;;
        --vendor-pool)  vendor_pool="$2";  shift 2;;
        -h|--help)
            grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
            exit 0;;
        *) echo "synth-auto: unknown arg: $1" >&2; exit 1;;
    esac
done

[[ -n "$output" ]] || { echo "synth-auto: --output required" >&2; exit 1; }
mode_count=0
[[ -n "$input" ]]    && mode_count=$(( mode_count + 1 ))
[[ -n "$segments" ]] && mode_count=$(( mode_count + 1 ))
[[ "$mode_count" -eq 1 ]] || {
    echo "synth-auto: pass exactly one of --input or --segments" >&2; exit 1; }
[[ -f "$CHUNKER" ]] || {
    echo "synth-auto: chunker not found at $CHUNKER (set TTS_CHUNKER_PATH)" >&2; exit 1; }

# --- Vendor pool ----------------------------------------------------------
# entry = name|voice|resource_id|quota_check args   (resource_id empty for MiniMax)
declare -a POOL=(
    "minimax|mm-Chinese (Mandarin)_Radio_Host||--vendor minimax"
    "volc-2.0|volc-zh_male_yuanboxiaoshu_uranus_bigtts|seed-tts-2.0|--vendor volcengine --tier 2.0"
    "volc-1.0|volc-zh_male_M392_conversation_wvae_bigtts|seed-tts-1.0|--vendor volcengine --tier 1.0"
)

if [[ -n "$vendor_pool" ]]; then
    declare -a FILTERED=()
    IFS=',' read -ra _want <<<"$vendor_pool"
    for _w in "${_want[@]}"; do
        for _entry in "${POOL[@]}"; do
            [[ "${_entry%%|*}" == "$_w" ]] && FILTERED+=("$_entry")
        done
    done
    [[ ${#FILTERED[@]} -gt 0 ]] || {
        echo "synth-auto: --vendor-pool '$vendor_pool' matched no known vendor" >&2; exit 1; }
    POOL=("${FILTERED[@]}")
fi

# --- Chunk once, estimate total chars -------------------------------------
work_dir="$(mktemp -d -t tts-auto-XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT
seg_json="$work_dir/segments.json"

if [[ -n "$input" ]]; then
    [[ -f "$input" ]] || { echo "synth-auto: input not found: $input" >&2; exit 1; }
    # Chunk ONCE here (generic format → has char_count + metadata.total_chars);
    # synth.sh is then called in --segments mode so it never re-chunks.
    python3 "$CHUNKER" --input "$input" --output "$seg_json" \
        --max-chars "$max_chars" --vendor-format generic
else
    [[ -f "$segments" ]] || { echo "synth-auto: segments not found: $segments" >&2; exit 1; }
    cp "$segments" "$seg_json"
fi

total_chars="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
meta = d.get("metadata", {}) if isinstance(d, dict) else {}
if isinstance(meta, dict) and "total_chars" in meta:
    print(int(meta["total_chars"]))
else:
    segs = d if isinstance(d, list) else d.get("segments", [])
    print(sum(len(s.get("text", "")) for s in segs))
' "$seg_json")"

if [[ -z "$total_chars" || "$total_chars" -le 0 ]]; then
    echo "synth-auto: empty/zero-char input — nothing to synthesize" >&2
    exit 1
fi
echo "synth-auto: full job ≈ ${total_chars} chars (+${reserve_pct}% reserve)" >&2

# --- Pre-flight: pick the first vendor that can do the WHOLE job ----------
selected_name="" selected_voice="" selected_resource=""
declare -a tried=()

for entry in "${POOL[@]}"; do
    IFS='|' read -r name voice resource qargs <<<"$entry"
    rc=0
    # shellcheck disable=SC2086 — qargs is intentional multi-word.
    bash "$QUOTA" check $qargs --required-chars "$total_chars" --reserve-pct "$reserve_pct" 1>&2 || rc=$?
    case "$rc" in
        0) selected_name="$name"; selected_voice="$voice"; selected_resource="$resource"; break;;
        1) tried+=("${name}: over-budget");   echo "synth-auto: ${name} over budget → next" >&2;;
        2) tried+=("${name}: vendor-down");   echo "synth-auto: ${name} vendor unavailable → next" >&2;;
        3) tried+=("${name}: auth-missing");  echo "synth-auto: ${name} auth/config missing → next" >&2;;
        *) tried+=("${name}: check-error rc=${rc}"); echo "synth-auto: ${name} quota check errored (rc=${rc}) → next" >&2;;
    esac
done

if [[ -z "$selected_name" ]]; then
    {
        echo "synth-auto: NO VENDOR can complete this ${total_chars}-char job (+${reserve_pct}% reserve)."
        echo "synth-auto: aborting BEFORE synthesis — zero characters spent. Vendor results:"
        printf 'synth-auto:   - %s\n' "${tried[@]}"
    } >&2
    exit 4
fi
echo "synth-auto: selected '${selected_name}' (voice: ${selected_voice})" >&2

# --- Synthesize the whole job on the selected vendor ----------------------
[[ -n "$selected_resource" ]] && export VOLC_TTS_RESOURCE_ID="$selected_resource"

# Watchdog keep-alive: synth.sh writes chunk mp3s to $TMPDIR, not the workspace,
# so a long run (many chunks + rate-limit waits) leaves the workspace mtime
# stale and a host watchdog may kill the task as hung. Touch a progress file
# next to the output every 45s while synth runs.
progress="$(dirname "$output")/.tts-auto-progress"
bash "$SYNTH" --segments "$seg_json" --voice "$selected_voice" \
    --output "$output" --concurrency "$concurrency" &
synth_pid=$!
# Poll every 1s so completion is detected promptly; refresh the progress file
# (workspace mtime) every ~45 ticks.
ticks=0
while kill -0 "$synth_pid" 2>/dev/null; do
    if (( ticks % 45 == 0 )); then
        { date -u +%Y-%m-%dT%H:%M:%SZ; echo "synth-auto: ${selected_name} synthesizing ${total_chars} chars"; } \
            > "$progress" 2>/dev/null || true
    fi
    ticks=$(( ticks + 1 ))
    sleep 1
done
rc=0
wait "$synth_pid" || rc=$?
rm -f "$progress"

if [[ "$rc" -eq 0 ]]; then
    echo "synth-auto: done — ${output}" >&2
    echo "$output"
fi
exit "$rc"
