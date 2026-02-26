# Design: Unify section tools + append_to_file into single edit_file tool

**Issue**: #127
**Date**: 2026-02-26

## Summary

Replace `prepend_to_file`, `replace_section`, `append_to_section`, and `append_to_file` with a single `edit_file` tool in a renamed module (`sections.py` → `editing.py`).

## API

```python
def edit_file(path: str, content: str, position: str, heading: str | None = None, mode: str | None = None) -> str:
```

| `position` | `heading` | `mode` | Behavior |
|---|---|---|---|
| `"prepend"` | ignored | ignored | Insert after frontmatter |
| `"append"` | ignored | ignored | Append to end of file |
| `"section"` | required | `"replace"` | Replace heading + content |
| `"section"` | required | `"append"` | Append to end of section |

Validation (early `err()` returns):
- Unknown `position` → error
- `position="section"` without `heading` → error
- `position="section"` without valid `mode` → error

## Internal Structure

One `edit_file` entry point validates params then dispatches to private helpers: `_prepend`, `_append`, `_section_replace`, `_section_append`. Each helper handles resolve/read/write independently (~15-20 lines each).

## Files Affected

- `src/tools/sections.py` → `src/tools/editing.py` (rewrite)
- `src/tools/files.py` — remove `append_to_file`
- `src/mcp_server.py` — import/register `edit_file` from `editing`, remove 4 old registrations
- `system_prompt.txt.example` — update tool reference and decision tree
- `CLAUDE.md` — update tool table
- `tests/test_tools_sections.py` — adapt tests to `edit_file` API
- `tests/test_tools_files.py` — remove `TestAppendToFile`, update imports

No compaction changes needed (these tools use the generic fallback).
