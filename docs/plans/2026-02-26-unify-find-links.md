# Unify find_backlinks + find_outlinks into find_links — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `find_backlinks` and `find_outlinks` with a single `find_links(path, direction)` tool, reducing the MCP schema by one tool.

**Architecture:** New `find_links()` dispatches to existing `_scan_backlinks()` and outlink helpers based on `direction` param ("backlinks"/"outlinks"/"both"). Path is resolved once upfront; note name derived via `Path.stem` for backlink scanning. "both" mode returns separate `backlinks`/`outlinks` sections with independent pagination.

**Tech Stack:** Python, pytest, MCP (FastMCP)

**Issue:** #128

---

### Task 1: Implement find_links and remove old public functions

**Files:**
- Modify: `src/tools/links.py:15-107`

**Step 1: Replace `find_backlinks` and `find_outlinks` with `find_links`**

Delete the `find_backlinks` function (lines 15–44) and `find_outlinks` function (lines 64–107). Replace with a single `find_links` function. Keep all private helpers (`_scan_backlinks`, `_build_note_path_map`, `_resolve_link`) unchanged.

```python
def find_links(
    path: str,
    direction: str = "both",
    limit: int = LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Find links to or from a vault note.

    Args:
        path: Path to the note (relative to vault or absolute).
        direction: "backlinks" (files linking to this note),
                   "outlinks" (wikilinks from this note),
                   or "both" (both in one call).
        limit: Maximum results per direction (default 500).
        offset: Results to skip per direction (default 0).

    Returns:
        JSON response with link results. Backlinks are file path strings,
        outlinks are {name, path} objects. "both" returns separate sections.
    """
    if direction not in ("backlinks", "outlinks", "both"):
        return err(f"Invalid direction: {direction}. Must be 'backlinks', 'outlinks', or 'both'")

    file_path, error = resolve_file(path)
    if error:
        return err(error)

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    if direction == "backlinks":
        return _get_backlinks(file_path, validated_offset, validated_limit)

    if direction == "outlinks":
        return _get_outlinks(file_path, path, validated_offset, validated_limit)

    # direction == "both"
    backlinks_data = _backlinks_data(file_path, validated_offset, validated_limit)
    outlinks_data = _outlinks_data(file_path, path, validated_offset, validated_limit)
    return ok(backlinks=backlinks_data, outlinks=outlinks_data)


def _get_backlinks(file_path: Path, offset: int, limit: int) -> str:
    """Return paginated backlinks as a top-level ok() response."""
    note_name = file_path.stem
    all_results = _scan_backlinks(note_name)
    if not all_results:
        return ok(f"No backlinks found to [[{note_name}]]", results=[], total=0)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _get_outlinks(file_path: Path, display_path: str, offset: int, limit: int) -> str:
    """Return paginated outlinks as a top-level ok() response."""
    all_results = _extract_outlinks(file_path)
    if all_results is None:
        return err(f"Reading file failed: {display_path}")
    if not all_results:
        return ok(f"No outlinks found in {display_path}", results=[], total=0)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _backlinks_data(file_path: Path, offset: int, limit: int) -> dict:
    """Return backlinks as a dict for embedding in 'both' response."""
    note_name = file_path.stem
    all_results = _scan_backlinks(note_name)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return {"results": page, "total": total}


def _outlinks_data(file_path: Path, display_path: str, offset: int, limit: int) -> dict:
    """Return outlinks as a dict for embedding in 'both' response."""
    all_results = _extract_outlinks(file_path) or []
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return {"results": page, "total": total}


def _extract_outlinks(file_path: Path) -> list[dict] | None:
    """Extract and resolve wikilinks from a file. Returns None on read error."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Failed to read %s for outlinks: %s", file_path, e)
        return None

    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)
    if not matches:
        return []

    stem_map, path_map = _build_note_path_map()
    unique_names = sorted(set(matches))
    return [
        {"name": name, "path": _resolve_link(name, stem_map, path_map)}
        for name in unique_names
    ]
```

**Step 2: Run all link tests to see them fail (imports broken)**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py -v --tb=short 2>&1 | head -30`
Expected: ImportError — `find_backlinks` and `find_outlinks` no longer exist.

**Step 3: Commit**

```
feat: replace find_backlinks/find_outlinks with find_links (#128)
```

---

### Task 2: Update MCP server registration

**Files:**
- Modify: `src/mcp_server.py:33-34,74-75`

**Step 1: Update import**

Change:
```python
from tools.links import (
    find_backlinks,
    find_outlinks,
```
To:
```python
from tools.links import (
    find_links,
```

**Step 2: Update registration**

Change:
```python
mcp.tool()(find_backlinks)
mcp.tool()(find_outlinks)
```
To:
```python
mcp.tool()(find_links)
```

**Step 3: Commit**

```
refactor: register find_links in MCP server (#128)
```

---

### Task 3: Update compaction stub builders

**Files:**
- Modify: `src/services/compaction.py:119-122`

**Step 1: Replace entries in `_TOOL_STUB_BUILDERS`**

Change:
```python
    "find_backlinks": _build_list_stub,
    "find_outlinks": _build_list_stub,
```
To:
```python
    "find_links": _build_list_stub,
```

**Step 2: Update compaction test references**

In `tests/test_session_management.py:159`, change:
```python
        for tool in ["find_backlinks", "find_outlinks", "list_files",
                      "search_by_date_range"]:
```
To:
```python
        for tool in ["find_links", "list_files",
                      "search_by_date_range"]:
```

At line 175, change:
```python
        stub = build_tool_stub(content, "find_backlinks")
```
To:
```python
        stub = build_tool_stub(content, "find_links")
```

**Step 3: Run compaction tests**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestToolStubBuilders -v`
Expected: PASS

**Step 4: Commit**

```
refactor: update compaction stubs for find_links (#128)
```

---

### Task 4: Adapt tests to new find_links API

**Files:**
- Modify: `tests/test_tools_links.py`

**Step 1: Update imports**

Change:
```python
from tools.links import (
    compare_folders,
    find_backlinks,
    find_outlinks,
)
```
To:
```python
from tools.links import (
    compare_folders,
    find_links,
)
```

**Step 2: Rewrite TestFindBacklinks to use find_links**

```python
class TestFindBacklinks:
    """Tests for find_links with direction='backlinks'."""

    def test_find_backlinks_basic(self, vault_config):
        """Should find files that link to a note."""
        result = json.loads(find_links("note1.md", direction="backlinks"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_alias_links(self, vault_config):
        """Should find links with aliases."""
        result = json.loads(find_links("note3.md", direction="backlinks"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_none_found(self, vault_config):
        """Should return message when no backlinks found."""
        (vault_config / "lonely.md").write_text("# No links here")
        result = json.loads(find_links("lonely.md", direction="backlinks"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No backlinks found" in result["message"]

    def test_find_backlinks_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(find_links("nonexistent.md", direction="backlinks"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()
```

Note: the old `test_find_backlinks_empty_name` and `test_find_backlinks_with_extension` tests are dropped — empty path is handled by `resolve_file()` (returns "not found"), and extension stripping is now internal (always uses `Path.stem`).

**Step 3: Rewrite TestFindBacklinksPagination**

```python
class TestFindBacklinksPagination:
    """Tests for find_links backlinks pagination."""

    def test_pagination_limit(self, vault_config):
        """Should respect limit parameter."""
        result = json.loads(find_links("note1.md", direction="backlinks", limit=1))
        assert result["success"] is True
        assert len(result["results"]) <= 1
        assert result["total"] >= 1

    def test_pagination_offset(self, vault_config):
        """Should respect offset parameter."""
        full = json.loads(find_links("note1.md", direction="backlinks"))
        total = full["total"]
        result = json.loads(find_links("note1.md", direction="backlinks", offset=total))
        assert result["results"] == [] or len(result["results"]) == 0
```

**Step 4: Rewrite TestFindOutlinks**

Replace every `find_outlinks("path")` call with `find_links("path", direction="outlinks")`. The assertions stay the same — response shape is identical.

```python
class TestFindOutlinks:
    """Tests for find_links with direction='outlinks'."""

    def test_find_outlinks_basic(self, vault_config):
        result = json.loads(find_links("note2.md", direction="outlinks"))
        assert result["success"] is True
        names = [r["name"] for r in result["results"]]
        assert "note1" in names
        assert "note3" in names
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert by_name["note1"] == "note1.md"
        assert by_name["note3"] == "note3.md"

    def test_find_outlinks_unresolved_link(self, vault_config):
        result = json.loads(find_links("note1.md", direction="outlinks"))
        assert result["success"] is True
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert "wikilink" in by_name
        assert by_name["wikilink"] is None

    def test_find_outlinks_heading_suffix(self, vault_config):
        (vault_config / "heading_links.md").write_text(
            "See [[note1#Section A]] for details."
        )
        result = json.loads(find_links("heading_links.md", direction="outlinks"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "note1#Section A"
        assert link["path"] == "note1.md"

    def test_find_outlinks_subfolder_resolution(self, vault_config):
        (vault_config / "links_to_project.md").write_text(
            "Check [[project1]] for status."
        )
        result = json.loads(find_links("links_to_project.md", direction="outlinks"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "project1"
        assert link["path"] == "projects/project1.md"

    def test_find_outlinks_none_found(self, vault_config):
        result = json.loads(find_links("note3.md", direction="outlinks"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No outlinks found" in result["message"]

    def test_find_outlinks_file_not_found(self, vault_config):
        result = json.loads(find_links("nonexistent.md", direction="outlinks"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_find_outlinks_deduplicates(self, vault_config):
        (vault_config / "dupes.md").write_text(
            "[[same]] and [[same]] and [[same|alias]]"
        )
        result = json.loads(find_links("dupes.md", direction="outlinks"))
        assert result["success"] is True
        names = [r["name"] for r in result["results"]]
        assert names.count("same") == 1

    def test_find_outlinks_folder_qualified_with_colliding_stems(self, vault_config):
        (vault_config / "foo.md").write_text("# Root foo")
        sub = vault_config / "sub"
        sub.mkdir()
        (sub / "foo.md").write_text("# Sub foo")
        (vault_config / "qualifier_test.md").write_text(
            "[[foo]] and [[sub/foo]]"
        )
        result = json.loads(find_links("qualifier_test.md", direction="outlinks"))
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert by_name["foo"] == "foo.md"
        assert by_name["sub/foo"] == "sub/foo.md"
```

**Step 5: Rewrite outlinks pagination test and parametrized validation test**

```python
class TestListToolPagination:
    """Tests for limit/offset pagination on list tools."""

    def test_find_outlinks_pagination(self, vault_config):
        links = " ".join(f"[[note{i}]]" for i in range(10))
        (vault_config / "many_links.md").write_text(f"# Links\n\n{links}")

        result = json.loads(find_links("many_links.md", direction="outlinks", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] == 10

        result2 = json.loads(find_links("many_links.md", direction="outlinks", limit=3, offset=3))
        assert len(result2["results"]) == 3
        assert result2["total"] == 10

    def test_default_pagination_includes_total(self, vault_config):
        result = json.loads(find_links("note1.md", direction="outlinks"))
        assert result["success"] is True
        assert "total" in result


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"offset": -1}, "offset must be >= 0"),
        ({"limit": 0}, "limit must be >= 1"),
        ({"limit": 2001}, "limit must be <= 2000"),
    ],
)
def test_paginated_link_tools_reject_invalid_pagination(vault_config, kwargs, expected_error):
    """find_links should return pagination validation errors for all directions."""
    for direction in ("backlinks", "outlinks", "both"):
        result = json.loads(find_links("note1.md", direction=direction, **kwargs))
        assert result["success"] is False
        assert expected_error in result["error"], f"Failed for direction={direction}"
```

**Step 6: Add tests for direction="both" and invalid direction**

```python
class TestFindLinksBoth:
    """Tests for find_links with direction='both'."""

    def test_both_returns_backlinks_and_outlinks(self, vault_config):
        """Should return both sections in one call."""
        result = json.loads(find_links("note2.md", direction="both"))
        assert result["success"] is True
        assert "backlinks" in result
        assert "outlinks" in result
        assert isinstance(result["backlinks"]["results"], list)
        assert isinstance(result["outlinks"]["results"], list)
        assert "total" in result["backlinks"]
        assert "total" in result["outlinks"]

    def test_both_pagination(self, vault_config):
        """Pagination should apply to both sections."""
        result = json.loads(find_links("note2.md", direction="both", limit=1))
        assert result["success"] is True
        assert len(result["backlinks"]["results"]) <= 1
        assert len(result["outlinks"]["results"]) <= 1

    def test_both_file_not_found(self, vault_config):
        """Should error for missing file in both mode."""
        result = json.loads(find_links("nonexistent.md", direction="both"))
        assert result["success"] is False


class TestFindLinksValidation:
    """Tests for find_links input validation."""

    def test_invalid_direction(self, vault_config):
        """Should reject invalid direction values."""
        result = json.loads(find_links("note1.md", direction="invalid"))
        assert result["success"] is False
        assert "Invalid direction" in result["error"]

    def test_default_direction_is_both(self, vault_config):
        """Default direction should be 'both'."""
        result = json.loads(find_links("note2.md"))
        assert result["success"] is True
        assert "backlinks" in result
        assert "outlinks" in result
```

**Step 7: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py tests/test_session_management.py -v`
Expected: ALL PASS

**Step 8: Commit**

```
test: adapt link tests for find_links API (#128)
```

---

### Task 5: Update system prompt and CLAUDE.md

**Files:**
- Modify: `system_prompt.txt.example`
- Modify: `CLAUDE.md`

**Step 1: Update system_prompt.txt.example**

Replace all references to `find_backlinks` and `find_outlinks` with `find_links`. Key changes:

- Decision tree row: merge the two rows into one:
  ```
  | "What links to/from X" / relationship discovery | find_links | Structural relationships via wikilinks — use direction param |
  ```

- Vault Navigation Strategy section: replace steps 2-3 with:
  ```
  2. Use find_links on key notes to discover related notes, meetings, and
     tasks that reference them via wikilinks (direction="backlinks") and
     see what a note links to (direction="outlinks")
  3. Outlink results include resolved file paths — use those paths directly
     with read_file
  ```

- Tool reference section: replace the two entries with:
  ```
  - find_links: Find links to or from a note. Pass file path and direction
    ("backlinks", "outlinks", or "both"). Backlinks scan the vault for
    [[note]] references. Outlinks extract wikilinks and resolve paths.
    Supports limit/offset pagination. Returns results with total count.
  ```

- Large results section: replace `find_backlinks, find_outlinks` with `find_links`.

- The "broader relationship discovery" tip: replace `find_backlinks` with `find_links (direction="backlinks")`.

**Step 2: Update CLAUDE.md tool table**

Replace the `find_backlinks` and `find_outlinks` rows with:
```
| `find_links` | Find links to/from a vault note | `path`, `direction` ("backlinks"/"outlinks"/"both"), `limit`, `offset` |
```

**Step 3: Commit**

```
docs: update tool references for find_links rename (#128)
```

---

### Task 6: Run full test suite and verify

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: ALL PASS, no regressions.

**Step 2: Verify no stale references**

Run: `grep -rn "find_backlinks\|find_outlinks" src/ tests/ --include="*.py" | grep -v __pycache__`
Expected: No output (no remaining references).

**Step 3: Final commit if any fixups needed, then create PR**

```
Branch: feature/find-links-unification
PR title: Unify find_backlinks + find_outlinks into find_links (#128)
```
