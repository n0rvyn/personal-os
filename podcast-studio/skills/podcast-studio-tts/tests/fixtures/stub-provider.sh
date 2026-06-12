#!/usr/bin/env bash
# Stub TTS provider for tests.
# Ignores text ($1), voice ($2), writes a tiny valid mp3 silence frame to output ($3).
# Uses ffmpeg to generate a real (0.1s) silence mp3 that ffmpeg concat can process.
# Args: text voice output [speed] [rate]
output="${3:?output required}"
ffmpeg -f lavfi -i anullsrc=r=24000:cl=mono -t 0.1 -q:a 9 -acodec libmp3lame "$output" -y 2>/dev/null
