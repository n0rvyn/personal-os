"""podcast-studio fact-check helper.

The deterministic half of the 数据质检员 (data fact-checker). Like the gates in
`lib/episode.py`, the parts that must NOT rely on Claude getting it right live
here:

- `parse_sources`   — read the recorded provenance from a material-summary's
                      "当日新闻背景" section (url / vault refs).
- `trace_claim`     — does a claim's cited fact resolve to a recorded source?
- `check_factcheck` — the blocking gate. It RECOMPUTES each objective claim's
                      sourced-ness via `trace_claim`; it does NOT trust the
                      agent's per-claim `verdict` label for sourcing (the same
                      reason `lib/episode.select_draft` recomputes the winner
                      from `scores.total` and ignores the LLM's `selected`
                      flag). The agent's WebSearch can only ADD a flag
                      (`contradicted`); it can never clear an untraceable claim.

DP-001=A scope boundary: the objective-vs-subjective classification itself is
made by the agent and is a trusted boundary — subjective material (opinions,
the host's conditional/predictive bets) is by the temperature principle neither
verifiable nor in scope, so `subjective-skip` claims are never flagged. This
module re-adjudicates SOURCING within the objective set, not fact-vs-opinion.

Pure, deterministic, no network (WebSearch lives in the agent, not here).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Per-line length cap before any regex work (ReDoS belt-and-suspenders; real
# bullets are well under this).
_LINE_CAP = 4000

# Anchored, non-backtracking trailing-source pattern: `(source: <ref>, <date>)`.
# `\S+?` is a bounded non-greedy token with no nested quantifier — linear scan,
# no catastrophic backtracking.
_SOURCE_RE = re.compile(r"\(source:\s*(\S+?)\s*,\s*(\d{4}-\d{2}-\d{2})\)")

# Bold lead term of a bullet: `**...**`.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Section heading containing 当日新闻背景.
_NEWS_HEADING_RE = re.compile(r"^\s*#{1,6}\s*.*当日新闻背景")
# Any markdown heading / hard rule / brief marker that ends the news section.
_SECTION_END_RE = re.compile(r"^\s*(#{1,6}\s|---\s*$|brief-)")

_HTTPS_RE = re.compile(r"^https?://")


def _normalize(s: str | None) -> str:
    """Stable key form for a fact lead / a claim's cited_fact_id: collapse
    whitespace, lowercase (no-op on CJK). Used on BOTH the parsed key and the
    agent's `cited_fact_id` so minor formatting differences still match."""
    if not s or not isinstance(s, str):
        return ""
    return " ".join(s.split()).lower()


def _classify_ref(token: str, date: str) -> dict | None:
    """Map a source token to a typed ref, or None if not an accepted provenance.

    Accepted: an `https?://` URL, or the literal token `vault` (host-recorded
    material). Anything else → None (treated as unsourced, never crashes).
    """
    if _HTTPS_RE.match(token):
        return {"kind": "url", "url": token, "date": date}
    if token == "vault":
        return {"kind": "vault", "date": date}
    return None


def _lead_of(bullet: str) -> str:
    """Extract the lead term of a bullet (the bold heading, else text up to the
    first colon, else the whole bullet)."""
    m = _BOLD_RE.search(bullet)
    if m:
        return m.group(1).strip()
    # split on full-width or ASCII colon
    for sep in ("：", ":"):
        if sep in bullet:
            return bullet.split(sep, 1)[0].strip()
    return bullet.strip()


def _news_section(text: str) -> list[str]:
    """Return the bullet lines inside the 当日新闻背景 section (empty if none)."""
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        if not in_section:
            if _NEWS_HEADING_RE.match(line):
                in_section = True
            continue
        # in section: stop at the next heading / rule / brief marker
        if _SECTION_END_RE.match(line):
            break
        out.append(line)
    return out


def parse_sources(text: str) -> dict[str, dict[str, Any]]:
    """Parse the 当日新闻背景 section into `{fact_id: {lead, text, ref}}`.

    `fact_id` is the normalized lead term. `ref` is a typed dict
    (`{"kind":"url",...}` or `{"kind":"vault",...}`) or None (unsourced).
    Only the news section is read — pkos_note / brief JSON are ignored.
    """
    sources: dict[str, dict[str, Any]] = {}
    if not text or not isinstance(text, str):
        return sources

    for raw in _news_section(text):
        line = raw.strip()
        if not line.startswith("- "):
            continue
        bullet = line[2:].strip()
        capped = bullet[:_LINE_CAP]
        m = _SOURCE_RE.search(capped)
        ref = _classify_ref(m.group(1), m.group(2)) if m else None
        lead = _lead_of(capped)
        fid = _normalize(lead)
        if not fid:
            continue
        sources[fid] = {"lead": lead, "text": bullet, "ref": ref}
    return sources


def trace_claim(cited_fact_id: str | None, parsed_sources: dict[str, dict[str, Any]]) -> bool:
    """True iff `cited_fact_id` resolves to a parsed fact that has a recorded
    ref (url OR vault). False for None / unknown id / a fact whose ref is None.
    """
    key = _normalize(cited_fact_id)
    if not key:
        return False
    fact = parsed_sources.get(key)
    if fact is None:
        return False
    return fact.get("ref") is not None


def _fail(reason: str) -> dict[str, Any]:
    """Fail-closed gate result (deny-default, matching check_stance_card)."""
    return {"ok": False, "reason": reason, "flagged": []}


def check_factcheck(scratch_dir: str | Path, material_summary_path: str | Path) -> dict[str, Any]:
    """Blocking fact-check gate.

    Reads `factcheck-verdict.json` from `scratch_dir` and the recorded
    provenance from `material_summary_path`, then RECOMPUTES sourcing:
    an objective claim is flagged iff it does not trace to a recorded source
    (via `trace_claim`) OR the agent's WebSearch found a contradiction.
    `subjective-skip` claims are never flagged. The agent's per-claim `sourced`
    label and any top-level `ok` are ignored.

    Returns `{"ok": bool, "reason": str, "flagged": list}` — same shape family
    as `lib/episode.py` gates. Fail-closed on any read/parse error.
    """
    scratch = Path(scratch_dir)
    verdict_path = scratch / "factcheck-verdict.json"

    # --- load verdict (fail-closed) ---
    if not verdict_path.is_file():
        return _fail(f"missing verdict: {verdict_path}")
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — fail-closed on any parse error
        return _fail(f"unparseable verdict {verdict_path}: {e}")
    if not isinstance(verdict, dict):
        return _fail(f"verdict is not an object: {verdict_path}")

    # --- load provenance (fail-closed) ---
    try:
        sources = parse_sources(Path(material_summary_path).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return _fail(f"unreadable material-summary {material_summary_path}: {e}")

    claims = verdict.get("claims")
    if not isinstance(claims, list):
        return _fail("verdict.claims missing or not a list")

    flagged: list[dict[str, Any]] = []
    n_skip = 0
    n_traceable = 0
    n_contra = 0
    for c in claims:
        if not isinstance(c, dict):
            # malformed claim entry → fail-closed (flag it)
            flagged.append({"claim": str(c), "reason": "malformed claim entry"})
            continue
        label = c.get("verdict")
        if label == "subjective-skip":
            n_skip += 1
            continue  # opinions / bets — never flagged (temperature principle)
        # objective claim: recompute sourcing; honor only the agent's `contradicted`
        traceable = trace_claim(c.get("cited_fact_id"), sources)
        if c.get("verdict") == "contradicted":
            n_contra += 1
            flagged.append({**c, "reason": "web-contradicted"})
        elif not traceable:
            flagged.append({**c, "reason": "untraceable: no recorded source"})
        else:
            n_traceable += 1

    ok = len(flagged) == 0
    reason = (
        f"{'pass' if ok else 'FAIL'}: traceable={n_traceable} "
        f"subjective-skip={n_skip} flagged={len(flagged)} "
        f"(contradicted={n_contra}, untraceable={len(flagged) - n_contra})"
    )
    return {"ok": ok, "reason": reason, "flagged": flagged}
