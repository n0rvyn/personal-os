#!/usr/bin/env bats
# test_podcast_env.bats — TTS config→env shim tests.
#
# Asserts that `lib/podcast-env.sh` reads the podcast-studio config and exports
# the tts provider / host_voice as env vars the vendored tts scripts consume,
# WITHOUT printing credential values to stdout.

SHIM="$BATS_TEST_DIRNAME/../podcast-env.sh"
PLUGIN_ROOT="$BATS_TEST_DIRNAME/.."

setup() {
  TEST_TMPDIR="$BATS_TMPDIR/env-shim-test-$$-$RANDOM"
  mkdir -p "$TEST_TMPDIR/vault"
  mkdir -p "$TEST_TMPDIR/news"
  mkdir -p "$TEST_TMPDIR/output"

  cat > "$TEST_TMPDIR/config.yaml" <<EOF
vault:
  subjective_dir: $TEST_TMPDIR/vault
  news_dir: $TEST_TMPDIR/news
  output_dir: $TEST_TMPDIR/output
tts:
  provider: volc
  host_voice: BV001_streaming
EOF

  export PODCAST_STUDIO_CONFIG="$TEST_TMPDIR/config.yaml"
  # Pre-existing credential env vars (would come from user's shell / keychain).
  # The shim must pass these through without printing them.
  export VOLC_TTS_APPID="fake-appid-12345"
  export VOLC_TTS_TOKEN="fake-token-67890"
  export MINIMAX_API_KEY="fake-mm-key-abcdef"
}

teardown() {
  rm -rf "$TEST_TMPDIR"
  unset PODCAST_STUDIO_CONFIG VOLC_TTS_APPID VOLC_TTS_TOKEN MINIMAX_API_KEY
  unset PODCAST_TTS_PROVIDER PODCAST_HOST_VOICE
  unset TTS_VENDOR_PRIORITY TTS_LOCAL_VOICE
}

@test "shim exists and is executable or sourceable" {
  [ -f "$SHIM" ]
}

@test "sourcing shim exports PODCAST_TTS_PROVIDER from config" {
  source "$SHIM"
  [ "$PODCAST_TTS_PROVIDER" = "volc" ]
}

@test "sourcing shim exports PODCAST_HOST_VOICE from config" {
  source "$SHIM"
  [ "$PODCAST_HOST_VOICE" = "BV001_streaming" ]
}

# --- optional vendor-priority / local-voice passthrough ------------------
# ⛔ The gap these close: a key that resolves correctly in lib/config.py but
#    never reaches the env is a setting that looks applied and is not — the
#    `tts` skill would silently keep its default order. Python-side tests
#    cannot see this; only sourcing the shim can.

@test "shim omits TTS_VENDOR_PRIORITY when the config does not set it" {
  # The base config in setup() has no vendor_priority. Absent must stay
  # absent — writing a default here would pin an order the user never chose.
  source "$SHIM"
  [ -z "${TTS_VENDOR_PRIORITY:-}" ]
  [ -z "${TTS_LOCAL_VOICE:-}" ]
}

@test "shim exports TTS_VENDOR_PRIORITY from tts.vendor_priority" {
  cat >> "$TEST_TMPDIR/config.yaml" <<'EOF'
  vendor_priority: [local, minimax]
EOF
  source "$SHIM"
  [ "$TTS_VENDOR_PRIORITY" = "local,minimax" ]
}

@test "shim exports TTS_LOCAL_VOICE from tts.local_voice" {
  cat >> "$TEST_TMPDIR/config.yaml" <<'EOF'
  local_voice: vits-zh-ll-sid3
EOF
  source "$SHIM"
  [ "$TTS_LOCAL_VOICE" = "vits-zh-ll-sid3" ]
}

@test "the exported priority is a name synth-auto actually accepts" {
  # End-to-end across the two repos: the shim's output format must be the
  # format the skill parses. A tuple rendered as "('local', 'minimax')"
  # would pass every Python test and fail here.
  cat >> "$TEST_TMPDIR/config.yaml" <<'EOF'
  vendor_priority: [local, minimax]
EOF
  source "$SHIM"
  [[ "$TTS_VENDOR_PRIORITY" == "local,minimax" ]]
  # comma-separated, no quotes, no brackets, no spaces
  [[ "$TTS_VENDOR_PRIORITY" != *"("* ]]
  [[ "$TTS_VENDOR_PRIORITY" != *"'"* ]]
  [[ "$TTS_VENDOR_PRIORITY" != *"["* ]]
}

@test "sourcing shim passes through pre-existing VOLC_TTS_APPID" {
  source "$SHIM"
  [ "$VOLC_TTS_APPID" = "fake-appid-12345" ]
}

@test "sourcing shim passes through pre-existing VOLC_TTS_TOKEN" {
  source "$SHIM"
  [ "$VOLC_TTS_TOKEN" = "fake-token-67890" ]
}

@test "sourcing shim passes through pre-existing MINIMAX_API_KEY" {
  source "$SHIM"
  [ "$MINIMAX_API_KEY" = "fake-mm-key-abcdef" ]
}

@test "sourcing shim does not echo credential values to stdout" {
  run bash -c "source '$SHIM'; true"
  [ "$status" -eq 0 ]
  [[ "$output" != *"fake-appid-12345"* ]]
  [[ "$output" != *"fake-token-67890"* ]]
  [[ "$output" != *"fake-mm-key-abcdef"* ]]
}

@test "sourcing shim does not eval config content" {
  # Threat model guard: the shim must NEVER eval config YAML.
  # Plant a malicious payload — if `eval` were used, this would execute.
  cat > "$TEST_TMPDIR/evil-config.yaml" <<EOF
vault:
  subjective_dir: $TEST_TMPDIR/vault
  news_dir: $TEST_TMPDIR/news
  output_dir: $TEST_TMPDIR/output
tts:
  provider: volc
  host_voice: "BV001_streaming\$(touch /tmp/should-not-exist-eval-marker)"
EOF
  export PODCAST_STUDIO_CONFIG="$TEST_TMPDIR/evil-config.yaml"
  rm -f /tmp/should-not-exist-eval-marker
  source "$SHIM" || true
  [ ! -e /tmp/should-not-exist-eval-marker ]
  rm -f /tmp/should-not-exist-eval-marker
}
