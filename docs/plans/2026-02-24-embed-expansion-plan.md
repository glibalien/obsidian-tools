# Embed Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Auto-expand `![[...]]` embeds in `read_file` so the LLM sees a unified document instead of opaque embed syntax.

**Architecture:** After reading `.md` content but before pagination, `_expand_embeds()` scans for embed patterns (skipping code blocks), resolves each file, and replaces the embed syntax with a labeled blockquote containing the expanded content. Binary results are cached in-memory by `(path, mtime)`.

**Tech Stack:** Python, existing `readers.py` handlers, `find_section()` from `vault.py`, `is_fence_line()` from `vault.py`.

---

### Task 1: `_extract_block` helper — test + implement

Extracts a block ID reference (`^blockid`) and its indented children from a list of lines.

**Files:**
- Modify: `src/tools/files.py` (add `_extract_block` function)
- Test: `tests/test_tools_files.py` (add `TestExtractBlock` class)

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py`, updating the import to include `_extract_block`:

```python
from tools.files import (
    ...existing imports...,
    _extract_block,
)


class TestExtractBlock:
    """Tests for _extract_block helper."""

    def test_simple_block_id(self):
        """Finds a line with ^blockid and returns it (suffix stripped)."""
        lines = ["# Heading", "- Item one ^abc123", "- Item two"]
        result = _extract_block(lines, "abc123")
        assert result == "- Item one"

    def test_block_with_indented_children(self):
        """Returns the anchor line plus all indented children."""
        lines = [
            "- Parent ^myblock",
            "  - Child 1",
            "  - Child 2",
            "    - Grandchild",
            "- Sibling (not included)",
        ]
        result = _extract_block(lines, "myblock")
        assert result == "- Parent\n  - Child 1\n  - Child 2\n    - Grandchild"

    def test_block_at_end_of_file(self):
        """Block at end of file with children up to EOF."""
        lines = [
            "Some intro",
            "- Last item ^endblock",
            "  - Sub-item",
        ]
        result = _extract_block(lines, "endblock")
        assert result == "- Last item\n  - Sub-item"

    def test_block_not_found(self):
        """Returns None when block ID doesn't exist."""
        lines = ["# Heading", "No blocks here"]
        result = _extract_block(lines, "nonexistent")
        assert result is None

    def test_block_id_mid_line(self):
        """Block ID must be at end of line (after space)."""
        lines = ["Text ^abc123 more text"]
        result = _extract_block(lines, "abc123")
        # Not at end of line, should not match
        assert result is None

    def test_block_no_children(self):
        """Block with no indented children returns just the anchor."""
        lines = [
            "- Item A ^solo",
            "- Item B",
        ]
        result = _extract_block(lines, "solo")
        assert result == "- Item A"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExtractBlock -v`
Expected: ImportError — `_extract_block` doesn't exist yet.

**Step 3: Implement `_extract_block`**

Add to `src/tools/files.py` after the `_BINARY_EXTENSIONS` line (before `read_file`):

```python
import re as _re_module  # already imported at top

_BLOCK_ID_RE = re.compile(r"\s\^(\S+)\s*$")


def _extract_block(lines: list[str], block_id: str) -> str | None:
    """Extract a block by its ^blockid suffix and all indented children.

    Args:
        lines: File content split into lines.
        block_id: The block ID to find (without ^ prefix).

    Returns:
        The anchor line (suffix stripped) plus indented children, or None if not found.
    """
    anchor_idx = None
    for i, line in enumerate(lines):
        m = _BLOCK_ID_RE.search(line)
        if m and m.group(1) == block_id:
            anchor_idx = i
            break

    if anchor_idx is None:
        return None

    # Strip the ^blockid suffix from the anchor line
    anchor_line = _BLOCK_ID_RE.sub("", lines[anchor_idx]).rstrip()

    # Determine the indentation of the anchor line
    anchor_indent = len(anchor_line) - len(anchor_line.lstrip())

    # Collect indented children
    result_lines = [anchor_line]
    for i in range(anchor_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            # Empty line — include if next non-empty line is still indented
            # For simplicity, stop at empty lines
            break
        line_indent = len(line) - len(line.lstrip())
        if line_indent > anchor_indent:
            result_lines.append(line)
        else:
            break

    return "\n".join(result_lines)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExtractBlock -v`
Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add _extract_block helper for block ID embed extraction"
```

---

### Task 2: `_expand_embeds` core logic — test + implement

The main function that scans markdown content for `![[...]]` patterns and expands them inline.

**Files:**
- Modify: `src/tools/files.py` (add `_expand_embeds`, `_resolve_embed`, `_format_embed`, `_embed_cache`)
- Test: `tests/test_tools_files.py` (add `TestExpandEmbeds` class)

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py`, updating the import to include `_expand_embeds` and `_embed_cache`:

```python
from tools.files import (
    ...existing imports...,
    _expand_embeds,
    _embed_cache,
)


class TestExpandEmbeds:
    """Tests for _expand_embeds — inline embed expansion."""

    def test_no_embeds_unchanged(self, vault_config):
        """Content without embeds is returned unchanged."""
        content = "# Hello\n\nNo embeds here."
        source = vault_config / "source.md"
        result = _expand_embeds(content, source)
        assert result == content

    def test_markdown_full_note_embed(self, vault_config):
        """![[note3]] expands to full note body (no frontmatter)."""
        content = "# Parent\n\n![[note3]]\n\nAfter."
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embedded: note3]" in result
        assert "> # Note 3" in result
        assert "![[note3]]" not in result
        assert "After." in result

    def test_markdown_embed_strips_frontmatter(self, vault_config):
        """Embedded markdown notes have frontmatter stripped."""
        content = "Before\n\n![[note1]]\n\nAfter"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embedded: note1]" in result
        assert "---" not in result.split("> [Embedded: note1]")[1].split("After")[0]
        assert "> # Note 1" in result

    def test_heading_embed(self, vault_config):
        """![[note2#Section A]] expands only that section."""
        content = "See: ![[note2#Section A]]"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embedded: note2#Section A]" in result
        assert "Content in section A" in result
        assert "Content in section B" not in result

    def test_block_id_embed(self, vault_config):
        """![[note#^blockid]] expands the block and its children."""
        (vault_config / "blocks.md").write_text(
            "# Blocks\n\n- Item one ^myid\n  - Child\n- Other\n"
        )
        content = "Reference: ![[blocks#^myid]]"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embedded: blocks#^myid]" in result
        assert "Item one" in result
        assert "Child" in result
        assert "Other" not in result

    def test_unresolved_embed_error_marker(self, vault_config):
        """Unresolvable embeds produce an error marker."""
        content = "![[nonexistent_file]]"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embed error: nonexistent_file" in result

    def test_self_embed_skipped(self, vault_config):
        """Self-referencing embeds produce an error marker."""
        (vault_config / "self.md").write_text("# Self\n\n![[self]]\n")
        result = _expand_embeds("# Self\n\n![[self]]\n", vault_config / "self.md")
        assert "> [Embed error: self" in result
        assert "self-reference" in result.lower()

    def test_embed_in_code_block_not_expanded(self, vault_config):
        """Embeds inside fenced code blocks are left as-is."""
        content = "# Doc\n\n```\n![[note3]]\n```\n\n![[note3]]\n"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        # The one inside the code block should be literal
        assert "```\n![[note3]]\n```" in result
        # The one outside should be expanded
        assert "> [Embedded: note3]" in result

    def test_multiple_embeds(self, vault_config):
        """Multiple embeds in one file are all expanded."""
        content = "![[note1]]\n\n![[note3]]"
        source = vault_config / "parent.md"
        result = _expand_embeds(content, source)
        assert "> [Embedded: note1]" in result
        assert "> [Embedded: note3]" in result

    def test_binary_embed_audio(self, vault_config, monkeypatch):
        """Audio embeds call handle_audio and format the result."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake audio")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            mock_audio.return_value = '{"success": true, "transcript": "Hello world"}'
            content = "![[rec.m4a]]"
            source = vault_config / "parent.md"
            _embed_cache.clear()
            result = _expand_embeds(content, source)
            assert "> [Embedded: rec.m4a]" in result
            assert "> Hello world" in result

    def test_binary_embed_cache_hit(self, vault_config, monkeypatch):
        """Second expansion of same binary embed uses cache."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake audio")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            mock_audio.return_value = '{"success": true, "transcript": "Cached"}'
            content = "![[rec.m4a]]"
            source = vault_config / "parent.md"
            _embed_cache.clear()
            _expand_embeds(content, source)
            _expand_embeds(content, source)
            # Handler called only once — second call uses cache
            assert mock_audio.call_count == 1
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExpandEmbeds -v`
Expected: ImportError — `_expand_embeds` doesn't exist yet.

**Step 3: Implement the embed expansion functions**

Add to `src/tools/files.py` after `_extract_block`, before `read_file`:

```python
from services.vault import (
    ...existing imports...,
    find_section,
    is_fence_line,
)

# In-memory cache for binary embed results: (path_str, mtime) -> content
_embed_cache: dict[tuple[str, float], str] = {}

_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")


def _expand_embeds(content: str, source_path: Path) -> str:
    """Expand ![[...]] embeds inline in markdown content.

    Scans for embed patterns outside code fences, resolves each file,
    and replaces the embed syntax with a labeled blockquote.

    Args:
        content: The raw markdown text.
        source_path: Path of the file being read (to detect self-embeds).

    Returns:
        Content with embeds replaced by expanded blockquotes.
    """
    lines = content.split("\n")
    result_lines: list[str] = []
    in_fence = False

    for line in lines:
        if is_fence_line(line):
            in_fence = not in_fence
            result_lines.append(line)
            continue

        if in_fence:
            result_lines.append(line)
            continue

        # Check for embeds on this line
        if "![[" not in line:
            result_lines.append(line)
            continue

        # Replace all embeds on the line
        new_line = _EMBED_RE.sub(
            lambda m: _resolve_and_format(m.group(1), source_path),
            line,
        )
        result_lines.append(new_line)

    return "\n".join(result_lines)


def _resolve_and_format(reference: str, source_path: Path) -> str:
    """Resolve an embed reference and return formatted blockquote.

    Args:
        reference: The inner part of ![[reference]] (filename, possibly with #fragment).
        source_path: The file containing the embed (for self-embed detection).

    Returns:
        Formatted blockquote string, or error marker.
    """
    # Parse reference: split on first #
    if "#" in reference:
        filename, fragment = reference.split("#", 1)
    else:
        filename, fragment = reference, None

    # Determine file extension
    if "." not in filename:
        # No extension — treat as markdown
        lookup_name = filename + ".md"
    else:
        lookup_name = filename

    ext = Path(lookup_name).suffix.lower()

    # Resolve the file
    file_path = _resolve_embed_file(lookup_name, ext)
    if file_path is None:
        return f"> [Embed error: {reference} — File not found]"

    # Self-embed check
    try:
        if file_path.resolve() == source_path.resolve():
            return f"> [Embed error: {reference} — Self-reference skipped]"
    except (OSError, ValueError):
        pass

    # Expand based on type
    if ext in _BINARY_EXTENSIONS:
        return _expand_binary(file_path, reference)

    # Markdown embed
    return _expand_markdown(file_path, reference, fragment)


def _resolve_embed_file(lookup_name: str, ext: str) -> Path | None:
    """Resolve an embed filename to a Path, with Attachments fallback for binaries."""
    file_path, error = resolve_file(lookup_name)
    if error and ext in _BINARY_EXTENSIONS:
        file_path, error = resolve_file(lookup_name, base_path=config.ATTACHMENTS_DIR)
    if error:
        return None
    return file_path


def _expand_binary(file_path: Path, reference: str) -> str:
    """Expand a binary embed (audio/image/office) with caching."""
    import json as _json

    # Check cache
    path_str = str(file_path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return f"> [Embed error: {reference} — Cannot stat file]"

    cache_key = (path_str, mtime)
    if cache_key in _embed_cache:
        expanded = _embed_cache[cache_key]
    else:
        ext = file_path.suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            raw = handle_audio(file_path)
        elif ext in IMAGE_EXTENSIONS:
            raw = handle_image(file_path)
        elif ext in OFFICE_EXTENSIONS:
            raw = handle_office(file_path)
        else:
            return f"> [Embed error: {reference} — Unsupported binary type]"

        result = _json.loads(raw)
        if not result.get("success"):
            return f"> [Embed error: {reference} — {result.get('error', 'Unknown error')}]"

        # Extract the content from the result
        expanded = (
            result.get("transcript")
            or result.get("description")
            or result.get("content")
            or ""
        )
        _embed_cache[cache_key] = expanded

    return _format_embed(reference, expanded)


def _expand_markdown(file_path: Path, reference: str, fragment: str | None) -> str:
    """Expand a markdown embed (full note, heading section, or block ID)."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return f"> [Embed error: {reference} — Cannot read file]"

    # Strip frontmatter
    fm_match = re.match(r"^---\n.*?^---(?:\n|$)", text, re.DOTALL | re.MULTILINE)
    body = text[fm_match.end():] if fm_match else text

    if fragment is None:
        # Full note
        return _format_embed(reference, body.strip())

    if fragment.startswith("^"):
        # Block ID
        block_id = fragment[1:]
        lines = text.split("\n")  # use full text for block search
        extracted = _extract_block(lines, block_id)
        if extracted is None:
            return f"> [Embed error: {reference} — Block ID not found]"
        return _format_embed(reference, extracted)

    # Heading section
    heading_text = fragment
    lines = body.split("\n")
    # Try to find the section — we need to construct the heading format
    # find_section expects "## Heading Text" format, but the fragment is just "Heading Text"
    # Search across heading levels
    section_start, section_end, error = _find_section_by_text(lines, heading_text)
    if error:
        return f"> [Embed error: {reference} — {error}]"

    section_lines = lines[section_start:section_end]
    return _format_embed(reference, "\n".join(section_lines).strip())


def _find_section_by_text(lines: list[str], heading_text: str) -> tuple[int | None, int | None, str | None]:
    """Find a section by heading text (without # prefix).

    Searches all heading levels for a case-insensitive match.
    """
    target = heading_text.lower().strip()

    # Find all headings that match
    from services.vault import _HEADING_PATTERN
    matches = []
    in_fence = False

    for i, line in enumerate(lines):
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_PATTERN.match(line)
        if m and m.group(2).strip().lower() == target:
            matches.append((i, len(m.group(1)), line))

    if not matches:
        return None, None, f"Heading not found: {heading_text}"

    if len(matches) > 1:
        line_nums = ", ".join(str(m[0] + 1) for m in matches)
        return None, None, f"Multiple headings match '{heading_text}': lines {line_nums}"

    start_idx, level, _ = matches[0]

    # Find section end: next heading of same or higher level
    section_end = len(lines)
    in_fence = False
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_PATTERN.match(line)
        if m and len(m.group(1)) <= level:
            section_end = i
            break

    return start_idx, section_end, None


def _format_embed(reference: str, content: str) -> str:
    """Format expanded embed as a labeled blockquote."""
    if not content.strip():
        return f"> [Embedded: {reference}]\n> (empty)"

    quoted_lines = [f"> {line}" if line.strip() else ">" for line in content.split("\n")]
    return f"> [Embedded: {reference}]\n" + "\n".join(quoted_lines)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestExpandEmbeds -v`
Expected: All 12 tests PASS.

**Step 5: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add _expand_embeds for inline embed expansion"
```

---

### Task 3: Wire `_expand_embeds` into `read_file` — test + implement

**Files:**
- Modify: `src/tools/files.py` (add one line in `read_file`)
- Test: `tests/test_tools_files.py` (add integration test to `TestReadFile`)

**Step 1: Write the failing integration test**

Add to `TestReadFile` class in `tests/test_tools_files.py`:

```python
    def test_read_file_expands_embeds(self, vault_config):
        """read_file on a .md file with embeds should auto-expand them."""
        (vault_config / "parent.md").write_text(
            "# Parent\n\nSee: ![[note3]]\n\nEnd.\n"
        )
        result = json.loads(read_file("parent.md"))
        assert result["success"] is True
        assert "> [Embedded: note3]" in result["content"]
        assert "> # Note 3" in result["content"]
        assert "![[note3]]" not in result["content"]

    def test_read_file_embeds_pagination(self, vault_config):
        """Pagination offsets apply to expanded content."""
        body = "x" * 100
        (vault_config / "embedded_target.md").write_text(f"# Target\n\n{body}\n")
        (vault_config / "paginate.md").write_text(
            "# Start\n\n![[embedded_target]]\n\n" + "y" * 5000
        )
        result = json.loads(read_file("paginate.md"))
        assert result["success"] is True
        # Expanded content should be present and pagination applied to total
        assert "> [Embedded: embedded_target]" in result["content"]
        assert "[... truncated" in result["content"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFile::test_read_file_expands_embeds -v`
Expected: FAIL — embed syntax returned literally.

**Step 3: Wire into read_file**

In `src/tools/files.py`, in the `read_file` function, after `content = file_path.read_text(...)` (line 70) and before `total = len(content)` (line 74), add:

```python
    content = _expand_embeds(content, file_path)
```

So it becomes:

```python
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading file: {e}")

    content = _expand_embeds(content, file_path)

    total = len(content)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFile -v`
Expected: All tests PASS (existing + 2 new).

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS. The `note_with_audio_embeds` fixture and existing tests should still work because:
- Tests reading non-.md files bypass expansion entirely.
- Tests reading .md files without embeds get content unchanged.
- The `note_with_audio_embeds` fixture creates a .md file with `![[meeting.m4a]]` embeds — these will now attempt expansion. Audio handler mocking may need adjustment if any existing test reads this file as markdown.

Check if any existing tests break and fix as needed.

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: wire embed expansion into read_file (#112)"
```

---

### Task 4: Update compaction stub for expanded embeds

The compaction system needs to handle the larger content that comes from expanded embeds. No functional change needed — the existing `read_file` stub builder in `services/compaction.py` already truncates content. Just verify it works.

**Files:**
- Test: `tests/test_tools_files.py` or manual verification

**Step 1: Verify compaction handles expanded content**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v -k read_file`
Expected: Existing compaction tests still pass. The stub builder truncates `content` regardless of whether embeds were expanded.

**Step 2: Commit (only if changes were needed)**

No commit expected unless compaction needed fixes.

---

### Task 5: Update CLAUDE.md and system prompt

**Files:**
- Modify: `CLAUDE.md` (update read_file description to mention embed expansion)
- Modify: `system_prompt.txt.example` (update read_file tool description)

**Step 1: Update CLAUDE.md**

In the MCP Tools table, update the `read_file` row description to mention auto-expanding embeds:

```
| `read_file` | Read any vault file (text, audio, image, Office). Auto-expands `![[embeds]]` in .md files. | `path`, `offset` (0), `length` (3500). Auto-dispatches by extension: audio→Whisper, image→vision model, .docx/.xlsx/.pptx→text extraction. Markdown files auto-expand `![[...]]` embeds inline (1 level deep). |
```

**Step 2: Update system_prompt.txt.example**

Add a note to the `read_file` tool description about embed expansion behavior so the agent knows embeds are already expanded.

**Step 3: Commit**

```bash
git add CLAUDE.md system_prompt.txt.example
git commit -m "docs: update CLAUDE.md and system prompt for embed expansion"
```

---

### Task 6: Create GitHub issue and feature branch cleanup

**Step 1: Verify all tests pass**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS.

**Step 2: Count tests and update MEMORY.md**

Check test count and update the memory file with the new count and session summary.
