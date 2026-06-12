#!/usr/bin/env bats
# test_cleanup.bats — 8 BATS tests for cleanup.sh operator-side temp-file cleanup.

CLEANUP="$BATS_TEST_DIRNAME/../scripts/cleanup.sh"

setup() {
  # Per-test isolated tmpdir
  TEST_TMPDIR="$BATS_TMPDIR/cleanup-test-$$-$RANDOM"
  mkdir -p "$TEST_TMPDIR/my-tmpdir"
  export TTS_CLEANUP_TMPDIR="$TEST_TMPDIR/my-tmpdir"
}

teardown() {
  rm -rf "$TEST_TMPDIR"
}

# Helper: create a fake staging dir aged N days (via touch -t)
make_old_staging() {
  local base="$1" name="$2" days="$3"
  mkdir -p "$base/$name"
  # touch -t format: [[CC]YY]MMDDhhmm[.ss]
  local ts
  ts="$(python3 -c "import datetime; d=datetime.datetime.now()-datetime.timedelta(days=$days); print(d.strftime('%Y%m%d%H%M.%S'))")"
  touch -t "$ts" "$base/$name"
}

@test "dry-run prints candidates but does not delete" {
  make_old_staging "$TTS_CLEANUP_TMPDIR" "tts-batch-AABBCC" 10

  run bash "$CLEANUP" --older-than 7 --dry-run --scope staging
  [ "$status" -eq 0 ]
  [[ "$output" == *"would delete"* ]]
  # Directory must still exist after dry-run
  [ -d "$TTS_CLEANUP_TMPDIR/tts-batch-AABBCC" ]
}

@test "apply actually deletes staging dirs older than threshold" {
  make_old_staging "$TTS_CLEANUP_TMPDIR" "tts-batch-DDEEFF" 10

  run bash "$CLEANUP" --older-than 7 --apply --scope staging
  [ "$status" -eq 0 ]
  # Directory must be gone after apply
  [ ! -d "$TTS_CLEANUP_TMPDIR/tts-batch-DDEEFF" ]
}

@test "newer-than-threshold staging dirs are not listed" {
  # Old dir (15 days) — should be listed
  make_old_staging "$TTS_CLEANUP_TMPDIR" "tts-batch-OLD111" 15
  # Fresh dir (1 day) — should NOT be listed
  make_old_staging "$TTS_CLEANUP_TMPDIR" "tts-batch-NEW222" 1

  run bash "$CLEANUP" --older-than 7 --dry-run --scope staging
  [ "$status" -eq 0 ]
  [[ "$output" == *"tts-batch-OLD111"* ]]
  [[ "$output" != *"tts-batch-NEW222"* ]]
}

@test "audio files are NEVER deleted" {
  # Case 1: standalone mp3 in tmpdir root — must survive even with apply
  echo "standalone podcast" > "$TTS_CLEANUP_TMPDIR/final-podcast.mp3"
  local ts
  ts="$(python3 -c "import datetime; d=datetime.datetime.now()-datetime.timedelta(days=20); print(d.strftime('%Y%m%d%H%M.%S'))")"
  touch -t "$ts" "$TTS_CLEANUP_TMPDIR/final-podcast.mp3"

  # Case 2: staging dir (old) containing a chunk mp3 — dir deleted, mp3 inside goes with it
  # Add file BEFORE aging the dir, so the dir's mtime reflects the old date.
  mkdir -p "$TTS_CLEANUP_TMPDIR/tts-batch-AUDIO"
  echo "chunk audio" > "$TTS_CLEANUP_TMPDIR/tts-batch-AUDIO/chunk_001.mp3"
  local ts2
  ts2="$(python3 -c "import datetime; d=datetime.datetime.now()-datetime.timedelta(days=10); print(d.strftime('%Y%m%d%H%M.%S'))")"
  touch -t "$ts2" "$TTS_CLEANUP_TMPDIR/tts-batch-AUDIO"

  run bash "$CLEANUP" --older-than 7 --apply --scope staging
  [ "$status" -eq 0 ]

  # Standalone mp3 must still exist
  [ -f "$TTS_CLEANUP_TMPDIR/final-podcast.mp3" ]
  # Staging dir is gone (its internal chunk goes with it — that's expected intermediate audio)
  [ ! -d "$TTS_CLEANUP_TMPDIR/tts-batch-AUDIO" ]
}

@test "non-existent scan root produces no output and exit 0" {
  export TTS_CLEANUP_TMPDIR="$TEST_TMPDIR/nonexistent-tmp"

  run bash "$CLEANUP" --older-than 7 --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"no stale"* ]]
}

@test "cross-user scoping: sibling tempdirs are NOT walked" {
  # Own user's tmpdir
  mkdir -p "$TEST_TMPDIR/my-tmpdir"
  make_old_staging "$TEST_TMPDIR/my-tmpdir" "tts-batch-MINE" 10

  # Sibling "other user's" tmpdir — must NOT be walked
  mkdir -p "$TEST_TMPDIR/other-user-tmpdir"
  make_old_staging "$TEST_TMPDIR/other-user-tmpdir" "tts-batch-THEIRS" 10

  export TTS_CLEANUP_TMPDIR="$TEST_TMPDIR/my-tmpdir"

  run bash "$CLEANUP" --older-than 7 --apply --scope staging
  [ "$status" -eq 0 ]

  # Own staging dir deleted
  [ ! -d "$TEST_TMPDIR/my-tmpdir/tts-batch-MINE" ]
  # Sibling's staging dir untouched
  [ -d "$TEST_TMPDIR/other-user-tmpdir/tts-batch-THEIRS" ]
}

@test "argument validation: --older-than non-integer yields exit 1" {
  run bash "$CLEANUP" --older-than abc
  [ "$status" -eq 1 ]
  [[ "$output$stderr" == *"positive integer"* ]] || [[ "${lines[*]}" == *"positive integer"* ]]
}

@test "argument validation: --scope invalid yields exit 1" {
  run bash "$CLEANUP" --scope nonsense
  [ "$status" -eq 1 ]
}
