# Design: find_notes — Unified Discovery Tool

**Issue**: #131
**Date**: 2026-02-26
**Status**: Approved

## Summary

Replace `search_vault`, `list_files`, and `search_by_date_range` with a single `find_notes` tool that accepts any combination of semantic search, frontmatter filters, folder scope, and date range.

## API

```python
def find_notes(
    query: str = "",                    # semantic/keyword search text
    mode: str = "hybrid",               # "hybrid"/"semantic"/"keyword" (ignored without query)
    folder: str = "",                   # folder scope
    recursive: bool = False,            # include subfolders
    frontmatter: list[FilterCondition] | None = None,  # metadata filters (AND)
    date_start: str = "",               # YYYY-MM-DD inclusive
    date_end: str = "",                 # YYYY-MM-DD inclusive
    date_type: str = "modified",        # "modified" or "created"
    sort: str = "relevance",            # "relevance"/"modified"/"created"/"name"
    include_fields: list[str] | None = None,  # frontmatter fields to return
    n_results: int = 20,               # max results
    offset: int = 0,                    # pagination
) -> str:
```

### Validation

- At least one of `query`, `folder`, `frontmatter`, `date_start`/`date_end` must be provided.
- `sort="relevance"` requires `query` (error otherwise).

## Execution Strategy: Two-Phase Intersect

### When query is provided with other filters

1. Run semantic/hybrid search via `hybrid_search()` — returns paths + scores for all matching chunks.
2. Run vault scan with folder/frontmatter/date filters — returns a set of matching paths.
3. Intersect: keep only semantic results whose source path is in the filter set.
4. Sort by semantic score, apply pagination.

### When no query (pure vault scan)

Single pass over `get_vault_files()` applying all filters (folder, frontmatter, date). Sort by requested field, apply pagination. Reuses existing `_find_matching_files` logic with added date filtering.

## Result Format

### With query (semantic results)

```json
{
  "success": true,
  "results": [
    {"source": "path.md", "content": "matched chunk...", "heading": "section"}
  ],
  "total": 42
}
```

### Without query (vault scan), no include_fields

```json
{
  "success": true,
  "results": ["path1.md", "path2.md"],
  "total": 42
}
```

### Without query, with include_fields

```json
{
  "success": true,
  "results": [
    {"path": "path.md", "status": "active", "tags": "..."}
  ],
  "total": 42
}
```

## What Gets Removed

- `search_vault` tool from MCP schema and `src/tools/search.py`
- `list_files` tool from MCP schema (internal helpers `_find_matching_files`, `_matches_field` stay for batch ops)
- `search_by_date_range` tool from MCP schema (date helpers stay as internal functions)

## File Layout

- `find_notes` lives in `src/tools/search.py` (primary discovery tool)
- Internal vault-scan helpers stay in place (frontmatter.py)
- New compaction stub in `compaction.py` handles both semantic and vault-scan result shapes

## Compaction

Stub builder handles two shapes:
- Semantic results: snippet from content, preserve source + heading (like current search_vault stub)
- Vault scan results: preserve path list or field projection (like current list stub)

## Testing

New `tests/test_find_notes.py`:
- Single-filter equivalence: `find_notes(query=...)` matches old `search_vault`, etc.
- Combined filters: query + folder, query + frontmatter, query + date, all four together
- Two-phase intersect: semantic results correctly filtered by vault scan
- Sort options: relevance, modified, created, name
- Pagination across both modes
- Validation: no filters → error, sort="relevance" without query → error
- include_fields projection in vault-scan mode
- Compaction stub for both result shapes

## Documentation Updates

- System prompt: update tool reference and decision tree
- CLAUDE.md: update tool table (remove 3, add find_notes)
