#!/usr/bin/env python3
"""BM25 keyword relevance scoring.

Ports the semantic intent of lumina-backend/internal/pipeline/filter.go:224-263
calculateRelevance() (keyword-weighted log-damped scoring), upgraded to proper
BM25 which includes IDF weighting and document-length normalization.

Reference: rank_bm25 Python package (pip install rank-bm25).

BM25 Formula (per query term t):
  score(t,d) = IDF(t) * (f(t,d) * (k1 + 1)) / (f(t,d) + k1 * (1 - b + b * |d|/avgdl))

Where:
  - f(t,d) = term frequency of t in document d
  - |d| = document length (word count)
  - avgdl = average document length in corpus
  - k1 = term frequency saturation parameter (default 1.2)
  - b = document length normalization parameter (default 0.75)
  - IDF(t) = log((N - df(t) + 0.5) / (df(t) + 0.5))  [Robertson-Zipf]

Normalized to [0, 1] by self-scoring (query against itself).

Storage (DP-A4): In-memory per-scan. No persistence layer.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75

# Common English stop words (used for IDF-aware tokenization)
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
    "to", "was", "were", "will", "with",
})

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def _tokenize(text: str, remove_stops: bool = True) -> list[str]:
    """Lowercase, split on non-alphanumeric, optionally drop stop words."""
    words = []
    current = ""
    for ch in text.lower():
        if ch.isalnum():
            current += ch
        else:
            if current:
                if not remove_stops or current not in STOP_WORDS:
                    words.append(current)
                current = ""
    if current:
        if not remove_stops or current not in STOP_WORDS:
            words.append(current)
    return words


# ---------------------------------------------------------------------------
# BM25Relevance
# ---------------------------------------------------------------------------

class BM25Relevance:
    """BM25 relevance scorer built from a document corpus at scan time."""

    def __init__(
        self,
        corpus: list[list[str]],
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.N = len(corpus)
        self.avgdl = (
            sum(len(doc) for doc in corpus) / self.N if self.N > 0 else 1
        )

        # Build document frequency table: term -> count of docs containing term
        self.doc_freq: dict[str, int] = {}
        for doc in corpus:
            seen_in_doc: set[str] = set()
            for token in doc:
                if token not in seen_in_doc:
                    self.doc_freq[token] = self.doc_freq.get(token, 0) + 1
                    seen_in_doc.add(token)

        # Pre-compute IDF for every term in the corpus
        self.idf: dict[str, float] = {}
        for term, df in self.doc_freq.items():
            # Robertson-Zipf IDF
            self.idf[term] = math.log(
                (self.N - df + 0.5) / (df + 0.5) + 1e-8
            )

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """Raw BM25 score for one document against a query."""
        if doc_idx < 0 or doc_idx >= self.N:
            return 0.0
        doc = self.corpus[doc_idx]
        if not doc:
            return 0.0

        doc_len = len(doc)
        score = 0.0

        # Count term frequency in this document
        tf: dict[str, int] = {}
        for token in doc:
            tf[token] = tf.get(token, 0) + 1

        for token in query_tokens:
            if token not in self.idf:
                continue  # Unknown term — contributes 0
            idf = self.idf[token]
            f = tf.get(token, 0)
            # BM25 term scoring formula
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
            score += idf * numerator / (denominator + 1e-8)

        return score

    def relevance_for_insight(
        self,
        insight_text: str,
        keywords: list[str],
    ) -> float:
        """Score an insight against a keyword list, normalized to [0, 1].

        Returns 0.0 if the corpus is empty or no keywords are provided.
        Normalization: divides by the score of the insight against itself
        (all keywords present in itself), clamping to [0, 1].
        """
        if not self.N or not keywords:
            return 0.0

        tokens = _tokenize(insight_text)
        # Find which corpus index matches this insight (exact token list match)
        try:
            idx = self.corpus.index(tokens)
        except ValueError:
            # Not in corpus — score anyway using best-effort
            idx = 0

        raw = self.score(keywords, idx)

        # Self-normalization: score insight against itself
        self_score = self.score(tokens, idx)
        if self_score < 1e-8:
            return 0.0

        normalized = raw / self_score
        return max(0.0, min(1.0, normalized))


# ---------------------------------------------------------------------------
# Convenience constructor from corpus strings
# ---------------------------------------------------------------------------

def build_from_strings(corpus_texts: list[str]) -> BM25Relevance:
    """Build a BM25Relevance instance from a list of raw text strings."""
    tokenized = [_tokenize(t) for t in corpus_texts]
    return BM25Relevance(tokenized)


def score_insights(
    insights: list[dict],
    keywords: list[str],
) -> list[tuple[dict, float]]:
    """Score a list of insight dicts against keywords.

    Each insight dict must have a 'text' key (or 'title' + 'snippet' keys).

    Returns list of (insight, score) tuples sorted by score descending.
    """
    if not insights:
        return []

    # Build corpus from all insights
    texts = []
    for ins in insights:
        if "text" in ins:
            texts.append(ins["text"])
        else:
            parts = []
            for key in ("title", "snippet", "description", "content"):
                if key in ins:
                    parts.append(ins[key])
            texts.append(" ".join(parts))

    bm25 = build_from_strings(texts)
    kw_tokens = _tokenize(" ".join(keywords))

    results = []
    for ins in insights:
        score = bm25.relevance_for_insight(texts[insights.index(ins)], kw_tokens)
        results.append((ins, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="BM25 relevance scoring CLI")
    parser.add_argument("--corpus", required=True, help="JSON file: list of {id, text} objects")
    parser.add_argument("--keywords", required=True, help="Space-separated keyword string")
    parser.add_argument("--output", required=True, help="Output JSON file for id→score mapping")

    args = parser.parse_args()

    with open(args.corpus, encoding="utf-8") as fh:
        corpus_items = json.load(fh)

    if not corpus_items:
        # Empty corpus: write empty score map
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        exit(0)

    # Build corpus texts
    texts = [item.get("text", "") for item in corpus_items]
    keywords = args.keywords.strip().split() if args.keywords.strip() else []

    bm25 = build_from_strings(texts)
    kw_tokens = _tokenize(" ".join(keywords))

    scores: dict[str, float] = {}
    for i, item in enumerate(corpus_items):
        item_id = item.get("id", str(i))
        score = bm25.relevance_for_insight(texts[i], kw_tokens)
        scores[item_id] = score

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(scores, fh, indent=2)
