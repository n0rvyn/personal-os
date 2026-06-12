"""Simple Jaccard-on-shingles dedup. No external deps."""

# Stopwords for topic-similarity token-set construction. Excluded from the
# token set so that common-function-word overlap does not inflate the
# topic-level score. Kept small + ASCII-friendly to avoid unicode/case gotchas.
_TOPIC_STOPWORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
    "but", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "this", "that", "these", "those", "it", "its", "as", "if",
    "than", "then", "so", "such", "not", "no", "do", "does", "did", "can",
    "could", "should", "would", "may", "might", "must", "will", "shall",
    "we", "you", "they", "he", "she", "i", "our", "your", "their", "my",
    "have", "has", "had", "more", "most", "some", "any", "all", "each",
    "every", "where", "when", "what", "which", "who", "whom", "how", "why",
])


def _topic_tokens(text: str) -> set:
    """Lowercase alpha-tokens with stopwords + length-1 tokens removed.

    The result is a set suitable for set-Jaccard: words that survive are
    content-bearing. Length>=2 cutoff (in addition to stopword list) drops
    single-letter noise like "a", "I" defensively in case the stopword list
    misses something for non-ASCII scripts.
    """
    if not text:
        return set()
    out = set()
    for tok in text.lower().split():
        # Strip leading/trailing punctuation
        cleaned = "".join(c for c in tok if c.isalnum() or c in "-_")
        if len(cleaned) < 2:
            continue
        if cleaned in _TOPIC_STOPWORDS:
            continue
        out.add(cleaned)
    return out


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

def _topic_word_features(text: str) -> set:
    """Return a set of topic-level word features for `text`: content-word
    unigrams (length>=3, not in stopword list). Bigrams are intentionally NOT
    included — they dilute the signal on paraphrased text (paraphrase uses
    different words so shared bigrams are rare; including bigrams expands
    the union faster than the intersection).

    The unigram-only signal is the topic-vocabulary overlap. It is weaker
    than 4-gram char-similarity on near-verbatim text but stronger on
    paraphrased same-topic text where surface forms differ.
    """
    if not text:
        return set()
    out = set()
    for tok in text.lower().split():
        cleaned = "".join(c for c in tok if c.isalnum() or c in "-_")
        if len(cleaned) < 3:
            continue
        if cleaned in _TOPIC_STOPWORDS:
            continue
        out.add(cleaned)
    return out


def topic_similarity(a: str, b: str) -> float:
    """Topic-level similarity: set-Jaccard over content-word unigrams
    (stopword-stripped, length>=3).

    Purpose: catch '同话题换词' (same topic, different wording) repetition
    that pure 4-gram char Jaccard misses. Two paragraphs on the same topic
    with different phrasing will share content words (alignment, safety,
    agent, system, ...) even when surface forms and word order differ.

    Returns 0.0 when either side has no features after stripping.
    Returns 1.0 for identical input.
    """
    sa, sb = _topic_word_features(a), _topic_word_features(b)
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


def max_similarity_against(text: str, corpus: list) -> float:
    """Return the max of (char-4-gram jaccard, topic-level jaccard) between
    `text` and any string in `corpus`. Either signal alone can flag duplication:
    - 4-gram catches verbatim or near-verbatim reuse
    - topic-level catches paraphrased same-topic scripts
    Returns 0.0 if corpus is empty.
    """
    if not corpus:
        return 0.0
    best = 0.0
    for c in corpus:
        # Pure char
        sa, sb = shingle_4gram(text), shingle_4gram(c)
        if sa or sb:
            if not sa or not sb:
                char_sim = 0.0
            else:
                char_sim = len(sa & sb) / len(sa | sb)
        else:
            char_sim = 1.0
        # Topic-level
        ta, tb = _topic_tokens(text), _topic_tokens(c)
        if ta or tb:
            if not ta or not tb:
                topic_sim = 0.0
            else:
                topic_sim = len(ta & tb) / len(ta | tb)
        else:
            topic_sim = 1.0
        if char_sim > best:
            best = char_sim
        if topic_sim > best:
            best = topic_sim
    return best

