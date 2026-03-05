"""Shared ChromaDB connection management."""

import logging
import os
import shutil
import threading

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ChromaDB's Posthog telemetry has a thread-unsafe race condition:
# capture() manipulates a shared dict (batched_events) without locking,
# causing KeyError crashes under concurrent access from ThreadPoolExecutor.
# Setting anonymized_telemetry=False only suppresses the HTTP call, not the
# buggy capture() code path. Replace it with a no-op.
from chromadb.telemetry.product.posthog import Posthog as _Posthog
_Posthog.capture = lambda self, event: None  # type: ignore[assignment]

from config import CHROMA_PATH, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_client = None
_collection = None

# Nomic models require task prefixes for optimal quality.
_NOMIC_MODEL = "nomic" in EMBEDDING_MODEL.lower()


def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    """Create the embedding function for the configured model."""
    return SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL, trust_remote_code=True
    )


def prefix_document(text: str) -> str:
    """Add the document prefix required by the embedding model, if any."""
    if _NOMIC_MODEL:
        return f"search_document: {text}"
    return text


def prefix_query(text: str) -> str:
    """Add the query prefix required by the embedding model, if any."""
    if _NOMIC_MODEL:
        return f"search_query: {text}"
    return text


def get_client() -> chromadb.PersistentClient:
    """Get or create ChromaDB client (lazy singleton, thread-safe)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                os.makedirs(CHROMA_PATH, exist_ok=True)
                _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def get_collection() -> chromadb.Collection:
    """Get or create the vault collection (lazy singleton, thread-safe)."""
    global _collection
    if _collection is None:
        with _lock:
            if _collection is None:
                _collection = get_client().get_or_create_collection(
                    "obsidian_vault", embedding_function=get_embedding_function()
                )
    return _collection


def purge_database() -> None:
    """Delete and recreate the ChromaDB database from scratch.

    Removes the entire CHROMA_PATH directory and resets singletons so
    the next get_client/get_collection call creates a fresh database.
    Used by ``index_vault.py --reset`` to recover from corrupt or
    cross-platform-incompatible HNSW index files.
    """
    reset()
    if os.path.exists(CHROMA_PATH):
        shutil.rmtree(CHROMA_PATH)
        logger.info("Deleted ChromaDB database at %s", CHROMA_PATH)


def reset():
    """Reset singletons (for testing)."""
    global _client, _collection
    _client = None
    _collection = None
