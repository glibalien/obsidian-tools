# batch_create_files Design

## Problem

Creating multiple files requires serial LLM round-trips (existence check + create per file), compounded by frontmatter serialization retries. A 7-file batch costs ~18 calls / 225K tokens.

## Solution

New `batch_create_files` MCP tool that creates multiple files in a single call.

## Approach

Option A — reuse `create_file` internally. Check existence before calling, classify results into created/skipped/errors. No refactoring of existing code.

## Signature

```python
def batch_create_files(
    files: list[dict],       # [{path, content?, frontmatter?}, ...]
    skip_existing: bool = True,
    confirm: bool = False,
) -> str:
```

- `files` — each item has `path` (required), `content` (optional, default ""), `frontmatter` (optional, native dict)
- `skip_existing` — `True` (default): skip existing files as "skipped"; `False`: report as errors
- `confirm` — standard two-step confirmation gate

## Flow

1. Validate `files` is non-empty list with `path` in each item
2. Convert each `frontmatter` dict to JSON string (since `create_file` accepts `str | None`)
3. If `len(files) > BATCH_CONFIRM_THRESHOLD` and not confirmed: `store_preview`, return preview
4. If `confirm=True`: `consume_preview`, proceed
5. For each file: check existence via `resolve_vault_path` → skip/error if exists, else call `create_file()`
6. Return `ok()` with created/skipped/errors lists + summary string

## Response Structure

```json
{
  "success": true,
  "created": ["People/Alice.md", "People/Bob.md"],
  "skipped": ["People/Charlie.md"],
  "errors": [{"path": "../../escape.md", "error": "Path outside vault"}],
  "summary": "Created 2, skipped 1, 0 errors"
}
```

## Confirmation Preview

Key: `("batch_create_files", tuple(sorted(paths)))`. Returns `ok()` with `confirmation_required=True`, `preview_message`, `files` list.

## Compaction Stub

Custom stub builder keeping counts only:

```json
{"status": "ok", "created_count": 2, "skipped_count": 1, "error_count": 0}
```

## Touchpoints

1. `src/tools/files.py` — new function
2. `src/mcp_server.py` — register tool
3. `src/services/compaction.py` — add stub builder
4. `system_prompt.txt.example` — add to tool reference
5. `tests/test_tools_files.py` — tests

## Test Cases

- Create multiple files successfully
- Skip existing files with `skip_existing=True`
- Error on existing files with `skip_existing=False`
- Frontmatter as dict (not string)
- Confirmation gate triggers above threshold
- Confirmation gate bypass with `confirm=True` after preview
- Empty files list
- Mixed success/failure (some paths invalid)
- Directory creation for nested paths
