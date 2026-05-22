#!/usr/bin/env bats
# Test suite for scripts/synth-auto.sh — quota-aware orchestration.
# Runs fully offline: stub provider for synthesis, local ledger for Volcengine
# quota (no network). MiniMax is kept out of the test pool to avoid live calls.

HERE="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
SYNTH_AUTO="$HERE/../scripts/synth-auto.sh"
FIXTURES="$HERE/fixtures"

setup() {
    export TTS_PROVIDER_OVERRIDE="$FIXTURES/stub-provider.sh"
    # chunker.py is vendored into the skill — no TTS_CHUNKER_PATH needed.
    export TTS_LEDGER_DIR="$BATS_TMPDIR/ledger_${BATS_TEST_NUMBER}"
    mkdir -p "$TTS_LEDGER_DIR"
    INPUT="$BATS_TMPDIR/in_${BATS_TEST_NUMBER}.md"
    printf '# 测试\n\n这是一段用于额度预检测试的短文本。\n' > "$INPUT"
    OUT="$BATS_TMPDIR/out_${BATS_TEST_NUMBER}.mp3"
}

teardown() {
    rm -f "$OUT" "$INPUT" "$(dirname "$OUT")/.tts-auto-progress"
}

@test "arg error: missing --output yields exit 1" {
    run bash "$SYNTH_AUTO" --input "$INPUT"
    [ "$status" -eq 1 ]
}

@test "arg error: both --input and --segments yields exit 1" {
    run bash "$SYNTH_AUTO" --input "$INPUT" --segments "$INPUT" --output "$OUT"
    [ "$status" -eq 1 ]
}

@test "picks first pool vendor with enough quota (volc-2.0 over -> volc-1.0)" {
    export VOLC_TTS_DAILY_BUDGET_V2=5       # tiny -> over budget
    export VOLC_TTS_DAILY_BUDGET_V1=50000   # plenty
    run bash "$SYNTH_AUTO" --input "$INPUT" --output "$OUT" --vendor-pool volc-2.0,volc-1.0
    [ "$status" -eq 0 ]
    [ -f "$OUT" ]
    [[ "$output" == *"volc-2.0 over budget"* ]]
    [[ "$output" == *"selected 'volc-1.0'"* ]]
}

@test "all vendors over budget -> exit 4, no synthesis" {
    export VOLC_TTS_DAILY_BUDGET_V2=2
    export VOLC_TTS_DAILY_BUDGET_V1=2
    run bash "$SYNTH_AUTO" --input "$INPUT" --output "$OUT" --vendor-pool volc-2.0,volc-1.0
    [ "$status" -eq 4 ]
    [ ! -f "$OUT" ]
    [[ "$output" == *"NO VENDOR"* ]]
}

@test "missing per-tier budget -> vendor skipped, all skipped -> exit 4" {
    unset VOLC_TTS_DAILY_BUDGET_V1 VOLC_TTS_DAILY_BUDGET_V2
    run bash "$SYNTH_AUTO" --input "$INPUT" --output "$OUT" --vendor-pool volc-2.0,volc-1.0
    [ "$status" -eq 4 ]
    [ ! -f "$OUT" ]
}

@test "unknown --vendor-pool name yields exit 1" {
    run bash "$SYNTH_AUTO" --input "$INPUT" --output "$OUT" --vendor-pool nonsense
    [ "$status" -eq 1 ]
}
