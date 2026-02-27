# find_notes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `search_vault`, `list_files`, and `search_by_date_range` with a single `find_notes` tool that supports any combination of semantic search, frontmatter filters, folder scope, and date range.

**Architecture:** Two-phase intersect — when a semantic query is combined with vault-scan filters, run both independently then intersect by path. Pure vault-scan mode reuses `_find_matching_files` with added date filtering. Result format adapts to the mode (semantic chunks vs path lists).

**Tech Stack:** Python, ChromaDB, FastMCP, Pydantic

---

### Task 1: Add date filtering to `_find_matching_files`

Currently `_find_matching_files` handles folder + frontmatter filters but not date ranges. Add date filtering as an optional parameter so `find_notes` can do a single-pass vault scan for all non-semantic filters.

**Files:**
- Modify: `src/tools/frontmatter.py:168-231` (`_find_matching_files`)
- Test: `tests/test_find_notes.py` (new file)

**Step 1: Write the failing test**

Create `tests/test_find_notes.py`:

```python
"""Tests for find_notes unified discovery tool."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def dated_vault(temp_vault):
    """Create vault files with known modification times."""
    # Create files with specific mtimes
    old_file = temp_vault / "old-note.md"
    old_file.write_text("---\ntags: archive\n---\nOld content")
    old_time = datetime(2025, 1, 15).timestamp()
    os.utime(old_file, (old_time, old_time))

    recent_file = temp_vault / "recent-note.md"
    recent_file.write_text("---\ntags: active\n---\nRecent content")
    recent_time = datetime(2025, 6, 15).timestamp()
    os.utime(recent_file, (recent_time, recent_time))

    future_file = temp_vault / "future-note.md"
    future_file.write_text("---\ntags: draft\n---\nFuture content")
    future_time = datetime(2025, 12, 15).timestamp()
    os.utime(future_file, (future_time, future_time))

    return temp_vault


class TestFindMatchingFilesDateFilter:
    """Tests for date filtering in _find_matching_files."""

    def test_date_filter_modified(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2025, 6, 1)
        end = datetime(2025, 6, 30)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 1
        assert "recent-note.md" in results[0]

    def test_date_filter_with_frontmatter(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2025, 1, 1)
        end = datetime(2025, 12, 31)
        results = _find_matching_files(
            "tags", "active", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 1
        assert "recent-note.md" in results[0]

    def test_date_filter_no_matches(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindMatchingFilesDateFilter -v`
Expected: FAIL — `_find_matching_files` doesn't accept date parameters yet.

**Step 3: Write minimal implementation**

In `src/tools/frontmatter.py`, update `_find_matching_files` signature and body:

```python
def _find_matching_files(
    field: str | None,
    value: str,
    match_type: str,
    parsed_filters: list[dict],
    include_fields: list[str] | None = None,
    folder: Path | None = None,
    recursive: bool = False,
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    date_type: str = "modified",
) -> list[str | dict]:
```

Add date filtering inside the per-file loop, after frontmatter checks pass:

```python
    if date_start or date_end:
        file_date = _get_file_date(md_file, date_type, frontmatter if needs_frontmatter else None)
        if file_date is None:
            continue
        file_date_only = file_date.replace(hour=0, minute=0, second=0, microsecond=0)
        if date_start and file_date_only < date_start:
            continue
        if date_end and file_date_only > date_end:
            continue
```

Extract the date-getting logic from `search_by_date_range` into a helper:

```python
def _get_file_date(
    md_file: Path, date_type: str, frontmatter: dict | None = None,
) -> datetime | None:
    """Get the relevant date for a file based on date_type."""
    if date_type == "created":
        if frontmatter is None:
            frontmatter = extract_frontmatter(md_file)
        file_date = parse_frontmatter_date(frontmatter.get("Date"))
        if file_date is None:
            file_date = get_file_creation_time(md_file)
        return file_date
    else:  # modified
        try:
            mtime = md_file.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except OSError:
            return None
```

Note: when `needs_frontmatter` is False but dates are needed, the frontmatter variable won't exist. Adjust the loop:

```python
    needs_frontmatter = field is not None or parsed_filters or include_fields
    needs_date = date_start is not None or date_end is not None

    for md_file in files:
        frontmatter = None

        if needs_frontmatter:
            frontmatter = extract_frontmatter(md_file)
            # ... existing field/filter checks ...

        if needs_date:
            file_date = _get_file_date(md_file, date_type, frontmatter)
            if file_date is None:
                continue
            file_date_only = file_date.replace(hour=0, minute=0, second=0, microsecond=0)
            if date_start and file_date_only < date_start:
                continue
            if date_end and file_date_only > date_end:
                continue

        # ... existing result building ...
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindMatchingFilesDateFilter -v`
Expected: PASS

**Step 5: Run existing tests to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v`
Expected: All existing tests still pass (new params have defaults).

**Step 6: Commit**

```bash
git add src/tools/frontmatter.py tests/test_find_notes.py
git commit -m "feat: add date filtering to _find_matching_files (#131)"
```

---

### Task 2: Implement `find_notes` vault-scan mode (no query)

Implement the `find_notes` function handling the pure vault-scan case: folder, frontmatter, date, and sort options without semantic search.

**Files:**
- Modify: `src/tools/search.py` (add `find_notes`, keep `web_search`)
- Test: `tests/test_find_notes.py`

**Step 1: Write failing tests**

Add to `tests/test_find_notes.py`:

```python
class TestFindNotesVaultScan:
    """Tests for find_notes without semantic query (pure vault scan)."""

    def test_folder_only(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "projects").mkdir(exist_ok=True)
        (temp_vault / "projects" / "p1.md").write_text("---\nstatus: active\n---\nProject 1")
        (temp_vault / "projects" / "p2.md").write_text("---\nstatus: done\n---\nProject 2")

        result = json.loads(find_notes(folder="projects"))
        assert result["success"]
        assert len(result["results"]) == 2
        assert result["total"] == 2

    def test_frontmatter_only(self, temp_vault, vault_config):
        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        (temp_vault / "a.md").write_text("---\nstatus: active\n---\nA")
        (temp_vault / "b.md").write_text("---\nstatus: done\n---\nB")

        result = json.loads(find_notes(
            frontmatter=[FilterCondition(field="status", value="active")],
        ))
        assert result["success"]
        assert result["total"] == 1
        assert "a.md" in result["results"][0]

    def test_date_only(self, dated_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(
            date_start="2025-06-01", date_end="2025-06-30",
        ))
        assert result["success"]
        assert result["total"] == 1
        assert "recent-note.md" in result["results"][0]

    def test_folder_plus_frontmatter_plus_date(self, dated_vault, vault_config):
        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        result = json.loads(find_notes(
            folder=".",
            recursive=True,
            frontmatter=[FilterCondition(field="tags", value="active")],
            date_start="2025-01-01",
            date_end="2025-12-31",
        ))
        assert result["success"]
        assert result["total"] == 1
        assert "recent-note.md" in result["results"][0]

    def test_include_fields(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "note.md").write_text("---\nstatus: active\ntags: [test]\n---\nContent")

        result = json.loads(find_notes(
            folder=".",
            include_fields=["status", "tags"],
        ))
        assert result["success"]
        r = [x for x in result["results"] if isinstance(x, dict) and x.get("path", "").endswith("note.md")][0]
        assert r["status"] == "active"

    def test_sort_by_name(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "beta.md").write_text("B")
        (temp_vault / "alpha.md").write_text("A")

        result = json.loads(find_notes(folder=".", sort="name"))
        paths = result["results"]
        assert paths == sorted(paths)

    def test_sort_by_modified(self, dated_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(
            folder=".",
            recursive=True,
            sort="modified",
            date_start="2025-01-01",
            date_end="2025-12-31",
        ))
        assert result["success"]
        assert result["total"] >= 2

    def test_pagination(self, temp_vault, vault_config):
        from tools.search import find_notes

        for i in range(5):
            (temp_vault / f"note{i}.md").write_text(f"Note {i}")

        result = json.loads(find_notes(folder=".", n_results=2, offset=0))
        assert len(result["results"]) == 2
        assert result["total"] >= 5

        result2 = json.loads(find_notes(folder=".", n_results=2, offset=2))
        assert len(result2["results"]) == 2
        # Pages should not overlap
        assert set(r if isinstance(r, str) else r["path"] for r in result["results"]) != \
               set(r if isinstance(r, str) else r["path"] for r in result2["results"])

    def test_no_filters_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes())
        assert not result["success"]

    def test_sort_relevance_without_query_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(folder=".", sort="relevance"))
        assert not result["success"]
        assert "relevance" in result["error"].lower()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesVaultScan -v`
Expected: FAIL — `find_notes` doesn't exist yet.

**Step 3: Write implementation**

Replace `search_vault` in `src/tools/search.py` with `find_notes`. Keep `web_search` unchanged.

```python
"""Search tools for vault and web."""

from datetime import datetime

from ddgs import DDGS

from config import LIST_DEFAULT_LIMIT
from search_vault import search_results
from services.vault import err, ok, resolve_dir
from tools._validation import validate_pagination
from tools.frontmatter import (
    FilterCondition,
    _find_matching_files,
    _validate_filters,
)

VALID_MODES = {"hybrid", "semantic", "keyword"}
VALID_SORTS = {"relevance", "modified", "created", "name"}


def find_notes(
    query: str = "",
    mode: str = "hybrid",
    folder: str = "",
    recursive: bool = False,
    frontmatter: list[FilterCondition] | None = None,
    date_start: str = "",
    date_end: str = "",
    date_type: str = "modified",
    sort: str = "relevance",
    include_fields: list[str] | None = None,
    n_results: int = 20,
    offset: int = 0,
) -> str:
    """Find vault notes using any combination of semantic search, frontmatter
    filters, folder scope, and date range.

    Args:
        query: Semantic/keyword search text. When provided, results include
            matched content chunks. When omitted, returns file paths.
        mode: Search mode when query is provided - "hybrid" (default),
            "semantic", or "keyword".
        folder: Restrict to files within this folder (relative to vault root).
        recursive: Include subfolders when folder is set (default false).
        frontmatter: Metadata filter conditions (AND logic). Each needs
            'field', optional 'value' and 'match_type'.
        date_start: Start of date range (inclusive), format: YYYY-MM-DD.
        date_end: End of date range (inclusive), format: YYYY-MM-DD.
        date_type: Which date to check - "modified" (default) or "created"
            (frontmatter Date field, falls back to filesystem creation time).
        sort: Sort order - "relevance" (default, requires query),
            "modified", "created", or "name".
        include_fields: Frontmatter field names to include in results
            (only applies when query is not provided).
        n_results: Maximum number of results (default 20).
        offset: Number of results to skip for pagination.

    Returns:
        JSON with results and total count. With query: list of
        {source, content, heading} dicts. Without query: list of paths
        or {path, field1, field2, ...} dicts when include_fields is set.
    """
    has_query = bool(query and query.strip())
    has_folder = bool(folder)
    has_frontmatter = bool(frontmatter)
    has_date = bool(date_start or date_end)

    if not (has_query or has_folder or has_frontmatter or has_date):
        return err(
            "At least one filter is required: query, folder, frontmatter, "
            "or date_start/date_end"
        )

    if sort not in VALID_SORTS:
        return err(f"sort must be one of {sorted(VALID_SORTS)}, got '{sort}'")

    if sort == "relevance" and not has_query:
        return err("sort='relevance' requires a query parameter")

    if has_query and mode not in VALID_MODES:
        return err(f"mode must be one of {sorted(VALID_MODES)}, got '{mode}'")

    if date_type not in ("created", "modified"):
        return err(f"date_type must be 'created' or 'modified', got '{date_type}'")

    # Parse dates
    parsed_start = None
    parsed_end = None
    if date_start:
        try:
            parsed_start = datetime.strptime(date_start, "%Y-%m-%d")
        except ValueError:
            return err(f"Invalid date_start format. Use YYYY-MM-DD, got '{date_start}'")
    if date_end:
        try:
            parsed_end = datetime.strptime(date_end, "%Y-%m-%d")
        except ValueError:
            return err(f"Invalid date_end format. Use YYYY-MM-DD, got '{date_end}'")
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return err(f"date_start ({date_start}) is after date_end ({date_end})")

    # Validate frontmatter filters
    parsed_filters, filter_err = _validate_filters(frontmatter)
    if filter_err:
        return err(filter_err)

    # Validate pagination
    validated_offset, validated_limit, pagination_error = validate_pagination(
        offset, n_results
    )
    if pagination_error:
        return err(pagination_error)

    # Resolve folder
    folder_path = None
    if has_folder:
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return err(folder_err)

    if has_query:
        return _query_mode(
            query, mode, folder_path, recursive, parsed_filters,
            parsed_start, parsed_end, date_type, include_fields,
            validated_offset, validated_limit,
        )
    else:
        return _scan_mode(
            folder_path, recursive, parsed_filters,
            parsed_start, parsed_end, date_type, sort,
            include_fields, validated_offset, validated_limit,
        )


def _scan_mode(
    folder_path, recursive, parsed_filters,
    date_start, date_end, date_type, sort,
    include_fields, offset, limit,
) -> str:
    """Pure vault-scan mode: no semantic query."""
    matching = _find_matching_files(
        None, "", "contains", parsed_filters,
        include_fields=include_fields,
        folder=folder_path, recursive=recursive,
        date_start=date_start, date_end=date_end, date_type=date_type,
    )

    if not matching:
        return ok("No matching notes found", results=[], total=0)

    # Sort
    if sort in ("modified", "created"):
        matching = _sort_by_date(matching, sort)
    # else: "name" — _find_matching_files already returns sorted by name

    total = len(matching)
    page = matching[offset:offset + limit]
    return ok(results=page, total=total)


def _sort_by_date(items: list, date_type: str) -> list:
    """Sort results by file date (most recent first)."""
    from services.vault import (
        extract_frontmatter,
        get_file_creation_time,
        parse_frontmatter_date,
        resolve_file,
    )

    def get_date(item):
        path_str = item["path"] if isinstance(item, dict) else item
        resolved, _ = resolve_file(path_str)
        if not resolved:
            return datetime.min
        if date_type == "created":
            fm = extract_frontmatter(resolved)
            d = parse_frontmatter_date(fm.get("Date"))
            if d is None:
                d = get_file_creation_time(resolved)
            return d or datetime.min
        else:
            try:
                return datetime.fromtimestamp(resolved.stat().st_mtime)
            except OSError:
                return datetime.min

    return sorted(items, key=get_date, reverse=True)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesVaultScan -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/tools/search.py tests/test_find_notes.py
git commit -m "feat: implement find_notes vault-scan mode (#131)"
```

---

### Task 3: Implement `find_notes` query mode (two-phase intersect)

Add semantic search support with post-filtering by vault scan results.

**Files:**
- Modify: `src/tools/search.py` (add `_query_mode`)
- Modify: `src/search_vault.py` (expose search without n_results cap for intersection)
- Test: `tests/test_find_notes.py`

**Step 1: Write failing tests**

Add to `tests/test_find_notes.py`:

```python
class TestFindNotesQueryMode:
    """Tests for find_notes with semantic query."""

    def test_query_only(self, temp_vault, vault_config):
        """Query-only mode delegates to search_results."""
        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "note.md", "content": "some content", "heading": "Section"},
            ]
            result = json.loads(find_notes(query="test query"))
            assert result["success"]
            assert len(result["results"]) == 1
            assert result["results"][0]["source"] == "note.md"
            mock_search.assert_called_once()

    def test_query_with_folder_filter(self, temp_vault, vault_config):
        """Query + folder: only return semantic results from matching folder."""
        from tools.search import find_notes

        (temp_vault / "projects").mkdir(exist_ok=True)
        (temp_vault / "projects" / "p1.md").write_text("Project content")
        (temp_vault / "other.md").write_text("Other content")

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "projects/p1.md", "content": "Project content", "heading": ""},
                {"source": "other.md", "content": "Other content", "heading": ""},
            ]
            result = json.loads(find_notes(query="content", folder="projects"))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "projects/p1.md" in sources
            assert "other.md" not in sources

    def test_query_with_frontmatter_filter(self, temp_vault, vault_config):
        """Query + frontmatter: only return semantic results from files matching metadata."""
        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        (temp_vault / "active.md").write_text("---\nstatus: active\n---\nActive note")
        (temp_vault / "done.md").write_text("---\nstatus: done\n---\nDone note")

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "active.md", "content": "Active note", "heading": ""},
                {"source": "done.md", "content": "Done note", "heading": ""},
            ]
            result = json.loads(find_notes(
                query="note",
                frontmatter=[FilterCondition(field="status", value="active")],
            ))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "active.md" in sources
            assert "done.md" not in sources

    def test_query_with_date_filter(self, dated_vault, vault_config):
        """Query + date: only return semantic results from files in date range."""
        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "old-note.md", "content": "Old", "heading": ""},
                {"source": "recent-note.md", "content": "Recent", "heading": ""},
            ]
            result = json.loads(find_notes(
                query="content",
                date_start="2025-06-01",
                date_end="2025-06-30",
            ))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "recent-note.md" in sources
            assert "old-note.md" not in sources

    def test_query_mode_pagination(self, temp_vault, vault_config):
        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": f"note{i}.md", "content": f"Content {i}", "heading": ""}
                for i in range(10)
            ]
            result = json.loads(find_notes(query="test", n_results=3, offset=0))
            assert len(result["results"]) == 3
            assert result["total"] == 10

            result2 = json.loads(find_notes(query="test", n_results=3, offset=3))
            assert len(result2["results"]) == 3

    def test_query_search_failure(self, temp_vault, vault_config):
        from tools.search import find_notes

        with patch("tools.search.search_results", side_effect=Exception("DB error")):
            result = json.loads(find_notes(query="test"))
            assert not result["success"]
            assert "Search failed" in result["error"]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesQueryMode -v`
Expected: FAIL — `_query_mode` not implemented.

**Step 3: Write implementation**

Add `_query_mode` to `src/tools/search.py`:

```python
def _query_mode(
    query, mode, folder_path, recursive, parsed_filters,
    date_start, date_end, date_type, include_fields,
    offset, limit,
) -> str:
    """Semantic/keyword search with optional vault-scan post-filtering."""
    has_filters = folder_path or parsed_filters or date_start or date_end

    try:
        # When intersecting, fetch all results (no limit) so we don't miss
        # matches that pass the vault-scan filter
        search_limit = limit + offset if not has_filters else 500
        results = search_results(query, search_limit, mode)
    except Exception as e:
        return err(f"Search failed: {e}. Is the vault indexed? Run: python src/index_vault.py")

    if not results:
        return ok("No matching notes found", results=[], total=0)

    if has_filters:
        # Build filter set from vault scan
        filter_paths = set(
            _find_matching_files(
                None, "", "contains", parsed_filters,
                folder=folder_path, recursive=recursive,
                date_start=date_start, date_end=date_end, date_type=date_type,
            )
        )
        results = [r for r in results if r["source"] in filter_paths]

    if not results:
        return ok("No matching notes found", results=[], total=0)

    total = len(results)
    page = results[offset:offset + limit]
    return ok(results=page, total=total)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesQueryMode -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/tools/search.py tests/test_find_notes.py
git commit -m "feat: implement find_notes query mode with two-phase intersect (#131)"
```

---

### Task 4: Add compaction stub for `find_notes`

**Files:**
- Modify: `src/services/compaction.py`
- Test: `tests/test_find_notes.py`

**Step 1: Write failing tests**

```python
class TestFindNotesCompaction:
    """Tests for find_notes compaction stub."""

    def test_stub_semantic_results(self):
        from services.compaction import build_tool_stub

        content = json.dumps({
            "success": True,
            "results": [
                {"source": "note.md", "content": "A" * 200, "heading": "Section"},
                {"source": "other.md", "content": "B" * 50, "heading": ""},
            ],
            "total": 2,
        })
        stub = json.loads(build_tool_stub(content, "find_notes"))
        assert stub["status"] == "success"
        assert stub["result_count"] == 2
        assert stub["total"] == 2
        # Semantic results: should have snippet, not full content
        assert "snippet" in stub["results"][0]
        assert len(stub["results"][0]["snippet"]) <= 100  # COMPACTION_SNIPPET_LENGTH

    def test_stub_vault_scan_paths(self):
        from services.compaction import build_tool_stub

        content = json.dumps({
            "success": True,
            "results": ["note1.md", "note2.md", "note3.md"],
            "total": 3,
        })
        stub = json.loads(build_tool_stub(content, "find_notes"))
        assert stub["status"] == "success"
        assert stub["result_count"] == 3
        assert stub["results"] == ["note1.md", "note2.md", "note3.md"]
        assert stub["total"] == 3

    def test_stub_vault_scan_with_fields(self):
        from services.compaction import build_tool_stub

        content = json.dumps({
            "success": True,
            "results": [
                {"path": "note.md", "status": "active"},
            ],
            "total": 1,
        })
        stub = json.loads(build_tool_stub(content, "find_notes"))
        assert stub["result_count"] == 1
        assert stub["results"][0]["path"] == "note.md"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesCompaction -v`
Expected: FAIL — `find_notes` not in `_TOOL_STUB_BUILDERS`, falls back to generic.

**Step 3: Write implementation**

Add to `src/services/compaction.py`:

```python
def _build_find_notes_stub(data: dict) -> str:
    """Compact find_notes: detect result shape and use appropriate format."""
    stub = _base_stub(data)
    if "total" in data:
        stub["total"] = data["total"]
    if "results" in data and isinstance(data["results"], list):
        results = data["results"]
        stub["result_count"] = len(results)
        if results and isinstance(results[0], dict) and "content" in results[0]:
            # Semantic results: snippet format
            stub["results"] = [
                {
                    "source": r["source"],
                    "heading": r.get("heading", ""),
                    "snippet": r.get("content", "")[:COMPACTION_SNIPPET_LENGTH],
                }
                for r in results
                if isinstance(r, dict) and "source" in r
            ]
        else:
            # Vault scan results: preserve as-is (paths or field projections)
            stub["results"] = results
    return json.dumps(stub)
```

Register in `_TOOL_STUB_BUILDERS`:

```python
_TOOL_STUB_BUILDERS: dict[str, Callable[[dict], str]] = {
    "find_notes": _build_find_notes_stub,
    "read_file": _build_read_file_stub,
    "web_search": _build_web_search_stub,
    "find_links": _build_find_links_stub,
}
```

Remove `search_vault`, `list_files`, `search_by_date_range` entries.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_find_notes.py::TestFindNotesCompaction -v`
Expected: PASS

**Step 5: Run all compaction tests**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: Some tests reference `search_vault` — will be updated in Task 6.

**Step 6: Commit**

```bash
git add src/services/compaction.py tests/test_find_notes.py
git commit -m "feat: add find_notes compaction stub (#131)"
```

---

### Task 5: Wire up MCP server and remove old tools

Remove `search_vault`, `list_files`, `search_by_date_range` from MCP registration and `tools/__init__.py`. Register `find_notes`.

**Files:**
- Modify: `src/mcp_server.py`
- Modify: `src/tools/__init__.py`
- Modify: `src/tools/search.py` (remove `search_vault` function)
- Modify: `src/tools/frontmatter.py` (remove `list_files` and `search_by_date_range` functions — keep internal helpers)
- Test: verify MCP tool list

**Step 1: Update `src/mcp_server.py`**

Remove imports for `search_vault`, `list_files`, `search_by_date_range`. Add import for `find_notes`. Update registration:

```python
from tools.search import (
    find_notes,
    web_search,
)
from tools.frontmatter import (
    batch_update_frontmatter,
    update_frontmatter,
)
```

Remove `mcp.tool()(search_vault)`, `mcp.tool()(list_files)`, `mcp.tool()(search_by_date_range)`. Add `mcp.tool()(find_notes)`.

**Step 2: Update `src/tools/__init__.py`**

Remove `search_vault`, `list_files`, `search_by_date_range` from imports and `__all__`. Add `find_notes`.

**Step 3: Remove old tool functions**

In `src/tools/search.py`: delete the `search_vault` function (already replaced by `find_notes`).

In `src/tools/frontmatter.py`: delete `list_files` and `search_by_date_range` functions. Keep all internal helpers (`_find_matching_files`, `_matches_field`, `_validate_filters`, `_get_file_date`, `FilterCondition`, etc.) and `batch_update_frontmatter`, `update_frontmatter`.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: Some tests will fail due to referencing removed functions — these are fixed in Task 6.

**Step 5: Commit**

```bash
git add src/mcp_server.py src/tools/__init__.py src/tools/search.py src/tools/frontmatter.py
git commit -m "refactor: wire find_notes into MCP, remove search_vault/list_files/search_by_date_range (#131)"
```

---

### Task 6: Update existing tests

Tests in `test_tools_frontmatter.py` and `test_session_management.py` reference the removed tools. Update them to use `find_notes` or test internal helpers directly.

**Files:**
- Modify: `tests/test_tools_frontmatter.py`
- Modify: `tests/test_session_management.py`
- Modify: `tests/test_agent.py`

**Step 1: Update `test_tools_frontmatter.py`**

Tests for `list_files` and `search_by_date_range` should be converted:
- Tests that tested the MCP tool API (validation, pagination, result format) → test `find_notes` equivalents in `test_find_notes.py` (most already covered in Tasks 2-3).
- Tests that tested internal matching logic (field matching, wikilink stripping, etc.) → keep as-is, they test `_find_matching_files` / `_matches_field` directly.

Change `from tools.frontmatter import list_files, search_by_date_range` to test the internal helpers directly where they do unit-style testing, or replace with `from tools.search import find_notes` where they test tool behavior.

Key replacements:
- `list_files(field="x", value="y")` → `find_notes(frontmatter=[FilterCondition(field="x", value="y")])`
- `list_files(folder="x")` → `find_notes(folder="x")`
- `search_by_date_range(start_date="x", end_date="y")` → `find_notes(date_start="x", date_end="y")`

**Step 2: Update `test_session_management.py`**

- Change `build_tool_stub(content, "search_vault")` → `build_tool_stub(content, "find_notes")`
- Update compaction tests for the new stub format
- Update tool name references in compaction dispatch tests
- Remove `search_vault`, `list_files`, `search_by_date_range` from the list tool stub tests — replace with `find_notes`

**Step 3: Update `test_agent.py`**

- Change `"search_vault"` tool name references in mock data to `"find_notes"` where they appear in compaction/agent tests
- These are mostly in mock tool_call/message structures, not testing search logic

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_tools_frontmatter.py tests/test_session_management.py tests/test_agent.py
git commit -m "test: update existing tests for find_notes migration (#131)"
```

---

### Task 7: Update documentation

Update system prompt and CLAUDE.md to reflect the tool changes.

**Files:**
- Modify: `system_prompt.txt.example`
- Modify: `CLAUDE.md`

**Step 1: Update `system_prompt.txt.example`**

Remove `search_vault`, `list_files`, `search_by_date_range` from the tool reference. Add `find_notes` with its full parameter documentation. Update the decision tree to route all discovery queries to `find_notes`.

**Step 2: Update `CLAUDE.md`**

In the MCP Tools table:
- Remove `search_vault`, `list_files`, `search_by_date_range` rows
- Add `find_notes` row with key parameters

Update the Architecture section if `search_vault` is mentioned.

Update the compaction description to reference `find_notes` instead of `search_vault`.

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: update tool references for find_notes (#131)"
```

---

### Task 8: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Verify no stale references**

Run: `grep -r "search_vault\|search_by_date_range" src/ --include="*.py" | grep -v __pycache__`
Expected: Only `search_vault.py` (the internal module) and `search_results` import. No tool-level references.

Run: `grep -r "list_files" src/ --include="*.py" | grep -v __pycache__`
Expected: Only internal helper references in `frontmatter.py`, not MCP tool registrations.

**Step 3: Verify MCP tool count**

The MCP server should register these tools (14 total, down from 17):
- `find_notes`, `web_search`
- `read_file`, `create_file`, `move_file`, `batch_move_files`, `merge_files`, `batch_merge_files`
- `update_frontmatter`, `batch_update_frontmatter`
- `compare_folders`, `find_links`
- `edit_file`
- `manage_preferences`
- `log_interaction`

**Step 4: Commit any remaining fixes and push**

```bash
git push origin <branch>
```
