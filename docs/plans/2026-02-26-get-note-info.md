# get_note_info Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `get_note_info` MCP tool that returns structured metadata (frontmatter, headings, size, timestamps, link counts) without file content.

**Architecture:** New public function in `src/tools/files.py` with a `_extract_headings` helper. Reuses `_scan_backlinks` and `_extract_outlinks` from `links.py` for link counts. Custom compaction stub in `compaction.py`.

**Tech Stack:** Python, FastMCP, PyYAML

---

### Task 1: Add `_extract_headings` helper and `get_note_info` with tests

**Files:**
- Modify: `src/tools/files.py` (add `_extract_headings` helper + `get_note_info` function at end of file)
- Test: `tests/test_tools_files.py`

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py` — import `get_note_info` and `_extract_headings` in the existing import block, then add a new test class at the end of the file:

```python
# Add to the import block at top (from tools.files import ...):
#   get_note_info,
#   _extract_headings,

class TestExtractHeadings:
    """Tests for _extract_headings helper."""

    def test_basic_headings(self):
        """Should extract headings from markdown content."""
        content = "# Title\n\nSome text\n\n## Section 1\n\nMore text\n\n### Subsection\n\n## Section 2\n"
        assert _extract_headings(content) == ["# Title", "## Section 1", "### Subsection", "## Section 2"]

    def test_no_headings(self):
        """Should return empty list for content without headings."""
        assert _extract_headings("Just plain text\nwith lines\n") == []

    def test_headings_inside_code_fence_skipped(self):
        """Should skip headings inside code fences."""
        content = "# Real Heading\n\n```\n# Not a heading\n## Also not\n```\n\n## Real Section\n"
        assert _extract_headings(content) == ["# Real Heading", "## Real Section"]

    def test_tilde_code_fence(self):
        """Should skip headings inside tilde fences."""
        content = "# Title\n\n~~~\n## Fake\n~~~\n\n## Real\n"
        assert _extract_headings(content) == ["# Title", "## Real"]

    def test_empty_content(self):
        """Should return empty list for empty string."""
        assert _extract_headings("") == []


class TestGetNoteInfo:
    """Tests for get_note_info tool."""

    def test_basic_metadata(self, vault_config, temp_vault):
        """Should return frontmatter, headings, size, timestamps."""
        result = json.loads(get_note_info("note1.md"))
        assert result["success"] is True
        assert result["path"] == "note1.md"
        assert result["frontmatter"]["tags"] == ["project", "work"]
        assert "# Note 1" in result["headings"]
        assert isinstance(result["size"], int)
        assert result["size"] > 0
        assert "modified" in result
        assert "created" in result

    def test_link_counts(self, vault_config, temp_vault):
        """Should include backlink and outlink counts."""
        result = json.loads(get_note_info("note1.md"))
        assert "backlink_count" in result
        assert "outlink_count" in result
        assert isinstance(result["backlink_count"], int)
        assert isinstance(result["outlink_count"], int)
        # note1 has [[wikilink]] outlink
        assert result["outlink_count"] >= 1

    def test_no_frontmatter(self, vault_config, temp_vault):
        """Should return empty frontmatter for files without it."""
        (temp_vault / "plain.md").write_text("# Just a heading\n\nNo frontmatter here.\n")
        result = json.loads(get_note_info("plain.md"))
        assert result["success"] is True
        assert result["frontmatter"] == {}
        assert "# Just a heading" in result["headings"]

    def test_nonexistent_file(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(get_note_info("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_non_markdown_file(self, vault_config, temp_vault):
        """Should return basic metadata for non-markdown files."""
        (temp_vault / "data.csv").write_text("a,b,c\n1,2,3\n")
        result = json.loads(get_note_info("data.csv"))
        assert result["success"] is True
        assert result["frontmatter"] == {}
        assert result["headings"] == []
        assert result["backlink_count"] == 0
        assert result["outlink_count"] == 0

    def test_headings_respect_code_fences(self, vault_config, temp_vault):
        """Should skip headings inside code fences."""
        (temp_vault / "fenced.md").write_text(
            "# Real\n\n```\n## Fake\n```\n\n## Also Real\n"
        )
        result = json.loads(get_note_info("fenced.md"))
        assert result["headings"] == ["# Real", "## Also Real"]

    def test_created_from_frontmatter_date(self, vault_config, temp_vault):
        """Should use frontmatter Date field for created timestamp."""
        result = json.loads(get_note_info("note1.md"))
        # note1.md has Date: 2024-01-15
        assert result["created"].startswith("2024-01-15")

    def test_nbs_in_path(self, vault_config, temp_vault):
        """Should handle non-breaking spaces in path."""
        (temp_vault / "test nbs.md").write_text("# Test\n")
        result = json.loads(get_note_info("test\xa0nbs.md"))
        assert result["success"] is True
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExtractHeadings tests/test_tools_files.py::TestGetNoteInfo -v`
Expected: FAIL — `ImportError` (functions don't exist yet)

**Step 3: Write minimal implementation**

Add to `src/tools/files.py` — first add the necessary imports at the top (in the `from services.vault import` block, add `extract_frontmatter`, `get_file_creation_time`, `parse_frontmatter_date`). Then add at end of file:

```python
def _extract_headings(content: str) -> list[str]:
    """Extract markdown headings from content, skipping code fences.

    Args:
        content: Raw markdown text.

    Returns:
        List of heading lines with # prefixes (e.g. ["## Section 1", "### Sub"]).
    """
    headings = []
    in_fence = False
    for line in content.split("\n"):
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_PATTERN.match(line)
        if m:
            headings.append(line.rstrip())
    return headings


def get_note_info(path: str) -> str:
    """Get structured metadata about a vault note without returning content.

    Returns frontmatter, headings, file size, timestamps, and link counts.
    Useful for triaging notes before deciding whether to read them.

    Args:
        path: Path to the note (relative to vault or absolute).

    Returns:
        JSON with path, frontmatter, headings, size, modified, created,
        backlink_count, and outlink_count.
    """
    path = path.replace("\xa0", " ")
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    rel_path = get_relative_path(file_path)

    # File stats
    try:
        stat = file_path.stat()
    except OSError as e:
        return err(f"Cannot stat file: {e}")

    modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    is_md = file_path.suffix.lower() == ".md"

    # Frontmatter + created date
    if is_md:
        frontmatter = extract_frontmatter(file_path)
        created_dt = parse_frontmatter_date(frontmatter.get("Date"))
        if not created_dt:
            created_dt = get_file_creation_time(file_path)
    else:
        frontmatter = {}
        created_dt = get_file_creation_time(file_path)

    created = created_dt.isoformat() if created_dt else modified

    # Content-based metadata (headings, size)
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading file: {e}")

    headings = _extract_headings(content) if is_md else []
    size = len(content)

    # Link counts
    if is_md:
        from tools.links import _extract_outlinks, _scan_backlinks

        note_name = file_path.stem
        backlinks = _scan_backlinks(note_name, rel_path)
        outlinks = _extract_outlinks(file_path)
        backlink_count = len(backlinks)
        outlink_count = len(outlinks) if outlinks is not None else 0
    else:
        backlink_count = 0
        outlink_count = 0

    return ok(
        path=rel_path,
        frontmatter=frontmatter,
        headings=headings,
        size=size,
        modified=modified,
        created=created,
        backlink_count=backlink_count,
        outlink_count=outlink_count,
    )
```

Also add `from datetime import datetime` to the stdlib imports at the top of `files.py`.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExtractHeadings tests/test_tools_files.py::TestGetNoteInfo -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add get_note_info tool with tests (#129)"
```

---

### Task 2: Register tool in MCP server

**Files:**
- Modify: `src/mcp_server.py`

**Step 1: Add import and registration**

In `src/mcp_server.py`, add `get_note_info` to the `from tools.files import` block, then register it in the "File tools" section:

```python
mcp.tool()(get_note_info)
```

**Step 2: Run all existing tests to verify nothing breaks**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -q`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: register get_note_info in MCP server (#129)"
```

---

### Task 3: Add compaction stub builder

**Files:**
- Modify: `src/services/compaction.py`
- Test: `tests/test_compaction.py`

**Step 1: Write the failing test**

Add to `tests/test_compaction.py` (find the existing test file and add a new test):

```python
def test_build_get_note_info_stub():
    """get_note_info stub keeps path and counts, drops frontmatter/headings detail."""
    data = {
        "success": True,
        "path": "Meetings/standup.md",
        "frontmatter": {"category": ["meeting"], "project": "archbrain", "people involved": ["Alice"]},
        "headings": ["## Attendees", "## Agenda", "## Action Items"],
        "size": 4521,
        "modified": "2026-02-20T10:30:00",
        "created": "2026-02-20T09:00:00",
        "backlink_count": 3,
        "outlink_count": 7,
    }
    stub = json.loads(build_tool_stub(json.dumps(data), "get_note_info"))
    assert stub["status"] == "success"
    assert stub["path"] == "Meetings/standup.md"
    assert stub["size"] == 4521
    assert stub["backlink_count"] == 3
    assert stub["outlink_count"] == 7
    assert "frontmatter" not in stub
    assert "headings" not in stub
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_compaction.py::test_build_get_note_info_stub -v`
Expected: FAIL — falls through to generic stub, missing expected fields

**Step 3: Write implementation**

Add to `src/services/compaction.py`, before the `_TOOL_STUB_BUILDERS` dict:

```python
def _build_get_note_info_stub(data: dict) -> str:
    """Compact get_note_info: keep path and counts, drop frontmatter/headings detail."""
    stub = _base_stub(data)
    if "path" in data:
        stub["path"] = data["path"]
    for key in ("size", "modified", "created", "backlink_count", "outlink_count"):
        if key in data:
            stub[key] = data[key]
    return json.dumps(stub)
```

Then add `"get_note_info": _build_get_note_info_stub,` to the `_TOOL_STUB_BUILDERS` dict.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_compaction.py::test_build_get_note_info_stub -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/compaction.py tests/test_compaction.py
git commit -m "feat: add compaction stub for get_note_info (#129)"
```

---

### Task 4: Update documentation

**Files:**
- Modify: `CLAUDE.md` — add `get_note_info` row to the MCP Tools table
- Modify: `system_prompt.txt.example` — add to tool reference section

**Step 1: Add to CLAUDE.md tool table**

Add a row to the MCP Tools table after `read_file`:

```
| `get_note_info` | Lightweight metadata (frontmatter, headings, size, timestamps, link counts) | `path` |
```

**Step 2: Update system_prompt.txt.example**

Add `get_note_info` to the tool reference section and decision tree. The exact placement depends on the current prompt structure — add it near `read_file` as a lightweight alternative.

**Step 3: Commit**

```bash
git add CLAUDE.md system_prompt.txt.example
git commit -m "docs: add get_note_info to tool reference (#129)"
```

---

### Task 5: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short -q`
Expected: All tests pass, no regressions

**Step 2: Verify tool count**

The project should now have 16 MCP tools (up from 15). Confirm by counting `mcp.tool()` calls in `mcp_server.py`.
