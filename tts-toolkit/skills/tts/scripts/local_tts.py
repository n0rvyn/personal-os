#!/usr/bin/env python3
"""local_tts.py — on-device (offline) Chinese TTS via sherpa-onnx + VITS-zh-ll.

Writes a 16 kHz mono WAV. The shell provider (`providers/local.sh`) encodes it
to mp3 with ffmpeg, so this script never shells out.

Why this exists: every other provider in this skill bills a remote vendor. When
all of them are out of quota (or the key expired), `synth-auto` used to abort
with exit 4 and no audio at all. This is the floor under that — it costs
nothing, needs no network, and cannot run out.

Ported from ArtLens's iOS narration engine
(`ArtLens/Player/SherpaSynthesizer.swift` + `NarrationAudio.swift`). What came
across, and what deliberately did not:

  transferred
    · the model (VITS-zh-ll, 5 speakers) and `num_threads=4`
    · ALL FOUR rule FSTs. ⛔ Dropping `new_heteronym.fst` does not error — it
      just reads 多音字 wrong ("行旅" gets the other 行). Silent.
    · the loudness calibration: ×2.1 gain then a tanh soft knee at 0.95.
      ArtLens measured this model at RMS −24.5/−25.3 dBFS, peak 0.277–0.304
      against Apple's AVSpeechSynthesizer at −18.4 / 0.606 — i.e. this model is
      ~6 dB quieter than everything else. Without the gain the local track is
      audibly softer than the MiniMax/Volcengine chunks it stands in for.
      The limiter is UNCONDITIONAL (2026-09-22 local measurement: sid 3 peaks
      at 0.970 post-gain — the knee does real work on some speakers).

  NOT transferred
    · `autoreleasepool` around create/generate — that is a Swift-actor
      pointer-lifetime bug, meaningless here.
    · the AppleArchive (.aar) repack + `dict_dir` plumbing — those exist only
      because iOS app bundles flatten directories and cannot untar bz2. On a
      Mac we keep the vendor's own directory layout, and sherpa ≥1.12.15
      ignores `dict_dir` for this model anyway (it says so on stderr).
    · `max_num_sentences=1` — that is a first-sound-latency choice for
      interactive playback. This is batch: 2 is a better throughput/prosody
      trade and still bounds peak memory.

⚠️ KNOWN LIMITATION, and it is a silent one: the lexicon is Chinese-only.
English words ("Swift", "Apple") and full-width colons are dropped with an
`Ignore OOV` line on sherpa's stderr and simply do not get spoken. We count
them and warn on our own stderr so the omission shows up in a pipeline log
instead of only in the finished audio.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import wave
from pathlib import Path

# Loudness constants — see the ArtLens measurement table in the docstring.
GAIN = 2.1
KNEE = 0.95

RULE_FSTS = ["date.fst", "number.fst", "phone.fst", "new_heteronym.fst"]
REQUIRED = ["model.onnx", "tokens.txt", "lexicon.txt", *RULE_FSTS]

# Runs of ASCII letters the Chinese lexicon cannot pronounce.
_OOV_RE = re.compile(r"[A-Za-z][A-Za-z.\-']*")

# Anything this model CAN voice: Han characters (incl. ext-A), or digits — the
# number/date rule FSTs turn those into spoken Chinese.
_SPEAKABLE_RE = re.compile(r"[一-鿿㐀-䶿0-9０-９]")

# How much silence stands in for a chunk this model cannot voice at all.
UNSPEAKABLE_SILENCE_S = 0.3


def is_speakable(text: str) -> bool:
    """Does the text contain anything this Chinese-only model can voice?

    ⚠️ Used ONLY to classify a zero-sample result, never to skip synthesis
    ahead of time — the engine, not this regex, decides what it can say.
    """
    return bool(_SPEAKABLE_RE.search(text))


def model_dir() -> Path:
    return Path(
        os.environ.get("TTS_LOCAL_MODEL_DIR")
        or Path.home() / ".tts-toolkit" / "models" / "vits-zh-ll"
    )


def check_model(d: Path) -> None:
    """Fail loudly with the fix, ArtLens's `packageMissing` pattern.

    ⛔ Never degrade to "synthesize nothing and exit 0" — a provider that
    succeeds without audio is the failure this whole skill is built to avoid.
    """
    missing = [n for n in REQUIRED if not (d / n).exists()]
    if missing:
        sys.stderr.write(
            f"local-tts: model incomplete at {d} — missing: {', '.join(missing)}\n"
            f"local-tts: run  {Path(__file__).parent / 'fetch-local-model.sh'}\n"
        )
        sys.exit(2)


def parse_voice(voice: str) -> int:
    """`vits-zh-ll-sid<N>` → N. The `local-` prefix is stripped by synth.sh."""
    m = re.fullmatch(r"vits-zh-ll-sid(\d+)", voice)
    if not m:
        sys.stderr.write(
            f"local-tts: unsupported voice '{voice}' "
            "(expected vits-zh-ll-sid0 … vits-zh-ll-sid4)\n"
        )
        sys.exit(1)
    return int(m.group(1))


def warn_oov(text: str) -> None:
    hits = _OOV_RE.findall(text)
    if hits:
        uniq = sorted(set(hits))
        sys.stderr.write(
            f"local-tts: WARNING — {len(hits)} Latin token(s) will NOT be "
            f"spoken (Chinese-only lexicon): {', '.join(uniq[:12])}"
            + (" …\n" if len(uniq) > 12 else "\n")
        )


def apply_gain(samples):
    """×GAIN, then a tanh soft knee above KNEE. Ported verbatim in behaviour
    from `NarrationGain.apply` — 1.0 is the asymptote, so clipping is
    mathematically unreachable."""
    import numpy as np

    v = np.asarray(samples, dtype=np.float32) * GAIN
    a = np.abs(v)
    over = (a - KNEE) / (1.0 - KNEE)
    compressed = KNEE + (1.0 - KNEE) * np.tanh(over)
    return np.where(a > KNEE, np.sign(v) * compressed, v).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description="offline Chinese TTS (sherpa-onnx VITS-zh-ll)")
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", required=True, help="vits-zh-ll-sid0 … sid4")
    ap.add_argument("--output", required=True, help="destination .wav")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()

    if not args.text.strip():
        sys.stderr.write("local-tts: empty text\n")
        return 1

    sid = parse_voice(args.voice)
    d = model_dir()
    check_model(d)

    try:
        import numpy as np  # noqa: F401  (used by apply_gain)
        import sherpa_onnx
    except ImportError as e:
        sys.stderr.write(
            f"local-tts: {e}\n"
            f"local-tts: run  {Path(__file__).parent / 'fetch-local-model.sh'}  "
            "(it creates ~/.tts-toolkit/venv with sherpa-onnx + numpy)\n"
        )
        return 2

    warn_oov(args.text)

    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(d / "model.onnx"),
                lexicon=str(d / "lexicon.txt"),
                tokens=str(d / "tokens.txt"),
            ),
            num_threads=int(os.environ.get("TTS_LOCAL_THREADS", "4")),
            provider="cpu",
        ),
        rule_fsts=",".join(str(d / n) for n in RULE_FSTS),
        max_num_sentences=2,
    )
    tts = sherpa_onnx.OfflineTts(cfg)

    # Read back before trusting the engine — a bad config can yield a usable
    # object with zero speakers (ArtLens hit the C-pointer version of this).
    if tts.num_speakers <= 0:
        sys.stderr.write("local-tts: engine reports 0 speakers — model load failed\n")
        return 2
    if sid >= tts.num_speakers:
        sys.stderr.write(
            f"local-tts: sid {sid} out of range (model has {tts.num_speakers} speakers)\n"
        )
        return 1

    import numpy as np

    audio = tts.generate(args.text, sid=sid, speed=args.speed)
    # ⚠️ A FAILED generate returns sample_rate 0, not just empty samples
    #    (2026-09-22 measured: `wave.Error: bad frame rate` on the silence
    #    path). Fall back to the model's own rate — the engine object always
    #    reports it, generate does not.
    sample_rate = audio.sample_rate or tts.sample_rate
    if sample_rate <= 0:
        sys.stderr.write("local-tts: engine reports no sample rate\n")
        return 2

    if not len(audio.samples):
        # ⛔ Two very different causes, and collapsing them aborts whole runs.
        #
        # ① The chunk holds nothing this model can voice — an all-Latin line, a
        #    URL, a code fragment left after markdown stripping. sherpa logs
        #    "Failed to convert … to token IDs" and hands back zero samples.
        #    This is NOT an engine fault, and it must NOT fail the run: `local`
        #    is the LAST pool entry, so a non-zero exit here aborts synth.sh
        #    with nothing to fall back to — the offline engine exists precisely
        #    so a run never ends with no audio. Stand a short silence in its
        #    place and say so loudly.
        # ② Text that DOES contain Chinese yet still yields nothing is a real
        #    failure. Keep exiting 2 for that.
        if is_speakable(args.text):
            sys.stderr.write(
                "local-tts: engine returned 0 samples for text that contains "
                "Chinese — treating as an engine failure\n"
            )
            return 2
        sys.stderr.write(
            f"local-tts: WARNING — nothing in this chunk is voiceable by a "
            f"Chinese-only model; substituting {UNSPEAKABLE_SILENCE_S}s of "
            f"silence. ⚠️ THE TEXT IS NOT SPOKEN: {args.text[:120]!r}\n"
        )
        samples = np.zeros(int(sample_rate * UNSPEAKABLE_SILENCE_S), dtype=np.float32)
    else:
        samples = apply_gain(audio.samples)

    pcm = (samples * 32767.0).astype("<i2")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())

    # ⚠️ Measure what was WRITTEN, not what the engine returned — in the
    # silence-substitution branch those differ (engine 0, written 0.3s).
    dur = len(samples) / sample_rate
    sys.stderr.write(
        f"local-tts: sid={sid} chars={len(args.text)} audio={dur:.2f}s "
        f"peak={float(np.abs(pcm).max()) / 32767.0:.3f}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
