# Structure-Aware Chunking for Vault Indexer

**Date:** 2026-02-15
**Status:** Approved

## Problem

The indexer's `chunk_text()` does naive 500-character splits with 50-character overlap, cutting through headings, sentences, and frontmatter boundaries. This produces low-quality chunks that hurt search relevance.

## Approach

Single-pass regex splitter (Approach A) — no new dependencies, handles real-world vault content well.

## Design

### Chunking Pipeline

`chunk_markdown(text, max_chunk_size=1500)` returns `list[dict]` with `text`, `heading`, and `chunk_type` keys.

1. **Strip frontmatter** — detect `---` delimiters at position 0, skip the YAML block (not indexed as a chunk; files on disk are unchanged).
2. **Split by headings** — regex split on `^#{1,6} ` (respecting code fences). Each section = heading line + content until next same-or-higher-level heading. Sections within `max_chunk_size` become chunks with `chunk_type="section"`.
3. **Paragraph fallback** — sections exceeding `max_chunk_size` split on `\n\n`. Chunks get `chunk_type="paragraph"`, inheriting the parent heading.
4. **Sentence fallback** — paragraphs still exceeding the limit split on sentence boundaries (`. `, `? `, `! `). Chunks get `chunk_type="sentence"`.
5. **Character fallback** — content with no natural boundaries uses `_fixed_chunk_text()` (renamed current function) with `chunk_type="fragment"`.

Code fence tracking: boolean flag toggled on `^```|^~~~` lines prevents splitting on headings inside code blocks.

### Metadata

Each chunk stored in ChromaDB:

```python
{
    "source": "path/to/file.md",   # existing
    "chunk": 0,                     # existing (sequential index)
    "heading": "## Meeting Notes",  # NEW - full heading text, or "top-level"
    "chunk_type": "section"         # NEW - section|paragraph|sentence|fragment
}
```

### File Changes

- **`src/index_vault.py`**: Add `chunk_markdown()`, rename `chunk_text` to `_fixed_chunk_text`, update `index_file()` to use new chunker and enriched metadata.
- **`src/hybrid_search.py`**: Include `heading` from metadata in `semantic_search()` and `keyword_search()` return dicts.
- **Tests**: Unit tests for `chunk_markdown()` (frontmatter, headings, paragraph/sentence/character fallbacks, code fences). Integration test for `index_file()` metadata.

### What doesn't change

- ChromaDB collection name
- `search_vault` MCP tool signature
- File content on disk (frontmatter tools unaffected)
- Incremental indexing logic (mtime check, pruning)
- Requires `--full` reindex after deployment
