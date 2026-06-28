"""TTS helper for video-studio.

Wraps MiniMax speech-02-hd to synthesize one text segment at a time and
return its real audio duration in milliseconds (the value the
`extra_info.audio_length` field carries). The duration is used by the
spike driver as that beat's on-screen time ([D-006] narration-driven
timeline) and by the subtitle builder as the beat's total length.

All API calls go through `lib.mmclient.post_json` (Bearer auth,
env-driven, base_resp check, key-redacted logs). The response carries
audio as a hex string in `data.audio`; we decode it to bytes and write
the mp3 to a fixed local path. The remote filename is never trusted.
"""

from __future__ import annotations

import os
import sys

from . import mmclient


# Single voice id for v1; Stage 1 will move this into config.
VOICE_ID = "male-qn-qingse"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def synth(text: str, out_path: str, timeout: int = 120) -> int:
    """Synthesize `text` to an mp3 file and return its real length in ms.

    Args:
        text: Narration text for this beat (Chinese by default; the voice
            is locked to a single v1 id, see VOICE_ID).
        out_path: Local file path to write the mp3 to. Parent directory
            must already exist.
        timeout: Request timeout in seconds.

    Returns:
        Integer audio length in milliseconds, taken from
        `extra_info.audio_length` in the API response. This is the real
        rendered duration; use it as the beat's on-screen length.

    Raises:
        RuntimeError: on API failure, missing audio payload, decode
            failure, or write failure.
    """
    payload = {
        "model": "speech-02-hd",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": VOICE_ID,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "format": "mp3",
        },
    }

    resp = mmclient.post_json("/v1/t2a_v2", payload, timeout=timeout)

    data = resp.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("MiniMax t2a_v2: missing 'data' object")

    audio_hex = data.get("audio")
    if not isinstance(audio_hex, str) or not audio_hex:
        raise RuntimeError("MiniMax t2a_v2: missing 'audio' (hex) payload")

    # Decode hex -> bytes; even-length required.
    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError as e:
        raise RuntimeError(f"MiniMax t2a_v2: invalid hex audio payload: {e}") from e

    if len(audio_bytes) < 1024:
        raise RuntimeError(
            f"MiniMax t2a_v2: audio payload too small: {len(audio_bytes)} bytes"
        )

    extra_info = data.get("extra_info")
    if not isinstance(extra_info, dict):
        raise RuntimeError("MiniMax t2a_v2: missing 'extra_info' object")
    audio_length = extra_info.get("audio_length")
    if not isinstance(audio_length, int) or audio_length <= 0:
        raise RuntimeError(
            f"MiniMax t2a_v2: invalid 'audio_length': {audio_length!r}"
        )

    _log(f"[media_tts] writing {len(audio_bytes)} bytes of mp3 to {out_path}")
    # Atomic-ish write: write then rename if the destination lives on
    # the same filesystem; we don't depend on this for correctness.
    tmp_out = out_path + ".tmp"
    with open(tmp_out, "wb") as fh:
        fh.write(audio_bytes)
    os.replace(tmp_out, out_path)

    return audio_length
