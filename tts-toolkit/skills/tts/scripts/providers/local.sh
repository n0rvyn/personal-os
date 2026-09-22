#!/usr/bin/env bash
# Local (on-device, offline) TTS provider — sherpa-onnx + VITS-zh-ll.
#
# The only provider in this skill that talks to nothing. No key, no quota, no
# network. It is the pool's last entry: worse-sounding than any cloud vendor,
# but it cannot fail for money reasons, which is the whole point.
#
# Args (positional, same contract as every other provider):
#   $1 text, $2 voice_id (stripped of `local-` by synth.sh), $3 output mp3,
#   $4 speed, $5 output sample rate
#
# Env:
#   TTS_LOCAL_PYTHON     python with sherpa-onnx+numpy (default ~/.tts-toolkit/venv/bin/python,
#                        falling back to python3 on PATH)
#   TTS_LOCAL_MODEL_DIR  model dir (default ~/.tts-toolkit/models/vits-zh-ll)
#   TTS_LOCAL_THREADS    onnx threads (default 4)
#
# Exit codes: 0 success / 1 arg or voice error / 2 model or runtime missing /
#             3 encode error
#
# ⚠️ The engine emits 16 kHz; ffmpeg resamples to $rate so the local chunks
#    concat cleanly with the rest of the run (merge.sh uses `-c copy`, which
#    needs every segment at the same codec/rate). ⛔ Do not skip the resample.

set -euo pipefail

text="${1:?text required}"
voice="${2:?voice required}"
output="${3:?output required}"
speed="${4:-1.0}"
rate="${5:-24000}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$HERE/local_tts.py"

[[ -f "$ENGINE" ]] || { echo "local: engine not found at $ENGINE" >&2; exit 2; }

PY="${TTS_LOCAL_PYTHON:-}"
if [[ -z "$PY" ]]; then
    if [[ -x "$HOME/.tts-toolkit/venv/bin/python" ]]; then
        PY="$HOME/.tts-toolkit/venv/bin/python"
    else
        PY="python3"
    fi
fi
command -v "$PY" >/dev/null 2>&1 || [[ -x "$PY" ]] || {
    echo "local: python not found: $PY (run $HERE/fetch-local-model.sh)" >&2; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || { echo "local: ffmpeg not on PATH" >&2; exit 3; }

wav="$(mktemp -t tts-local-XXXXXX).wav"
# shellcheck disable=SC2064 — expand $wav now, the trap must survive the run.
trap "rm -f '$wav'" EXIT

"$PY" "$ENGINE" --text "$text" --voice "$voice" --output "$wav" --speed "$speed"

[[ -s "$wav" ]] || { echo "local: engine exited 0 but wrote no audio" >&2; exit 2; }

# ⚠️ `-write_xing 0 -id3v2_version 0`: bare frames, no container metadata.
#    merge.sh concatenates chunks with `-c copy`, so anything ffmpeg puts in
#    front of chunk 1 lands in the MIDDLE of the stream for chunks 2..N.
#    2026-09-22 measured: with the defaults, a 2-chunk run made ffmpeg report
#    "non monotonically increasing dts" twice at merge time. The cloud
#    providers write vendor mp3 bytes straight through and never hit this —
#    it is specific to us re-encoding locally.
ffmpeg -y -loglevel error -i "$wav" -ar "$rate" -ac 1 -b:a 128k \
       -write_xing 0 -id3v2_version 0 "$output" \
    || { echo "local: ffmpeg encode failed" >&2; exit 3; }

# Read back: a 0-byte mp3 with ffmpeg exit 0 is the silent failure this
# whole skill is written against.
[[ -s "$output" ]] || { echo "local: encode produced an empty file" >&2; exit 3; }
