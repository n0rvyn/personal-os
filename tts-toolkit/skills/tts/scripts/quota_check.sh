#!/usr/bin/env bash
# quota_check.sh — Real-time vendor quota check (no local ledger).
#
# Subcommands:
#   check --vendor <minimax|volcengine> --required-chars N [--reserve-pct N] [--model <name>]
#       exit 0: ok (sufficient quota)
#       exit 1: over-budget
#       exit 2: vendor-down / model-not-found
#       exit 3: auth credentials missing
#
#   show --vendor <vendor> [--model <name>]
#       Prints the parsed quota line to stdout for human eyes.
#
# Env (MiniMax):
#   MINIMAX_API_KEY (required)
#
# Env (Volcengine):
#   VOLC_ACCESS_KEY_ID + VOLC_SECRET_ACCESS_KEY  — IAM credentials (different from TTS APPID/TOKEN)
#   VOLC_TTS_DAILY_BUDGET — self-imposed daily char ceiling (required)
#   VOLC_PROJECT_NAME     — Volcengine console project name (default: "default")
#
# No local ledger; every check hits the real vendor API.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Allow tests to inject a stub volcsign.py via TTS_QUOTA_HELPER_DIR
VOLCSIGN="${TTS_QUOTA_HELPER_DIR:+$TTS_QUOTA_HELPER_DIR/volcsign.py}"
VOLCSIGN="${VOLCSIGN:-$HERE/volcsign.py}"

subcommand="${1:-}"
if [[ -z "$subcommand" ]]; then
    echo "usage: quota_check.sh <check|show> --vendor <minimax|volcengine> [options]" >&2
    exit 1
fi
shift

vendor=""
required_chars=0
reserve_pct=0
model=""
tier=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --vendor)       vendor="$2";         shift 2;;
        --required-chars) required_chars="$2"; shift 2;;
        --reserve-pct)  reserve_pct="$2";    shift 2;;
        --model)        model="$2";           shift 2;;
        --tier)         tier="$2";            shift 2;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done

if [[ -z "$vendor" ]]; then
    echo "quota_check: --vendor required (minimax|volcengine)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# MiniMax branch
# ---------------------------------------------------------------------------
minimax_check() {
    if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
        echo "minimax: MINIMAX_API_KEY required" >&2
        exit 3
    fi

    curl_resp="$(curl -sS \
        -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
        --max-time 15 \
        "https://www.minimaxi.com/v1/token_plan/remains")" || exit 2

    # Map variant model name to family name (quota endpoint returns family rows).
    model_in="${model:-speech-2.8-hd}"
    case "$model_in" in
        speech-hd|speech-turbo|MiniMax-*) model_family="$model_in" ;;
        speech-*-hd)    model_family="speech-hd" ;;
        speech-*-turbo) model_family="speech-turbo" ;;
        *)              model_family="$model_in" ;;
    esac

    quota_line="$(echo "$curl_resp" | python3 -c "
import json, sys
data = json.load(sys.stdin)
family = '$model_family'
for row in data.get('model_remains', []):
    if row.get('model_name') == family:
        print(row['current_interval_usage_count'], row['current_interval_total_count'])
        sys.exit(0)
sys.exit(1)
" 2>/dev/null)" || { echo "minimax: model ${model_family} (mapped from ${model_in}) not in response" >&2; exit 2; }

    read -r used total <<<"$quota_line"

    if [[ -z "${used:-}" || -z "${total:-}" ]]; then
        echo "minimax: model ${model_family} (mapped from ${model_in}) not in response" >&2
        exit 2
    fi

    available=$(( total - used ))
    required_with_reserve=$(( required_chars + required_chars * reserve_pct / 100 ))

    echo "minimax quota: family=${model_family} (requested=${model_in}) used=${used}/${total} available=${available}" >&2

    if [[ "$subcommand" == "show" ]]; then
        echo "minimax: used=${used}/${total} available=${available} family=${model_family} (requested=${model_in})"
        return 0
    fi

    if (( available < required_with_reserve )); then
        echo "minimax over-budget: need ${required_with_reserve} (req=${required_chars} +${reserve_pct}% reserve); available=${available} (used=${used}/${total} on family=${model_family}, requested=${model_in})" >&2
        exit 1
    fi
    echo "minimax ok: available=${available}, need=${required_with_reserve}, family=${model_family} (requested=${model_in})"
}

# ---------------------------------------------------------------------------
# Volcengine branch
# ---------------------------------------------------------------------------
volcengine_check() {
    # Prefer IAM-prefixed env names; fall back to legacy unprefixed names for back-compat.
    local _volc_ak="${VOLC_IAM_ACCESS_KEY_ID:-${VOLC_ACCESS_KEY_ID:-}}"
    local _volc_sk="${VOLC_IAM_SECRET_ACCESS_KEY:-${VOLC_SECRET_ACCESS_KEY:-}}"
    if [[ -z "$_volc_ak" || -z "$_volc_sk" ]]; then
        echo "volcengine: VOLC_IAM_ACCESS_KEY_ID + VOLC_IAM_SECRET_ACCESS_KEY required (from IAM 访问控制 console, NOT the speech APPID/TOKEN). Legacy VOLC_ACCESS_KEY_ID/VOLC_SECRET_ACCESS_KEY also accepted." >&2
        exit 3
    fi
    if [[ -z "${VOLC_TTS_DAILY_BUDGET:-}" ]]; then
        echo "volcengine: VOLC_TTS_DAILY_BUDGET required (user-set daily char ceiling, e.g. 20000). NOT the vendor's package size — this is YOUR self-imposed daily cap. Free-tier vs paid status is in the Volcengine console, not here." >&2
        exit 3
    fi
    if [[ -z "${VOLC_TTS_APPID:-}" ]]; then
        echo "volcengine: VOLC_TTS_APPID required to scope UsageMonitoring to a specific AppID. Get it from the Volcengine speech console — it's the same 10-digit numeric ID used for TTS calls." >&2
        exit 3
    fi

    project="${VOLC_PROJECT_NAME:-default}"
    # ResourceID identifies the speech product family being queried. 10029 = 大模型语音合成
    # (the Seed-TTS family covering tts-toolkit's v0.1 scope). Different products use different IDs.
    resource_id="${VOLC_USAGE_RESOURCE_ID:-volc.service_type.10029}"
    # AppID is required by UsageMonitoring even though the official Volcengine doc spec
    # (as of 2026-05-17) does not list it. Omitting it returns 403 UnauthorizedRequest.AppID.
    # Use the same 10-digit speech APPID that authorizes TTS calls.
    appid="$VOLC_TTS_APPID"
    today="$(date -u +%Y-%m-%d)"
    body="$(python3 -c "
import json, sys
print(json.dumps({'ProjectName': '$project', 'ResourceID': '$resource_id', 'AppID': '$appid', 'Mode': 'daily', 'UsageType': 'text_words', 'Start': '$today', 'End': '$today'}))
")"

    # volcsign.py picks up VOLC_IAM_* (preferred) or VOLC_* (legacy) from env automatically.
    resp="$(VOLC_ACCESS_KEY_ID="$_volc_ak" VOLC_SECRET_ACCESS_KEY="$_volc_sk" python3 "$VOLCSIGN" UsageMonitoring speech_saas_prod cn-beijing "$body")" || exit 2
    used="$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('Result',{}).get('UsageMonitoring',[{}])[0].get('Value',0))")"
    daily_budget="$VOLC_TTS_DAILY_BUDGET"

    # Always emit effective budget + today's usage so user catches a wrong env.
    echo "volcengine quota: daily_budget=${daily_budget} (user-supplied via VOLC_TTS_DAILY_BUDGET); used_today=${used}" >&2

    available=$(( daily_budget - used ))
    required_with_reserve=$(( required_chars + required_chars * reserve_pct / 100 ))

    if [[ "$subcommand" == "show" ]]; then
        echo "volcengine: used_today=${used} daily_budget=${daily_budget} available=${available}"
        return 0
    fi

    if (( available < required_with_reserve )); then
        echo "volcengine over-budget: need ${required_with_reserve} (req=${required_chars} +${reserve_pct}% reserve); today_remaining=${available} (used=${used}/budget=${daily_budget})" >&2
        exit 1
    fi
    echo "volcengine ok: today_remaining=${available}, need=${required_with_reserve} (used=${used}/budget=${daily_budget})"
}

# ---------------------------------------------------------------------------
# Volcengine per-model-tier branch (1.0 / 2.0 are separate billing products /
# separate quota pools). UsageMonitoring cannot split them (both public voices
# bill under volc.service_type.10029), so per-tier used_today comes from the
# local ledger written by providers/volcengine.sh — actual input chars synthed.
# ---------------------------------------------------------------------------
volcengine_tier_check() {
    local t="$1" budget_var budget ledger_dir today used available req
    case "$t" in
        1.0) budget_var="VOLC_TTS_DAILY_BUDGET_V1" ;;
        2.0) budget_var="VOLC_TTS_DAILY_BUDGET_V2" ;;
        *) echo "quota_check: --tier must be 1.0 or 2.0 (got '${t}')" >&2; exit 1 ;;
    esac
    budget="${!budget_var:-}"
    if [[ -z "$budget" ]]; then
        echo "volcengine tier ${t}: ${budget_var} required (your self-set daily char ceiling for the ${t} model tier)" >&2
        exit 3
    fi
    ledger_dir="${TTS_LEDGER_DIR:-$HOME/.tts-toolkit/ledger}"
    today="$(date -u +%Y-%m-%d)"
    used="$(awk -F'\t' -v d="$today" -v tr="$t" \
        '$1==d && $2==tr {s+=$3} END{print s+0}' \
        "$ledger_dir/volc-usage.log" 2>/dev/null || echo 0)"
    [[ -n "$used" ]] || used=0
    available=$(( budget - used ))
    req=$(( required_chars + required_chars * reserve_pct / 100 ))

    echo "volcengine tier ${t}: budget=${budget} used_today=${used} available=${available} (local ledger)" >&2

    if [[ "$subcommand" == "show" ]]; then
        echo "volcengine tier ${t}: used_today=${used} budget=${budget} available=${available}"
        return 0
    fi
    if (( available < req )); then
        echo "volcengine tier ${t} over-budget: need ${req} (req=${required_chars} +${reserve_pct}% reserve); available=${available} (used=${used}/budget=${budget})" >&2
        exit 1
    fi
    echo "volcengine tier ${t} ok: available=${available}, need=${req}"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "$vendor" in
    minimax)     minimax_check ;;
    volcengine)
        if [[ -n "$tier" ]]; then
            volcengine_tier_check "$tier"
        else
            volcengine_check
        fi
        ;;
    *) echo "quota_check: unknown vendor: ${vendor} (supported: minimax, volcengine)" >&2; exit 1 ;;
esac
