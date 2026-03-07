#!/usr/bin/env python3
"""Hybrid search combining semantic and keyword matching with RRF merge."""

import logging
from collections import defaultdict

from bm25_index import query_index as bm25_query
from config import MAX_CHUNKS_PER_SOURCE, RRF_K
from services.chroma import embed_query, get_collection, rerank

logger = logging.getLogger(__name__)

STOPWORDS = {
    "the", "a", "an", "is", "in", "of", "and", "or", "to", "for", "it",
    "on", "at", "by", "be", "this", "that", "with", "from", "have", "has",
    "was", "were", "been", "not", "but", "are", "can", "will", "just",
    "about", "into", "over", "also",
}

_QUESTION_WORDS = {
    "who", "what", "where", "when", "why", "how", "which",
    "is", "are", "does", "do", "can", "could", "would", "should",
}


def _is_question(query: str) -> bool:
    """Detect whether a query is a question using simple heuristics."""
    if not query:
        return False
    if query.rstrip().endswith("?"):
        return True
    first_word = query.split()[0].lower().strip(".,!?;:\"'()[]{}")
    return first_word in _QUESTION_WORDS


def _semantic_retrieve(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Raw semantic retrieval without reranking or diversity."""
    collection = get_collection()
    query_embedding = embed_query(query)
    query_kwargs: dict = {"query_embeddings": [query_embedding], "n_results": n_results}
    if chunk_type:
        query_kwargs["where"] = {"chunk_type": chunk_type}
    results = collection.query(**query_kwargs)

    return [
        {"source": metadata["source"], "content": doc, "heading": metadata.get("heading", "")}
        for doc, metadata in zip(results["documents"][0], results["metadatas"][0])
    ]


def semantic_search(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Search the vault using semantic similarity via ChromaDB embeddings.

    Retrieves extra candidates, reranks with cross-encoder, and applies
    source diversity before returning final results.

    Args:
        query: Natural language search query.
        n_results: Maximum number of results to return.
        chunk_type: Filter by chunk type (e.g. "frontmatter", "section").

    Returns:
        List of dicts with 'source', 'content', and 'heading' keys.
    """
    candidates = _semantic_retrieve(query, n_results=n_results * 4, chunk_type=chunk_type)
    return _diversify(rerank(query, candidates))[:n_results]


def _extract_query_terms(query: str) -> list[str]:
    """Split query into meaningful terms, filtering stopwords and short words."""
    terms = []
    for word in query.split():
        cleaned = word.strip(".,!?;:\"'()[]{}").lower()
        if len(cleaned) >= 3 and cleaned not in STOPWORDS:
            terms.append(cleaned)
    return terms


def _keyword_retrieve(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Raw keyword retrieval via BM25 without reranking or diversity."""
    return bm25_query(query, n_results=n_results, chunk_type=chunk_type)


def keyword_search(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Search the vault for chunks containing query keywords.

    Retrieves extra candidates, reranks with cross-encoder, and applies
    source diversity before returning final results.

    Args:
        query: Search query string.
        n_results: Maximum number of results to return.
        chunk_type: Filter by chunk type (e.g. "frontmatter", "section").

    Returns:
        List of dicts with 'source', 'content', and 'heading' keys.
    """
    candidates = _keyword_retrieve(query, n_results=n_results * 4, chunk_type=chunk_type)
    return _diversify(rerank(query, candidates))[:n_results]


def _dedup_key(result: dict[str, str]) -> tuple[str, str]:
    """Create a deduplication key from a result dict."""
    return (result["source"], result["content"][:100])


def _diversify(results: list[dict], max_per_source: int | None = None) -> list[dict]:
    """Limit chunks per source file to enforce result diversity.

    Iterates results in rank order, skipping chunks from sources that
    have already reached the cap. This preserves ranking order while
    ensuring no single source dominates the result set.

    Args:
        results: Ranked search results (must have 'source' key).
        max_per_source: Maximum chunks per source. 0 or negative disables
            filtering. Defaults to MAX_CHUNKS_PER_SOURCE from config.

    Returns:
        Filtered results with at most max_per_source per source.
    """
    if max_per_source is None:
        max_per_source = MAX_CHUNKS_PER_SOURCE
    if max_per_source <= 0:
        return results

    counts: dict[str, int] = defaultdict(int)
    diverse = []
    for r in results:
        if counts[r["source"]] < max_per_source:
            diverse.append(r)
            counts[r["source"]] += 1
    return diverse


def merge_results(
    semantic: list[dict[str, str]],
    keyword: list[dict[str, str]],
    n_results: int = 5,
    semantic_weight: float = 0.5,
    keyword_weight: float = 0.5,
) -> list[dict[str, str]]:
    """Merge two ranked result lists using Reciprocal Rank Fusion.

    Each result receives a score of weight / (rank + k) from each list
    it appears in. Duplicate results have their scores summed.

    Args:
        semantic: Ranked results from semantic search.
        keyword: Ranked results from keyword search.
        n_results: Maximum number of merged results to return.
        semantic_weight: Weight for semantic search scores.
        keyword_weight: Weight for keyword search scores.

    Returns:
        Merged and deduplicated results sorted by combined RRF score.
    """
    scores: dict[tuple, float] = defaultdict(float)
    result_map: dict[tuple, dict[str, str]] = {}

    for rank, result in enumerate(semantic, start=1):
        key = _dedup_key(result)
        scores[key] += semantic_weight / (rank + RRF_K)
        result_map[key] = result

    for rank, result in enumerate(keyword, start=1):
        key = _dedup_key(result)
        scores[key] += keyword_weight / (rank + RRF_K)
        if key not in result_map:
            result_map[key] = result

    ranked_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return [result_map[key] for key in ranked_keys[:n_results]]


def hybrid_search(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Run semantic and keyword search, merging results with RRF.

    Fetches extra candidates from each source (4x n_results) to ensure
    good coverage after deduplication, reranking, and diversity filtering.

    Args:
        query: Search query string.
        n_results: Maximum number of final results to return.
        chunk_type: Filter by chunk type (e.g. "frontmatter", "section").

    Returns:
        Merged results from both search strategies, reranked and diversified.
    """
    candidate_count = n_results * 4
    sem_results = _semantic_retrieve(query, n_results=candidate_count, chunk_type=chunk_type)
    kw_results = _keyword_retrieve(query, n_results=candidate_count, chunk_type=chunk_type)
    merged = merge_results(sem_results, kw_results, n_results=candidate_count)
    return _diversify(rerank(query, merged))[:n_results]
