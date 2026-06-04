#!/usr/bin/env bats
# Test suite for scripts/synth.sh
# Acceptance (Task 3-tests): all tests FAIL until Task 3-impl lands.
# Acceptance (Task 3-impl): 7 tests, 0 failures.

HERE="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
SYNTH="$HERE/../scripts/synth.sh"
FIXTURES="$HERE/fixtures"

setup() {
    # Stub provider: write a minimal mp3 to $3 (output arg)
    export TTS_PROVIDER_OVERRIDE="$FIXTURES/stub-provider.sh"
    # chunker.py is vendored into the skill's scripts/ dir — synth.sh finds it
    # as a sibling, so no TTS_CHUNKER_PATH override is needed.
    # These tests exercise synth.sh's batch internals directly (the unit under
    # test), so they explicitly opt past the synth-auto boundary guard.
    export TTS_ALLOW_DIRECT_BATCH=1
}

teardown() {
    :
}

@test "--help lists --text --input --segments and --concurrency" {
    run bash "$SYNTH" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"--text"* ]]
    [[ "$output" == *"--input"* ]]
    [[ "$output" == *"--segments"* ]]
    [[ "$output" == *"--concurrency"* ]]
}

@test "--text mode happy path via stub provider" {
    out="$BATS_TMPDIR/text_out_${BATS_TEST_NUMBER}.mp3"
    run bash "$SYNTH" --text "hi" --voice volc-test --output "$out"
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out"
}

@test "--input mode calls text-to-segments chunker" {
    out="$BATS_TMPDIR/input_out_${BATS_TEST_NUMBER}.mp3"
    run bash "$SYNTH" --input "$FIXTURES/sample-input.md" --voice volc-test --output "$out"
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out"
}

@test "--segments mode reads provided segments.json" {
    out="$BATS_TMPDIR/seg_out_${BATS_TEST_NUMBER}.mp3"
    run bash "$SYNTH" --segments "$FIXTURES/segments-sample.json" --voice volc-test --output "$out"
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out"
}

@test "--segments + --input mutually exclusive" {
    out="$BATS_TMPDIR/mutex_out_${BATS_TEST_NUMBER}.mp3"
    run bash "$SYNTH" --segments "$FIXTURES/segments-sample.json" --input "$FIXTURES/sample-input.md" --voice volc-test --output "$out"
    [ "$status" -eq 1 ]
}

@test "--concurrency flag accepted and 3-segment run completes" {
    out="$BATS_TMPDIR/conc_out_${BATS_TEST_NUMBER}.mp3"
    # Create a 3-segment fixture
    seg3="$BATS_TMPDIR/seg3_${BATS_TEST_NUMBER}.json"
    python3 -c "
import json
segs = [{'id': f'seg_{i:03d}', 'text': f'segment {i}'} for i in range(1,4)]
print(json.dumps({'segments': segs}))
" > "$seg3"
    run bash "$SYNTH" --segments "$seg3" --voice volc-test --output "$out" --concurrency 3
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out" "$seg3"
}

@test "boundary guard: direct batch without synth-auto/opt-in is refused (exit 1)" {
    out="$BATS_TMPDIR/guard_out_${BATS_TEST_NUMBER}.mp3"
    # Clear both bypass signals → a direct --segments call must be refused.
    unset TTS_ALLOW_DIRECT_BATCH
    unset TTS_VIA_SYNTH_AUTO
    run bash "$SYNTH" --segments "$FIXTURES/segments-sample.json" --voice volc-test --output "$out"
    [ "$status" -eq 1 ]
    [[ "$output" == *"synth-auto"* ]]
    [ ! -f "$out" ]
}

@test "boundary guard: single --text is NOT gated by the batch guard" {
    out="$BATS_TMPDIR/guard_text_${BATS_TEST_NUMBER}.mp3"
    unset TTS_ALLOW_DIRECT_BATCH
    unset TTS_VIA_SYNTH_AUTO
    run bash "$SYNTH" --text "hi" --voice volc-test --output "$out"
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out"
}

@test "boundary guard: TTS_VIA_SYNTH_AUTO=1 allows direct batch (synth-auto's path)" {
    out="$BATS_TMPDIR/guard_via_${BATS_TEST_NUMBER}.mp3"
    unset TTS_ALLOW_DIRECT_BATCH
    export TTS_VIA_SYNTH_AUTO=1
    run bash "$SYNTH" --segments "$FIXTURES/segments-sample.json" --voice volc-test --output "$out"
    [ "$status" -eq 0 ]
    [ -f "$out" ]
    rm -f "$out"
}

@test "unknown voice prefix returns exit 1" {
    out="$BATS_TMPDIR/prefix_out_${BATS_TEST_NUMBER}.mp3"
    run bash "$SYNTH" --text "hello" --voice foo-bar --output "$out"
    [ "$status" -eq 1 ]
    [[ "$output" == *"volc-"* ]] || [[ "$output" == *"prefix"* ]] || [[ "$output" == *"supported"* ]]
}
