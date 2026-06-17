"""tools/sync_voice_corpus.py tests (FAIL-first — the module is written after).

Loaded via importlib (it lives under tools/, not lib/). The network opener is
injected so no test ever hits the real norvyn.com.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = PLUGIN_ROOT / "tools"


def _load_mod():
    spec = importlib.util.spec_from_file_location(
        "sync_voice_corpus", TOOLS / "sync_voice_corpus.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


def _opener_for(payload):
    def _open(url, timeout=None):
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    return _open


_POSTS = {
    "data": [
        {"slug": "kai-fa-ri-zhi-6-xiu-bug", "title": "开发日志6: 像治病一样修 Bug", "content": "第一段\n\n第二段"},
        {"slug": "kai-fa-ri-zhi-4-bo-ke", "title": "开发日志4: 做播客给自己听", "content": "正文"},
        {"slug": "some-other-post", "title": "随便一篇散文", "content": "不该进来"},
    ]
}


def test_filters_to_devlog_and_writes(tmp_path):
    mod = _load_mod()
    n = mod.sync(str(tmp_path), source_url="x", filter_str="开发日志", opener=_opener_for(_POSTS))

    written = sorted(p.name for p in tmp_path.glob("*.md"))
    assert n == 2
    assert written == ["kai-fa-ri-zhi-4-bo-ke.md", "kai-fa-ri-zhi-6-xiu-bug.md"]

    body = (tmp_path / "kai-fa-ri-zhi-6-xiu-bug.md").read_text(encoding="utf-8")
    assert body.startswith("---")
    assert "title: 开发日志6: 像治病一样修 Bug" in body
    assert "第一段\n\n第二段" in body  # real newlines preserved
    # The non-devlog post must not appear in any written file.
    all_text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.md"))
    assert "不该进来" not in all_text


def test_rejects_path_traversal_slug(tmp_path):
    mod = _load_mod()
    payload = {
        "data": [
            {"slug": "../evil", "title": "开发日志X: 恶意", "content": "x"},
            {"slug": "kai-fa-ri-zhi-7-ok", "title": "开发日志7: 正常", "content": "ok"},
        ]
    }
    n = mod.sync(str(tmp_path), source_url="x", filter_str="开发日志", opener=_opener_for(payload))

    names = sorted(p.name for p in tmp_path.glob("*.md"))
    assert names == ["kai-fa-ri-zhi-7-ok.md"]  # traversal slug rejected
    assert not (tmp_path.parent / "evil.md").exists()  # nothing escaped the out dir
    assert n == 1


def test_network_failure_no_wipe(tmp_path):
    """A fetch failure leaves any existing corpus untouched and exits non-zero
    (fail-soft — this repo's silent-degradation guard)."""
    mod = _load_mod()
    keep = tmp_path / "kai-fa-ri-zhi-1-existing.md"
    keep.write_text("已有的料", encoding="utf-8")

    def _boom(url, timeout=None):
        raise OSError("network down")

    rc = mod.main(["--out", str(tmp_path), "--source-url", "x"], opener=_boom)

    assert rc != 0
    assert keep.exists()
    assert keep.read_text(encoding="utf-8") == "已有的料"  # not wiped
