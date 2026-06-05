"""Podcast-prep orchestrator. Composes helpers (topic_log + angle_slots + minhash_check
+ contrarian_pull + cross_domain) into check + finalize subcommands. PKOS note is
caller-provided per DP-001 A.

The `check` brief carries three insight-density fields beyond the base topic gate:
- cross_domain_candidates: PKOS notes from NON-tech domains, to seed cross-domain synthesis
- self_past_candidates: past notes (7-30d) on a similar topic, for self-contrarian dialogue
- named_concept_prompt: a directive nudging the writer to name an emergent pattern
"""
import argparse, json, os, re, sys
from datetime import date
from pathlib import Path

# Helper imports (relative to scripts/)
# DP-001 A: NO pkos_pull import — PKOS metadata is supplied by 达芬奇 invoking
# pkos:serendipity SKILL and passing the resulting note as `--pkos-note` argument.
sys.path.insert(0, str(Path(__file__).parent))
from topic_log import load_topic_log, recent_topic_tags, append_episode
from angle_slots import DEFAULT_ANGLES, pick_unused_angle
from minhash_check import max_jaccard_against, max_similarity_against
from contrarian_pull import pick_contrarian_source
from cross_domain import load_pkos_notes, cross_domain_candidates, same_topic_past_notes, rank_open_questions, fresh_today_notes
from domain_select import select_with_domain_quota

NAMED_CONCEPT_PROMPT = (
    "命名是挣来的，不是硬塞的：命名为可选项，只在今天的素材里真的浮现出一个还没有名字的"
    "新现象、新模式或新张力时才命名——给它起一个 3-5 字、有画面感、可复用的名字"
    "（参考范例「信息平原」）。如果今天没有任何值得命名的新现象，就完全省略命名，"
    "不要硬凑、不要为了凑而命名；这种省略是正确选择，不会被扣分。"
)

# Indirection point for test mocking (contrarian-source random pick)
def _contrarian_pull(seed=None, exclude_categories=None, force_source=None):
    return pick_contrarian_source(seed=seed, exclude_categories=exclude_categories,
                                  force_source=force_source)


def _resolve_vault_root(explicit=None):
    """Resolve the PKOS vault root. Priority: explicit arg > PKOS_VAULT_ROOT env >
    personal-os.yaml (pkos_root key, else exchange_dir parent) > ~/Obsidian/PKOS default."""
    if explicit:
        return explicit
    env = os.environ.get("PKOS_VAULT_ROOT")
    if env:
        return env
    cfg = Path(os.path.expanduser("~/.claude/personal-os.yaml"))
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r"^pkos_root:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip("\"'")
        m = re.search(r"^exchange_dir:\s*(.+)$", text, re.MULTILINE)
        if m:
            ex = Path(os.path.expanduser(m.group(1).strip().strip("\"'")))
            return str(ex.parent) if ex.name == ".exchange" else str(ex)
    return "~/Obsidian/PKOS"


def _expand_topic_tags(approved_topics):
    """Flatten approved topic_tags plus their hyphen-split tokens for looser tag overlap.
    e.g. 'ai-agents' → {'ai-agents', 'ai', 'agents'}."""
    out = set()
    for t in approved_topics:
        tag = t.get("topic_tag", "")
        if not tag:
            continue
        out.add(tag)
        for part in tag.split("-"):
            if len(part) >= 2:
                out.add(part)
    return list(out)

# Task 5-impl: constants for tightened drop + consecutive-day penalty.
# DROP_THRESHOLD: raised from 0.3 to 0.4 — fewer weak-novelty topics make it through,
# but the in-app contract stays "score must be > this to keep" (run_check uses
# `if score < DROP_THRESHOLD: continue`).
# PENALTY_PER_CONSECUTIVE_DAY: subtracted from base score when the same tag has
# appeared in a row (streak >= 2). Clamped to [0, 1] inside novelty_score.
# NEAR_SYNONYM_JACCARD: Jaccard threshold over hyphen-split tokens for two tags
# to count as "the same topic, different wording". Calibrated so safety/security
# match (shared {ai, agent}) but ai-agent-safety vs quantum-computing do not.
DROP_THRESHOLD = 0.4
PENALTY_PER_CONSECUTIVE_DAY = 0.10
NEAR_SYNONYM_JACCARD = 0.5

# Task 6-impl: finalize-dedup threshold — kept on the lower side (was 0.15
# implicit in the old hardcoded default) because the new combined similarity
# (max of 4-gram char Jaccard + topic-level unigram Jaccard) already
# lifts paraphrased-same-topic scores into the 0.4+ band. This is the
# dedup gate that flags a near-duplicate of yesterday's script.
FINALIZE_DEDUP_THRESHOLD = 0.4

def _tag_token_set(tag: str) -> set:
    """Hyphen-split tokens of length >= 2 (matches _expand_topic_tags style)."""
    return {p for p in tag.split("-") if len(p) >= 2}

def _tags_are_near_synonym(a: str, b: str) -> bool:
    """True if hyphen-split tokens share enough overlap to count as same topic."""
    if a == b:
        return True
    sa, sb = _tag_token_set(a), _tag_token_set(b)
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    union = len(sa | sb)
    return (inter / union) >= NEAR_SYNONYM_JACCARD

def novelty_score(candidate_tag: str, topic_log_path: str, today: str, window_days: int = 7) -> float:
    """Novelty score in [0, 1]: 1 - matching_days / window_days.

    Matching is NEAR-SYNONYM (hyphen-split token Jaccard >= NEAR_SYNONYM_JACCARD),
    not exact string equality. The consecutive-day penalty is applied by
    callers (e.g. run_check) — this function returns the raw base score so
    pre-existing callers/tests that read the score see the unpenalized value.
    Per design D-002 Option A (chosen by user), now upgraded with semantic hit.
    """
    recent_tags = recent_topic_tags(topic_log_path, today=today, window_days=window_days)
    matching = sum(1 for t in recent_tags if _tags_are_near_synonym(t, candidate_tag))
    return 1 - (matching / window_days)


def _consecutive_day_penalty(candidate_tag: str, topic_log_path: str, today: str, window_days: int = 7) -> float:
    """Return the penalty to subtract from `novelty_score` when the same tag has
    appeared on consecutive CALENDAR days within the window. The streak is
    the longest run of distinct consecutive days; it does NOT need to end at
    today — the plan's intent ("连续天出现的话题被压低") is to penalize ANY
    consecutive run within the window, not just the most recent tail. Multiple
    episodes on the same day collapse to a single day. Penalty =
    PENALTY_PER_CONSECUTIVE_DAY * (streak - 1); streak=1 → 0 penalty.
    """
    from topic_log import load_topic_log
    from datetime import timedelta
    data = load_topic_log(topic_log_path)
    today_d = date.fromisoformat(today)
    cutoff = today_d - timedelta(days=window_days)
    days_with_hit: set[date] = set()
    for ep in data["episodes"]:
        try:
            ep_d = date.fromisoformat(ep.get("date", ""))
        except ValueError:
            continue
        if not (cutoff <= ep_d <= today_d):
            continue
        if any(_tags_are_near_synonym(t.get("tag", ""), candidate_tag) for t in ep.get("topics", [])):
            days_with_hit.add(ep_d)
    if len(days_with_hit) < 2:
        return 0.0
    # Sort ascending; walk to find the longest run of consecutive days
    sorted_days = sorted(days_with_hit)
    longest = 1
    current = 1
    for i in range(1, len(sorted_days)):
        if sorted_days[i] == sorted_days[i - 1] + timedelta(days=1):
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return PENALTY_PER_CONSECUTIVE_DAY * max(0, longest - 1)


def _adjusted_novelty(candidate_tag: str, topic_log_path: str, today: str, window_days: int = 7) -> float:
    """novelty_score minus consecutive-day penalty, clamped to [0, 1].
    Used by run_check for the drop decision; novelty_score itself stays raw."""
    return max(0.0, min(1.0, novelty_score(candidate_tag, topic_log_path, today, window_days)
                        - _consecutive_day_penalty(candidate_tag, topic_log_path, today, window_days)))

def used_angles_for_topic(topic_tag: str, topic_log_path: str, today: str, window_days: int = 14) -> list:
    """Return the list of angles previously used for this topic_tag within window_days."""
    data = load_topic_log(topic_log_path)
    angles = []
    cutoff_d = date.fromisoformat(today)
    from datetime import timedelta
    cutoff = cutoff_d - timedelta(days=window_days)
    for ep in data["episodes"]:
        try:
            ep_d = date.fromisoformat(ep.get("date", ""))
        except ValueError:
            continue
        if not (cutoff <= ep_d <= cutoff_d):
            continue
        for t in ep.get("topics", []):
            if t.get("tag") == topic_tag and "angle" in t:
                angles.append(t["angle"])
    return angles

def run_check(candidates: list, topic_log_path: str, today: str,
              pkos_note: dict = None, seed: int = None,
              vault_root: str = None, force_domain: str = None,
              force_contrarian: str = None,
              show_type: str = None,
              required_domains: list = None) -> dict:
    """Build the structured brief consumed by the writer agent.

    DP-001 A: pkos_note is supplied by the caller (达芬奇 via pkos:serendipity SKILL).
    The orchestrator validates it's present but does NOT pull PKOS itself. If missing,
    returns an error brief that 达芬奇 must remediate (re-invoke pkos:serendipity + retry).

    The brief also carries cross_domain_candidates + self_past_candidates (pulled from the
    PKOS vault) and a named_concept_prompt directive. vault_root is resolved from
    personal-os.yaml when not given explicitly.

    `force_domain` / `force_contrarian`: parallel-N brief perturbation — pin this brief
    to a specific cross-domain bucket and reverse source so each of the N paths diverges
    at the brief layer. The applied perturbation is echoed in the `brief_perturbation`
    field, which the review-editor copies verbatim into editor_scores.brief_diff.

    Phase-2 plan Task 6/7: `show_type` selects between morning/evening brief shapes.
    - None (default): legacy event-centric brief — candidates are bare topic_tag strings;
      no domain quota; no spine inversion. Preserves backward compatibility for the 79
      existing tests.
    - "morning": candidates are dicts carrying `domain` + `topic_tag` keys. They get
      routed through select_with_domain_quota(candidates, required_domains, ...) so the
      brief enforces cross-domain coverage in CODE (not LLM trust). The selection result
      is exposed as `domain_selection` for the writer step.
    - "evening": brief spine inverts — vault open questions (same_topic_past_notes +
      cross_domain_candidates) become the PRIMARY `open_questions` field; news
      candidates demote to secondary `evidence`. Implemented in Task 7.

    `required_domains`: list of domain strings the morning quota selector must cover
    (caller-passed, NEVER hardcoded — fork-safe per D-019).
    """
    if not pkos_note or not isinstance(pkos_note, dict) or not pkos_note.get("id"):
        return {
            "error": "pkos_note required — invoke pkos:serendipity SKILL and pass the resulting {id, title, excerpt} as --pkos-note input. See podcast-prep SKILL.md for caller protocol.",
            "approved_topics": [],
            "pkos_note": None,
            "contrarian_source": None,
            "cross_domain_candidates": [],
            "self_past_candidates": [],
            "named_concept_prompt": NAMED_CONCEPT_PROMPT,
            "brief_perturbation": None,
            "generated_at": f"{today}T00:00:00Z",
        }

    # When candidates are dicts (Phase-2 morning/evening producer/consumer contract),
    # extract topic_tag for the novelty/angle computation. Legacy bare-string contract
    # passes through unchanged. Morning branch additionally runs the dict list through
    # the cross-domain quota selector first; evening branch keeps all dicts intact
    # (the spine inversion happens later in the brief).
    domain_selection = None
    novelty_inputs: list = candidates
    if show_type in ("morning", "evening"):
        if show_type == "morning":
            domain_selection = select_with_domain_quota(
                candidates, required_domains=required_domains or [],
            )
            dict_pool = domain_selection["selected"]
        else:  # evening — no quota filter; full dict list is news-as-evidence
            dict_pool = [c for c in candidates if isinstance(c, dict)]
        novelty_inputs = [
            c.get("topic_tag") or c.get("id", "")
            for c in dict_pool if isinstance(c, dict)
        ]

    approved = []
    for cand in novelty_inputs:
        if not cand:
            continue
        # Adjusted novelty: base score minus consecutive-day penalty (Task 5-impl)
        score = _adjusted_novelty(cand, topic_log_path, today)
        if score < DROP_THRESHOLD:
            continue  # drop
        used = used_angles_for_topic(cand, topic_log_path, today)
        if score > 0.7:
            required_angle = DEFAULT_ANGLES[0]  # free; default to first
        else:
            required_angle = pick_unused_angle(used)
        approved.append({
            "topic_tag": cand,
            "novelty_score": round(score, 3),
            "required_angle": required_angle,
        })
    # Single vault walk shared by both cross-domain and self-past recall.
    resolved_root = _resolve_vault_root(vault_root)
    today_tags = _expand_topic_tags(approved)
    notes = load_pkos_notes(resolved_root) if today_tags else []
    contrarian = _contrarian_pull(seed=seed, force_source=force_contrarian)
    brief = {
        "approved_topics": approved,
        "pkos_note": pkos_note,  # caller-provided, propagated verbatim
        "contrarian_source": contrarian,
        "cross_domain_candidates": cross_domain_candidates(
            today_tags, resolved_root, n=5, notes=notes, seed=seed,
            force_domain=force_domain),
        "self_past_candidates": same_topic_past_notes(
            today_tags, resolved_root, today=today, n=5, notes=notes),
        "named_concept_prompt": NAMED_CONCEPT_PROMPT,
        # parallel-N perturbation record — copied into editor_scores.brief_diff.
        # cross_domain_bucket is None on unperturbed (normal daily) runs.
        "brief_perturbation": {
            "cross_domain_bucket": force_domain,
            "contrarian_source": contrarian["source"] if contrarian else None,
        },
        "generated_at": f"{today}T00:00:00Z",
    }
    # Morning branch: expose the quota-selection result so the writer step can
    # see what was filtered + which required domains are missing.
    if domain_selection is not None:
        brief["domain_selection"] = domain_selection

    # Evening branch (Task 7): invert the brief spine — vault open questions
    # become the PRIMARY `open_questions` field, news candidates demote to the
    # secondary `evidence` field. The event-centric `approved_topics` schema
    # is the MORNING shape; evening deliberately drops it as the primary.
    if show_type == "evening":
        # Build a flat tag set from the raw candidates (morning quota or
        # bare strings — either works for vault recall).
        candidate_tag_list: list = []
        for c in candidates:
            if isinstance(c, dict):
                tt = c.get("topic_tag") or c.get("id") or ""
            else:
                tt = c
            if tt:
                candidate_tag_list.append(tt)
        open_q_notes = rank_open_questions(
            notes or [], today_tags=candidate_tag_list, n=5,
        )
        # News candidates: when --candidates was a dict list (per
        # producer/consumer contract), carry each as evidence; else fall back
        # to approved topic_tags (legacy string contract).
        evidence: list = []
        for c in candidates:
            if isinstance(c, dict):
                evidence.append({
                    "id": c.get("id", ""),
                    "domain": c.get("domain", ""),
                    "topic_tag": c.get("topic_tag") or c.get("id", ""),
                    "title": c.get("title", ""),
                })
            else:
                evidence.append({"topic_tag": c})
        # ADDITIONAL field (does NOT replace open_questions/evidence/pkos_note):
        # notes that newly entered the vault today — surfaced for the evening brief.
        # D-025 single-funnel: pure selection over the already-loaded `notes` var.
        fresh_today = fresh_today_notes(notes or [], today=today, n=5)
        return {
            "open_questions": open_q_notes,
            "evidence": evidence,
            "fresh_today": fresh_today,
            "pkos_note": pkos_note,
            "contrarian_source": contrarian,
            "named_concept_prompt": NAMED_CONCEPT_PROMPT,
            "brief_perturbation": brief["brief_perturbation"],
            "generated_at": f"{today}T00:00:00Z",
        }

    return brief

def _archive_episode(script_text: str, today: str, approved_topics: list,
                     archive_dir: str, named_concept: str = None) -> str:
    """Archive a finished episode to the vault as a `type: podcast` note (KL-3).

    One note per episode at {archive_dir}/{today}-{slug}.md. Recall (self_past) reads
    this directory — see the vault directory contract. Returns the written path.
    """
    archive_dir = os.path.expanduser(archive_dir)
    os.makedirs(archive_dir, exist_ok=True)
    # Slug from the script's first H1, else the first topic_tag.
    slug = ""
    for line in script_text.splitlines():
        if line.startswith("# "):
            slug = line[2:].strip()
            break
    if not slug and approved_topics:
        slug = approved_topics[0].get("topic_tag", "")
    slug = re.sub(r"\s+", "-", slug)[:48]
    slug = "".join(c for c in slug if c.isalnum() or c in "-_") or "episode"
    tags = [t.get("topic_tag", "") for t in approved_topics if t.get("topic_tag")]
    fm = [
        "---", "type: podcast", f"created: {today}",
        "tags: [" + ", ".join(tags) + "]",
    ]
    if named_concept:
        fm.append(f"named_concept: {named_concept}")
    fm += ["status: archived", "---", ""]
    path = os.path.join(archive_dir, f"{today}-{slug}.md")
    Path(path).write_text("\n".join(fm) + script_text, encoding="utf-8")
    return path


def run_finalize(script_path: str, topic_log_path: str, today: str,
                 approved_topics: list, script_archive_dir: str = None,
                 threshold: float = None, archive_dir: str = None,
                 named_concept: str = None) -> dict:
    """MinHash-dedupe the script against the past-7-day script archive.

    Task 6-impl: similarity is now the max of (4-gram char Jaccard, topic-level
    unigram Jaccard). Either signal alone can flag duplication:
    - 4-gram catches verbatim or near-verbatim reuse
    - topic-level catches paraphrased same-topic scripts

    The default threshold is FINALIZE_DEDUP_THRESHOLD (0.4). The old
    hardcoded 0.15 is preserved as a fallback when callers pass `threshold`
    explicitly (back-compat for callers that pin the old gate).

    On accept: append the episode to topic_log, and (if archive_dir is given) archive
    the episode to the vault 90-Productions/Podcasts directory. On retry: return without state change.
    """
    if threshold is None:
        threshold = FINALIZE_DEDUP_THRESHOLD
    script = Path(script_path).read_text(encoding="utf-8")
    corpus = []
    if script_archive_dir and Path(script_archive_dir).exists():
        # Collect scripts from past 7 days; filenames assumed YYYY-MM-DD.md
        today_d = date.fromisoformat(today)
        from datetime import timedelta
        for d_offset in range(1, 8):
            d_str = (today_d - timedelta(days=d_offset)).isoformat()
            p = Path(script_archive_dir) / f"{d_str}.md"
            if p.exists():
                corpus.append(p.read_text(encoding="utf-8"))
    # Combined max of char-4-gram + topic-level (Task 6-impl). Catches
    # paraphrased same-topic scripts that pure 4-gram misses.
    max_sim = max_similarity_against(script, corpus)
    if max_sim >= threshold:
        return {"action": "retry", "jaccard": round(max_sim, 4),
                "reason": f"4-gram/topic similarity {max_sim:.4f} >= threshold {threshold}"}
    # Accept: write topic_log
    append_episode(topic_log_path, today, [
        {"tag": t["topic_tag"], "angle": t["required_angle"]}
        for t in approved_topics
    ])
    result = {"action": "accept", "jaccard": round(max_sim, 4),
              "topics_appended": len(approved_topics)}
    if archive_dir:
        result["archived"] = _archive_episode(
            script, today, approved_topics, archive_dir, named_concept)
    return result

def main():
    parser = argparse.ArgumentParser(description="Podcast-prep orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--candidates", required=True, help="JSON array of candidate topic tags")
    chk.add_argument("--date", required=True)
    chk.add_argument("--topic-log", required=True)
    chk.add_argument("--pkos-note", default=None,
                     help="REQUIRED. JSON object {id, title, excerpt} from a prior pkos:serendipity invocation. DP-001 A: caller (达芬奇) supplies this.")
    chk.add_argument("--seed", type=int, default=None)
    chk.add_argument("--vault-root", default=None,
                     help="PKOS vault root. Defaults to personal-os.yaml (pkos_root or exchange_dir parent).")
    chk.add_argument("--force-domain", default=None,
                     help="parallel-N perturbation: pin cross-domain recall to ONE bucket "
                          "(philosophy/management/cognition/history/literature/natural-science).")
    chk.add_argument("--force-contrarian", default=None,
                     help="parallel-N perturbation: pin the reverse source by name "
                          "(stratechery/matt-levine/marginal-revolution/quanta-magazine/lesswrong/pkos-vault).")
    chk.add_argument("--show-type", default=None, choices=[None, "morning", "evening"],
                     help="Phase-2 brief shape. Default (None) preserves the legacy event-centric brief "
                          "and treats --candidates as bare topic_tag strings. 'morning' routes a dict "
                          "candidate list (each with `domain` + `topic_tag` keys) through the "
                          "cross-domain quota selector and exposes `domain_selection` in the brief. "
                          "'evening' inverts the spine — vault open questions become the PRIMARY "
                          "`open_questions` field; news demotes to secondary `evidence`.")
    chk.add_argument("--required-domains", default=None,
                     help="Comma-separated list of required domains for morning quota selection "
                          "(e.g. tech,market,science,geo,culture). Caller-passed (NEVER hardcoded) — "
                          "fork-safe per D-019.")
    fin = sub.add_parser("finalize")
    fin.add_argument("--script", required=True)
    fin.add_argument("--topic-log", required=True)
    fin.add_argument("--date", required=True)
    fin.add_argument("--approved-topics", required=True, help="JSON array of {topic_tag, required_angle}")
    fin.add_argument("--script-archive-dir", default=None)
    fin.add_argument("--archive-dir", default=None,
                     help="Vault 90-Productions/Podcasts dir. When set, an accepted episode is archived there as a type:podcast note.")
    fin.add_argument("--named-concept", default=None,
                     help="Optional — the concept this episode named, written to the archive note frontmatter.")
    args = parser.parse_args()
    if args.cmd == "check":
        candidates = json.loads(args.candidates)
        pkos_note = json.loads(args.pkos_note) if args.pkos_note else None
        required_domains = (
            [d.strip() for d in args.required_domains.split(",") if d.strip()]
            if args.required_domains else None
        )
        brief = run_check(candidates, args.topic_log, args.date,
                          pkos_note=pkos_note, seed=args.seed,
                          vault_root=args.vault_root,
                          force_domain=args.force_domain,
                          force_contrarian=args.force_contrarian,
                          show_type=args.show_type,
                          required_domains=required_domains)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif args.cmd == "finalize":
        approved = json.loads(args.approved_topics)
        result = run_finalize(args.script, args.topic_log, args.date, approved,
                              script_archive_dir=args.script_archive_dir,
                              archive_dir=args.archive_dir,
                              named_concept=args.named_concept)
        print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
