"""Input fingerprints ("stamps") for durable render artifacts (beats.json /
shotlist.json).

A stamp is a hash over everything upstream of an LLM-produced durable
artifact (source text, persona/director prompt text, relevant config
fields). Before reusing a durable artifact on resume, the caller recomputes
the stamp from current inputs and compares it (via ``stamp_matches``) against
the stamp recorded alongside the artifact when it was first written. Any
upstream change (textbook source, persona text, host name/tone/gender,
art_style, budget override, etc.) changes the stamp and forces a re-roll.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional


def _sig(*parts: str) -> str:
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def beats_stamp(fulltext: str, persona_text: str, config_fields: dict) -> str:
    return _sig(
        fulltext,
        persona_text,
        json.dumps(config_fields, sort_keys=True, ensure_ascii=False),
    )


def shotlist_stamp(beats_json_text: str, persona_text: str, config_fields: dict) -> str:
    return _sig(
        beats_json_text,
        persona_text,
        json.dumps(config_fields, sort_keys=True, ensure_ascii=False),
    )


def write_stamp(path: Path, value: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    import os

    os.replace(tmp, path)


def read_stamp(path: Path) -> Optional[str]:
    path = Path(path)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def stamp_matches(path: Path, value: str) -> bool:
    current = read_stamp(path)
    if current is None:
        return False
    return current == value
