# edit_file Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace `prepend_to_file`, `replace_section`, `append_to_section`, and `append_to_file` with a single `edit_file` tool.

**Architecture:** Single `edit_file(path, content, position, heading?, mode?)` entry point validates params then dispatches to private helpers. Module renamed from `sections.py` → `editing.py`. -3 tools from MCP schema.

**Issue:** #127

---

### Task 1: Create `src/tools/editing.py` with `edit_file` and tests

**Files:**
- Create: `src/tools/editing.py`
- Modify: `tests/test_tools_sections.py` (rewrite imports + test calls)

**Step 1: Write `src/tools/editing.py`**

Port the four operations into a single module with validation + dispatch:

```python
"""Editing tools - unified file content editing."""

import re

from services.vault import (
    err,
    find_section,
    get_relative_path,
    ok,
    resolve_file,
)


def edit_file(
    path: str,
    content: str,
    position: str,
    heading: str | None = None,
    mode: str | None = None,
) -> str:
    """Edit a vault file by inserting or replacing content.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to insert or replace with.
        position: Where to edit — "prepend", "append", or "section".
        heading: Required for position="section". Full heading with # symbols.
        mode: Required for position="section". One of "replace" or "append".

    Returns:
        JSON response: {"success": true, "path": "..."} on success,
        or {"success": false, "error": "..."} on failure.
    """
    if position == "prepend":
        return _prepend(path, content)
    elif position == "append":
        return _append(path, content)
    elif position == "section":
        if not heading:
            return err("heading is required when position is 'section'")
        if mode not in ("replace", "append"):
            return err("mode must be 'replace' or 'append' when position is 'section'")
        if mode == "replace":
            return _section_replace(path, content, heading)
        else:
            return _section_append(path, content, heading)
    else:
        return err(f"Unknown position: {position!r}. Must be 'prepend', 'append', or 'section'")


def _prepend(path: str, content: str) -> str:
    """Insert content after frontmatter (or at start if none)."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        existing_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", existing_content, re.DOTALL)

    if frontmatter_match:
        frontmatter_end = frontmatter_match.end()
        body = existing_content[frontmatter_end:]
        new_content = (
            existing_content[:frontmatter_end]
            + content
            + "\n\n"
            + body.lstrip("\n")
        )
    else:
        new_content = content + "\n\n" + existing_content.lstrip("\n")

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def _append(path: str, content: str) -> str:
    """Append content to end of file."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return err(f"Appending to file failed: {e}")

    rel = str(get_relative_path(file_path))
    return ok(f"Appended to {rel}", path=rel)


def _section_replace(path: str, content: str, heading: str) -> str:
    """Replace heading + content with new content."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    new_lines = lines[:section_start] + [content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def _section_append(path: str, content: str, heading: str) -> str:
    """Append content to end of section."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    new_lines = lines[:section_end] + ["", content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))
```

**Step 2: Rewrite tests**

Rewrite `tests/test_tools_sections.py` to test `edit_file` with appropriate params. Keep the same test classes but add validation tests:

```python
"""Tests for tools/editing.py - unified file editing."""

import json

import pytest

from tools.editing import edit_file


class TestEditFilePrepend:
    """Tests for edit_file with position='prepend'."""

    def test_prepend_with_frontmatter(self, vault_config):
        """Should prepend after frontmatter."""
        result = edit_file("note1.md", "**IMPORTANT NOTICE**", "prepend")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note1.md").read_text()
        assert content.index("IMPORTANT NOTICE") < content.index("# Note 1")
        assert content.index("---") < content.index("IMPORTANT NOTICE")

    def test_prepend_without_frontmatter(self, vault_config):
        """Should prepend at beginning when no frontmatter."""
        result = edit_file("note3.md", "Prepended content", "prepend")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note3.md").read_text()
        assert content.startswith("Prepended content")

    def test_prepend_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = edit_file("nonexistent.md", "content", "prepend")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestEditFileAppend:
    """Tests for edit_file with position='append'."""

    def test_append_content(self, vault_config):
        """Should append content to file."""
        result = json.loads(edit_file("note3.md", "\n## New Section\n\nAppended content.", "append"))
        assert result["success"] is True
        assert result["path"]

        content = (vault_config / "note3.md").read_text()
        assert "New Section" in content
        assert "Appended content" in content

    def test_append_to_nonexistent_file(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(edit_file("nonexistent.md", "content", "append"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestEditFileSectionReplace:
    """Tests for edit_file with position='section', mode='replace'."""

    def test_replace_section_basic(self, vault_config):
        """Should replace a section with new content."""
        result = edit_file("note2.md", "## Section A\n\nNew content for section A.", "section", heading="## Section A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "New content for section A" in content
        assert "Content in section A." not in content

    def test_replace_section_preserves_other_sections(self, vault_config):
        """Should not affect other sections."""
        result = edit_file("note2.md", "## Section A\n\nReplaced.", "section", heading="## Section A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "## Section B" in content
        assert "Content in section B" in content

    def test_replace_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = edit_file("note2.md", "content", "section", heading="## Nonexistent", mode="replace")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_replace_section_case_insensitive(self, vault_config):
        """Should match headings case-insensitively."""
        result = edit_file("note2.md", "## Section A\n\nReplaced.", "section", heading="## SECTION A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True


class TestEditFileSectionAppend:
    """Tests for edit_file with position='section', mode='append'."""

    def test_append_to_section_basic(self, vault_config):
        """Should append content at end of section."""
        result = edit_file("note2.md", "Appended text.", "section", heading="## Section A", mode="append")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Content in section A" in content
        assert "Appended text" in content
        assert content.index("Content in section A") < content.index("Appended text")
        assert content.index("Appended text") < content.index("## Section B")

    def test_append_to_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = edit_file("note2.md", "content", "section", heading="## Missing", mode="append")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_append_to_last_section(self, vault_config):
        """Should append to section at end of file."""
        result = edit_file("note2.md", "Final content.", "section", heading="## Section B", mode="append")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Final content" in content


class TestEditFileValidation:
    """Tests for edit_file parameter validation."""

    def test_unknown_position(self, vault_config):
        """Should reject unknown position values."""
        result = json.loads(edit_file("note1.md", "content", "unknown"))
        assert result["success"] is False
        assert "unknown position" in result["error"].lower()

    def test_section_without_heading(self, vault_config):
        """Should require heading for section position."""
        result = json.loads(edit_file("note1.md", "content", "section"))
        assert result["success"] is False
        assert "heading" in result["error"].lower()

    def test_section_without_mode(self, vault_config):
        """Should require mode for section position."""
        result = json.loads(edit_file("note1.md", "content", "section", heading="## Test"))
        assert result["success"] is False
        assert "mode" in result["error"].lower()

    def test_section_invalid_mode(self, vault_config):
        """Should reject invalid mode values."""
        result = json.loads(edit_file("note1.md", "content", "section", heading="## Test", mode="delete"))
        assert result["success"] is False
        assert "mode" in result["error"].lower()
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_sections.py -v`
Expected: All 15 tests pass (10 ported + 4 validation + 1 append)

**Step 4: Commit**

```bash
git add src/tools/editing.py tests/test_tools_sections.py
git commit -m "feat: add unified edit_file tool (#127)"
```

---

### Task 2: Remove old tools and update registrations

**Files:**
- Delete: `src/tools/sections.py`
- Modify: `src/tools/files.py:960-981` — remove `append_to_file`
- Modify: `src/mcp_server.py:17-18,44-48,68,84-87` — update imports and registrations
- Modify: `tests/test_tools_files.py:22,415-432` — remove `append_to_file` import and `TestAppendToFile`

**Step 1: Delete `src/tools/sections.py`**

```bash
rm src/tools/sections.py
```

**Step 2: Remove `append_to_file` from `src/tools/files.py`**

Delete the function at lines 960-981.

**Step 3: Update `src/mcp_server.py`**

Replace the imports and registrations:
- Remove `append_to_file` from `tools.files` import (line 18)
- Replace `from tools.sections import (...)` block (lines 44-48) with `from tools.editing import edit_file`
- Remove `mcp.tool()(append_to_file)` (line 68)
- Replace section tool registrations (lines 84-87) with `mcp.tool()(edit_file)`

**Step 4: Update `tests/test_tools_files.py`**

- Remove `append_to_file` from import (line 22)
- Delete `TestAppendToFile` class (lines 415-432)

**Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass, count drops by 2 (removed `TestAppendToFile` 2 tests) + gains 5 (4 validation + 1 append moved to sections test file)

**Step 6: Commit**

```bash
git add -u
git commit -m "refactor: remove old section/append tools, wire up edit_file (#127)"
```

---

### Task 3: Update documentation

**Files:**
- Modify: `system_prompt.txt.example:138-160` — replace 4 tool entries with single `edit_file`
- Modify: `CLAUDE.md` — update tool table

**Step 1: Update `system_prompt.txt.example`**

Replace lines 138-160 (the `append_to_file`, `prepend_to_file` entries and the Section Editing section) with:

```
- edit_file: Edit file content. Parameters: path, content, position, heading, mode.
  position="prepend": insert content after frontmatter (or at start if none).
  position="append": append content to end of file.
  position="section": edit a specific section (requires heading with # symbols,
  e.g. "## Meeting Notes", and mode). mode="replace" replaces the heading and
  its content. mode="append" appends to the end of the section. Heading matching
  is case-insensitive.
```

**Step 2: Update `CLAUDE.md` tool table**

Replace the 4 entries (`append_to_file`, `prepend_to_file`, `replace_section`, `append_to_section`) with single `edit_file` entry. Update the `src/tools/` listing to show `editing.py` instead of `sections.py`.

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: update tool references for edit_file rename (#127)"
```

---

### Task 4: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

**Step 2: Verify no stale references**

Run: `grep -r "prepend_to_file\|append_to_file\|replace_section\|append_to_section" src/ tests/ --include="*.py"`
Expected: No matches (only in docs/plans or git history).
