# Embedding Model Upgrade (#156)

## Problem

ChromaDB defaults to `all-MiniLM-L6-v2` (384 dims, 2021-era). Modern models significantly outperform it on retrieval benchmarks.

## Solution

Switch to `nomic-ai/nomic-embed-text-v1.5` (768 dims, 8192-token context) via ChromaDB's `embedding_function` parameter.

### Config

- `EMBEDDING_MODEL` env var in `config.py`, defaults to `nomic-ai/nomic-embed-text-v1.5`

### ChromaDB integration

- Use `SentenceTransformerEmbeddingFunction` in `get_or_create_collection()`
- `trust_remote_code=True` required for nomic's custom architecture

### Nomic prefix requirement

nomic-embed-text-v1.5 requires task prefixes:
- Indexing: `"search_document: "` prepended to documents
- Querying: `"search_query: "` prepended to query text

Add prefix helpers in `chroma.py` so call sites stay model-agnostic. Apply in:
- `_prepare_file_chunks` (index_vault.py) — document prefix
- `hybrid_search.py` — query prefix

### Migration

Requires `--reset` (dimension change invalidates HNSW index). Documented behavior.

### Dependencies

Add explicit `sentence-transformers` and `einops` to requirements.

### Tests

- `get_collection()` passes embedding function
- Prefix helpers produce correct output
- `EMBEDDING_MODEL` config respected
