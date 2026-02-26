# Unify find_backlinks + find_outlinks into find_links

**Issue**: #128
**Date**: 2026-02-26

## Summary

Replace `find_backlinks` and `find_outlinks` with a single `find_links` tool that uses a `direction` parameter. -1 MCP tool from schema.

## API

```python
find_links(
    path: str,                          # relative or absolute vault path
    direction: str = "both",            # "backlinks" | "outlinks" | "both"
    limit: int = LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> str  # JSON via ok()/err()
```

**`path` handling**: For backlinks, derive note name internally — `Path(resolved).stem` (strips directory + `.md`). For outlinks, use `resolve_file()` as today. Both directions resolve the path first, so invalid paths fail early regardless of direction.

## Response shapes

**direction="backlinks"**:
```json
{"success": true, "results": ["folder/note2.md", ...], "total": 5}
```

**direction="outlinks"**:
```json
{"success": true, "results": [{"name": "note1", "path": "note1.md"}, ...], "total": 3}
```

**direction="both"** — separate sections, each independently paginated:
```json
{
  "success": true,
  "backlinks": {"results": [...], "total": 5},
  "outlinks": {"results": [...], "total": 3}
}
```

## Internal structure

Keep `_scan_backlinks()` and outlink helpers (`_build_note_path_map`, `_resolve_link`) as-is. New `find_links()` dispatches based on `direction`. Remove `find_backlinks`/`find_outlinks` as public functions.

## Compaction

Replace `find_backlinks`/`find_outlinks` entries with `find_links` → `_build_list_stub`.

## Files affected

1. `src/tools/links.py` — new `find_links`, remove old public functions
2. `src/mcp_server.py` — register `find_links` instead of two tools
3. `src/services/compaction.py` — replace two entries with `find_links`
4. `tests/test_tools_links.py` — adapt tests to `find_links(path, direction=...)`
5. `tests/test_session_management.py` — update compaction test references
6. `system_prompt.txt.example` — update tool reference + decision tree
7. `CLAUDE.md` — update tool table
