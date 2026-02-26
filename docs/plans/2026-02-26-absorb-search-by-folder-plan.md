# Absorb search_by_folder into list_files Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove `search_by_folder` by making `field` optional in `list_files_by_frontmatter`, rename to `list_files`. -1 MCP tool.

**Architecture:** Make `field` default to `""` so folder-only calls work. Add validation requiring at least `field` or `folder`. Rename everywhere. The internal `_find_matching_files` already handles `field=None`.

**Tech Stack:** Python, FastMCP, pytest

---

### Task 1: Rename `list_files_by_frontmatter` → `list_files` and make `field` optional

**Files:**
- Modify: `src/tools/frontmatter.py:228-293`

**Step 1: Rename function and update signature**

In `src/tools/frontmatter.py`, rename the function and make `field` optional:

```python
def list_files(
    field: str = "",
    value: str = "",
    match_type: str = "contains",
    filters: list[FilterCondition] | None = None,
    include_fields: list[str] | None = None,
    folder: str = "",
    recursive: bool = False,
    limit: int = LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """List vault files, optionally filtered by frontmatter and/or folder. Use for "list files in folder X", "find notes with field=Y", or combined queries.

    Args:
        field: Frontmatter field name to match (e.g., 'tags', 'project', 'category').
            Optional — omit to list all files in a folder without filtering.
        value: Value to match against. Wikilink brackets are stripped automatically.
            Not required for 'missing' or 'exists' match types.
        match_type: How to match the field value:
            'contains' - substring/list member match (default).
            'equals' - exact match.
            'missing' - field is absent (value ignored).
            'exists' - field is present with any value (value ignored).
            'not_contains' - field is absent or doesn't contain value.
            'not_equals' - field is absent or doesn't equal value.
        filters: Additional AND conditions. Each needs 'field', optional 'value', and optional 'match_type'.
        include_fields: Field names whose values to return with each result, e.g. ["status", "scheduled"].
        folder: Restrict search to files within this folder (relative to vault root).
            When field is omitted, lists all files in this folder.
        recursive: Include subfolders when folder is set (default false). Set true to include subfolders.

    Returns:
        JSON with results (file paths or objects when include_fields is set) and total count.
    """
```

**Step 2: Update validation logic in the function body**

Replace the existing validation block (lines 260-266) with:

```python
    if not field and not folder:
        return err("At least one of 'field' or 'folder' is required")

    if field:
        if match_type not in VALID_MATCH_TYPES:
            return err(
                f"match_type must be one of {VALID_MATCH_TYPES}, got '{match_type}'"
            )

        if match_type not in NO_VALUE_MATCH_TYPES and not value:
            return err(f"value is required for match_type '{match_type}'")
```

**Step 3: Update the "no results" message**

Replace the hardcoded message (line 289) with a dynamic one:

```python
    if not matching:
        if field:
            msg = f"No files found where {field} {match_type} '{value}'"
        else:
            mode = "recursively " if recursive else ""
            msg = f"No markdown files found {mode}in {folder}"
        return ok(msg, results=[], total=0)

    total = len(matching)
    page = matching[validated_offset:validated_offset + validated_limit]
    return ok(f"Found {total} matching files", results=page, total=total)
```

**Step 4: Pass `field` as `None` (not empty string) to `_find_matching_files`**

Update the call to pass `None` when field is empty, matching the existing folder-only convention:

```python
    matching = _find_matching_files(
        field or None, value, match_type, parsed_filters, parsed_include, folder_path, recursive
    )
```

**Step 5: Run existing tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v`
Expected: All existing tests pass (they all pass `field` explicitly).

**Step 6: Commit**

```bash
git add src/tools/frontmatter.py
git commit -m "feat: rename list_files_by_frontmatter to list_files, make field optional"
```

---

### Task 2: Write tests for folder-only mode and validation

**Files:**
- Modify: `tests/test_tools_frontmatter.py`

**Step 1: Write new tests**

Add a new test class to `tests/test_tools_frontmatter.py`. First, update the import at the top to use `list_files` instead of `list_files_by_frontmatter`:

```python
from tools.frontmatter import (
    ...,
    list_files,   # was list_files_by_frontmatter
    ...
)
```

Then rename the existing `list_files_by_frontmatter` references throughout the file to `list_files` (find-and-replace).

Add the new test class:

```python
class TestListFilesFolderOnly:
    """Tests for list_files folder-only mode (no field)."""

    def test_folder_only_basic(self, vault_config):
        """Should list markdown files in folder without requiring field."""
        result = json.loads(list_files(folder="projects"))
        assert result["success"] is True
        assert any("project1.md" in f for f in result["results"])

    def test_folder_only_recursive(self, vault_config):
        """Should include subfolders when recursive=True."""
        result = json.loads(list_files(folder=".", recursive=True))
        assert result["success"] is True
        assert any("note1.md" in f for f in result["results"])
        assert any("project1.md" in f for f in result["results"])

    def test_folder_only_non_recursive(self, vault_config):
        """Should not include subfolders when recursive=False."""
        result = json.loads(list_files(folder=".", recursive=False))
        assert result["success"] is True
        assert any("note1.md" in f for f in result["results"])
        assert not any("projects/" in f for f in result["results"])

    def test_folder_not_found(self, vault_config):
        """Should return error for missing folder."""
        result = json.loads(list_files(folder="nonexistent"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_folder_empty(self, vault_config):
        """Should return message for empty folder."""
        (vault_config / "empty_folder").mkdir()
        result = json.loads(list_files(folder="empty_folder"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No markdown files found" in result["message"]

    def test_no_field_no_folder_error(self, vault_config):
        """Should return error when neither field nor folder is provided."""
        result = json.loads(list_files())
        assert result["success"] is False
        assert "field" in result["error"].lower()
        assert "folder" in result["error"].lower()

    def test_folder_only_pagination(self, vault_config):
        """Folder-only mode should respect limit and offset."""
        for i in range(5):
            (vault_config / f"page_test_{i}.md").write_text(f"# Page {i}")
        result = json.loads(list_files(folder=".", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] >= 5

    def test_folder_only_offset_beyond_results(self, vault_config):
        """Offset beyond results returns empty list with correct total."""
        result = json.loads(list_files(folder=".", limit=100, offset=9999))
        assert result["success"] is True
        assert result["results"] == []
        assert result["total"] >= 1

    def test_folder_with_field_filter(self, vault_config):
        """Folder + field should scope the frontmatter query to that folder."""
        result = json.loads(list_files(
            folder="projects", field="status", value="active", match_type="equals"
        ))
        assert result["success"] is True
        # This tests the combined mode works (same as before)
```

**Step 2: Run new tests**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v -k "TestListFilesFolderOnly"`
Expected: All pass.

**Step 3: Commit**

```bash
git add tests/test_tools_frontmatter.py
git commit -m "test: add folder-only mode tests for list_files"
```

---

### Task 3: Remove `search_by_folder` and update registrations

**Files:**
- Modify: `src/tools/links.py` — remove `search_by_folder` function (lines 153-196)
- Modify: `src/mcp_server.py` — remove `search_by_folder` import and registration
- Modify: `src/tools/__init__.py` — remove `search_by_folder`, rename `list_files_by_frontmatter` → `list_files`
- Modify: `src/services/compaction.py` — remove `search_by_folder` entry, rename `list_files_by_frontmatter` → `list_files`

**Step 1: Remove `search_by_folder` from `src/tools/links.py`**

Delete the `search_by_folder` function (lines 153-196). Also remove `EXCLUDED_DIRS` and `LIST_DEFAULT_LIMIT` from the imports if they're only used by `search_by_folder` — check first.

`EXCLUDED_DIRS` is not used elsewhere in links.py (backlinks uses `get_vault_files` which filters internally, compare_folders uses `_scan_folder` which imports it). Actually `_scan_folder` does use `EXCLUDED_DIRS` so keep it. `LIST_DEFAULT_LIMIT` is used by `find_backlinks` and `find_outlinks`. Keep both imports.

**Step 2: Update `src/mcp_server.py`**

- Remove `search_by_folder` from the `tools.links` import
- Change `list_files_by_frontmatter` to `list_files` in the `tools.frontmatter` import
- Change `mcp.tool()(list_files_by_frontmatter)` to `mcp.tool()(list_files)`
- Remove `mcp.tool()(search_by_folder)`

**Step 3: Update `src/tools/__init__.py`**

- Remove `search_by_folder` from the `tools.links` import and `__all__`
- Change `list_files_by_frontmatter` to `list_files` in the `tools.frontmatter` import and `__all__`

**Step 4: Update `src/services/compaction.py`**

- Remove the `"search_by_folder": _build_list_stub` entry
- Change `"list_files_by_frontmatter": _build_list_stub` to `"list_files": _build_list_stub`

**Step 5: Remove `search_by_folder` tests from `tests/test_tools_links.py`**

- Remove `search_by_folder` from the import
- Delete `TestSearchByFolder` class (lines 161-198)
- Delete `test_search_by_folder_pagination` (lines 417-425)
- Delete `test_pagination_offset_beyond_results` (lines 427-432) — this is a search_by_folder test
- Update the parametrized `test_paginated_link_tools_reject_invalid_pagination` (lines 441-457): remove the `search_by_folder` call and assertion from it

**Step 6: Update `tests/test_session_management.py`**

Change the tool list in the compaction test (line 159-160):
```python
        for tool in ["find_backlinks", "find_outlinks",
                      "list_files", "search_by_date_range"]:
```

**Step 7: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass. No references to `search_by_folder` remain.

**Step 8: Commit**

```bash
git add src/tools/links.py src/mcp_server.py src/tools/__init__.py src/services/compaction.py tests/test_tools_links.py tests/test_session_management.py
git commit -m "refactor: remove search_by_folder, absorbed into list_files"
```

---

### Task 4: Update documentation

**Files:**
- Modify: `system_prompt.txt.example`
- Modify: `CLAUDE.md`

**Step 1: Update `system_prompt.txt.example`**

- In the decision tree table, change the `search_by_folder` row:
  - `"List files in folder X" | search_by_folder | ...` → `"List files in folder X" | list_files (folder only) | Direct folder listing`
- Rename all `list_files_by_frontmatter` → `list_files` (there are ~5 occurrences)
- In the tool descriptions section, remove the `search_by_folder` bullet and update the `list_files` bullet:
  ```
  - list_files: List and filter vault files. Supports folder-only listing (just pass folder),
    frontmatter filtering (pass field, value, match_type), or combined (folder + field to scope queries).
    Supports limit/offset pagination.
  ```
- In the "Handling Large Results" section, update the tool list: replace `search_by_folder, list_files_by_frontmatter` with `list_files`
- In the batch_update_frontmatter section, change `list_files_by_frontmatter filters` → `list_files filters`

**Step 2: Update `CLAUDE.md`**

- In the MCP Tools table: remove `search_by_folder` row, rename `list_files_by_frontmatter` → `list_files`, update description to mention folder-only mode
- Update the "Handling Large Results" mention in the notes if present

**Step 3: Run tests once more (sanity check)**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass.

**Step 4: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: update tool references for list_files rename"
```
