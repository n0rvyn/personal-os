"""Tests for lib/factcheck.py — source parsing, claim traceability, and the
coded fact-check gate.

Written before lib/factcheck.py exists; collection must fail at this point
(`No module named 'lib.factcheck'`). The gate's central contract: it RECOMPUTES
sourcing from the recorded provenance via trace_claim and does NOT trust the
agent's per-claim `verdict` label (mirroring lib/episode.select_draft ignoring
the LLM's `selected` flag). subjective-skip claims are never flagged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.factcheck import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 1-impl will resolve this."""
    from lib import factcheck  # noqa: F401

    assert hasattr(factcheck, "parse_sources")
    assert hasattr(factcheck, "trace_claim")
    assert hasattr(factcheck, "check_factcheck")


# ---------- fixtures ----------

# A material-summary with a "当日新闻背景" section: one url-sourced fact, one
# vault-sourced fact, one unsourced fact, one bad-scheme fact.
MATERIAL_SUMMARY = """# 素材摘要 — 2026-06-11 早间

## 选定主观笔记 (pkos_note)

**id**: 多邻国与AI有感

## 当日新闻背景（搜索补充，2026-06-11）

- **Anthropic $965B**: 完成 Series H，估值接近 OpenAI (source: https://example.com/anthropic, 2026-06-11)
- **多邻国留存观察**: 宿主自己记录的一手观察 (source: vault, 2026-06-10)
- **GPT-5.4**: 百万 token 上下文，OSWorld-V 基准 75%
- **某传闻**: 据说有大事 (source: 据说, 2026-06-11)

## 前晚未解问题

- 渐进式替代是否已经在发生？

---

brief-A

```json
{"approved_topics": []}
```
"""


def _fact_id(sources: dict, needle: str) -> str:
    """Return the parsed fact_id (key) whose lead/text contains `needle`.

    Tests look facts up by content so they don't couple to the slug algorithm.
    """
    for fid, v in sources.items():
        if needle in v.get("lead", "") or needle in v.get("text", ""):
            return fid
    raise AssertionError(f"no parsed fact contains {needle!r}; keys={list(sources)}")


# ---------- parse_sources ----------

def test_parse_sources_url_kind():
    from lib.factcheck import parse_sources

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "Anthropic")
    ref = s[fid]["ref"]
    assert ref is not None
    assert ref["kind"] == "url"
    assert ref["url"] == "https://example.com/anthropic"
    assert ref["date"] == "2026-06-11"


def test_parse_sources_vault_kind():
    from lib.factcheck import parse_sources

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "多邻国留存")
    ref = s[fid]["ref"]
    assert ref is not None
    assert ref["kind"] == "vault"
    assert ref["date"] == "2026-06-10"
    # vault is a provenance, not a web url
    assert "url" not in ref


def test_parse_sources_unsourced_is_none():
    from lib.factcheck import parse_sources

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "GPT-5.4")
    assert s[fid]["ref"] is None


def test_parse_sources_bad_scheme_is_none():
    from lib.factcheck import parse_sources

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "某传闻")
    # "据说" is neither http(s) nor the literal vault → treated as unsourced
    assert s[fid]["ref"] is None


def test_parse_sources_ftp_scheme_is_none():
    from lib.factcheck import parse_sources

    text = MATERIAL_SUMMARY.replace(
        "(source: 据说, 2026-06-11)", "(source: ftp://x/y, 2026-06-11)"
    )
    s = parse_sources(text)
    fid = _fact_id(s, "某传闻")
    assert s[fid]["ref"] is None


def test_parse_sources_only_reads_news_section():
    """Facts outside the 当日新闻背景 section (e.g. pkos_note, brief JSON) are
    not parsed as news facts."""
    from lib.factcheck import parse_sources

    s = parse_sources(MATERIAL_SUMMARY)
    # the pkos_note id line and the brief block must not appear as facts
    assert all("approved_topics" not in v["text"] for v in s.values())


def test_parse_sources_redos_guard_returns():
    """A pathological long line must return (anchored non-backtracking regex),
    not hang."""
    from lib.factcheck import parse_sources

    evil = "## 当日新闻背景\n- " + ("(" * 50000) + " (source: https://x, 2026-06-11)\n"
    s = parse_sources(evil)  # must simply return
    assert isinstance(s, dict)


def test_parse_sources_empty_text():
    from lib.factcheck import parse_sources

    assert parse_sources("") == {}
    assert parse_sources("no news section here") == {}


# ---------- trace_claim ----------

def test_trace_claim_url_kind_true():
    from lib.factcheck import parse_sources, trace_claim

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "Anthropic")
    assert trace_claim(fid, s) is True


def test_trace_claim_vault_kind_true():
    from lib.factcheck import parse_sources, trace_claim

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "多邻国留存")
    assert trace_claim(fid, s) is True


def test_trace_claim_unsourced_false():
    from lib.factcheck import parse_sources, trace_claim

    s = parse_sources(MATERIAL_SUMMARY)
    fid = _fact_id(s, "GPT-5.4")
    assert trace_claim(fid, s) is False


def test_trace_claim_nonexistent_false():
    from lib.factcheck import parse_sources, trace_claim

    s = parse_sources(MATERIAL_SUMMARY)
    assert trace_claim("no-such-fact", s) is False


def test_trace_claim_none_false():
    from lib.factcheck import parse_sources, trace_claim

    s = parse_sources(MATERIAL_SUMMARY)
    assert trace_claim(None, s) is False


# ---------- check_factcheck ----------

def _write(scratch: Path, verdict: dict) -> None:
    (scratch / "factcheck-verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False), encoding="utf-8"
    )


def _material(tmp_path: Path) -> Path:
    p = tmp_path / "material-summary.md"
    p.write_text(MATERIAL_SUMMARY, encoding="utf-8")
    return p


def _ids(mat_path: Path):
    """Return (anthropic_id url-sourced, gpt_id unsourced) from the fixture."""
    from lib.factcheck import parse_sources

    s = parse_sources(mat_path.read_text(encoding="utf-8"))
    return _fact_id(s, "Anthropic"), _fact_id(s, "GPT-5.4")


def test_check_missing_verdict_fails_closed(tmp_path):
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False
    assert "reason" in out


def test_check_clean_passes(tmp_path):
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    anthropic, _ = _ids(mat)
    _write(tmp_path, {"claims": [
        {"claim": "Anthropic 估值九百六十五亿", "type": "number",
         "cited_fact_id": anthropic, "verdict": "sourced", "note": "ok"},
        {"claim": "怪物养在体内", "type": "number",
         "cited_fact_id": None, "verdict": "subjective-skip", "note": "opinion"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is True
    assert out["flagged"] == []


def test_check_bypass_sourced_but_null_cite(tmp_path):
    """BYPASS pin: agent labels a claim `sourced` but cites nothing → gate
    recomputes via trace_claim and STILL flags it."""
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    _write(tmp_path, {"claims": [
        {"claim": "估值九百六十五亿", "type": "number",
         "cited_fact_id": None, "verdict": "sourced", "note": "agent lies"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False
    assert any(c["claim"] == "估值九百六十五亿" for c in out["flagged"])


def test_check_bypass_sourced_but_unsourced_fact(tmp_path):
    """BYPASS pin 2: agent labels `sourced` citing a fact whose ref is None →
    flagged."""
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    _, gpt = _ids(mat)
    _write(tmp_path, {"claims": [
        {"claim": "OSWorld-V 75%", "type": "number",
         "cited_fact_id": gpt, "verdict": "sourced", "note": "agent lies"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False
    assert any(c["claim"] == "OSWorld-V 75%" for c in out["flagged"])


def test_check_contradicted_flagged_even_if_traces(tmp_path):
    """A recorded-but-wrong source: agent found a contradiction → flagged even
    though it traces."""
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    anthropic, _ = _ids(mat)
    _write(tmp_path, {"claims": [
        {"claim": "Anthropic 估值九百六十五亿", "type": "number",
         "cited_fact_id": anthropic, "verdict": "contradicted", "note": "web says 850"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False
    assert any(c["claim"] == "Anthropic 估值九百六十五亿" for c in out["flagged"])


def test_check_subjective_skip_never_flagged(tmp_path):
    """A subjective-skip claim with no cite is never flagged (opinions/bets carry
    no source by design) — temperature preservation."""
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    _write(tmp_path, {"claims": [
        {"claim": "如果续约率超过 90%", "type": "number",
         "cited_fact_id": None, "verdict": "subjective-skip", "note": "host bet"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is True
    assert out["flagged"] == []


def test_check_ignores_agent_top_level_ok(tmp_path):
    """Agent self-reports ok:true but an objective claim is untraceable → gate
    recomputes and returns ok=False (mirrors select_draft ignoring `selected`)."""
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    _write(tmp_path, {"ok": True, "claims": [
        {"claim": "GPT-5.4 OSWorld-V 75%", "type": "number",
         "cited_fact_id": None, "verdict": "sourced", "note": "agent insists fine"},
    ]})
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False


def test_check_malformed_json_fails_closed(tmp_path):
    from lib.factcheck import check_factcheck

    mat = _material(tmp_path)
    (tmp_path / "factcheck-verdict.json").write_text("{not json", encoding="utf-8")
    out = check_factcheck(str(tmp_path), str(mat))
    assert out["ok"] is False


def test_check_missing_material_summary_fails_closed(tmp_path):
    from lib.factcheck import check_factcheck

    _write(tmp_path, {"claims": []})
    out = check_factcheck(str(tmp_path), str(tmp_path / "nope.md"))
    assert out["ok"] is False
