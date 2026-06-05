"""Domain-quota selector for morning brief candidates (Task 6 / D-011 / D-019).

Pure function operating on a caller-passed candidate list. Each candidate is a
dict carrying at least a literal `domain` key (string ∈ caller's domain taxonomy
— typically tech/market/science/geo/culture for the production morning brief,
but any taxonomy works because `required_domains` is a parameter, NEVER hardcoded
— fork-safe per personal-os contract D-019).

The selector enforces:
- Per-domain cap (single domain cannot dominate the brief — the bug that the
  early-morning "all AI" episodes had).
- Coverage floor (at least 1 from each required_domain if available).
- Diagnostic surfacing: missing required_domains + by-domain counts + malformed
  candidate count, so the caller (orchestrator → brief → writer step) knows
  whether the selection is degenerate.

NO DB, NO IO — operates purely on caller's data. Safe to fork.
"""
from __future__ import annotations

from typing import Any, Iterable

DEFAULT_PER_DOMAIN_CAP = 2


def select_with_domain_quota(
    candidates: Iterable[dict],
    required_domains: list[str],
    per_domain_cap: int = DEFAULT_PER_DOMAIN_CAP,
) -> dict[str, Any]:
    """Bucket candidates by `c['domain']`, cap per domain, surface missing.

    Args:
        candidates: iterable of dicts; each candidate MUST carry a `domain` key
            whose value is a string from the caller's domain taxonomy. Candidates
            without a `domain` key are dropped (and counted in diagnostic.malformed_count)
            so the producer side is forced to add the tag.
        required_domains: list of domain strings the caller wants covered. NOT
            hardcoded — caller passes their taxonomy (default for the production
            morning brief: tech/market/science/geo/culture).
        per_domain_cap: max number of candidates kept from any single domain
            (defaults to DEFAULT_PER_DOMAIN_CAP). Prevents single-domain dominance.

    Returns:
        {
          "selected": list of candidate dicts (input order within each bucket;
                      domains ordered by required_domains list, then any extras),
          "diagnostic": {
            "missing_domains": list of required_domains with zero candidates,
            "by_domain_counts": {domain: count_after_cap},
            "malformed_count": int (candidates dropped for missing `domain` key),
          },
        }
    """
    # Discipline: do not mutate the caller's list. Materialize once, walk twice.
    by_domain: dict[str, list[dict]] = {}
    malformed_count = 0
    for c in candidates:
        dom = c.get("domain") if isinstance(c, dict) else None
        if not isinstance(dom, str) or not dom:
            malformed_count += 1
            continue
        by_domain.setdefault(dom, []).append(c)

    # Cap each bucket.
    capped: dict[str, list[dict]] = {
        dom: items[:per_domain_cap] for dom, items in by_domain.items()
    }

    # Selection order: required_domains first (in the caller's order), then any
    # extra buckets the caller did NOT list. This keeps the brief domain order
    # stable for the writer step.
    selected: list[dict] = []
    for dom in required_domains:
        selected.extend(capped.get(dom, []))
    extras = [d for d in capped.keys() if d not in required_domains]
    for dom in extras:
        selected.extend(capped[dom])

    by_domain_counts = {dom: len(items) for dom, items in capped.items()}
    missing_domains = [d for d in required_domains if by_domain_counts.get(d, 0) == 0]

    return {
        "selected": selected,
        "diagnostic": {
            "missing_domains": missing_domains,
            "by_domain_counts": by_domain_counts,
            "malformed_count": malformed_count,
        },
    }
