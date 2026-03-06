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
_embedding_function = None

# Nomic models require task prefixes for optimal quality.
_NOMIC_MODEL = "nomic" in EMBEDDING_MODEL.lower()

_MODEL_MARKER = ".embedding_model"


def _cuda_available() -> bool:
    """Check if CUDA is available for GPU-accelerated embeddings."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _get_model_marker_path() -> str:
    """Return path to the embedding model marker file."""
    return os.path.join(CHROMA_PATH, _MODEL_MARKER)


def _check_model_marker() -> None:
    """Check that the stored embedding model matches the configured one.

    Raises RuntimeError if there's a mismatch, instructing the user to
    run --reset. Writes the marker if it doesn't exist (new DB).
    """
    marker_path = _get_model_marker_path()
    if os.path.exists(marker_path):
        with open(marker_path) as f:
            stored_model = f.read().strip()
        if stored_model != EMBEDDING_MODEL:
            raise RuntimeError(
                f"Embedding model mismatch: database was indexed with "
                f"'{stored_model}' but EMBEDDING_MODEL is '{EMBEDDING_MODEL}'. "
                f"Run index_vault.py --reset to rebuild the database."
            )
    else:
        # Check for a pre-existing database from a prior version that
        # never wrote this marker.  A chroma.sqlite3 file without a
        # marker means legacy data — refuse rather than silently
        # mislabeling a potentially incompatible index.
        chroma_db_file = os.path.join(CHROMA_PATH, "chroma.sqlite3")
        if os.path.exists(chroma_db_file):
            raise RuntimeError(
                f"Existing ChromaDB database found without an embedding model marker. "
                f"Cannot verify compatibility with '{EMBEDDING_MODEL}'. "
                f"Run index_vault.py --reset to rebuild the database."
            )
        os.makedirs(CHROMA_PATH, exist_ok=True)
        with open(marker_path, "w") as f:
            f.write(EMBEDDING_MODEL)


def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    """Get or create the embedding function (lazy singleton, thread-safe)."""
    global _embedding_function
    if _embedding_function is None:
        with _lock:
            if _embedding_function is None:
                _embedding_function = SentenceTransformerEmbeddingFunction(
                    model_name=EMBEDDING_MODEL, trust_remote_code=True,
                    device="cuda" if _cuda_available() else "cpu",
                )
    return _embedding_function


def embed_documents(texts: list[str]) -> list:
    """Compute embeddings for documents, applying model-specific prefixes.

    The prefix is only used for embedding computation — callers should
    store the original unprefixed text in ChromaDB.
    """
    ef = get_embedding_function()
    if _NOMIC_MODEL:
        texts = [f"search_document: {t}" for t in texts]
    return ef(texts)


def embed_query(text: str) -> list:
    """Compute embedding for a query, applying model-specific prefix."""
    ef = get_embedding_function()
    if _NOMIC_MODEL:
        text = f"search_query: {text}"
    return ef([text])[0]


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
                _check_model_marker()
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
    global _client, _collection, _embedding_function
    _client = None
    _collection = None
    _embedding_function = None
