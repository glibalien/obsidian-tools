# System Prompt Redesign

## Problem

The agent frequently chooses the wrong tool for a given user intent — defaulting to `search_vault` when `list_files_by_frontmatter`, `find_backlinks`, or direct `read_file` would be more appropriate. Additionally, several tool capabilities are undocumented in the prompt (pagination, `get_continuation`, search heading metadata), leading to suboptimal tool usage.

## Goals

1. Fix wrong tool selection by adding a decision tree that maps user intent to the correct tool
2. Document all tool parameters and capabilities accurately
3. Document truncation handling (`get_continuation` + `read_file` pagination)
4. Tailor vault structure section to actual vault (remove customize comment)
5. Condense verbose sections to offset additions

## Non-Goals

- Restructuring around task-oriented sections (approach 3 — rejected as harder to maintain)
- Changing tool implementations or behavior
- Adding new tools

## Design

### Prompt Structure

```
1. Opening + Vault Structure (tailored)
2. Vault Relationships
3. Choosing the Right Tool        ← NEW
4. Vault Navigation Strategy      ← CONDENSED
5. Available Tools                 ← GAPS FILLED
6. Handling Large Results          ← NEW
7. Tool Usage Guidelines
8. Interaction Logging             ← TIGHTENED
```

### Section 1: Opening + Vault Structure

Remove the `# CUSTOMIZE THIS SECTION` comment block (6 lines). The folder list is already tailored to the actual vault. No other changes.

### Section 2: Vault Relationships

Unchanged.

### Section 3: Choosing the Right Tool (NEW)

Decision tree with three parts:

**Intent-to-tool table:**

| User intent | Tool | Why |
|---|---|---|
| "Find all notes tagged/categorized as X" | list_files_by_frontmatter | Exhaustive scan by metadata |
| "Find notes about X" / conceptual query | search_vault | Relevance-ranked content search |
| "What links to X" / tasks for a project | find_backlinks | Structural relationships via wikilinks |
| "What does X link to" | find_outlinks | Extract wikilinks from a specific file |
| "List files in folder X" | search_by_folder | Direct folder listing |
| "Notes from last week" / date queries | search_by_date_range | Date-based filtering |
| "Read/show me this file" | read_file | Direct file access |
| "Today's daily note" | read_file("Daily Notes/YYYY-MM-DD.md") | Known path, skip search |

**Key distinctions:**
- Exhaustive vs relevant (frontmatter/folder/backlinks vs search_vault)
- Structural vs textual (wikilinks invisible to text search)
- Known path vs discovery (read_file vs search)

**Common mistakes to avoid:**
- search_vault for "all tasks for project X" → use find_backlinks
- search_vault for "all meetings" → use list_files_by_frontmatter
- Searching for today's daily note → read by path

### Section 4: Vault Navigation Strategy (CONDENSED)

Reduced from 42 lines to 22 lines. Removes repetition of the backlinks-vs-search point (now in section 3). Emphasizes:
- Link index is fast (pre-built, O(1))
- find_backlinks catches frontmatter wikilinks that search misses
- find_outlinks maps a note's context
- "Always explore backlinks" for summarization/review tasks
- Answer from search results when heading metadata matches; read_file only for additional context
- Use read_file directly for known files, not search

### Section 5: Available Tools (GAPS FILLED)

Same grouping as current prompt. Changes:

- **search_vault**: Added heading field documentation — results include a "heading" field showing which section each chunk came from
- **list_files_by_frontmatter**: Added match_type parameter ("contains"/"equals"), limit/offset pagination, total count
- **search_by_date_range**: Added limit/offset pagination, total count
- **search_by_folder**: Added limit/offset pagination, total count
- **find_backlinks**: Added limit/offset pagination, total count
- **find_outlinks**: Clarified takes file path not note name, added limit/offset pagination, total count
- **batch_move_files**: Added parameter format example `[{"source": "...", "destination": "..."}]`
- **replace_section**: Specified replaces heading AND content, heading needs # symbols, case-insensitive
- **append_to_section**: Specified preserves heading and existing content, same heading format
- **update_frontmatter**: Added JSON value syntax, operation details, append creates list/skips dupes
- **batch_update_frontmatter**: Added continues-after-failure behavior, summary response
- **create_file**: Added frontmatter JSON example

### Section 6: Handling Large Results (NEW)

Three subsections:

1. **Pagination on list tools**: limit/offset pattern, total count, how to page
2. **Truncated tool results**: `get_continuation(tool_call_id, offset)` for any tool result >4000 chars
3. **Truncated read_file results**: read_file's own offset/length pagination (separate from get_continuation)

Explicitly distinguishes the two truncation mechanisms to prevent confusion.

### Section 7: Tool Usage Guidelines

Unchanged.

### Section 8: Interaction Logging

Tightened from 17 to 14 lines. Same content, less repetition.

## Size Impact

- Added: ~30 lines (decision tree + truncation handling)
- Removed: ~20 lines (navigation condensing + logging tightening + comment block)
- Net: ~+10 lines

## Success Criteria

- [ ] Agent uses list_files_by_frontmatter instead of search_vault for exhaustive queries
- [ ] Agent uses find_backlinks for structural relationship queries (tasks for a project)
- [ ] Agent reads daily notes by path instead of searching
- [ ] Agent uses get_continuation when tool results are truncated
- [ ] Agent uses read_file offset when file content is truncated
- [ ] Agent leverages search result heading metadata before calling read_file
- [ ] All MCP tool parameters accurately documented

## Testing

Manual testing — observe agent behavior across these query types:
1. "Find all tasks for project X" → should use find_backlinks, not search_vault
2. "Find all meetings" → should use list_files_by_frontmatter
3. "What did I work on last week?" → should use search_by_date_range
4. "Show me today's daily note" → should read by path
5. Long file read → should page through with offset
6. Truncated search results → should use get_continuation
