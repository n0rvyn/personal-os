#!/usr/bin/env bats
# Test suite for scripts/quota_check.sh
# Target: 10 tests, 0 failures.

HERE="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
QUOTA_CHECK="$HERE/../scripts/quota_check.sh"
VOLCSIGN="$HERE/../scripts/volcsign.py"
FIXTURES="$HERE/fixtures/quota"

setup() {
    export STUB_DIR="$BATS_TMPDIR/stubs_${BATS_TEST_NUMBER}"
    mkdir -p "$STUB_DIR"

    # Create a stub curl that cats a fixture file set per test via QUOTA_CURL_FIXTURE env
    cat > "$STUB_DIR/curl" <<'STUB'
#!/usr/bin/env bash
# Ignore all curl args; cat the fixture set by QUOTA_CURL_FIXTURE
cat "$QUOTA_CURL_FIXTURE"
STUB
    chmod +x "$STUB_DIR/curl"

    # Create a stub volcsign.py that cats a fixture set via QUOTA_VOLCSIGN_FIXTURE env
    cat > "$STUB_DIR/volcsign.py" <<'STUB'
#!/usr/bin/env python3
import sys, os
fixture = os.environ.get("QUOTA_VOLCSIGN_FIXTURE", "")
if not fixture:
    sys.stderr.write("volcsign stub: QUOTA_VOLCSIGN_FIXTURE not set\n")
    sys.exit(2)
with open(fixture) as f:
    print(f.read(), end="")
STUB

    export PATH="$STUB_DIR:$PATH"
    export TTS_QUOTA_HELPER_DIR="$STUB_DIR"
}

teardown() {
    rm -rf "$STUB_DIR"
}

# ---------------------------------------------------------------------------
# MiniMax tests — TokenPlan all-modality shared pool (post-2026-06).
# Speech draws from the shared "general" credits pool, reported as remaining
# PERCENT per window. Availability is pace-fair: weekly quota% must keep up with
# weekly time-remaining%; the 5-hour interval just must not be exhausted.
# ---------------------------------------------------------------------------

# Build a token_plan/remains fixture with a "general" shared-pool row.
# Args: interval_q interval_status weekly_q weekly_status weekly_time_left_frac
_mm_general_fixture() {
    local iq="$1" ist="$2" wq="$3" wst="$4" wfrac="$5"
    local f="$BATS_TMPDIR/mm_general_${BATS_TEST_NUMBER}.json"
    iq="$iq" ist="$ist" wq="$wq" wst="$wst" wfrac="$wfrac" python3 -c '
import json, os
week = 604800000  # 7d in ms
print(json.dumps({"base_resp": {"status_code": 0, "status_msg": "success"},
  "model_remains": [{
    "model_name": "general",
    "start_time": 0, "end_time": 18000000, "remains_time": 9000000,
    "current_interval_remaining_percent": int(os.environ["iq"]),
    "current_interval_status": int(os.environ["ist"]),
    "current_weekly_remaining_percent": int(os.environ["wq"]),
    "current_weekly_status": int(os.environ["wst"]),
    "weekly_start_time": 0, "weekly_end_time": week,
    "weekly_remains_time": int(week * float(os.environ["wfrac"])),
  }]}))' > "$f"
    echo "$f"
}

@test "minimax available when weekly quota ahead of burn pace" {
    export MINIMAX_API_KEY="test-key"
    # weekly quota 80% with 50% of the week left => 80 >= 50 => ok
    export QUOTA_CURL_FIXTURE="$(_mm_general_fixture 90 1 80 1 0.50)"
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 8645 --reserve-pct 25
    [ "$status" -eq 0 ]
    [[ "$output" == *"minimax ok"* ]]
}

@test "minimax yields when weekly quota behind burn pace" {
    export MINIMAX_API_KEY="test-key"
    # weekly quota 20% with 50% of the week left => 20 < 50 => over (yield to fallback)
    export QUOTA_CURL_FIXTURE="$(_mm_general_fixture 90 1 20 1 0.50)"
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 8645
    [ "$status" -eq 1 ]
    [[ "$output" == *"behind weekly burn-pace"* ]]
}

@test "minimax uses last drops right before a weekly reset (low quota, near reset => ok)" {
    export MINIMAX_API_KEY="test-key"
    # weekly quota 10% but only ~1.4% of the week left => 10 >= 1.4 => ok
    export QUOTA_CURL_FIXTURE="$(_mm_general_fixture 90 1 10 1 0.014)"
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 8645
    [ "$status" -eq 0 ]
    [[ "$output" == *"minimax ok"* ]]
}

@test "minimax yields when the 5-hour interval is exhausted even if weekly is healthy" {
    export MINIMAX_API_KEY="test-key"
    # interval quota 0% (exhausted) but weekly fine => over
    export QUOTA_CURL_FIXTURE="$(_mm_general_fixture 0 1 88 1 0.50)"
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 100
    [ "$status" -eq 1 ]
}

@test "minimax no 'general' shared-pool row yields exit 2" {
    export MINIMAX_API_KEY="test-key"
    local f="$BATS_TMPDIR/mm_nogeneral_${BATS_TEST_NUMBER}.json"
    python3 -c "import json; print(json.dumps({'base_resp':{'status_code':0,'status_msg':'success'},'model_remains':[{'model_name':'video','current_interval_total_count':3}]}))" > "$f"
    export QUOTA_CURL_FIXTURE="$f"
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 1000
    [ "$status" -eq 2 ]
    [[ "$output" == *"general"* ]]
}

@test "minimax missing MINIMAX_API_KEY yields exit 3" {
    unset MINIMAX_API_KEY
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 1000
    [ "$status" -eq 3 ]
    [[ "$output" == *"MINIMAX_API_KEY"* ]]
}

# ---------------------------------------------------------------------------
# Volcengine tests
# ---------------------------------------------------------------------------

@test "volcengine missing AK/SK yields exit 3" {
    unset VOLC_ACCESS_KEY_ID VOLC_SECRET_ACCESS_KEY
    export VOLC_TTS_DAILY_BUDGET=20000
    run bash "$QUOTA_CHECK" check --vendor volcengine --required-chars 1000
    [ "$status" -eq 3 ]
    [[ "$output" == *"VOLC_ACCESS_KEY_ID"* ]]
}

@test "volcengine missing VOLC_TTS_DAILY_BUDGET yields exit 3" {
    export VOLC_ACCESS_KEY_ID="test-ak"
    export VOLC_SECRET_ACCESS_KEY="test-sk"
    unset VOLC_TTS_DAILY_BUDGET
    run bash "$QUOTA_CHECK" check --vendor volcengine --required-chars 1000
    [ "$status" -eq 3 ]
    [[ "$output" == *"VOLC_TTS_DAILY_BUDGET"* ]]
}

@test "show subcommand prints shared-pool interval/weekly percents for minimax" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$(_mm_general_fixture 90 1 88 1 0.50)"
    run bash "$QUOTA_CHECK" show --vendor minimax
    [ "$status" -eq 0 ]
    [[ "$output" == *"interval["* ]]
    [[ "$output" == *"weekly["* ]]
}

@test "volcsign.py REAL binary never echoes VOLC_IAM_SECRET_ACCESS_KEY on network error" {
    # Exercises the REAL volcsign.py against an unreachable host to drive the
    # URLError path of the HTTPError handler. The sentinel SK must NOT appear
    # in stdout / stderr / any /tmp file the script could have left behind.
    local sentinel="sentinel-DO-NOT-LEAK-${RANDOM}-XYZ"
    export VOLC_IAM_ACCESS_KEY_ID="test-iam-ak-id"
    export VOLC_IAM_SECRET_ACCESS_KEY="$sentinel"
    # Force network error path — 0.0.0.0:443 is not bound, urlopen returns URLError.
    export VOLC_API_HOST="0.0.0.0"

    # Snapshot any existing /tmp files containing the sentinel BEFORE the run
    # (paranoia — defends against false-positive from a leftover from another test).
    local pre_count
    pre_count=$(grep -rl "$sentinel" /tmp 2>/dev/null | wc -l | tr -d ' ')

    # Call the REAL volcsign.py directly — bypass any stubs the setup() block left behind.
    run python3 "$VOLCSIGN" UsageMonitoring speech_saas_prod cn-beijing '{"ProjectName":"default","ResourceID":"volc.service_type.10029","AppID":"0000000000","Mode":"daily","UsageType":"text_words","Start":"2026-01-01","End":"2026-01-01"}'

    # Must exit non-zero (network error → exit 2 per volcsign.py contract).
    [ "$status" -ne 0 ]

    # Sentinel must NOT appear in combined stdout+stderr.
    [[ "$output" != *"$sentinel"* ]]

    # And no new /tmp file should contain the sentinel.
    local post_count
    post_count=$(grep -rl "$sentinel" /tmp 2>/dev/null | wc -l | tr -d ' ')
    [ "$post_count" -le "$pre_count" ]
}

# ---------------------------------------------------------------------------
# Volcengine per-tier (1.0 / 2.0) — local ledger
# ---------------------------------------------------------------------------

@test "volcengine --tier 2.0 ok when ledger usage well under per-tier budget" {
    export TTS_LEDGER_DIR="$BATS_TMPDIR/ledger_${BATS_TEST_NUMBER}"
    mkdir -p "$TTS_LEDGER_DIR"
    printf '%s\t2.0\t1000\n' "$(date -u +%Y-%m-%d)" > "$TTS_LEDGER_DIR/volc-usage.log"
    export VOLC_TTS_DAILY_BUDGET_V2=20000
    run bash "$QUOTA_CHECK" check --vendor volcengine --tier 2.0 --required-chars 5000
    [ "$status" -eq 0 ]
    [[ "$output" == *"tier 2.0 ok"* ]]
}

@test "volcengine --tier 2.0 over-budget when ledger usage + required exceeds budget" {
    export TTS_LEDGER_DIR="$BATS_TMPDIR/ledger_${BATS_TEST_NUMBER}"
    mkdir -p "$TTS_LEDGER_DIR"
    printf '%s\t2.0\t18000\n' "$(date -u +%Y-%m-%d)" > "$TTS_LEDGER_DIR/volc-usage.log"
    export VOLC_TTS_DAILY_BUDGET_V2=20000
    run bash "$QUOTA_CHECK" check --vendor volcengine --tier 2.0 --required-chars 5000
    [ "$status" -eq 1 ]
    [[ "$output" == *"over-budget"* ]]
}

@test "volcengine --tier sums only today's matching-tier ledger rows" {
    export TTS_LEDGER_DIR="$BATS_TMPDIR/ledger_${BATS_TEST_NUMBER}"
    mkdir -p "$TTS_LEDGER_DIR"
    today="$(date -u +%Y-%m-%d)"
    {
        printf '%s\t2.0\t3000\n' "$today"
        printf '%s\t1.0\t9000\n' "$today"     # other tier — must NOT count
        printf '2020-01-01\t2.0\t9999\n'      # other day  — must NOT count
    } > "$TTS_LEDGER_DIR/volc-usage.log"
    export VOLC_TTS_DAILY_BUDGET_V2=10000
    run bash "$QUOTA_CHECK" show --vendor volcengine --tier 2.0
    [ "$status" -eq 0 ]
    [[ "$output" == *"used_today=3000"* ]]
}

@test "volcengine --tier 2.0 missing VOLC_TTS_DAILY_BUDGET_V2 yields exit 3" {
    export TTS_LEDGER_DIR="$BATS_TMPDIR/ledger_${BATS_TEST_NUMBER}"
    mkdir -p "$TTS_LEDGER_DIR"
    unset VOLC_TTS_DAILY_BUDGET_V2
    run bash "$QUOTA_CHECK" check --vendor volcengine --tier 2.0 --required-chars 100
    [ "$status" -eq 3 ]
    [[ "$output" == *"VOLC_TTS_DAILY_BUDGET_V2"* ]]
}

@test "volcengine --tier with invalid tier yields exit 1" {
    export VOLC_TTS_DAILY_BUDGET_V2=20000
    run bash "$QUOTA_CHECK" check --vendor volcengine --tier 3.0 --required-chars 100
    [ "$status" -eq 1 ]
    [[ "$output" == *"tier must be"* ]]
}
