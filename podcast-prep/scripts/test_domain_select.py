"""Tests for select_with_domain_quota — pure-function cross-domain candidate selector.

Per plan Task 6-tests (D-011, D-019): early-morning brief must enforce code-side
cross-domain coverage instead of trusting an LLM's self-report. The selector takes
candidates pre-tagged with a literal `domain` key (values ∈ tech/market/science/
geo/culture — matching the producer/consumer contract) and the `required_domains`
list as a PARAMETER (NEVER hardcoded — fork-safe per personal-os contract).

Contract under test (selector does NOT yet exist — these tests FAIL until 6-impl):
- Pure function on caller-passed data; no IO, no DB.
- Enforces per-domain cap (single domain cannot dominate).
- Signals missing domains (so the caller knows the brief is degenerate / single-domain).
- Returns selected candidates + diagnostic so the orchestrator can decide what to do.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Will fail with ImportError pre-Task 6-impl — that IS the FAIL signal.
from domain_select import select_with_domain_quota


REQUIRED_DOMAINS = ["tech", "market", "science", "geo", "culture"]


class SelectWithDomainQuotaContractTests(unittest.TestCase):
    """select_with_domain_quota(candidates, required_domains, per_domain_cap=...) →
    {"selected": [...], "diagnostic": {"missing_domains": [...], "by_domain_counts": {...}}}

    Each candidate is a dict with at least a `domain` key (str literal). The
    selector buckets by `c['domain']`, enforces per-domain cap, and reports any
    missing required_domain so the caller can react (back off, prompt for more,
    or proceed degenerate)."""

    def test_all_ai_input_signals_overload_and_missing_domains(self):
        """Input degenerate — 8 candidates all in `tech` (the AI bucket).
        Selector must (a) cap tech to per_domain_cap and (b) report the other
        4 required domains as missing — this is the harness that prevents
        'today's brief is all AI' from passing silently."""
        candidates = [
            {"id": f"t{i}", "domain": "tech", "title": f"AI item {i}"}
            for i in range(8)
        ]
        result = select_with_domain_quota(
            candidates, required_domains=REQUIRED_DOMAINS, per_domain_cap=2,
        )
        # Cap enforced: at most per_domain_cap from any one domain.
        tech_count = sum(1 for s in result["selected"] if s["domain"] == "tech")
        self.assertLessEqual(tech_count, 2)
        # Diagnostic surfaces missing domains so caller knows brief is degenerate.
        missing = set(result["diagnostic"]["missing_domains"])
        self.assertEqual(missing, {"market", "science", "geo", "culture"})

    def test_five_domain_input_covers_all_with_per_domain_floor(self):
        """When candidates span all five required domains, the selector must
        keep at least one per domain (the coverage floor) and respect the cap."""
        candidates = []
        for dom in REQUIRED_DOMAINS:
            for i in range(3):
                candidates.append({"id": f"{dom}-{i}", "domain": dom,
                                   "title": f"{dom} item {i}"})
        result = select_with_domain_quota(
            candidates, required_domains=REQUIRED_DOMAINS, per_domain_cap=2,
        )
        # Each required domain present at least once.
        domains_present = {s["domain"] for s in result["selected"]}
        for dom in REQUIRED_DOMAINS:
            self.assertIn(dom, domains_present,
                          f"required domain {dom} missing from selection")
        # No domain exceeds the cap.
        counts = result["diagnostic"]["by_domain_counts"]
        for dom, cnt in counts.items():
            self.assertLessEqual(cnt, 2, f"{dom} over cap: {cnt}")
        # Five required domains present → no missing.
        self.assertEqual(result["diagnostic"]["missing_domains"], [])

    def test_required_domains_is_parameter_not_hardcoded(self):
        """Fork-safety: the selector MUST accept any required_domains list — a
        forked user with a different domain taxonomy can pass their own (e.g.
        only 3 domains) and the selector still works."""
        candidates = [
            {"id": "a", "domain": "alpha"},
            {"id": "b", "domain": "beta"},
            {"id": "c", "domain": "gamma"},
        ]
        custom_domains = ["alpha", "beta", "gamma"]
        result = select_with_domain_quota(
            candidates, required_domains=custom_domains, per_domain_cap=1,
        )
        # All three custom domains covered → no missing.
        self.assertEqual(result["diagnostic"]["missing_domains"], [])
        domains_present = {s["domain"] for s in result["selected"]}
        self.assertEqual(domains_present, {"alpha", "beta", "gamma"})

    def test_empty_candidates_returns_all_required_as_missing(self):
        result = select_with_domain_quota(
            [], required_domains=REQUIRED_DOMAINS, per_domain_cap=2,
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(set(result["diagnostic"]["missing_domains"]),
                         set(REQUIRED_DOMAINS))

    def test_candidate_missing_domain_key_is_dropped_or_recorded(self):
        """A candidate without a `domain` key cannot be bucketed. The selector
        must not crash — it should drop the malformed candidate AND record it
        in the diagnostic so the caller knows the producer side is buggy."""
        candidates = [
            {"id": "good", "domain": "tech"},
            {"id": "bad-no-domain"},  # missing 'domain' key
        ]
        result = select_with_domain_quota(
            candidates, required_domains=["tech"], per_domain_cap=2,
        )
        # The good one made it.
        selected_ids = [s["id"] for s in result["selected"]]
        self.assertIn("good", selected_ids)
        self.assertNotIn("bad-no-domain", selected_ids)
        # Diagnostic reports the malformed candidate so producer is forced to fix.
        self.assertGreaterEqual(result["diagnostic"].get("malformed_count", 0), 1)

    def test_per_domain_cap_default_is_reasonable(self):
        """Cap should default to a small int (>=1) so calling without the
        kwarg does not blow up. Exact default is impl detail but must be >=1."""
        candidates = [
            {"id": f"t{i}", "domain": "tech"} for i in range(10)
        ]
        result = select_with_domain_quota(
            candidates, required_domains=["tech"],
        )
        tech_count = sum(1 for s in result["selected"] if s["domain"] == "tech")
        self.assertGreaterEqual(tech_count, 1)

    def test_pure_function_does_not_mutate_input(self):
        """No side effects on caller's list — pure function discipline."""
        candidates = [
            {"id": "a", "domain": "tech"},
            {"id": "b", "domain": "market"},
        ]
        snapshot = [dict(c) for c in candidates]
        select_with_domain_quota(
            candidates, required_domains=["tech", "market"], per_domain_cap=2,
        )
        self.assertEqual(candidates, snapshot, "input list mutated")


if __name__ == "__main__":
    unittest.main()
