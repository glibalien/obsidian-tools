# Batch ChromaDB Upserts (#121)

## Problem

Full vault reindexing is slow (~16 min) because each file triggers its own `collection.upsert()`, giving the embedding model tiny batches. The bottleneck is embedding computation, not file I/O.

## Solution: Collect-then-bulk-upsert

Replace the per-file ChromaDB operations with three sequential phases.

### Phase 1 — Prepare chunks (parallel, existing ThreadPoolExecutor)

- Run `_prepare_file_chunks` for each file in worker threads (unchanged)
- Collect results into a list of `(source, ids, documents, metadatas)` tuples
- Track `failed` count and `valid_sources` discards as before
- Log: `Prepared N/M files...` every 100 files

### Phase 2 — Delete stale chunks (main thread)

- For each successfully prepared source, delete old chunks via `collection.delete(where={"source": source})`
- Log: `Deleted stale chunks for N files`

### Phase 3 — Bulk upsert (main thread)

- Concatenate all ids/documents/metadatas into flat lists
- Upsert in batches of `UPSERT_BATCH_SIZE` (500)
- Log: `Upserting batch K/N (X chunks)...`

### Error handling

- Phase 1: same as today — skip file, increment `failed`, discard from `valid_sources`
- Phase 2/3: batch failure skips `mark_run` so next run retries

### Constants

- `UPSERT_BATCH_SIZE = 500` in `config.py`

### Tests (in existing test_index_vault.py)

- Chunks from multiple files batched into single upserts
- Stale chunks deleted before new ones upserted
- Failed files don't block other files' chunks
- `UPSERT_BATCH_SIZE` splits large chunk sets correctly
