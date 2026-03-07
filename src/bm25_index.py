"""BM25 index built lazily from ChromaDB collection."""

import logging
import os
import threading

from rank_bm25 import BM25Okapi

from config import CHROMA_PATH
from services.chroma import get_collection

logger = logging.getLogger(__name__)

STOPWORDS = {
    "the", "a", "an", "is", "in", "of", "and", "or", "to", "for", "it",
    "on", "at", "by", "be", "this", "that", "with", "from", "have", "has",
    "was", "were", "been", "not", "but", "are", "can", "will", "just",
    "about", "into", "over", "also",
}

_lock = threading.RLock()
_bm25 = None
_doc_metadata = None
_built_at_mtime: float | None = None


def _tokenize(text: str) -> list[str]:
    """Split text into tokens: lowercase, strip punctuation, filter stopwords and short words."""
    tokens = []
    for word in text.split():
        cleaned = word.strip(".,!?;:\"'()[]{}").lower()
        if len(cleaned) >= 3 and cleaned not in STOPWORDS:
            tokens.append(cleaned)
    return tokens


def _empty_index() -> tuple:
    """Return an empty BM25 index."""
    return BM25Okapi([[""]]), []


def _build_index() -> tuple[bool, tuple]:
    """Load all documents from ChromaDB and build a BM25 index.

    Returns:
        (success, (BM25Okapi, doc_metadata)) — success is False on
        ChromaDB failure so the caller can avoid caching the result.
    """
    try:
        collection = get_collection()
        data = collection.get(include=["documents", "metadatas"])
    except Exception as e:
        logger.warning("Failed to load documents for BM25 index: %s", e)
        return False, _empty_index()

    documents = data["documents"]
    metadatas = data["metadatas"]

    if not documents:
        logger.info("Empty collection, creating placeholder BM25 index")
        return True, _empty_index()

    tokenized = [_tokenize(doc) for doc in documents]

    # BM25Okapi requires non-empty token lists; use [""] for empty docs
    corpus = [tokens if tokens else [""] for tokens in tokenized]
    bm25 = BM25Okapi(corpus)

    doc_metadata = []
    for doc, meta in zip(documents, metadatas):
        doc_metadata.append({
            "source": meta.get("source", ""),
            "content": doc,
            "heading": meta.get("heading", ""),
            "chunk_type": meta.get("chunk_type", ""),
        })

    logger.info("Built BM25 index with %d documents", len(doc_metadata))
    return True, (bm25, doc_metadata)


_BM25_STAMP = ".bm25_stamp"


def get_stamp_path() -> str:
    """Return path to the BM25 invalidation stamp file."""
    return os.path.join(CHROMA_PATH, _BM25_STAMP)


def touch_stamp() -> None:
    """Write the BM25 stamp file to signal cross-process cache invalidation.

    Called by index_vault after any successful ChromaDB mutation.
    """
    os.makedirs(CHROMA_PATH, exist_ok=True)
    with open(get_stamp_path(), "w") as f:
        f.write("")


def _get_marker_mtime() -> float | None:
    """Get mtime of the BM25 stamp for cross-process freshness."""
    try:
        return os.path.getmtime(get_stamp_path())
    except OSError:
        return None


def _get_index() -> tuple:
    """Get or build the BM25 index (lazy singleton, thread-safe).

    Checks the BM25 stamp to detect cross-process reindexing and
    rebuilds when stale. Failed builds are not cached so the next
    call retries (transient ChromaDB errors don't stick).
    """
    global _bm25, _doc_metadata, _built_at_mtime
    marker_mtime = _get_marker_mtime()
    if _bm25 is None or marker_mtime != _built_at_mtime:
        with _lock:
            marker_mtime = _get_marker_mtime()
            if _bm25 is None or marker_mtime != _built_at_mtime:
                ok, (bm25, docs) = _build_index()
                if ok:
                    _bm25, _doc_metadata = bm25, docs
                    _built_at_mtime = marker_mtime
                else:
                    # Don't cache — next call will retry
                    return bm25, docs
    return _bm25, _doc_metadata


def query_index(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Query the BM25 index.

    Args:
        query: Search query string.
        n_results: Maximum number of results to return.
        chunk_type: Optional filter by chunk type (applied post-scoring).

    Returns:
        List of dicts with 'source', 'content', and 'heading' keys,
        sorted by BM25 score descending.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    bm25, doc_metadata = _get_index()
    if not doc_metadata:
        return []

    scores = bm25.get_scores(tokens)

    # Filter out non-matching docs (score == 0 means no query terms matched).
    # Keep negative scores — BM25 IDF goes negative when a term appears in
    # more than half the corpus, but the document still matched.
    scored = sorted(
        ((idx, s) for idx, s in enumerate(scores) if s != 0),
        key=lambda x: x[1],
        reverse=True,
    )

    results = []
    for idx, _score in scored:
        meta = doc_metadata[idx]
        if chunk_type and meta["chunk_type"] != chunk_type:
            continue
        results.append({
            "source": meta["source"],
            "content": meta["content"],
            "heading": meta["heading"],
        })
        if len(results) >= n_results:
            break

    return results


def invalidate() -> None:
    """Invalidate the cached BM25 index, forcing a rebuild on next query."""
    global _bm25, _doc_metadata, _built_at_mtime
    with _lock:
        _bm25 = None
        _doc_metadata = None
        _built_at_mtime = None
        logger.debug("BM25 index invalidated")
