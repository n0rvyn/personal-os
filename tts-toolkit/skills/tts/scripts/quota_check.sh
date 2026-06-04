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
# MiniMax branch — TokenPlan all-modality shared pool (post-2026-06)
# ---------------------------------------------------------------------------
# As of MiniMax's early-June-2026 M3 / Token Plan upgrade, speech no longer has
# its own `speech-hd` quota row: text / speech / image / music all draw from one
# shared "general" credits pool. `token_plan/remains` reports that pool as a
# REMAINING PERCENT per window (a 5-hour rolling interval + a weekly window); the
# old per-model character counts (current_interval_total_count / usage_count) are
# now zeroed and meaningless. Querying it requires the sk-cp subscription key.
#
# Availability is judged by reset-time AND remaining-quota together (pace-fair):
#   - Weekly window (the real shared budget): pass iff remaining-quota% >=
#     remaining-time% of the week. Spend freely when ahead of the burn pace;
#     yield to fallback when spending faster than the pool refills; and naturally
#     use the last drops right before a weekly reset (threshold → 0 as t → 0).
#     This protects the credits the user's coding / other modalities also need.
#   - 5-hour interval (a burst limiter, not a budget): pass iff it is not
#     exhausted (active + remaining% > 0).
#
# --required-chars / --reserve-pct are accepted for interface compatibility but
# do NOT gate MiniMax here — the shared pool is percent-based, not char-based.
minimax_check() {
    if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
        echo "minimax: MINIMAX_API_KEY required" >&2
        exit 3
    fi

    curl_resp="$(curl -sS \
        -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
        --max-time 15 \
        "https://www.minimaxi.com/v1/token_plan/remains")" || exit 2

    # All schema interpretation + the pace-fair verdict happen in Python; the
    # script prints a single verdict token (always exits 0 so `set -e` / the
    # pipeline can't kill us on a parse hiccup — we map the token below).
    result="$(printf '%s' "$curl_resp" | MM_SUB="$subcommand" python3 -c '
import json, sys, os
sub = os.environ.get("MM_SUB", "check")
try:
    data = json.load(sys.stdin)
except Exception:
    print("PARSE_ERR"); sys.exit(0)
if data.get("base_resp", {}).get("status_code", -1) != 0:
    print("API_ERR " + str(data.get("base_resp", {}).get("status_msg", "unknown"))); sys.exit(0)
gen = next((r for r in data.get("model_remains", []) if r.get("model_name") == "general"), None)
if gen is None:
    print("NO_GENERAL"); sys.exit(0)

def time_pct(remains_field, start_field, end_field):
    tr = gen.get(remains_field); st = gen.get(start_field); en = gen.get(end_field)
    if tr is None or st is None or en is None or (en - st) <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * tr / (en - st)))

iq = gen.get("current_interval_remaining_percent")
ist = gen.get("current_interval_status", 1)
wq = gen.get("current_weekly_remaining_percent")
wst = gen.get("current_weekly_status", 1)
wt = time_pct("weekly_remains_time", "weekly_start_time", "weekly_end_time")

def f(x):
    return ("%.0f" % x) if isinstance(x, (int, float)) else "NA"

msg = "interval[q=%s%% st=%s] weekly[q=%s%% time-left=%s%% st=%s]" % (f(iq), ist, f(wq), f(wt), wst)

# 5-hour interval: just must not be exhausted.
interval_ok = (ist == 1) and (iq is not None) and (iq > 0)
# weekly: pace-fair — quota% must keep up with time-remaining%.
if wq is None:
    weekly_ok = False
elif wt is None:
    weekly_ok = (wst == 1) and (wq > 0)
else:
    weekly_ok = (wst == 1) and (wq >= wt)

if sub == "show":
    print("SHOW " + msg); sys.exit(0)
print(("OK " if (interval_ok and weekly_ok) else "OVER ") + msg)
sys.exit(0)
')"

    case "$result" in
        PARSE_ERR*)  echo "minimax: non-JSON quota response" >&2; exit 2;;
        API_ERR*)    echo "minimax: quota API error: ${result#API_ERR }" >&2; exit 2;;
        NO_GENERAL*) echo "minimax: no 'general' shared-pool row in token_plan/remains (unexpected TokenPlan schema — speech draws from the shared pool since 2026-06)" >&2; exit 2;;
        SHOW\ *)
            echo "minimax shared-pool: ${result#SHOW }"
            return 0;;
        OK\ *)
            echo "minimax shared-pool ok (pace-fair): ${result#OK }" >&2
            echo "minimax ok: ${result#OK }"
            ;;
        OVER\ *)
            echo "minimax shared-pool behind weekly burn-pace → yield to fallback: ${result#OVER }" >&2
            exit 1;;
        *) echo "minimax: unexpected quota result: ${result}" >&2; exit 2;;
    esac
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
