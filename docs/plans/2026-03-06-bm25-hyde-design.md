# BM25 Keyword Search + HyDE Design

Closes #154 (BM25) and #161 (HyDE).

## BM25 Keyword Search (#154)

### Problem

Keyword search uses ChromaDB's `$contains` substring matching with raw term frequency ranking. No IDF weighting — rare distinctive terms get the same weight as common ones. `_case_variants` is a workaround for case-sensitive `$contains`.

### Approach

Replace ChromaDB-based keyword search with an in-memory `rank_bm25` index.

**New module `src/bm25_index.py`:**
- Lazy singleton `BM25Okapi` index, built on first search from ChromaDB documents
- `build_index()` — loads all docs+metadata from ChromaDB, tokenizes, builds index. Stores parallel metadata list (source, heading, chunk_type, document text) for result lookup.
- `query_index(query, n_results, chunk_type)` — tokenizes query, scores via BM25, returns top-N results. `chunk_type` filtering applied post-score.
- `invalidate()` — called by `index_vault.py` after indexing to force rebuild on next search
- Thread-safe via `threading.RLock()` (same pattern as chroma.py singletons)

**Changes to `hybrid_search.py`:**
- `_keyword_retrieve` calls `bm25_index.query_index()` instead of ChromaDB `$contains`
- `_extract_query_terms` stays (reused for BM25 tokenization)
- `_case_variants` removed (BM25 tokenization is case-insensitive)
- `KEYWORD_LIMIT` config constant removed (BM25 returns scored results directly)

**Changes to `index_vault.py`:**
- After indexing completes, call `bm25_index.invalidate()` to ensure next search rebuilds

**Dependency:** `rank_bm25` added to requirements.

### Tokenization

Reuse `_extract_query_terms` logic: split on whitespace, strip punctuation, lowercase, filter stopwords and short words (<3 chars). Applied identically to both documents (at index build time) and queries (at search time).

## HyDE — Hypothetical Document Embeddings (#161)

### Problem

Question-type queries ("how does indexing work?") embed as questions, but vault content is answers/prose. Questions and answers occupy different embedding space regions, reducing semantic recall.

### Approach

For question-type queries, generate a hypothetical answer via LLM, embed it, and run a second semantic search. Merge both candidate sets (original + hypothetical) via RRF before reranking.

**Question detection — `_is_question(query)` in `hybrid_search.py`:**
- Starts with question word: who, what, where, when, why, how, which, is, are, does, do, can, could, would, should
- OR ends with `?`
- Simple heuristic, no LLM call for detection

**Hypothetical answer generation — `_generate_hyde(query)` in `hybrid_search.py`:**
- Calls Fireworks API with prompt: "Write a short paragraph that would answer this question in the context of a personal knowledge base: {query}"
- Short generation (~100-150 tokens max)
- Returns `None` on any failure — caller falls back to standard search
- Uses `FIREWORKS_BASE_URL`, `FIREWORKS_MODEL` from config

**Dual search + merge:**
- When `_is_question` is true and `HYDE_ENABLED`:
  1. Generate hypothetical answer
  2. Embed both original query and hypothetical answer
  3. Run two ChromaDB semantic queries
  4. Merge candidate lists via `merge_results` (RRF)
- When false or generation fails: standard single-query path
- Merged candidates flow into normal rerank + diversify pipeline

**Integration point:** New `_hyde_retrieve(query, n_results, chunk_type)` function called from `_semantic_retrieve` when conditions are met. Affects both `semantic_search` and `hybrid_search`. Keyword-only search is unaffected.

**Config:** `HYDE_ENABLED` (default `true`), same pattern as `RERANK_ENABLED`. No separate model config — uses `FIREWORKS_MODEL`.

## Files Changed

| File | Change |
|------|--------|
| `src/bm25_index.py` | New — BM25 index management |
| `src/hybrid_search.py` | Replace keyword retrieval with BM25, add HyDE to semantic retrieval |
| `src/index_vault.py` | Call `bm25_index.invalidate()` after indexing |
| `src/config.py` | Add `HYDE_ENABLED`, remove `KEYWORD_LIMIT` |
| `src/services/chroma.py` | No changes |
| `tests/test_bm25_index.py` | New — BM25 index tests |
| `tests/test_hybrid_search.py` | Update keyword tests, add HyDE tests |
| `requirements.txt` | Add `rank_bm25` |

## Not Doing

- SQLite FTS5 (chose `rank_bm25` for simplicity)
- HyDE caching (queries rarely repeat)
- Separate HyDE model config (use main model unless latency is a problem)
- Always-on HyDE (only for detected question-type queries)
- HyDE as a search mode parameter (invisible to the agent)
