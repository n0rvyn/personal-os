"""Content-addressed cache wrapping the four paid MediaSeams calls
(gen_still / gen_video / synth / gen_character_ref).

See docs/06-plans/2026-07-06-video-studio-resume-media-cache-design.md § Pillar B.

`cache_key` is a pure function: sha256 over a canonicalized string built from
provider + endpoint + style_prefix + prompt + sha256(ref_url) + extra (json,
sorted keys). `CachingMediaSeams` wraps an inner seams object with the *same*
interface (transparent to callers) and stores/replays results via a
content-addressed blob + a `.meta.json` sidecar (atomic write: `.tmp` then
`os.replace`).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


def cache_key(
    kind: str,
    *,
    provider: str,
    endpoint: str,
    style_prefix: str,
    prompt: str,
    ref_url: str | None,
    extra: dict,
) -> str:
    """Pure, reproducible cache key. `ref_url` is hashed (never embedded
    verbatim) to avoid leaking/ballooning base64 data URIs into the key."""
    ref_hash = hashlib.sha256((ref_url or "").encode("utf-8")).hexdigest()
    extra_json = json.dumps(extra or {}, sort_keys=True, ensure_ascii=False)
    parts = [
        kind,
        provider or "",
        endpoint or "",
        style_prefix or "",
        prompt or "",
        ref_hash,
        extra_json,
    ]
    payload = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_bytes(dst: Path, data: bytes) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dst)


def _atomic_write_text(dst: Path, text: str) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dst)


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


class CachingMediaSeams:
    """Wraps an `inner` MediaSeams-like object; same call interface, content-
    addressed cache in front of the four paid calls."""

    def __init__(self, inner, cache_dir, *, render_dims: tuple[int, int], fresh: bool = False, log=None):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._wh = render_dims
        self.fresh = fresh
        self.log = log or (lambda *a, **kw: None)
        self.hits = 0
        self.misses = 0

    # -- style_prefix must be a property: writes need to reach `inner`
    # (MR-3) because MediaSeams.gen_still prepends `self.style_prefix` at
    # call time (late-bound), and `step_extract` assigns
    # `seams.style_prefix = ...` *after* wrap_seams has already wrapped it.
    @property
    def style_prefix(self):
        return self.inner.style_prefix

    @style_prefix.setter
    def style_prefix(self, v):
        self.inner.style_prefix = v

    def __getattr__(self, name):
        # Only called for attributes not found via normal lookup (i.e. not
        # defined on this class) -- forwards read-only, early-bound-ish
        # attributes like `provider`/`voice_id`/`dry` to `inner`.
        return getattr(self.inner, name)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _blob_paths(self, key: str, ext: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}{ext}", self.cache_dir / f"{key}.meta.json"

    def _read_meta(self, meta_path: Path) -> dict:
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def _write_meta(self, meta_path: Path, meta: dict) -> None:
        _atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False))

    def _hit_valid(self, blob: Path, meta: Path) -> bool:
        return (
            not self.fresh
            and blob.exists()
            and blob.stat().st_size > 0
            and meta.exists()
        )

    # ------------------------------------------------------------------
    # gen_still
    # ------------------------------------------------------------------

    def gen_still(self, prompt, ref_url, out_path):
        endpoint = os.environ.get("PIC_ENDPOINT", "")
        style_prefix = getattr(self.inner, "style_prefix", "")
        extra = {"w": self._wh[0], "h": self._wh[1]}
        key = cache_key(
            "still",
            provider=getattr(self.inner, "provider", ""),
            endpoint=endpoint,
            style_prefix=style_prefix,
            prompt=prompt,
            ref_url=ref_url,
            extra=extra,
        )
        # ext unknown until we know the meta (hit) or the inner result (miss);
        # look for an existing meta first to discover the ext.
        candidates = list(self.cache_dir.glob(f"{key}.meta.json"))
        if candidates and not self.fresh:
            meta_path = candidates[0]
            try:
                meta = self._read_meta(meta_path)
                ext = meta["ext"]
                blob = self.cache_dir / f"{key}{ext}"
                if self._hit_valid(blob, meta_path):
                    self.hits += 1
                    dst = os.path.splitext(out_path)[0] + ext
                    _atomic_copy(blob, Path(dst))
                    return dst
            except Exception:
                pass

        # miss
        self.misses += 1
        real_path = self.inner.gen_still(prompt, ref_url, out_path)
        ext = os.path.splitext(real_path)[1]
        blob, meta_path = self._blob_paths(key, ext)
        _atomic_copy(Path(real_path), blob)
        self._write_meta(meta_path, {"ext": ext, "ret_kind": "path", "ret": None})
        return real_path

    # ------------------------------------------------------------------
    # gen_video
    # ------------------------------------------------------------------

    def gen_video(self, prompt, ref_url, out_path):
        endpoint = os.environ.get("VIDEO_ENDPOINT", "")
        style_prefix = getattr(self.inner, "style_prefix", "")
        extra = {"w": self._wh[0], "h": self._wh[1]}
        key = cache_key(
            "video",
            provider=getattr(self.inner, "provider", ""),
            endpoint=endpoint,
            style_prefix=style_prefix,
            prompt=prompt,
            ref_url=ref_url,
            extra=extra,
        )
        ext = os.path.splitext(out_path)[1] or ".mp4"
        blob, meta_path = self._blob_paths(key, ext)

        if self._hit_valid(blob, meta_path):
            self.hits += 1
            _atomic_copy(blob, Path(out_path))
            meta = self._read_meta(meta_path)
            ret = dict(meta["ret"])
            ret["path"] = out_path
            return ret

        self.misses += 1
        ret = self.inner.gen_video(prompt, ref_url, out_path)
        _atomic_copy(Path(out_path), blob)
        stored_ret = dict(ret)
        self._write_meta(meta_path, {"ext": ext, "ret_kind": "dict", "ret": stored_ret})
        return ret

    # ------------------------------------------------------------------
    # synth
    # ------------------------------------------------------------------

    def synth(self, text, out_path):
        voice_id = getattr(self.inner, "voice_id", "")
        key = cache_key(
            "synth",
            provider=getattr(self.inner, "provider", ""),
            endpoint="",
            style_prefix="",
            prompt=text,
            ref_url="",
            extra={"voice_id": voice_id},
        )
        ext = os.path.splitext(out_path)[1] or ".mp3"
        blob, meta_path = self._blob_paths(key, ext)

        if self._hit_valid(blob, meta_path):
            self.hits += 1
            _atomic_copy(blob, Path(out_path))
            meta = self._read_meta(meta_path)
            return meta["ret"]

        self.misses += 1
        ret = self.inner.synth(text, out_path)
        _atomic_copy(Path(out_path), blob)
        self._write_meta(meta_path, {"ext": ext, "ret_kind": "int", "ret": ret})
        return ret

    # ------------------------------------------------------------------
    # gen_character_ref
    # ------------------------------------------------------------------

    def gen_character_ref(self, prompt, out_path):
        endpoint = os.environ.get("PIC_ENDPOINT", "")
        style_prefix = getattr(self.inner, "style_prefix", "")
        key = cache_key(
            "char_ref",
            provider=getattr(self.inner, "provider", ""),
            endpoint=endpoint,
            style_prefix=style_prefix,
            prompt=prompt,
            ref_url="",
            extra={},
        )
        ext = os.path.splitext(out_path)[1] or ".png"
        blob, meta_path = self._blob_paths(key, ext)

        if self._hit_valid(blob, meta_path):
            self.hits += 1
            _atomic_copy(blob, Path(out_path))
            meta = self._read_meta(meta_path)
            return meta["ret"]

        self.misses += 1
        ret = self.inner.gen_character_ref(prompt, out_path)
        _atomic_copy(Path(out_path), blob)
        self._write_meta(meta_path, {"ext": ext, "ret_kind": "url", "ret": ret})
        return ret


def clean_cache(cache_dir) -> None:
    shutil.rmtree(cache_dir, ignore_errors=True)


def wrap_seams(inner, cache_dir, *, render_dims: tuple[int, int], fresh: bool = False, log=None):
    if getattr(inner, "dry", False):
        return inner
    return CachingMediaSeams(inner, cache_dir, render_dims=render_dims, fresh=fresh, log=log)
