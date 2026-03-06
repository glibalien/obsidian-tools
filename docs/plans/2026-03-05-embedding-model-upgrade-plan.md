# Embedding Model Upgrade Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace ChromaDB's default all-MiniLM-L6-v2 with nomic-embed-text-v1.5 for better semantic search quality.

**Architecture:** Add `EMBEDDING_MODEL` config var, pass `SentenceTransformerEmbeddingFunction` to `get_or_create_collection()`, and add prefix helpers for nomic's required task prefixes (`search_document:`/`search_query:`). Apply prefixes at the two call sites: `_prepare_file_chunks` (indexing) and `semantic_search` (querying).

**Tech Stack:** ChromaDB, sentence-transformers, nomic-embed-text-v1.5

---

### Task 1: Add EMBEDDING_MODEL config and dependencies

**Files:**
- Modify: `src/config.py` (add EMBEDDING_MODEL after UPSERT_BATCH_SIZE, ~line 56)
- Modify: `requirements.txt` (add sentence-transformers and einops)

**Step 1: Add the config constant**

In `src/config.py`, add after the `UPSERT_BATCH_SIZE = 500` line:

```python
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
```

**Step 2: Add dependencies to requirements.txt**

Add these lines to the end of `requirements.txt`:

```
sentence-transformers>=3.0.0
einops>=0.8.0
```

Note: `sentence-transformers` is currently pulled in transitively by ChromaDB but not pinned. `einops` is required by nomic's custom architecture.

**Step 3: Install the new dependencies**

Run: `pip install -r requirements.txt`

**Step 4: Commit**

```bash
git add src/config.py requirements.txt
git commit -m "feat: add EMBEDDING_MODEL config and explicit dependencies"
```

---

### Task 2: Add embedding function and prefix helpers to chroma.py

**Files:**
- Modify: `src/services/chroma.py`
- Modify: `tests/test_config.py` (add tests — chroma tests live here alongside other config/service tests)

**Step 1: Write failing tests**

Add the following tests to `tests/test_config.py`. They test:
1. `get_embedding_function()` returns a `SentenceTransformerEmbeddingFunction`
2. `prefix_document()` and `prefix_query()` add prefixes for nomic models
3. `prefix_document()` and `prefix_query()` pass through for non-nomic models
4. `get_collection()` passes the embedding function

```python
class TestEmbeddingFunction:
    """Tests for embedding function and prefix helpers in chroma.py."""

    def test_get_embedding_function_returns_sentence_transformer(self):
        """get_embedding_function returns a SentenceTransformerEmbeddingFunction."""
        from services.chroma import get_embedding_function
        ef = get_embedding_function()
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        assert isinstance(ef, SentenceTransformerEmbeddingFunction)

    def test_prefix_document_nomic(self):
        """prefix_document adds 'search_document: ' prefix for nomic models."""
        from services import chroma
        with patch.object(chroma, "_NOMIC_MODEL", True):
            result = chroma.prefix_document("hello world")
        assert result == "search_document: hello world"

    def test_prefix_query_nomic(self):
        """prefix_query adds 'search_query: ' prefix for nomic models."""
        from services import chroma
        with patch.object(chroma, "_NOMIC_MODEL", True):
            result = chroma.prefix_query("hello world")
        assert result == "search_query: hello world"

    def test_prefix_document_non_nomic(self):
        """prefix_document passes through for non-nomic models."""
        from services import chroma
        with patch.object(chroma, "_NOMIC_MODEL", False):
            result = chroma.prefix_document("hello world")
        assert result == "hello world"

    def test_prefix_query_non_nomic(self):
        """prefix_query passes through for non-nomic models."""
        from services import chroma
        with patch.object(chroma, "_NOMIC_MODEL", False):
            result = chroma.prefix_query("hello world")
        assert result == "hello world"

    def test_get_collection_uses_embedding_function(self):
        """get_collection passes the embedding function to get_or_create_collection."""
        from services import chroma
        chroma.reset()
        mock_client = MagicMock()
        with patch.object(chroma, "get_client", return_value=mock_client), \
             patch.object(chroma, "get_embedding_function") as mock_ef:
            mock_ef.return_value = "fake_ef"
            chroma.get_collection()
            mock_client.get_or_create_collection.assert_called_once_with(
                "obsidian_vault", embedding_function="fake_ef"
            )
        chroma.reset()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py::TestEmbeddingFunction -v`

Expected: FAIL — `get_embedding_function`, `prefix_document`, `prefix_query` don't exist yet.

**Step 3: Implement in chroma.py**

Replace the full content of `src/services/chroma.py` with:

```python
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
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_config.py::TestEmbeddingFunction -v`

Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add src/services/chroma.py tests/test_config.py
git commit -m "feat: add embedding function and prefix helpers to chroma.py (#156)"
```

---

### Task 3: Apply document prefix in index_vault.py

**Files:**
- Modify: `src/index_vault.py` (import `prefix_document`, apply in `_prepare_file_chunks`)
- Modify: `tests/test_chunking.py` (update `TestIndexFileBatching` tests)

**Step 1: Write failing test**

In `tests/test_chunking.py`, in the `TestIndexFileBatching` class, add a test that verifies the document prefix is applied. Add after the existing `test_index_file_prepends_note_name` test:

```python
    @patch("index_vault.get_collection")
    @patch("index_vault.prefix_document", side_effect=lambda t: f"search_document: {t}")
    def test_index_file_applies_document_prefix(self, mock_prefix, mock_get_collection, tmp_path):
        """_prepare_file_chunks applies prefix_document to each chunk."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello\n\nSome content here.\n")

        from index_vault import index_file
        index_file(md_file)

        call_args = mock_collection.upsert.call_args[1]
        for doc in call_args["documents"]:
            assert doc.startswith("search_document: ")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexFileBatching::test_index_file_applies_document_prefix -v`

Expected: FAIL — `prefix_document` not imported in index_vault.

**Step 3: Implement**

In `src/index_vault.py`, change the import on line 18:

```python
from services.chroma import get_collection, prefix_document, purge_database
```

Then in `_prepare_file_chunks`, change line 120 (the document assembly line):

```python
        documents.append(prefix_document(f"[{md_file.stem}] {chunk['text']}"))
```

**Step 4: Run test**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexFileBatching -v`

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: apply document prefix in _prepare_file_chunks (#156)"
```

---

### Task 4: Apply query prefix in hybrid_search.py

**Files:**
- Modify: `src/hybrid_search.py` (import `prefix_query`, apply in `semantic_search`)
- Modify: `tests/test_chunking.py` (add test in search test area)

**Step 1: Write failing test**

In `tests/test_chunking.py`, in the `TestHeadingMetadata` class (which has the semantic_search tests), add:

```python
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.prefix_query", side_effect=lambda t: f"search_query: {t}")
    def test_semantic_search_applies_query_prefix(self, mock_prefix, mock_get_collection):
        """semantic_search applies prefix_query to the query text."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        semantic_search("test query")

        call_args = mock_collection.query.call_args[1]
        assert call_args["query_texts"] == ["search_query: test query"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestHeadingMetadata::test_semantic_search_applies_query_prefix -v`

Expected: FAIL — `prefix_query` not imported in hybrid_search.

**Step 3: Implement**

In `src/hybrid_search.py`, change line 10:

```python
from services.chroma import get_collection, prefix_query
```

Then in `semantic_search`, change line 36 (where query_kwargs is built):

```python
    query_kwargs: dict = {"query_texts": [prefix_query(query)], "n_results": n_results}
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestHeadingMetadata -v`

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add src/hybrid_search.py tests/test_chunking.py
git commit -m "feat: apply query prefix in semantic_search (#156)"
```

---

### Task 5: Run full test suite and fix regressions

**Step 1: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: All tests pass. If any fail, they likely need `prefix_document` or `prefix_query` patches added where `index_vault._prepare_file_chunks` or `hybrid_search.semantic_search` are tested.

Common fix pattern: add `patch("index_vault.prefix_document", side_effect=lambda t: t)` to tests that mock `_prepare_file_chunks` return values directly (these bypass the prefix call, so no change needed). Tests that call the real `_prepare_file_chunks` or `index_file` may need the patch.

**Step 2: Fix any failures and commit**

```bash
git add tests/
git commit -m "test: fix regressions from embedding model prefix changes"
```

---

### Task 6: Update CLAUDE.md and system prompt

**Files:**
- Modify: `CLAUDE.md` (update Configuration table with EMBEDDING_MODEL)
- Modify: `system_prompt.txt.example` (if it references the embedding model or indexing)

**Step 1: Add EMBEDDING_MODEL to the Configuration table in CLAUDE.md**

Add a row to the Configuration table after `UPSERT_BATCH_SIZE`:

```
| `EMBEDDING_MODEL` | `nomic-ai/nomic-embed-text-v1.5` | Sentence-transformers model for embeddings |
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add EMBEDDING_MODEL to configuration table"
```

---

### Task 7: Final verification

**Step 1: Run the complete test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: All tests pass.

**Step 2: Review the diff**

Run: `git diff master --stat` and `git log --oneline master..HEAD`

Verify expected files changed: `src/config.py`, `src/services/chroma.py`, `src/index_vault.py`, `src/hybrid_search.py`, `tests/test_config.py`, `tests/test_chunking.py`, `requirements.txt`, `CLAUDE.md`, `docs/plans/`.

**Step 3: Remind user about migration**

After merging, the user must run `index_vault.py --reset` to rebuild the database with the new 768-dim embeddings. A `--full` reindex is NOT sufficient — the HNSW index dimension is fixed at collection creation.
