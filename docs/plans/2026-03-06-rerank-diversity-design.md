# Design: Cross-Encoder Reranking + Source Diversity

Closes #155 (cross-encoder reranking) and #158 (source-level diversity).

## Pipeline Change

Current:
```
semantic (2x) + keyword (2x) → RRF merge → return top N
```

New:
```
semantic (4x) + keyword (4x) → RRF merge → cross-encoder rerank → diversity filter → return top N
```

Candidate multiplier increases from 2x to 4x to provide enough candidates for reranking and diversity backfill.

## Cross-Encoder Reranking

- **Model**: `BAAI/bge-reranker-v2-m3` via sentence-transformers `CrossEncoder` class
- **Config**: `RERANK_MODEL` env var (defaults to above), `RERANK_ENABLED` env var (defaults to `true`)
- **Lazy singleton** in `services/chroma.py` — same pattern as the embedding function, with `threading.RLock()` for thread safety
- **Integration**: new `rerank(query, results)` function called after ranking in all three search modes (hybrid, semantic, keyword)
- Scores (query, chunk_content) pairs via cross-encoder, sorts by score descending
- **Fallback**: if model fails to load, logs error once, sets a flag, skips reranking for all subsequent calls (no retry per query). Original ranking preserved.

## Source Diversity

- **Config**: `MAX_CHUNKS_PER_SOURCE` env var (defaults to 3). Set to 0 to disable.
- **Integration**: `_diversify()` function applied after reranking in all three search modes
- Iterates ranked results, counts per source, skips chunks beyond the cap
- Continues through full candidate pool to backfill, so result count stays at `n_results` when possible
- Applied after reranking so diversity operates on the best-scored candidates

## Config Additions

| Variable | Default | Notes |
|----------|---------|-------|
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder model for reranking |
| `RERANK_ENABLED` | `true` | Toggle reranking on/off |
| `MAX_CHUNKS_PER_SOURCE` | `3` | Max chunks per source file in results. 0 = disabled. |

## Files Changed

| File | Change |
|------|--------|
| `src/config.py` | Add `RERANK_MODEL`, `RERANK_ENABLED`, `MAX_CHUNKS_PER_SOURCE` |
| `src/services/chroma.py` | Add `_reranker` lazy singleton, `rerank(query, results)` function |
| `src/hybrid_search.py` | Bump candidate multiplier to 4x, call `rerank()` after merge/rank, call `_diversify()` after rerank. Apply to all three search functions. |
| `tests/test_hybrid_search.py` (new) | Tests for reranking, diversity, fallback, config toggle |

## Error Handling

- `RERANK_ENABLED=false`: reranking skipped entirely, diversity still applies
- Cross-encoder model fails to load: log error once, set `_reranker_failed` flag, skip reranking on all subsequent calls
- `MAX_CHUNKS_PER_SOURCE=0`: diversity disabled (pass-through)

## Decisions

- **Local cross-encoder** (not API): sentence-transformers already installed, lower latency, no cost
- **bge-reranker-v2-m3** over ms-marco-MiniLM: substantially better quality, ~300ms on CPU for 80 pairs is acceptable for tool calls, faster on CUDA
- **All search modes**: diversity and reranking apply to hybrid, semantic, and keyword equally
- **Backfill**: over-fetch 4x candidates so diversity can drop duplicates and still fill n_results
- **Config toggle**: env var for reranking on/off, not per-query parameter
