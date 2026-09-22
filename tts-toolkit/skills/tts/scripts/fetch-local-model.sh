#!/usr/bin/env bash
# fetch-local-model.sh — one-time setup for the offline `local-*` provider.
#
# Installs two things under ~/.tts-toolkit (nothing enters the plugin dir, and
# nothing enters git — the model is 121 MB):
#
#   models/vits-zh-ll/   the sherpa-onnx VITS-zh-ll model (5 Mandarin speakers)
#   venv/                a private venv with sherpa-onnx + numpy
#
# ⚠️ A private venv, deliberately. The engine needs a compiled wheel; making
#    that a global `pip install` would put a 2.4 MB native extension into
#    whatever python the user's shell happens to resolve. Point
#    TTS_LOCAL_PYTHON at your own interpreter to skip the venv entirely.
#
# ⛔ Unlike the iOS port this does NOT repack to Apple Archive. That repack
#    exists only because iOS app bundles flatten directory trees and the
#    platform cannot untar bz2. macOS has tar; keep the vendor's layout.
#
# Idempotent: re-running with everything present prints and exits 0.

set -euo pipefail

ROOT="${TTS_LOCAL_ROOT:-$HOME/.tts-toolkit}"
MODEL_DIR="${TTS_LOCAL_MODEL_DIR:-$ROOT/models/vits-zh-ll}"
VENV="${TTS_LOCAL_VENV:-$ROOT/venv}"

FILE=sherpa-onnx-vits-zh-ll.tar.bz2
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${FILE}"

# Everything the engine reads. ⛔ All four FSTs: a missing new_heteronym.fst
# does not error, it just mispronounces 多音字.
REQUIRED=(model.onnx tokens.txt lexicon.txt date.fst number.fst phone.fst new_heteronym.fst)

have_model() {
    local n
    for n in "${REQUIRED[@]}"; do
        [[ -f "$MODEL_DIR/$n" ]] || return 1
    done
    return 0
}

# --- model ----------------------------------------------------------------
if have_model; then
    echo "✅ 模型已就绪：$MODEL_DIR"
else
    command -v curl >/dev/null || { echo "❌ 需要 curl" >&2; exit 1; }
    command -v tar  >/dev/null || { echo "❌ 需要 tar" >&2; exit 1; }

    WORK="$(mktemp -d -t tts-local-fetch-XXXXXX)"
    trap 'rm -rf "$WORK"' EXIT

    # ⚠️ `${FILE}` braced, not `$FILE`. A CJK full-width paren right after a
    #    bare `$NAME` gets swallowed into the variable name by bash — under
    #    `set -u` that is an "unbound variable" abort on a line that looks fine.
    echo "下载 ${FILE}（约 113 MB）…"
    curl -fL --retry 3 -o "$WORK/$FILE" "$URL"

    echo "解开 …"
    tar xjf "$WORK/$FILE" -C "$WORK"
    SRC="$WORK/sherpa-onnx-vits-zh-ll"
    [[ -f "$SRC/model.onnx" ]] || { echo "❌ 归档里没有 model.onnx —— 上游包变了" >&2; exit 1; }

    # Stage then move: a run killed mid-copy must not leave a half-model that
    # passes a "directory exists" check. sherpa reads a truncated onnx without
    # complaining.
    mkdir -p "$(dirname "$MODEL_DIR")"
    rm -rf "$MODEL_DIR.partial" "$MODEL_DIR"
    mv "$SRC" "$MODEL_DIR.partial"
    mv "$MODEL_DIR.partial" "$MODEL_DIR"

    have_model || { echo "❌ 解出来缺文件，看 $MODEL_DIR" >&2; exit 1; }
    echo "✅ 模型：${MODEL_DIR}（$(du -sh "$MODEL_DIR" | cut -f1)）"
fi

# --- venv -----------------------------------------------------------------
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import sherpa_onnx, numpy" 2>/dev/null; then
    echo "✅ venv 已就绪：$VENV"
else
    echo "建 venv 并装 sherpa-onnx + numpy …"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet sherpa-onnx numpy
    "$VENV/bin/python" -c "import sherpa_onnx, numpy; print('sherpa-onnx', sherpa_onnx.__version__)"
    echo "✅ venv：$VENV"
fi

# --- read back: synthesize one real sentence ------------------------------
# ⛔ "装完了" 不等于 "出得了声"。装置自己先跑一遍再报成功。
echo "自检：合成一句话 …"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$(mktemp -t tts-local-probe-XXXXXX).mp3"
if TTS_LOCAL_MODEL_DIR="$MODEL_DIR" TTS_LOCAL_PYTHON="$VENV/bin/python" \
     bash "$HERE/providers/local.sh" "本地语音合成自检通过。" "vits-zh-ll-sid0" "$PROBE" 1.0 24000
then
    echo "✅ 自检通过：${PROBE}（$(du -h "$PROBE" | cut -f1)）"
    echo
    echo "用法：synth-auto.sh 会把 local 作为最后一档自动兜底；"
    echo "     手动单句：synth.sh --text '你好' --voice local-vits-zh-ll-sid0 --output a.mp3"
else
    echo "❌ 自检没过 —— 上面有原因" >&2
    exit 1
fi
