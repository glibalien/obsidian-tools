# Frontmatter Key Rename — Design

## Summary

Add a `"rename"` operation to both `update_frontmatter` and `batch_update_frontmatter`.
When `operation="rename"`, `field` is the existing key name and `value` is the new key name.
If the target key already exists in a file, the operation errors for that file.

## API

```
update_frontmatter(path="note.md", field="old_key", value="new_key", operation="rename")
batch_update_frontmatter(field="old_key", value="new_key", operation="rename", paths=[...])
```

No new parameters — `field` (source key), `value` (destination key), and `operation` are reused.

## Conflict handling

If the new key name already exists in a file's frontmatter, the operation fails for that file
with an error message. No silent overwriting.

## Changes by layer

### 1. `services/vault.py` — `update_file_frontmatter`

New `rename` code path:
- Verify `field` exists (error if not)
- Verify `value` (new key) does NOT exist (error if it does)
- Pop old key, insert new key with same value
- Preserve key ordering via OrderedDict rebuild (insert new key at old key's position)

### 2. `services/vault.py` — `do_update_frontmatter`

Pass `rename=(operation == "rename")` to `update_file_frontmatter`.
Success message: `"Renamed '{field}' to '{value}' in {path}"`.

### 3. `tools/frontmatter.py` — `update_frontmatter`

- Expand operation validation to accept `"rename"`
- For rename: validate `value` is a non-empty string (it's a key name, not a YAML value)
- Skip `_normalize_frontmatter_value` for rename

### 4. `tools/frontmatter.py` — `batch_update_frontmatter`

Same validation. Existing `_resolve_batch_targets`, `_needs_confirmation`, and
`_confirmation_preview` work unchanged — they already handle `field`, `value`, `operation`
generically. Preview reads: `"rename 'old_key' = 'new_key' on N files"`.

### 5. Tests

- `test_tools_frontmatter.py`: Single rename (happy path, source missing, target exists,
  rename to same name). Batch rename (explicit paths, query-based, confirmation gate).
- `test_vault_service.py`: `update_file_frontmatter` rename (key ordering, errors).

### 6. System prompt + CLAUDE.md

Update tool references to mention the `rename` operation.

## What does NOT change

- `_normalize_frontmatter_value` — not called for rename
- `_find_matching_files` — targeting is independent of update operation
- `list_files_by_frontmatter` — read-only
- MCP server registration — no new tools
- ChromaDB index — re-indexed on next scheduled run
