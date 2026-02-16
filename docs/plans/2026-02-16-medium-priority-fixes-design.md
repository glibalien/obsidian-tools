# Medium-Priority Fixes Design

Addresses the four medium-priority issues from `docs/analyses/tool_analysis.txt`.

## 1. read_file Missing UTF-8 Encoding (2b)

**Problem:** `files.py:35` calls `file_path.read_text()` without specifying encoding. Uses system default, which may not be UTF-8 on all platforms. Other tools consistently use `encoding="utf-8", errors="ignore"`.

**Fix:** Change to `file_path.read_text(encoding="utf-8", errors="ignore")`.

## 2. index_file One-at-a-Time Upserts (1e)

**Problem:** `index_vault.py` upserts each chunk individually with a separate ChromaDB call. For a file with 10 chunks, that's 10 round-trips.

**Fix:** Collect all `ids`, `documents`, and `metadatas` for a file's chunks, then call `collection.upsert()` once with all of them.

## 3. find_backlinks Link Index (1b)

**Problem:** `find_backlinks` reads every `.md` file in the vault to search for wikilinks. O(n) per call.

**Fix:** Build a link index during vault indexing.

- **Index format:** `{note_name_lower: [relative_paths_that_link_to_it]}` stored as JSON
- **Location:** `CHROMA_PATH / "link_index.json"`
- **Built by:** `index_vault` scans wikilinks from each file being indexed, writes the full index after processing all files
- **Used by:** `find_backlinks` loads the JSON file and looks up the note name (case-insensitive)
- **Fallback:** If the index file doesn't exist, falls back to current O(n) scan for graceful degradation
- **Freshness:** Rebuilt on every indexer run (same as ChromaDB)

## 4. Pagination on List Tools (5c)

**Problem:** `find_backlinks`, `find_outlinks`, `list_files_by_frontmatter`, `search_by_folder`, and `search_by_date_range` return all results. For large vaults, results could exceed `MAX_TOOL_RESULT_CHARS` and get truncated, losing data silently.

**Fix:** Add `limit` (default 100) and `offset` (default 0) parameters to each tool. Include `total` count in response.

### Response format

```json
{"success": true, "results": ["file1.md", "file2.md"], "total": 250}
```

When `total > offset + limit`, the agent can request the next page.

### Tools to update

- `find_backlinks(note_name, limit=100, offset=0)`
- `find_outlinks(path, limit=100, offset=0)`
- `list_files_by_frontmatter(field, value, match_type, limit=100, offset=0)`
- `search_by_folder(folder, recursive, limit=100, offset=0)`
- `search_by_date_range(start_date, end_date, date_type, limit=100, offset=0)`

### Pagination logic (shared pattern)

Each tool collects all matching results into a list, then applies:
```python
total = len(all_results)
page = all_results[offset:offset + limit]
return ok(results=page, total=total)
```
