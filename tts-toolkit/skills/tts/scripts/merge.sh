#!/usr/bin/env bash
# Concat per-chunk mp3s into a single mp3 via ffmpeg concat demuxer.
# Requires all chunks to share codec + bitrate (guaranteed when synthesized by the
# same provider in a single batch run with identical --rate).
#
# Args: $1 list_file (ffmpeg concat list)  $2 output mp3 path
# Exit code 4: ffmpeg failure.

set -euo pipefail
list_file="${1:?list file required}"
output="${2:?output required}"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "merge: ffmpeg not on PATH" >&2
    exit 4
fi

ffmpeg -loglevel error -y -f concat -safe 0 -i "$list_file" -c copy "$output" || {
    echo "merge: ffmpeg concat failed" >&2
    exit 4
}
