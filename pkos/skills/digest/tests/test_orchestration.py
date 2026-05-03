"""Tests for digest orchestrator structural contract.

Per project lesson 2026-05-02-pytest-main-guard-silent-pass.md: no __main__ guards.
"""
import os, re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]  # personal-os/pkos
DIGEST = ROOT / "skills" / "digest" / "SKILL.md"
COLLECT = ROOT / "skills" / "digest-collect" / "SKILL.md"
RENDER = ROOT / "skills" / "digest-render" / "SKILL.md"
PUBLISH = ROOT / "skills" / "digest-publish" / "SKILL.md"

def test_three_sub_skills_exist():
    for p in (COLLECT, RENDER, PUBLISH):
        assert p.exists(), f"Missing: {p}"

def test_orchestrator_invokes_each_sub_skill_in_order():
    text = DIGEST.read_text()
    collect_pos = text.find("pkos:digest-collect")
    render_pos = text.find("pkos:digest-render")
    publish_pos = text.find("pkos:digest-publish")
    assert collect_pos != -1, "orchestrator does not reference digest-collect"
    assert render_pos != -1, "orchestrator does not reference digest-render"
    assert publish_pos != -1, "orchestrator does not reference digest-publish"
    assert collect_pos < render_pos < publish_pos, \
        "sub-skills must appear in order: collect → render → publish"

def test_orchestrator_explicit_sequence_instruction():
    """Per DP-002 Option C, orchestrator body MUST contain explicit
    'do not stop' instruction so LLM commits to all 3 invocations.
    Regex deliberately tightened to require an imperative phrase ('MUST execute' /
    'do NOT stop') rather than the generic phrase 'all three' which appears in
    innocuous descriptive text and would false-positive."""
    text = DIGEST.read_text()
    assert re.search(r"MUST execute|do NOT stop", text, re.IGNORECASE), \
        "orchestrator missing continuous-execution instruction"

def test_sub_skill_frontmatter_declares_internal():
    """Each sub-skill description should mark it as internal sub-skill of pkos:digest."""
    for p in (COLLECT, RENDER, PUBLISH):
        text = p.read_text()
        assert "Internal sub-skill" in text or "Sub-skill" in text, \
            f"{p.name}: description should mark it as internal sub-skill of /digest"

def test_no_mactools_versioned_path_regression():
    """Phase 1 B1: mactools/1.0.1 hardcoded path was supposed to be removed
    but digest/SKILL.md:90 retained it. After Phase 4 split, neither digest
    nor digest-publish may contain it."""
    for p in (DIGEST, PUBLISH):
        text = p.read_text()
        assert "mactools/1.0.1" not in text, \
            f"{p.name} still contains mactools/1.0.1 hardcoded version"

def test_publish_uses_rename_resilient_mactools():
    """digest-publish must use the MACTOOLS_VER autodetect pattern
    (matching pkos/skills/inbox/SKILL.md:35-36)."""
    text = PUBLISH.read_text()
    assert "MACTOOLS_VER" in text, "digest-publish missing rename-resilient mactools autodetect"
