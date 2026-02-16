#!/usr/bin/env python3
"""Hybrid search combining semantic and keyword matching with RRF merge."""

import logging
from collections import defaultdict

from services.chroma import get_collection

logger = logging.getLogger(__name__)

STOPWORDS = {"the", "a", "an", "is", "in", "of", "and", "or", "to", "for", "it", "on", "at", "by", "be"}
RRF_K = 60  # Standard reciprocal rank fusion constant
KEYWORD_LIMIT = 200  # Max chunks to scan for keyword matching


def semantic_search(query: str, n_results: int = 5) -> list[dict[str, str]]:
    """Search the vault using semantic similarity via ChromaDB embeddings.

    Args:
        query: Natural language search query.
        n_results: Maximum number of results to return.

    Returns:
        List of dicts with 'source' and 'content' keys.
    """
    collection = get_collection()
    results = collection.query(query_texts=[query], n_results=n_results)

    return [
        {"source": metadata["source"], "content": doc, "heading": metadata.get("heading", "")}
        for doc, metadata in zip(results["documents"][0], results["metadatas"][0])
    ]


def _extract_query_terms(query: str) -> list[str]:
    """Split query into meaningful terms, filtering stopwords and short words."""
    terms = []
    for word in query.split():
        cleaned = word.strip(".,!?;:\"'()[]{}").lower()
        if len(cleaned) >= 3 and cleaned not in STOPWORDS:
            terms.append(cleaned)
    return terms


def keyword_search(query: str, n_results: int = 5) -> list[dict[str, str]]:
    """Search the vault for chunks containing query keywords.

    Combines all query terms into a single ChromaDB $or query, then ranks
    results by number of matching terms.

    Args:
        query: Search query string.
        n_results: Maximum number of results to return.

    Returns:
        List of dicts with 'source', 'content', and 'heading' keys,
        sorted by hit count.
    """
    terms = _extract_query_terms(query)
    if not terms:
        return []

    collection = get_collection()

    # Build filter: single $contains for one term, $or for multiple
    if len(terms) == 1:
        where_document = {"$contains": terms[0]}
    else:
        where_document = {"$or": [{"$contains": t} for t in terms]}

    try:
        matches = collection.get(
            where_document=where_document,
            include=["documents", "metadatas"],
            limit=KEYWORD_LIMIT,
        )
    except Exception as e:
        logger.warning(f"Keyword search failed: {e}")
        return []

    if not matches["ids"]:
        return []

    # Count matching terms per chunk and build results
    scored = []
    for doc, metadata in zip(matches["documents"], matches["metadatas"]):
        doc_lower = doc.lower()
        hits = sum(1 for t in terms if t in doc_lower)
        scored.append({
            "source": metadata["source"],
            "content": doc[:500],
            "heading": metadata.get("heading", ""),
            "hits": hits,
        })

    scored.sort(key=lambda x: x["hits"], reverse=True)
    return [
        {"source": r["source"], "content": r["content"], "heading": r["heading"]}
        for r in scored[:n_results]
    ]


def _dedup_key(result: dict[str, str]) -> tuple[str, str]:
    """Create a deduplication key from a result dict."""
    return (result["source"], result["content"][:100])


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


def hybrid_search(query: str, n_results: int = 5) -> list[dict[str, str]]:
    """Run semantic and keyword search, merging results with RRF.

    Fetches extra candidates from each source (2x n_results) to ensure
    good coverage after deduplication and re-ranking.

    Args:
        query: Search query string.
        n_results: Maximum number of final results to return.

    Returns:
        Merged results from both search strategies.
    """
    candidate_count = n_results * 2
    sem_results = semantic_search(query, n_results=candidate_count)
    kw_results = keyword_search(query, n_results=candidate_count)
    return merge_results(sem_results, kw_results, n_results=n_results)
