# Design: Absorb search_by_folder into list_files

## Summary

Remove `search_by_folder` by making `field` optional in `list_files_by_frontmatter`, then rename the tool to `list_files`. When called with only `folder` (no `field`), it returns all files in that folder — same behavior as `search_by_folder`. Result: -1 MCP tool, clearer naming.

## Changes

### 1. Rename + modify `list_files_by_frontmatter` → `list_files` (frontmatter.py)

- Rename function to `list_files`
- Change `field: str` to `field: str = ""`
- When `field` is empty, skip `match_type`/`value` validation
- Validate: at least one of `field` or `folder` must be provided
- Update docstring to reflect dual-purpose nature
- Fix "no results" message for folder-only mode

### 2. Remove `search_by_folder` (links.py)

- Delete the function entirely
- Keep `compare_folders`, `find_backlinks`, `find_outlinks`

### 3. Update registrations (mcp_server.py)

- Remove `search_by_folder` import and `mcp.tool()` call
- Update `list_files_by_frontmatter` → `list_files` import and registration

### 4. Update exports (tools/__init__.py)

- Remove `search_by_folder`
- Rename `list_files_by_frontmatter` → `list_files`

### 5. Update compaction (services/compaction.py)

- Remove `search_by_folder` entry from stub dispatch map
- Rename `list_files_by_frontmatter` → `list_files`

### 6. Update tests

- Migrate `search_by_folder` tests in `test_tools_links.py` to call `list_files(folder=...)` instead
- Move migrated tests to `test_tools_frontmatter.py` (where `list_files_by_frontmatter` tests live)
- Add test for "no field, no folder" error
- Rename all `list_files_by_frontmatter` references in tests

### 7. Update docs

- `system_prompt.txt.example` — remove `search_by_folder`, update tool name
- `CLAUDE.md` — update tool table
