"""Tests for vault_contract.load_recall_dirs — parse the vault's directory
contract, exclude NOT-marked dirs, and fall back to PKOS defaults safely."""
import os

from vault_contract import (
    load_recall_dirs,
    DEFAULT_SELF_PAST_DIRS,
    DEFAULT_CROSS_DOMAIN_DIRS,
)

# Mirrors the live PKOS contract's Recall-contract section, including the
# multi-line self_past bullet and the `NOT 20-Ideas/产品想法/` exclusion.
SAMPLE_CONTRACT = """---
type: system-doc
---

# PKOS Vault — Directory Contract

## Content types

(unrelated table)

## Recall contract

`podcast-prep` recall reads strictly by directory:

- `self_past_candidates` ← `20-Ideas/观点心得/` (past viewpoints) + `90-Productions/Podcasts/`
  (past on-record stances). NOT `20-Ideas/产品想法/` — product ideas are not stances.
- `cross_domain_candidates` ← `10-Knowledge/` + `20-Ideas/`, bucketed by domain
- `50-References/` is supporting context, never a stance source
- `90-Productions/WeChat/` is a production archive — NOT read by recall

## 90-Productions subdirectories

(next section — parsing must stop here)
"""


def _write_contract(vault_root, body):
    sysdir = os.path.join(vault_root, "99-System")
    os.makedirs(sysdir, exist_ok=True)
    with open(os.path.join(sysdir, "10-Directory-Contract.md"), "w", encoding="utf-8") as f:
        f.write(body)


def test_parses_recall_section(tmp_path):
    root = str(tmp_path)
    _write_contract(root, SAMPLE_CONTRACT)
    dirs = load_recall_dirs(root)
    assert dirs["self_past"] == ("20-Ideas/观点心得/", "90-Productions/Podcasts/")
    assert dirs["cross_domain"] == ("10-Knowledge/", "20-Ideas/")


def test_not_marked_dir_is_excluded(tmp_path):
    # The `NOT 20-Ideas/产品想法/` exclusion must keep 产品想法 out of self_past.
    root = str(tmp_path)
    _write_contract(root, SAMPLE_CONTRACT)
    dirs = load_recall_dirs(root)
    assert "20-Ideas/产品想法/" not in dirs["self_past"]
    # And the WeChat "NOT read by recall" bullet must not leak into any channel.
    assert all("WeChat" not in d for d in dirs["self_past"] + dirs["cross_domain"])


def test_fallback_when_file_absent(tmp_path):
    # No 99-System/ contract → PKOS defaults, never raises.
    dirs = load_recall_dirs(str(tmp_path))
    assert dirs["self_past"] == DEFAULT_SELF_PAST_DIRS
    assert dirs["cross_domain"] == DEFAULT_CROSS_DOMAIN_DIRS


def test_fallback_when_no_recall_section(tmp_path):
    root = str(tmp_path)
    _write_contract(root, "# Contract\n\n## Content types\n\nno recall section here\n")
    dirs = load_recall_dirs(root)
    assert dirs["self_past"] == DEFAULT_SELF_PAST_DIRS
    assert dirs["cross_domain"] == DEFAULT_CROSS_DOMAIN_DIRS


def test_fallback_when_vault_root_none():
    dirs = load_recall_dirs(None)
    assert dirs["self_past"] == DEFAULT_SELF_PAST_DIRS
    assert dirs["cross_domain"] == DEFAULT_CROSS_DOMAIN_DIRS


def test_partial_section_uses_default_for_missing_channel(tmp_path):
    # Only self_past present → cross_domain falls back to default.
    root = str(tmp_path)
    body = "## Recall contract\n\n- `self_past_candidates` ← `20-Ideas/观点心得/`\n\n## End\n"
    _write_contract(root, body)
    dirs = load_recall_dirs(root)
    assert dirs["self_past"] == ("20-Ideas/观点心得/",)
    assert dirs["cross_domain"] == DEFAULT_CROSS_DOMAIN_DIRS
