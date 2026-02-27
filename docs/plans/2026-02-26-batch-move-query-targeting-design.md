# Design: Query-Based Targeting for batch_move_files

**Issue**: #139
**Date**: 2026-02-26

## Problem

`batch_move_files` only accepts explicit `{source, destination}` move lists. The agent must call `find_notes` first, then construct the list manually — an extra LLM round-trip that's error-prone.

`batch_update_frontmatter` already supports query-based targeting. `batch_move_files` should too.

## Approach: Move Targeting Utilities to services/vault.py

Extract frontmatter matching and vault-scanning utilities from `tools/frontmatter.py` into `services/vault.py` as shared infrastructure, then add query-based parameters to `batch_move_files`.

### What Moves to services/vault.py

| Function/Class | Purpose |
|----------------|---------|
| `FilterCondition` | Pydantic model for filter conditions |
| `_get_field_ci` | Case-insensitive frontmatter field lookup |
| `_strip_wikilinks` / `_WIKILINK_RE` | Wikilink bracket stripping |
| `_matches_field` | Core filter predicate |
| `VALID_MATCH_TYPES` / `NO_VALUE_MATCH_TYPES` | Validation constants |
| `_validate_filters` | Filter list validation |
| `_get_file_date` | Date helper (frontmatter Date / filesystem fallback) |
| `_find_matching_files` | Vault scan with filter/folder/date support |

### What Stays in tools/frontmatter.py

- `_normalize_frontmatter_value` — only used by frontmatter entry points
- `_confirmation_preview`, `_needs_confirmation`, `_resolve_batch_targets` — frontmatter-specific orchestration
- `update_frontmatter`, `batch_update_frontmatter` — tool entry points

### batch_move_files New Signature

```python
def batch_move_files(
    moves: list[dict] | None = None,
    destination_folder: str | None = None,
    target_field: str | None = None,
    target_value: str | None = None,
    target_match_type: str = "contains",
    target_filters: list[FilterCondition] | None = None,
    folder: str | None = None,
    recursive: bool = False,
    confirm: bool = False,
) -> str:
```

Query mode calls `_find_matching_files` from `services/vault.py`, builds `source -> destination_folder/filename` pairs, runs through the existing confirmation gate + `do_move_file` loop.

### Mutual Exclusivity

- `moves` vs query params (`target_field`/`folder` + `destination_folder`) — error if both
- `destination_folder` required in query mode, forbidden with explicit `moves`

### Confirmation Flow

Reuses `store_preview`/`consume_preview`. Key: `("batch_move_files", destination_folder, tuple(sorted(source_paths)))`. Preview shows each `source -> destination` mapping.

### Error Handling

- Destination conflicts: `do_move_file` reports per-file errors in batch summary
- Empty query results: early return with message
- Duplicate filenames in results: first succeeds, second fails (destination exists)
- `destination_folder` without query params: error

### Testing

- Query-based move with frontmatter filter
- Folder-scoped query
- Confirmation flow at >5 files with preview
- Mutual exclusivity errors (`moves` + `target_field`, `moves` + `destination_folder`)
- Missing `destination_folder` in query mode
- No matches → early return
- Existing explicit `moves` tests unchanged
