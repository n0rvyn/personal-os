#!/usr/bin/env bats
# Offline (`local-*`) provider: routing, readiness preflight, pool placement.
#
# These are WIRING tests and they run without the 121 MB model — they prove the
# plumbing reaches the provider and that an unprepared engine degrades the way
# `synth-auto` expects. ⛔ They do NOT prove audio comes out; that is the live
# check in `scripts/fetch-local-model.sh` (which synthesizes a real sentence
# before reporting success) and `tests/run_e2e.sh`.

setup() {
    SCRIPTS="$(cd "$BATS_TEST_DIRNAME/../scripts" && pwd)"
    SYNTH="$SCRIPTS/synth.sh"
    SYNTH_AUTO="$SCRIPTS/synth-auto.sh"
    QUOTA="$SCRIPTS/quota_check.sh"
    LOCAL="$SCRIPTS/providers/local.sh"
    TMP="$BATS_TEST_TMPDIR"
    # Point the engine at an empty dir so nothing here touches a real model.
    export TTS_LOCAL_MODEL_DIR="$TMP/no-model"
    mkdir -p "$TTS_LOCAL_MODEL_DIR"
}

# --- synth.sh prefix routing ---------------------------------------------

@test "synth.sh routes local-* to providers/local.sh" {
    # A stub standing in for the provider proves the route without the model.
    cat >"$TMP/stub.sh" <<'EOF'
#!/usr/bin/env bash
echo "stub-voice=$2" >"$(dirname "$3")/route.txt"
printf 'ID3fake' > "$3"
EOF
    chmod +x "$TMP/stub.sh"
    run bash "$SYNTH" --text "你好" --voice local-vits-zh-ll-sid0 \
        --output "$TMP/out.mp3" --provider-override "$TMP/stub.sh"
    [ "$status" -eq 0 ]
    [ -s "$TMP/out.mp3" ]
}

@test "synth.sh strips the local- prefix before calling the provider" {
    # voice_inner must be `vits-zh-ll-sid0`, NOT `local-vits-zh-ll-sid0` —
    # local_tts.py's voice regex rejects the unstripped form.
    run bash "$SYNTH" --text "你好" --voice local-vits-zh-ll-sid0 --output "$TMP/o.mp3"
    [ "$status" -ne 0 ]
    # Reaching the model check means the prefix parsed and the route resolved.
    [[ "$output" == *"model incomplete"* || "$output" == *"fetch-local-model"* ]]
}

@test "an unknown prefix still lists local-* among supported" {
    run bash "$SYNTH" --text "x" --voice bogus-voice --output "$TMP/o.mp3"
    [ "$status" -eq 1 ]
    [[ "$output" == *"local-*"* ]]
}

# --- provider-level guards ------------------------------------------------

@test "local provider rejects a voice id that is not vits-zh-ll-sid<N>" {
    run bash "$LOCAL" "你好" "some-other-voice" "$TMP/o.mp3" 1.0 24000
    [ "$status" -eq 1 ]
    [[ "$output" == *"unsupported voice"* ]]
}

@test "local provider fails loudly (not silently) when the model is absent" {
    run bash "$LOCAL" "你好" "vits-zh-ll-sid0" "$TMP/o.mp3" 1.0 24000
    [ "$status" -ne 0 ]
    [ ! -s "$TMP/o.mp3" ]           # ⛔ never a zero-byte "success"
    [[ "$output" == *"fetch-local-model.sh"* ]]   # the message carries the fix
}

# --- quota preflight ------------------------------------------------------

@test "quota_check --vendor local reports exit 3 when the model is missing" {
    # 3 = auth/config missing → synth-auto skips it like a keyless vendor,
    # instead of selecting it and dying mid-synthesis.
    run bash "$QUOTA" check --vendor local --required-chars 5000
    [ "$status" -eq 3 ]
    [[ "$output" == *"fetch-local-model.sh"* ]]
}

@test "quota_check --vendor local passes when the model and runtime are present" {
    # Fake a complete model tree + a python that imports cleanly. This checks
    # the readiness LOGIC, not the engine.
    for n in model.onnx tokens.txt lexicon.txt date.fst number.fst phone.fst new_heteronym.fst; do
        : > "$TTS_LOCAL_MODEL_DIR/$n"
    done
    cat >"$TMP/py" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$TMP/py"
    TTS_LOCAL_PYTHON="$TMP/py" run bash "$QUOTA" check --vendor local --required-chars 999999
    [ "$status" -eq 0 ]
    # Unlimited: a 999999-char job must not be judged over budget.
    [[ "$output" == *"no quota"* ]]
}

@test "quota_check still rejects a genuinely unknown vendor" {
    run bash "$QUOTA" check --vendor nosuchvendor --required-chars 10
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown vendor"* ]]
}

# --- pool placement -------------------------------------------------------

@test "local is a known vendor-pool name" {
    run bash "$SYNTH_AUTO" --input /nonexistent.md --output "$TMP/o.mp3" --vendor-pool local
    [[ "$output" != *"matched no known vendor"* ]]
}

# The next two evaluate the POOL literal itself rather than grepping for a
# string, so a reordered or renamed entry actually breaks them.
_pool_names() {
    # Extract the `declare -a POOL=( … )` block and evaluate it in a subshell,
    # then print the vendor names in order.
    sed -n '/^declare -a POOL=(/,/^)/p' "$SYNTH_AUTO" > "$TMP/pool.sh"
    echo 'for e in "${POOL[@]}"; do echo "${e%%|*}"; done' >> "$TMP/pool.sh"
    bash "$TMP/pool.sh"
}

@test "local sits LAST in the default pool" {
    # Cloud vendors must be tried before the offline engine, or every run
    # silently ships the rougher voice.
    run _pool_names
    [ "$status" -eq 0 ]
    [ "$(echo "$output" | tail -1)" = "local" ]
    [ "$(echo "$output" | head -1)" = "minimax" ]
    [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 4 ]
}

# --- vendor priority is configuration, not a hard-coded order ------------

@test "TTS_VENDOR_PRIORITY reorders the pool" {
    run bash -c "TTS_VENDOR_PRIORITY='local,minimax' bash '$SYNTH_AUTO' \
        --input '$BATS_TEST_DIRNAME/fixtures/podcast-smoke.md' --output '$TMP/x.mp3' 2>&1 | head -3"
    [[ "$output" == *"vendor priority from TTS_VENDOR_PRIORITY"* ]]
    # The announced order must be the requested one, not the default.
    [[ "$output" == *"local minimax"* ]]
}

@test "--vendor-pool outranks TTS_VENDOR_PRIORITY" {
    run bash -c "TTS_VENDOR_PRIORITY='local' bash '$SYNTH_AUTO' \
        --input '$BATS_TEST_DIRNAME/fixtures/podcast-smoke.md' --output '$TMP/x.mp3' \
        --vendor-pool minimax 2>&1 | head -3"
    [[ "$output" == *"priority from --vendor-pool"* ]]
}

@test "a misspelled vendor in TTS_VENDOR_PRIORITY is an error, not a silent default" {
    # ⛔ Falling back to the default order here would be a setting that looks
    #    applied and is not — the worst failure shape in this repo.
    run bash -c "TTS_VENDOR_PRIORITY='minmax,local' bash '$SYNTH_AUTO' \
        --input '$BATS_TEST_DIRNAME/fixtures/podcast-smoke.md' --output '$TMP/x.mp3'"
    [ "$status" -eq 1 ]
    [[ "$output" == *"unknown vendor: 'minmax'"* ]]
    [ ! -f "$TMP/x.mp3" ]
}

@test "dropping local from the priority restores exit 4 on an exhausted pool" {
    # The escape hatch: make a run FAIL rather than ship the offline voice.
    run bash -c "MINIMAX_API_KEY= VOLC_TTS_DAILY_BUDGET_V1= VOLC_TTS_DAILY_BUDGET_V2= \
        TTS_VENDOR_PRIORITY='minimax,volc-2.0,volc-1.0' bash '$SYNTH_AUTO' \
        --input '$BATS_TEST_DIRNAME/fixtures/podcast-smoke.md' --output '$TMP/x.mp3'"
    [ "$status" -eq 4 ]
    [ ! -f "$TMP/x.mp3" ]
}

# --- unspeakable chunks must not abort the run ---------------------------
# `local` is the LAST pool entry, so a non-zero exit here aborts synth.sh with
# nothing to fall back to. An all-Latin chunk (a URL, a code fragment left after
# markdown stripping) makes sherpa return zero samples — 2026-09-22 measured,
# that used to exit 2 and kill the whole episode.

@test "is_speakable classifies what this Chinese-only model can voice" {
    py="${TTS_LOCAL_PYTHON:-}"
    [[ -n "$py" ]] || { [[ -x "$HOME/.tts-toolkit/venv/bin/python" ]] && py="$HOME/.tts-toolkit/venv/bin/python"; }
    [[ -n "$py" ]] || py=python3
    run "$py" - <<EOF
import sys; sys.path.insert(0, "$SCRIPTS")
from local_tts import is_speakable
cases = [("ffmpeg TTS", False), ("https://example.com/a", False), ("...", False),
         ("今天", True), ("2026", True), ("２０２６", True), ("Swift 很快", True)]
bad = [(t, is_speakable(t), e) for t, e in cases if is_speakable(t) != e]
print("BAD", bad) if bad else print("ALLOK")
EOF
    [ "$status" -eq 0 ]
    [[ "$output" == *"ALLOK"* ]]
}

@test "live: an all-Latin chunk yields silence and exit 0, not a failed run" {
    # Needs the real model; the wiring tests above stay model-free.
    [ -f "$HOME/.tts-toolkit/models/vits-zh-ll/model.onnx" ] || skip "model not installed"
    unset TTS_LOCAL_MODEL_DIR
    run bash "$LOCAL" "ComposableArchitecture ffmpeg TTS" vits-zh-ll-sid0 \
        "$TMP/oov.mp3" 1.0 24000
    [ "$status" -eq 0 ]
    [ -s "$TMP/oov.mp3" ]
    # ⛔ Silent substitution would be worse than the crash — it must say so.
    [[ "$output" == *"NOT SPOKEN"* ]]
}

@test "TTS_LOCAL_VOICE selects the pool's local speaker; sid0 is the default" {
    sed -n '/^declare -a POOL=(/,/^)/p' "$SYNTH_AUTO" > "$TMP/pool2.sh"
    echo 'for e in "${POOL[@]}"; do case "$e" in local\|*) echo "$e" | cut -d"|" -f2;; esac; done' >> "$TMP/pool2.sh"

    run bash "$TMP/pool2.sh"
    [ "$output" = "local-vits-zh-ll-sid0" ]

    TTS_LOCAL_VOICE=vits-zh-ll-sid3 run bash "$TMP/pool2.sh"
    [ "$output" = "local-vits-zh-ll-sid3" ]
}
