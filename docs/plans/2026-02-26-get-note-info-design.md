# Design: get_note_info Tool

## Summary

Add a `get_note_info` MCP tool that returns structured metadata about a vault note (frontmatter, headings, size, timestamps, link counts) without returning the full content. Saves tokens when the LLM needs to triage or understand a note's context before deciding whether to read it.

## API

```python
get_note_info(path: str) -> str
```

Returns JSON:

```json
{
  "success": true,
  "path": "Meetings/2026-02-20 Standup.md",
  "frontmatter": {"category": ["meeting"], "project": "archbrain"},
  "headings": ["## Attendees", "## Agenda", "## Action Items"],
  "size": 4521,
  "modified": "2026-02-20T10:30:00",
  "created": "2026-02-20T09:00:00",
  "backlink_count": 3,
  "outlink_count": 7
}

```

## Design Decisions

- **File location:** `src/tools/files.py` — already has `read_file` and file-level operations; this is a lighter variant. No new module.
- **Always include link counts** — simple and consistent, vault scan is fast for a single file.
- **Heading extraction** — scan lines, skip code fences (using `is_fence_line`), collect lines matching `HEADING_PATTERN`.
- **Timestamps** — `modified` from `stat().st_mtime`; `created` from frontmatter `Date` field falling back to `get_file_creation_time()` (same pattern as `_get_file_date` in search.py).
- **`size`** — character count (matches `read_file`'s pagination model).
- **Backlinks** — reuse `_scan_backlinks` from `links.py`, just take `len()`.
- **Outlinks** — reuse `_extract_outlinks` from `links.py`, just take `len()`.
- **Compaction stub** — add a stub builder in `compaction.py` that preserves path + counts, drops frontmatter/headings detail.
- **Non-markdown files** — return what's available (size, timestamps, empty frontmatter/headings, link counts of 0).

## Files Affected

1. `src/tools/files.py` — add `get_note_info` + `_extract_headings` helper
2. `src/mcp_server.py` — register tool
3. `src/services/compaction.py` — add stub builder
4. `tests/test_tools_files.py` — new test cases
5. `CLAUDE.md` — update tool table
6. `system_prompt.txt.example` — add to tool reference

## Success Criteria

- `get_note_info("Meetings/2026-02-20 Standup.md")` returns all metadata fields
- Frontmatter is correctly parsed for files with and without frontmatter
- Headings are extracted correctly (respecting code fences)
- File size, modified, created timestamps are accurate
- Works for files in any vault folder
- Non-markdown files return sensible defaults (empty frontmatter/headings, 0 link counts)
- Returns clear error for non-existent files
