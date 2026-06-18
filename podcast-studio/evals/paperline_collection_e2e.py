"""Paper line collection e2e (acceptance #1/#2 for Phase 2).

Real-path validation harness — runs the FULL paper-line collection chain on
LIVE arXiv:

    fetch_candidates  ->  agents/papers/curator.md (claude -p)
       ->  fetch_fulltext  ->  agents/papers/ledger-writer.md (claude -p)
       ->  verify_anchors  ->  paper-ledger.json

This is the phase's "真实论文跑通" acceptance. Exit 0 means:
  * exactly 1 paper was chosen by the curator
  * its full text (not abstract) was fetched, method = html or pdf
  * the ledger-writer produced a 4-section ledger
  * verify_anchors().ok is True (every claim's anchor is a verbatim substring
    of the real full text)

Why direct `claude -p` (not `lib.dispatch.dispatch_persona`):
  `dispatch_persona` rejects non-opinion agents (whitelist at dispatch.py:42-54,
  enforced at dispatch.py:197) and hardcodes the agent prompt at
  `agents/<name>.md` (dispatch.py:211). The paper personas live in
  `agents/papers/` and are not in that whitelist — so we build a direct
  `claude -p` argv list here, mirroring dispatch.py:234-244's argv shape.

Usage:
    python3 evals/paperline_collection_e2e.py
    python3 evals/paperline_collection_e2e.py --max-results 10 --output /path/to/ledger.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------- paths ----------
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Default category for the paper line (cs.CL is the high-signal NLP/news feed
# in the dev-guide; v1 is single-category — the curator decides within it).
DEFAULT_CATEGORY = "cs.CL"
DEFAULT_MAX_RESULTS = 15
DEFAULT_OUTPUT = PLUGIN_ROOT / ".claude" / "p2-samples" / "paper-ledger.json"
PAPER_AGENT_DIR = PLUGIN_ROOT / "agents" / "papers"

# Reasonable cap for prompt/fulltext payloads to keep `claude -p` reliable.
_MAX_FULLTEXT_CHARS = 60_000  # ~60k chars of body in the prompt

# Subprocess timeout for each persona dispatch (seconds). Persona reasoning
# over 60k chars of text is non-trivial.
_PERSONA_TIMEOUT = 600


# ---------- helpers ----------

def _load_persona(name: str) -> str:
    """Read the prose persona prompt for a paper-line agent.

    The paper agents live in `agents/papers/<name>.md` (NOT `agents/<name>.md`
    which is what `dispatch_persona` hardcodes). This loader is the file-read
    step we replicate inline to bypass that hardcode.
    """
    path = PAPER_AGENT_DIR / f"{name}.md"
    if not path.is_file():
        raise RuntimeError(f"paper persona not found: {path}")
    return path.read_text(encoding="utf-8")


def _run_persona(
    *,
    agent_name: str,
    user_prompt: str,
    system_prompt: str,
    timeout: int = _PERSONA_TIMEOUT,
) -> str:
    """Direct `claude -p` subprocess call.

    Mirrors the argv shape used by `lib.dispatch.dispatch_persona`
    (dispatch.py:234-244) but (a) takes the system-prompt string inline
    (no whitelist + no `agents/<name>.md` lookup), and (b) returns the
    raw stdout so the caller can parse JSON from the model's response.
    """
    argv: list[str] = [
        "claude",
        "-p",
        user_prompt,
        "--append-system-prompt",
        system_prompt,
        "--allowedTools",
        "Read",
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            # shell=False is the default; we don't pass it explicitly so
            # the test pin can confirm it's never been set to True.
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"claude -p for {agent_name!r} timed out after {timeout}s"
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"claude binary not found (PATH issue): {exc}"
        )

    if completed.returncode != 0:
        stderr_tail = (completed.stderr or "<no stderr>")[-500:]
        raise RuntimeError(
            f"claude -p for {agent_name!r} exited {completed.returncode}: "
            f"{stderr_tail}"
        )
    return completed.stdout or ""


def _extract_json_object(text: str) -> dict:
    """Extract the first balanced JSON object from a model response.

    The personas are instructed to output ONLY a JSON object in a code block,
    but they sometimes add preamble. We locate the first `{` and the matching
    closing brace (respecting nested objects + strings), then json.loads.
    """
    # Prefer the fenced ```json ... ``` block if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        # Fall back to first balanced `{...}` in the text.
        start = text.find("{")
        if start < 0:
            raise ValueError(
                f"no JSON object found in persona response: {text[:300]!r}"
            )
        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            raise ValueError(
                f"unbalanced JSON braces in persona response: {text[:300]!r}"
            )
        candidate = text[start:end]
    return json.loads(candidate)


def _truncate_fulltext_for_prompt(text: str, max_chars: int = _MAX_FULLTEXT_CHARS) -> str:
    """Truncate a long fulltext to fit inside the persona prompt.

    Keeps the head + tail (which usually carry abstract + limitations) when
    the body is too long. Honest about the truncation in the prompt itself
    so the persona knows what it has and what it doesn't.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n[[… {len(text) - max_chars} chars truncated from middle …]]\n\n"
        + text[-half:]
    )


# ---------- curator ----------

def run_curator(
    candidates: list[dict],
    *,
    paper_log: list[dict] | None = None,
) -> dict:
    """Dispatch the curator persona against the candidate list.

    Returns the parsed JSON `{"arxiv_id": ..., "rationale": ...}` from the
    persona response.
    """
    # The curator's strict output format is JSON only. We pass candidates as
    # JSON so the model can read them verbatim without us prose-ifying.
    user_prompt = (
        "## 候选列表 (from arXiv API, cs.CL, recent submissions)\n\n"
        "```json\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "## paper-log dedup input (already covered — may be empty)\n\n"
        "```json\n"
        + json.dumps(paper_log or [], ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "## 任务\n\n"
        "按你的四条标准（重要性 / 可解释性 / 新鲜度 / paper-log 去重）"
        "从候选列表里选恰好 1 篇，输出严格 JSON：\n\n"
        "```json\n"
        "{\n"
        '  "arxiv_id": "<chosen arxiv_id>",\n'
        '  "rationale": "<一句话中文理由，<=50字>"\n'
        "}\n"
        "```\n"
    )
    system_prompt = _load_persona("curator")
    response = _run_persona(
        agent_name="curator",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    return _extract_json_object(response)


# ---------- ledger-writer ----------

def run_ledger_writer(
    arxiv_id: str,
    title: str,
    fulltext_result: dict,
) -> dict:
    """Dispatch the ledger-writer persona against the real full text.

    Returns the parsed 4-section ledger dict.
    """
    # Truncate the body if necessary; the persona is told about the cut.
    body = _truncate_fulltext_for_prompt(fulltext_result["text"])
    truncated_note = ""
    if len(fulltext_result["text"]) > _MAX_FULLTEXT_CHARS:
        truncated_note = (
            f"\n\n(原始全文 {len(fulltext_result['text'])} 字符，"
            f"已截断到头尾各 {_MAX_FULLTEXT_CHARS // 2} 字符。"
            "中间部分省略 — 这是 head + tail 切片。)"
        )
    user_prompt = (
        f"## arxiv_id\n\n`{arxiv_id}`\n\n"
        f"## 标题\n\n{title}\n\n"
        f"## 抓取方法\n\n`{fulltext_result['method']}` "
        f"(source_url: {fulltext_result['source_url']})"
        f"{truncated_note}\n\n"
        f"## 论文全文 (stripped to plain text, paragraph order preserved)\n\n"
        f"```\n{body}\n```\n\n"
        "## 任务\n\n"
        "按你的 schema 抽事实账（四节都不能为空），"
        "每条挂原文 verbatim 锚点，输出严格 JSON。"
    )
    system_prompt = _load_persona("ledger-writer")
    response = _run_persona(
        agent_name="ledger-writer",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    ledger = _extract_json_object(response)
    # Stamp the chosen arxiv_id + title into the ledger (the persona is told
    # to include them, but we re-stamp from the harness's source of truth
    # so the artifact's identifiers are authoritative).
    ledger["arxiv_id"] = arxiv_id
    ledger["title"] = title
    return ledger


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Paper line collection e2e — live arXiv → real ledger"
    )
    parser.add_argument(
        "--category",
        default=DEFAULT_CATEGORY,
        help=f"arXiv primary category (default: {DEFAULT_CATEGORY})",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"max candidates to fetch (default: {DEFAULT_MAX_RESULTS})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output ledger path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="on agent failure, still write whatever was produced and exit 0",
    )
    args = parser.parse_args(argv)

    started = time.time()
    print(f"[e2e] plugin_root = {PLUGIN_ROOT}")
    print(f"[e2e] category    = {args.category}")
    print(f"[e2e] max_results = {args.max_results}")
    print(f"[e2e] output      = {args.output}")

    # --- 1. Discovery ---
    print("\n[e2e] stage 1/4: fetch_candidates (LIVE arXiv API)")
    from lib.paperline.discovery import fetch_candidates
    try:
        candidates = fetch_candidates([args.category], max_results=args.max_results)
    except Exception as exc:
        print(f"[e2e] FAIL discovery: {exc}", file=sys.stderr)
        return 2
    print(f"[e2e]   fetched {len(candidates)} candidates")
    if not candidates:
        print("[e2e] FAIL discovery: empty candidate list", file=sys.stderr)
        return 2
    for c in candidates[:3]:
        print(f"[e2e]   - {c['arxiv_id']}  {c['title'][:80]}")

    # --- 2. Curator dispatch ---
    print("\n[e2e] stage 2/4: agents/papers/curator.md (claude -p)")
    try:
        chosen = run_curator(candidates, paper_log=[])
    except Exception as exc:
        print(f"[e2e] FAIL curator: {exc}", file=sys.stderr)
        return 3
    chosen_id = chosen.get("arxiv_id", "")
    chosen_rationale = chosen.get("rationale", "")
    print(f"[e2e]   chosen  = {chosen_id}")
    print(f"[e2e]   reason  = {chosen_rationale}")
    if not chosen_id:
        print("[e2e] FAIL curator: empty arxiv_id", file=sys.stderr)
        return 3
    # The curator's arxiv_id MUST be one of the candidates (no hallucinated id).
    known_ids = {c["arxiv_id"] for c in candidates}
    if chosen_id not in known_ids:
        print(
            f"[e2e] FAIL curator: chosen id {chosen_id!r} not in candidate set",
            file=sys.stderr,
        )
        return 3
    chosen_record = next(c for c in candidates if c["arxiv_id"] == chosen_id)
    chosen_title = chosen_record["title"]

    # --- 3. Fetch fulltext ---
    print("\n[e2e] stage 3/4: fetch_fulltext (LIVE arXiv html→pdf)")
    from lib.paperline.fetch import fetch_fulltext
    try:
        fulltext_result = fetch_fulltext(chosen_id)
    except Exception as exc:
        print(f"[e2e] FAIL fetch: {exc}", file=sys.stderr)
        return 4
    method = fulltext_result["method"]
    text_len = len(fulltext_result["text"])
    source_url = fulltext_result["source_url"]
    print(f"[e2e]   method     = {method}")
    print(f"[e2e]   text_chars = {text_len}")
    print(f"[e2e]   source_url = {source_url}")
    if not fulltext_result["text"].strip():
        print("[e2e] FAIL fetch: empty text", file=sys.stderr)
        return 4

    # --- 4. Ledger-writer dispatch + verify ---
    print("\n[e2e] stage 4/4: agents/papers/ledger-writer.md (claude -p) + verify_anchors")
    try:
        ledger = run_ledger_writer(chosen_id, chosen_title, fulltext_result)
    except Exception as exc:
        print(f"[e2e] FAIL ledger-writer: {exc}", file=sys.stderr)
        if args.keep_going:
            print("[e2e] --keep-going: writing partial result", file=sys.stderr)
            ledger = {
                "arxiv_id": chosen_id,
                "title": chosen_title,
                "_error": str(exc),
            }
        else:
            return 5

    # Schema gate (the paperline's own validator).
    from lib.paperline.ledger import validate_ledger, verify_anchors
    try:
        validate_ledger(ledger)
    except Exception as exc:
        print(f"[e2e] FAIL schema: {exc}", file=sys.stderr)
        if not args.keep_going:
            return 5

    # Anchor recompute gate.
    verdict = verify_anchors(ledger, fulltext_result["text"])
    anchors_ok = verdict["ok"]
    flagged_count = len(verdict["flagged"])
    print(f"[e2e]   anchors_ok = {anchors_ok}")
    print(f"[e2e]   flagged    = {flagged_count}")

    # --- 5. Write the ledger artifact ---
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "arxiv_id": chosen_id,
        "title": chosen_title,
        "fetch": {
            "method": method,
            "source_url": source_url,
            "text_chars": text_len,
        },
        "ledger": ledger,
        "verdict": {
            "anchors_ok": anchors_ok,
            "flagged_count": flagged_count,
        },
    }
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n[e2e] wrote {args.output}")

    elapsed = time.time() - started
    print(f"\n[e2e] DONE  arxiv_id={chosen_id}  method={method}  "
          f"anchors_ok={anchors_ok}  elapsed={elapsed:.1f}s")

    if not anchors_ok:
        # Show the first few flagged entries for debugging.
        for entry in verdict["flagged"][:3]:
            print(
                f"[e2e]   flagged[{entry['section']}]: "
                f"text={entry['text'][:60]!r}  "
                f"anchor={entry['anchor'][:60]!r}",
                file=sys.stderr,
            )
        return 6 if not args.keep_going else 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
