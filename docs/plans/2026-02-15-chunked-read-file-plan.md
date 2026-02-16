# Chunked File Reading Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add offset/length pagination to `read_file` so the agent can page through files longer than 4,000 characters.

**Architecture:** Add `offset` and `length` parameters to the existing `read_file()` function. When content exceeds the length, inline text markers tell the agent how to fetch the next page. No new files or dependencies.

**Tech Stack:** Python stdlib only.

---

### Task 1: Write tests for chunked read_file

**Files:**
- Modify: `tests/test_tools_files.py`

**Step 1: Write the failing tests**

Add these tests to the existing `TestReadFile` class in `tests/test_tools_files.py`:

```python
    def test_short_file_no_markers(self, vault_config):
        """Short files should be returned in full with no markers."""
        result = read_file("note3.md")
        assert "[... truncated" not in result
        assert "[Continuing from" not in result
        assert "# Note 3" in result

    def test_long_file_truncated_with_marker(self, vault_config):
        """Files longer than length should have a continuation marker."""
        long_content = "# Long Note\n\n" + "x" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md")
        assert result.startswith("# Long Note")
        assert "[... truncated at char 4000 of" in result
        assert "Use offset=4000 to read more." in result

    def test_offset_pagination(self, vault_config):
        """Reading with offset should show continuation header and may show truncation marker."""
        long_content = "A" * 10000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md", offset=4000)
        assert "[Continuing from char 4000 of 10000]" in result
        assert "[... truncated at char 8000 of 10000" in result

    def test_offset_final_chunk(self, vault_config):
        """Reading the last chunk should have no truncation marker."""
        long_content = "B" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md", offset=4000)
        assert "[Continuing from char 4000 of 5000]" in result
        assert "[... truncated" not in result

    def test_offset_past_end(self, vault_config):
        """Offset past end of file should return error."""
        result = read_file("note3.md", offset=99999)
        assert "Error" in result
        assert "offset" in result.lower()

    def test_custom_length(self, vault_config):
        """Custom length parameter should control chunk size."""
        content = "C" * 500
        (vault_config / "custom.md").write_text(content)
        result = read_file("custom.md", length=100)
        assert "[... truncated at char 100 of 500" in result
        assert len(result.split("\n\n[... truncated")[0]) == 100
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFile -v`
Expected: FAIL — `read_file()` does not accept `offset` or `length` parameters.

**Step 3: Commit test file**

```bash
git add tests/test_tools_files.py
git commit -m "test: add tests for chunked read_file pagination"
```

---

### Task 2: Implement chunked read_file

**Files:**
- Modify: `src/tools/files.py:17-33` (`read_file` function)

**Step 1: Update `read_file` implementation**

Replace the `read_file` function in `src/tools/files.py`:

```python
def read_file(path: str, offset: int = 0, length: int = 4000) -> str:
    """Read content of a vault note with optional pagination.

    Args:
        path: Path to the note, either relative to vault root or absolute.
        offset: Character position to start reading from (default 0).
        length: Maximum characters to return (default 4000).

    Returns:
        The text content of the note, with pagination markers if truncated.
    """
    file_path, error = resolve_file(path)
    if error:
        return f"Error: {error}"

    try:
        content = file_path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"

    total = len(content)

    # Short file with no offset — return as-is
    if offset == 0 and total <= length:
        return content

    # Offset past end of file
    if offset >= total:
        return f"Error: offset {offset} exceeds file length {total}"

    # Slice the content
    chunk = content[offset:offset + length]
    end_pos = offset + length

    # Build result with markers
    parts = []
    if offset > 0:
        parts.append(f"[Continuing from char {offset} of {total}]\n\n")
    parts.append(chunk)
    if end_pos < total:
        parts.append(f"\n\n[... truncated at char {end_pos} of {total}. Use offset={end_pos} to read more.]")

    return "".join(parts)
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFile -v`
Expected: All tests pass.

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass, no regressions.

**Step 4: Commit**

```bash
git add src/tools/files.py
git commit -m "feat: add offset/length pagination to read_file"
```

---

### Task 3: Update documentation

**Files:**
- Modify: `system_prompt.txt.example` (read_file description, around line 106)
- Modify: `CLAUDE.md` (read_file tool table row and docs section)

**Step 1: Update system_prompt.txt.example**

Find the `read_file` line in the File Operations section and replace it:

```
- read_file: Read content of a note. For long files, returns the first 4000
  chars with a continuation marker. Use the offset parameter to page through
  the rest. Always check for truncation markers before concluding content
  isn't in a file.
```

**Step 2: Update CLAUDE.md tool table**

Find the `read_file` row in the MCP Tools table and update:

```
| `read_file` | Read content of a vault note | `path` (string: relative to vault or absolute), `offset` (int, default 0), `length` (int, default 4000) |
```

**Step 3: Update CLAUDE.md read_file section**

Find the `### read_file` section and update it:

```markdown
### read_file

Reads content of a vault note with pagination for long files. Accepts either a relative path (from vault root) or an absolute path.

Parameters:
- `path`: Path to the note (relative to vault root or absolute)
- `offset`: Character position to start reading from (default 0)
- `length`: Maximum characters to return (default 4000)

For files that fit within `length`, returns the full content with no markers (current behavior). For longer files, appends a truncation marker with the offset needed to read the next chunk. When reading with a non-zero offset, prepends a continuation header.

Security measures:
- Rejects paths that escape the vault (path traversal protection)
- Blocks access to excluded directories (`.obsidian`, `.git`, etc.)
```

**Step 4: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: document chunked read_file pagination"
```

---

### Task 4: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

**Step 2: Verify MCP tool registration**

Run: `.venv/bin/python -c "from tools.files import read_file; import inspect; print(inspect.signature(read_file))"`
Expected: `(path: str, offset: int = 0, length: int = 4000) -> str`
