# Keyword Search Quality Improvements

## Problem
Three keyword search quality issues in `hybrid_search.py`:
1. Binary term presence ranking instead of frequency-based
2. Keyword results truncated to 500 chars but semantic results return full chunks
3. Incomplete stopword list

## Design

### Term frequency ranking
Change `sum(1 for t in terms if t in doc_lower)` to `sum(doc_lower.count(t) for t in terms)`. Counts occurrences rather than presence. No full TF-IDF needed — ChromaDB already filters the candidate pool.

### Consistent truncation
Remove `doc[:500]` truncation in `keyword_search`. Return full chunk content matching semantic search. Truncation for display is handled by compaction.

### Expanded stopwords
Add common English words: "this", "that", "with", "from", "have", "has", "was", "were", "been", "not", "but", "are", "can", "will", "just", "about", "into", "over", "also".

## Files
- `src/hybrid_search.py` — all three changes
- `tests/test_chunking.py` — keyword search tests
