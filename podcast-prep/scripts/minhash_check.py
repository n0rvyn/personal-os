"""Simple Jaccard-on-shingles dedup. No external deps."""
def shingle_4gram(text: str) -> set:
    """Return the set of 4-character shingles from text. Used for cheap, deterministic
    script-similarity comparison; full MinHash is overkill for our 7-day corpus size."""
    if len(text) < 4:
        return set()
    return {text[i:i+4] for i in range(len(text) - 3)}

def jaccard_similarity(a: str, b: str) -> float:
    sa, sb = shingle_4gram(a), shingle_4gram(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union

def max_jaccard_against(text: str, corpus: list) -> float:
    """Return the max Jaccard similarity between `text` and any string in `corpus`.
    Returns 0.0 if corpus is empty."""
    if not corpus:
        return 0.0
    return max(jaccard_similarity(text, c) for c in corpus)
