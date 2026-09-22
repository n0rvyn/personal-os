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
# Vendor priority is CONFIGURABLE, not baked in. Highest source wins:
#   1. `--vendor-pool a,b,c`   per-call
#   2. `TTS_VENDOR_PRIORITY`   standing setting (podcast-studio exports it from
#                              `tts.vendor_priority` in its config YAML)
#   3. built-in default:       minimax → volc-2.0 → volc-1.0 → local
# The order given is the order tried; an unknown name is an error, not a skip.
#
# ⚠️ `local` is the offline sherpa-onnx engine (see providers/local.sh). It is
#    LAST on purpose: it sounds worse than every cloud vendor and its lexicon
#    drops English words. What it gives back is that it cannot run out — once
#    it is installed, a fully-exhausted cloud pool yields a rougher episode
#    instead of no episode.
#
# ⛔ Consequence, stated rather than discovered: with `local` installed, exit 4
#    effectively stops happening on the default pool. The "never a half-spent
#    budget" guarantee is untouched (local spends nothing); what changes is
#    that "no audio at all" is no longer an outcome. Drop it with
#    `--vendor-pool minimax,volc-2.0,volc-1.0` when you specifically want the
#    run to fail rather than ship the offline voice.
#
# Exit codes:
#   0  success (mp3 written to --output; path echoed to stdout)
#   1  argument error
#   4  no vendor has enough quota / is usable — decided before any synthesis
#      (reachable when local is excluded, or not yet installed)
#   other: propagated from synth.sh
#
# Env (read indirectly by quota_check.sh / providers):
#   MINIMAX_API_KEY                                  — MiniMax quota + synth
#   VOLC_TTS_APPID / VOLC_TTS_TOKEN                  — Volcengine synth
#   VOLC_TTS_DAILY_BUDGET_V1 / _V2                   — per-tier self-set ceilings
#   VOLC_IAM_ACCESS_KEY_ID / VOLC_IAM_SECRET_ACCESS_KEY — (optional) UsageMonitoring
#   TTS_LOCAL_VOICE                                  — local speaker (default vits-zh-ll-sid0)
#   TTS_LOCAL_MODEL_DIR / TTS_LOCAL_PYTHON           — offline engine overrides
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
    "local|local-${TTS_LOCAL_VOICE:-vits-zh-ll-sid0}||--vendor local"
)

# --- Priority resolution --------------------------------------------------
# Which vendors, in what order, is CONFIGURATION — not something baked into the
# array above. Three sources, highest wins:
#
#   1. `--vendor-pool a,b,c`   per-call override (a human, or a test)
#   2. `TTS_VENDOR_PRIORITY`   the standing setting; podcast-studio's
#                              `lib/podcast-env.sh` exports it from
#                              `tts.vendor_priority` in the config YAML
#   3. the POOL literal above  the default when nothing is configured
#
# ⭐ The order given is the order tried. Reorder freely — put `local` first to
#    stop spending money; leave it out entirely to make an exhausted pool fail
#    (exit 4) instead of shipping the offline voice.
# ⛔ An unknown name is an ERROR, never a silent skip. A typo'd
#    `TTS_VENDOR_PRIORITY` that quietly fell back to the default would be a
#    setting that looks applied and is not.
_priority="${vendor_pool:-${TTS_VENDOR_PRIORITY:-}}"
if [[ -n "$vendor_pool" ]]; then _priority_src="--vendor-pool"
else                             _priority_src="TTS_VENDOR_PRIORITY"; fi

if [[ -n "$_priority" ]]; then
    declare -a FILTERED=()
    IFS=',' read -ra _want <<<"$_priority"
    for _w in "${_want[@]}"; do
        # Tolerate whitespace around names ("minimax, local").
        _w="${_w#"${_w%%[![:space:]]*}"}"; _w="${_w%"${_w##*[![:space:]]}"}"
        [[ -n "$_w" ]] || continue
        _found=0
        for _entry in "${POOL[@]}"; do
            if [[ "${_entry%%|*}" == "$_w" ]]; then FILTERED+=("$_entry"); _found=1; fi
        done
        [[ "$_found" -eq 1 ]] || {
            echo "synth-auto: ${_priority_src} names an unknown vendor: '${_w}'" >&2
            echo "synth-auto: known vendors: ${POOL[*]%%|*}" >&2
            exit 1; }
    done
    [[ ${#FILTERED[@]} -gt 0 ]] || {
        echo "synth-auto: ${_priority_src} '$_priority' matched no known vendor" >&2; exit 1; }
    POOL=("${FILTERED[@]}")
    echo "synth-auto: vendor priority from ${_priority_src}: ${POOL[*]%%|*}" >&2
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

# --- Synthesize the whole job on one vendor, with watchdog keep-alive ------
# synth.sh writes chunk mp3s to $TMPDIR, not the workspace, so a long run (many
# chunks + rate-limit waits) leaves the workspace mtime stale and a host
# watchdog may kill the task as hung. Touch a progress file next to the output
# every ~45s while synth runs. Returns synth.sh's exit code.
run_synth_with_progress() {
    local _voice="$1" _label="$2" progress synth_pid ticks rc
    progress="$(dirname "$output")/.tts-auto-progress"
    # TTS_VIA_SYNTH_AUTO lets synth.sh's batch boundary guard recognize this as
    # synth-auto's own (sanctioned) call rather than a hand-rolled bypass.
    TTS_VIA_SYNTH_AUTO=1 bash "$SYNTH" --segments "$seg_json" --voice "$_voice" \
        --output "$output" --concurrency "$concurrency" &
    synth_pid=$!
    ticks=0
    while kill -0 "$synth_pid" 2>/dev/null; do
        if (( ticks % 45 == 0 )); then
            { date -u +%Y-%m-%dT%H:%M:%SZ; echo "synth-auto: ${_label} synthesizing ${total_chars} chars"; } \
                > "$progress" 2>/dev/null || true
        fi
        ticks=$(( ticks + 1 ))
        sleep 1
    done
    rc=0
    wait "$synth_pid" || rc=$?
    rm -f "$progress"
    return "$rc"
}

# --- Walk the pool: the first vendor with quota synthesizes; if its SYNTHESIS
# --- fails, fall back to the next quota-OK vendor (issue #279, A2).
# Pre-flight still guarantees synthesis never *starts* unless a vendor has quota
# (exit 4 otherwise — zero characters spent). The added robustness: if a
# selected vendor fails mid-run, we retry on the next vendor instead of failing
# the whole job. Tradeoff (user-approved): a vendor that fails partway may have
# spent some of its budget before we fall back — a complete podcast beats a
# half-spent abort. Each vendor uses a distinct synth.sh staging dir (keyed by
# voice), so no cross-vendor chunk contamination.
declare -a tried=()
quota_passed=0          # did ANY vendor clear the quota check? (exit 4 vs synth-fail)
last_synth_rc=0
succeeded=0
final_vendor=""

for entry in "${POOL[@]}"; do
    IFS='|' read -r name voice resource qargs <<<"$entry"
    rc=0
    # shellcheck disable=SC2086 — qargs is intentional multi-word.
    bash "$QUOTA" check $qargs --required-chars "$total_chars" --reserve-pct "$reserve_pct" 1>&2 || rc=$?
    case "$rc" in
        0) : ;;  # quota OK → attempt synthesis below
        1) tried+=("${name}: over-budget");   echo "synth-auto: ${name} over budget → next" >&2; continue;;
        2) tried+=("${name}: vendor-down");   echo "synth-auto: ${name} vendor unavailable → next" >&2; continue;;
        3) tried+=("${name}: auth-missing");  echo "synth-auto: ${name} auth/config missing → next" >&2; continue;;
        *) tried+=("${name}: check-error rc=${rc}"); echo "synth-auto: ${name} quota check errored (rc=${rc}) → next" >&2; continue;;
    esac

    quota_passed=1
    echo "synth-auto: selected '${name}' (voice: ${voice}) — synthesizing" >&2
    # Per-vendor resource id (Volcengine tier); unset so a previous vendor's id
    # never leaks into MiniMax or a different tier.
    if [[ -n "$resource" ]]; then export VOLC_TTS_RESOURCE_ID="$resource"; else unset VOLC_TTS_RESOURCE_ID; fi

    rc=0
    run_synth_with_progress "$voice" "$name" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        succeeded=1; final_vendor="$name"; break
    fi
    last_synth_rc="$rc"
    tried+=("${name}: synth-failed rc=${rc}")
    echo "synth-auto: ${name} synthesis FAILED (rc=${rc}) → falling back to next vendor" >&2
done

if [[ "$succeeded" -eq 1 ]]; then
    echo "synth-auto: done — ${output} (vendor: ${final_vendor})" >&2
    echo "$output"
    exit 0
fi

if [[ "$quota_passed" -eq 0 ]]; then
    {
        echo "synth-auto: NO VENDOR can complete this ${total_chars}-char job (+${reserve_pct}% reserve)."
        echo "synth-auto: aborting BEFORE synthesis — zero characters spent. Vendor results:"
        printf 'synth-auto:   - %s\n' "${tried[@]}"
    } >&2
    exit 4
fi

{
    echo "synth-auto: all quota-OK vendors FAILED synthesis. Vendor results:"
    printf 'synth-auto:   - %s\n' "${tried[@]}"
} >&2
exit "$last_synth_rc"
