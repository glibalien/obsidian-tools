# Cross-Encoder Reranking + Source Diversity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add cross-encoder reranking and source-level diversity filtering to all search modes, improving result precision and breadth.

**Architecture:** After RRF merge (or initial ranking for single-mode searches), score candidates with a local cross-encoder model (`BAAI/bge-reranker-v2-m3`), then cap chunks per source file to enforce diversity. Over-fetch 4x candidates to backfill after diversity drops.

**Tech Stack:** sentence-transformers `CrossEncoder`, ChromaDB, pytest

---

### Task 1: Add config constants

**Files:**
- Modify: `src/config.py:65-67` (after existing hybrid search constants)

**Step 1: Write the failing test**

Create `tests/test_rerank_diversity.py`:

```python
"""Tests for cross-encoder reranking and source diversity."""

import importlib
from unittest.mock import patch


class TestRerankConfig:
    """Config constants for reranking and diversity."""

    def test_rerank_model_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_MODEL == "BAAI/bge-reranker-v2-m3"

    def test_rerank_enabled_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_ENABLED is True

    def test_rerank_enabled_false(self, monkeypatch):
        monkeypatch.setenv("RERANK_ENABLED", "false")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_ENABLED is False

    def test_max_chunks_per_source_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 3

    def test_max_chunks_per_source_env(self, monkeypatch):
        monkeypatch.setenv("MAX_CHUNKS_PER_SOURCE", "5")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 5

    def test_max_chunks_per_source_zero_disables(self, monkeypatch):
        monkeypatch.setenv("MAX_CHUNKS_PER_SOURCE", "0")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestRerankConfig -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'RERANK_MODEL'`

**Step 3: Write minimal implementation**

Add to `src/config.py` after line 67 (`KEYWORD_LIMIT`):

```python
# Reranking
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
MAX_CHUNKS_PER_SOURCE = int(os.getenv("MAX_CHUNKS_PER_SOURCE", "3"))
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestRerankConfig -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_rerank_diversity.py
git commit -m "feat: add config constants for reranking and diversity"
```

---

### Task 2: Add reranker singleton and rerank function to chroma.py

**Files:**
- Modify: `src/services/chroma.py` (add reranker singleton + `rerank()`)
- Modify: `tests/test_rerank_diversity.py` (add reranker tests)

**Step 1: Write the failing tests**

Append to `tests/test_rerank_diversity.py`:

```python
from unittest.mock import MagicMock, patch


class TestRerank:
    """Tests for the rerank function in services/chroma.py."""

    @patch("services.chroma.get_reranker")
    def test_rerank_sorts_by_score(self, mock_get_reranker):
        """Rerank should sort results by cross-encoder score descending."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]
        mock_get_reranker.return_value = mock_model

        from services.chroma import rerank
        results = [
            {"source": "a.md", "content": "low relevance", "heading": ""},
            {"source": "b.md", "content": "high relevance", "heading": ""},
            {"source": "c.md", "content": "mid relevance", "heading": ""},
        ]
        ranked = rerank("test query", results)
        assert [r["source"] for r in ranked] == ["b.md", "c.md", "a.md"]

    @patch("services.chroma.get_reranker")
    def test_rerank_preserves_all_fields(self, mock_get_reranker):
        """Rerank should preserve all dict fields in results."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.8]
        mock_get_reranker.return_value = mock_model

        from services.chroma import rerank
        results = [{"source": "a.md", "content": "text", "heading": "## H1", "extra": "val"}]
        ranked = rerank("query", results)
        assert ranked[0]["heading"] == "## H1"
        assert ranked[0]["extra"] == "val"

    @patch("services.chroma.get_reranker")
    def test_rerank_empty_results(self, mock_get_reranker):
        """Rerank on empty input returns empty list."""
        from services.chroma import rerank
        assert rerank("query", []) == []
        mock_get_reranker.assert_not_called()

    @patch("services.chroma.RERANK_ENABLED", False)
    def test_rerank_disabled_returns_unchanged(self):
        """When RERANK_ENABLED is False, return results unchanged."""
        from services.chroma import rerank
        results = [
            {"source": "a.md", "content": "text1", "heading": ""},
            {"source": "b.md", "content": "text2", "heading": ""},
        ]
        ranked = rerank("query", results)
        assert ranked == results

    @patch("services.chroma._reranker_failed", True)
    @patch("services.chroma.RERANK_ENABLED", True)
    def test_rerank_after_load_failure_returns_unchanged(self):
        """If reranker failed to load, skip reranking."""
        from services.chroma import rerank
        results = [{"source": "a.md", "content": "text", "heading": ""}]
        ranked = rerank("query", results)
        assert ranked == results
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestRerank -v`
Expected: FAIL — `ImportError: cannot import name 'rerank' from 'services.chroma'`

**Step 3: Write minimal implementation**

Add to `src/services/chroma.py`. After the existing imports block (line 19), add:

```python
from config import CHROMA_PATH, EMBEDDING_MODEL, RERANK_ENABLED, RERANK_MODEL
```

(Replace the existing `from config import CHROMA_PATH, EMBEDDING_MODEL` line.)

After the existing singletons (`_embedding_function = None`, around line 27), add:

```python
_reranker = None
_reranker_failed = False
```

After `embed_query()` (after line 111), add:

```python
def get_reranker():
    """Get or create the cross-encoder reranker (lazy singleton, thread-safe).

    Returns None if the model fails to load (sets _reranker_failed flag).
    """
    global _reranker, _reranker_failed
    if _reranker_failed:
        return None
    if _reranker is None:
        with _lock:
            if _reranker is None and not _reranker_failed:
                try:
                    from sentence_transformers import CrossEncoder
                    _reranker = CrossEncoder(
                        RERANK_MODEL,
                        device="cuda" if _cuda_available() else "cpu",
                    )
                except Exception as e:
                    _reranker_failed = True
                    logger.error("Failed to load reranker model %s: %s", RERANK_MODEL, e)
                    return None
    return _reranker


def rerank(query: str, results: list[dict]) -> list[dict]:
    """Rerank search results using a cross-encoder model.

    Scores (query, content) pairs and sorts by score descending.
    Falls back to original ordering if reranking is disabled or the
    model failed to load.

    Args:
        query: The search query.
        results: List of result dicts (must have 'content' key).

    Returns:
        Results sorted by cross-encoder score, or unchanged if skipped.
    """
    if not results or not RERANK_ENABLED or _reranker_failed:
        return results

    model = get_reranker()
    if model is None:
        return results

    pairs = [(query, r["content"]) for r in results]
    try:
        scores = model.predict(pairs)
    except Exception as e:
        logger.warning("Reranking failed: %s", e)
        return results

    scored = sorted(zip(scores, results), key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]
```

Update `reset()` to also clear reranker state:

```python
def reset():
    """Reset singletons (for testing)."""
    global _client, _collection, _embedding_function, _reranker, _reranker_failed
    _client = None
    _collection = None
    _embedding_function = None
    _reranker = None
    _reranker_failed = False
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestRerank -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/services/chroma.py tests/test_rerank_diversity.py
git commit -m "feat: add cross-encoder reranker singleton and rerank function"
```

---

### Task 3: Add diversity filter function

**Files:**
- Modify: `src/hybrid_search.py` (add `_diversify()`)
- Modify: `tests/test_rerank_diversity.py` (add diversity tests)

**Step 1: Write the failing tests**

Append to `tests/test_rerank_diversity.py`:

```python
class TestDiversify:
    """Tests for source-level diversity filtering."""

    def test_caps_chunks_per_source(self):
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": f"chunk{i}", "heading": ""} for i in range(5)
        ]
        diverse = _diversify(results, max_per_source=3)
        assert len(diverse) == 3
        assert all(r["source"] == "a.md" for r in diverse)

    def test_backfills_from_other_sources(self):
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "a.md", "content": "a2", "heading": ""},
            {"source": "a.md", "content": "a3", "heading": ""},
            {"source": "a.md", "content": "a4", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "b.md", "content": "b2", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=2)
        sources = [r["source"] for r in diverse]
        assert sources.count("a.md") == 2
        assert sources.count("b.md") == 2

    def test_preserves_ranking_order(self):
        """Within the cap, original ranking order is preserved."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "a.md", "content": "a2", "heading": ""},
            {"source": "b.md", "content": "b2", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=2)
        assert [r["content"] for r in diverse] == ["a1", "b1", "a2", "b2"]

    def test_zero_max_disables(self):
        """max_per_source=0 disables diversity (pass-through)."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": f"chunk{i}", "heading": ""} for i in range(5)
        ]
        diverse = _diversify(results, max_per_source=0)
        assert len(diverse) == 5

    def test_empty_results(self):
        from hybrid_search import _diversify
        assert _diversify([], max_per_source=3) == []

    def test_fewer_results_than_cap(self):
        """When all sources are under the cap, nothing is dropped."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "c.md", "content": "c1", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=3)
        assert len(diverse) == 3
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestDiversify -v`
Expected: FAIL — `ImportError: cannot import name '_diversify' from 'hybrid_search'`

**Step 3: Write minimal implementation**

Add to `src/hybrid_search.py`, after the existing imports (line 10), add:

```python
from config import KEYWORD_LIMIT, MAX_CHUNKS_PER_SOURCE, RRF_K
```

(Replace the existing `from config import KEYWORD_LIMIT, RRF_K` line.)

Add the function after `_dedup_key()` (after line 141):

```python
def _diversify(results: list[dict], max_per_source: int = MAX_CHUNKS_PER_SOURCE) -> list[dict]:
    """Limit chunks per source file to enforce result diversity.

    Iterates results in rank order, skipping chunks from sources that
    have already reached the cap. This preserves ranking order while
    ensuring no single source dominates the result set.

    Args:
        results: Ranked search results (must have 'source' key).
        max_per_source: Maximum chunks per source. 0 disables filtering.

    Returns:
        Filtered results with at most max_per_source per source.
    """
    if not max_per_source:
        return results

    counts: dict[str, int] = defaultdict(int)
    diverse = []
    for r in results:
        if counts[r["source"]] < max_per_source:
            diverse.append(r)
            counts[r["source"]] += 1
    return diverse
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestDiversify -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/hybrid_search.py tests/test_rerank_diversity.py
git commit -m "feat: add source-level diversity filter function"
```

---

### Task 4: Wire reranking + diversity into search functions

**Files:**
- Modify: `src/hybrid_search.py` (update `hybrid_search`, `semantic_search`, `keyword_search`)
- Modify: `tests/test_rerank_diversity.py` (add integration tests)

**Step 1: Write the failing tests**

Append to `tests/test_rerank_diversity.py`:

```python
class TestSearchIntegration:
    """Tests that reranking and diversity are wired into search functions."""

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_semantic_search_calls_rerank(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["doc1", "doc2"]],
            "metadatas": [[
                {"source": "a.md", "heading": ""},
                {"source": "b.md", "heading": ""},
            ]],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r  # pass-through

        from hybrid_search import semantic_search
        semantic_search("test", n_results=2)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    def test_keyword_search_calls_rerank(self, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["1", "2"],
            "documents": ["keyword match one", "keyword match two"],
            "metadatas": [
                {"source": "a.md", "heading": ""},
                {"source": "b.md", "heading": ""},
            ],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import keyword_search
        keyword_search("keyword match", n_results=2)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_hybrid_search_calls_rerank(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["doc1"]],
            "metadatas": [[{"source": "a.md", "heading": ""}]],
        }
        mock_collection.get.return_value = {
            "ids": ["1"],
            "documents": ["doc1"],
            "metadatas": [{"source": "a.md", "heading": ""}],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import hybrid_search
        hybrid_search("test", n_results=1)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_hybrid_search_fetches_4x_candidates(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
        mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import hybrid_search
        hybrid_search("test", n_results=5)
        # semantic_search should request 4x = 20 candidates
        mock_collection.query.assert_called_once()
        assert mock_collection.query.call_args[1]["n_results"] == 20

    @patch("hybrid_search.rerank", side_effect=lambda q, r: r)
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_diversity_applied_after_rerank(self, mock_embed, mock_coll, mock_rerank):
        """Source diversity caps results even after reranking."""
        # 5 chunks from same source
        docs = [f"doc{i}" for i in range(5)]
        metas = [{"source": "a.md", "heading": ""} for _ in range(5)]
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [docs],
            "metadatas": [metas],
        }
        mock_coll.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test", n_results=5)
        # Default MAX_CHUNKS_PER_SOURCE=3, so capped at 3
        assert len(results) == 3
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py::TestSearchIntegration -v`
Expected: FAIL — `rerank` not imported in `hybrid_search`

**Step 3: Write implementation**

Modify `src/hybrid_search.py`:

Add import (line 10, after the existing config import):

```python
from services.chroma import embed_query, get_collection, rerank
```

(Replace existing `from services.chroma import embed_query, get_collection`.)

Update `semantic_search()` — change the return statement (lines 42-45) to:

```python
    results = [
        {"source": metadata["source"], "content": doc, "heading": metadata.get("heading", "")}
        for doc, metadata in zip(results["documents"][0], results["metadatas"][0])
    ]
    return _diversify(rerank(query, results))[:n_results]
```

Update `keyword_search()` — change the final return (lines 133-136) to:

```python
    ranked = [
        {"source": r["source"], "content": r["content"], "heading": r["heading"]}
        for r in scored[:n_results * 4]
    ]
    return _diversify(rerank(query, ranked))[:n_results]
```

Note: We widen the pre-diversity pool to `n_results * 4` (from `n_results`) so diversity can backfill. The term-frequency sort is still applied first — reranking refines this ordering.

Update `hybrid_search()` — change the function body (lines 200-203) to:

```python
    candidate_count = n_results * 4
    sem_results = semantic_search(query, n_results=candidate_count, chunk_type=chunk_type)
    kw_results = keyword_search(query, n_results=candidate_count, chunk_type=chunk_type)
    merged = merge_results(sem_results, kw_results, n_results=candidate_count)
    return _diversify(rerank(query, merged))[:n_results]
```

**Important**: `semantic_search` and `keyword_search` already apply rerank+diversify internally. To avoid double-reranking in `hybrid_search`, we need to restructure slightly. The cleanest approach: extract raw retrieval functions that don't rerank, used by `hybrid_search`.

Actually, simpler: have `hybrid_search` call the raw retrieval logic directly instead of the public functions. Refactor `semantic_search` and `keyword_search` to separate retrieval from post-processing:

```python
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
    """Search the vault using semantic similarity via ChromaDB embeddings."""
    candidates = _semantic_retrieve(query, n_results=n_results * 4, chunk_type=chunk_type)
    return _diversify(rerank(query, candidates))[:n_results]


def _keyword_retrieve(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Raw keyword retrieval without reranking or diversity."""
    terms = _extract_query_terms(query)
    if not terms:
        return []

    collection = get_collection()

    variants = _case_variants(terms)
    if len(variants) == 1:
        where_document = {"$contains": variants[0]}
    else:
        where_document = {"$or": [{"$contains": v} for v in variants]}

    get_kwargs: dict = {
        "where_document": where_document,
        "include": ["documents", "metadatas"],
        "limit": KEYWORD_LIMIT,
    }
    if chunk_type:
        get_kwargs["where"] = {"chunk_type": chunk_type}

    try:
        matches = collection.get(**get_kwargs)
    except ChromaError as e:
        logger.warning("Keyword search failed: %s", e)
        return []

    if not matches["ids"]:
        return []

    scored = []
    for doc, metadata in zip(matches["documents"], matches["metadatas"]):
        doc_lower = doc.lower()
        hits = sum(doc_lower.count(t) for t in terms)
        scored.append({
            "source": metadata["source"],
            "content": doc,
            "heading": metadata.get("heading", ""),
            "hits": hits,
        })

    scored.sort(key=lambda x: x["hits"], reverse=True)
    return [
        {"source": r["source"], "content": r["content"], "heading": r["heading"]}
        for r in scored[:n_results]
    ]


def keyword_search(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Search the vault for chunks containing query keywords."""
    candidates = _keyword_retrieve(query, n_results=n_results * 4, chunk_type=chunk_type)
    return _diversify(rerank(query, candidates))[:n_results]


def hybrid_search(
    query: str, n_results: int = 5, chunk_type: str | None = None
) -> list[dict[str, str]]:
    """Run semantic and keyword search, merging results with RRF."""
    candidate_count = n_results * 4
    sem_results = _semantic_retrieve(query, n_results=candidate_count, chunk_type=chunk_type)
    kw_results = _keyword_retrieve(query, n_results=candidate_count, chunk_type=chunk_type)
    merged = merge_results(sem_results, kw_results, n_results=candidate_count)
    return _diversify(rerank(query, merged))[:n_results]
```

This avoids double-reranking: `hybrid_search` uses `_semantic_retrieve` and `_keyword_retrieve` (raw), while the public `semantic_search` and `keyword_search` each apply rerank+diversify once.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_rerank_diversity.py -v`
Expected: PASS (all tests)

**Step 5: Run existing tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSearchHeadingMetadata tests/test_find_notes.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/hybrid_search.py tests/test_rerank_diversity.py
git commit -m "feat: wire reranking and diversity into all search modes"
```

---

### Task 5: Update CLAUDE.md and create GitHub issue branch

**Files:**
- Modify: `CLAUDE.md` (add config vars to table, update hybrid_search description)

**Step 1: Update CLAUDE.md**

Add to the Configuration table (after the `EMBEDDING_MODEL` row):

```
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder model for result reranking |
| `RERANK_ENABLED` | `true` | Toggle cross-encoder reranking |
| `MAX_CHUNKS_PER_SOURCE` | `3` | Max chunks per source in results (0 = disabled) |
```

Update the `hybrid_search.py` description in the Architecture section to mention reranking and diversity.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for reranking and diversity config"
```

---

### Task 6: Final validation

**Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (existing + new)

**Step 2: Verify import chain works**

Run: `.venv/bin/python -c "from hybrid_search import hybrid_search, semantic_search, keyword_search; print('OK')"`
Expected: `OK` (no import errors — note: the CrossEncoder model is lazy-loaded, so this won't download anything)
