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
# MiniMax tests
# ---------------------------------------------------------------------------

@test "minimax check ok when usage well below total" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$FIXTURES/minimax_quota_ok.json"
    # used=1000, total=19000, available=18000; required=5000 => ok
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 5000
    [ "$status" -eq 0 ]
    [[ "$output" == *"minimax ok"* ]]
}

@test "minimax check over-budget when usage + required exceeds total" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$FIXTURES/minimax_quota_high.json"
    # used=15000, total=19000, available=4000; required=5000 => over-budget
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 5000
    [ "$status" -eq 1 ]
    [[ "$output" == *"over-budget"* ]]
}

@test "minimax reserve-pct factored in correctly" {
    export MINIMAX_API_KEY="test-key"
    # used=10000, total=19000, available=9000
    # Create a fixture with used=10000
    local fixture="$BATS_TMPDIR/minimax_mid_${BATS_TEST_NUMBER}.json"
    python3 -c "
import json
print(json.dumps({'model_remains': [{'model_name': 'speech-hd', 'current_interval_usage_count': 10000, 'current_interval_total_count': 19000}]}))
" > "$fixture"
    export QUOTA_CURL_FIXTURE="$fixture"

    # reserve-pct=30: required_with_reserve = 5000 + 5000*30/100 = 6500; available=9000 => ok
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 5000 --reserve-pct 30
    [ "$status" -eq 0 ]
    [[ "$output" == *"minimax ok"* ]]

    # reserve-pct=100: required_with_reserve = 5000 + 5000*100/100 = 10000; available=9000 => over-budget
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 5000 --reserve-pct 100
    [ "$status" -eq 1 ]
    [[ "$output" == *"over-budget"* ]]
}

@test "minimax model not in response yields exit 2" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$FIXTURES/minimax_quota_no_model.json"
    # Only speech-turbo in fixture; default model maps to speech-hd => not found
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 1000
    [ "$status" -eq 2 ]
    [[ "$output" == *"not in response"* ]]
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

@test "show subcommand prints used/total/available for minimax" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$FIXTURES/minimax_quota_ok.json"
    run bash "$QUOTA_CHECK" show --vendor minimax
    [ "$status" -eq 0 ]
    [[ "$output" == *"used="* ]]
    [[ "$output" == *"available="* ]]
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

@test "minimax model speech-2.8-hd maps to family speech-hd" {
    export MINIMAX_API_KEY="test-key"
    export QUOTA_CURL_FIXTURE="$FIXTURES/minimax_quota_ok.json"
    # minimax_quota_ok.json has speech-hd row; passing --model speech-2.8-hd should map => speech-hd => ok
    run bash "$QUOTA_CHECK" check --vendor minimax --required-chars 1000 --model speech-2.8-hd
    [ "$status" -eq 0 ]
    [[ "$output" == *"minimax ok"* ]]
}
