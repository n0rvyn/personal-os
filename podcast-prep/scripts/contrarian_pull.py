"""Pick a reverse-source candidate from a curated pool. Seeded RNG for testability."""
import random

CONTRARIAN_POOL = [
    {"source": "stratechery", "category": "business-strategy", "url": "https://stratechery.com"},
    {"source": "matt-levine", "category": "finance-macro", "url": "https://www.bloomberg.com/opinion/authors/AS6kQL1jhuk/matthew-s-levine"},
    {"source": "marginal-revolution", "category": "economics-cognition", "url": "https://marginalrevolution.com"},
    {"source": "quanta-magazine", "category": "natural-science", "url": "https://www.quantamagazine.org"},
    {"source": "lesswrong", "category": "rationality", "url": "https://www.lesswrong.com"},
    {"source": "pkos-vault", "category": "personal-knowledge", "url": "local://obsidian/PKOS"},
]

def pick_contrarian_source(seed: int = None, exclude_categories: list = None) -> dict:
    """Pick a contrarian source. `seed` controls deterministic selection (for testing).
    `exclude_categories` filters the pool; if all categories are excluded, return the last
    pool entry (acts as fallback / general-knowledge category)."""
    pool = CONTRARIAN_POOL
    if exclude_categories:
        filtered = [c for c in pool if c["category"] not in exclude_categories]
        if not filtered:
            # All excluded → return last pool entry as fall-through (tests assert this contract)
            return pool[-1]
        pool = filtered
    rng = random.Random(seed) if seed is not None else random.Random()
    return rng.choice(pool)
