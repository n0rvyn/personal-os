#!/usr/bin/env bats
# Test suite for providers/minimax.sh
# Acceptance criterion: all 4 tests FAIL until Task 1-impl lands.

HERE="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
PROVIDER="$HERE/../scripts/providers/minimax.sh"
FIXTURES="$HERE/fixtures"

setup() {
    # Create a per-test tmpdir for outputs
    export TEST_OUT="$BATS_TMPDIR/out_${BATS_TEST_NUMBER}.mp3"

    # Create a stub curl that returns a fixture file
    export STUB_DIR="$BATS_TMPDIR/stubs_${BATS_TEST_NUMBER}"
    mkdir -p "$STUB_DIR"

    # Export a known API key for tests that need it
    export MINIMAX_API_KEY="test-key-fixture"
}

teardown() {
    rm -f "$TEST_OUT"
    rm -rf "$STUB_DIR"
}

# Helper: create a stub curl that cat's a given fixture file
make_curl_stub() {
    local fixture="$1"
    cat > "$STUB_DIR/curl" <<STUB
#!/usr/bin/env bash
cat "$fixture"
STUB
    chmod +x "$STUB_DIR/curl"
    export PATH="$STUB_DIR:$PATH"
}

@test "exit 2 when MINIMAX_API_KEY missing" {
    unset MINIMAX_API_KEY
    run bash "$PROVIDER" "hello" "voice-id" "$TEST_OUT" "1.0" "24000"
    [ "$status" -eq 2 ]
    [[ "$output" == *MINIMAX_API_KEY* ]]
}

@test "exit 3 when API returns base_resp error" {
    make_curl_stub "$FIXTURES/minimax_error_response.json"
    run bash "$PROVIDER" "hello" "voice-id" "$TEST_OUT" "1.0" "24000"
    [ "$status" -eq 3 ]
    [[ "$output" == *1004* ]]
    [ ! -f "$TEST_OUT" ]
}

@test "exit 3 when audio bytes are not MP3 magic" {
    # Create a fixture with random hex (not MP3 magic bytes)
    local bad_fixture="$BATS_TMPDIR/minimax_bad_audio_${BATS_TEST_NUMBER}.json"
    python3 -c "
import json
# 4 bytes that are NOT MP3/ID3 magic
payload = (b'\x00\x01\x02\x03' + b'\x00'*200).hex()
print(json.dumps({'data': {'audio': payload}, 'base_resp': {'status_code': 0, 'status_msg': 'success'}}))
" > "$bad_fixture"
    make_curl_stub "$bad_fixture"
    run bash "$PROVIDER" "hello" "voice-id" "$TEST_OUT" "1.0" "24000"
    [ "$status" -eq 3 ]
    [ ! -f "$TEST_OUT" ]
}

@test "exit 0 + writes valid mp3 when audio decodes to MP3" {
    make_curl_stub "$FIXTURES/minimax_success_response.json"
    run bash "$PROVIDER" "hello" "voice-id" "$TEST_OUT" "1.0" "24000"
    [ "$status" -eq 0 ]
    [ -f "$TEST_OUT" ]
    # The file command should report MPEG or MP3 or similar
    run file "$TEST_OUT"
    [[ "$output" == *MPEG* ]] || [[ "$output" == *MP3* ]] || [[ "$output" == *Audio* ]] || [[ "$output" == *audio* ]]
}
