# Content-Aware File Merge Tools — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `merge_files` and `batch_merge_files` MCP tools that intelligently merge duplicate vault files — deduplicating identical content, merging frontmatter (union lists, destination wins scalars), and positioning unique blocks under matching headings.

**Architecture:** Merge logic lives in private helpers in `src/tools/files.py`. `_split_frontmatter_body()` separates YAML from body. `_merge_frontmatter()` unions list fields and keeps destination scalars. `_split_blocks()` breaks body into `(heading_context, content)` tuples by headings/blank lines. `_merge_bodies()` dedup-compares normalized blocks, inserts unique source blocks under matching headings or appends at end. `merge_files` is the single-pair tool; `batch_merge_files` calls `compare_folders` internally and follows the existing `store_preview`/`consume_preview` confirmation gate.

**Tech Stack:** Python, PyYAML (already a dependency), existing vault service helpers, FastMCP.

---

### Task 1: Frontmatter split/merge helpers + tests

**Files:**
- Modify: `src/tools/files.py` (add helpers)
- Modify: `tests/test_tools_files.py` (add tests)

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py` at the end:

```python
from tools.files import _split_frontmatter_body, _merge_frontmatter


class TestSplitFrontmatterBody:
    """Tests for _split_frontmatter_body helper."""

    def test_file_with_frontmatter(self):
        content = "---\ntitle: Test\ntags:\n  - a\n---\n\n# Body\n\nParagraph."
        fm, body = _split_frontmatter_body(content)
        assert fm == {"title": "Test", "tags": ["a"]}
        assert body.strip() == "# Body\n\nParagraph."

    def test_file_without_frontmatter(self):
        content = "# Just a heading\n\nSome text."
        fm, body = _split_frontmatter_body(content)
        assert fm == {}
        assert body == content

    def test_empty_frontmatter(self):
        content = "---\n---\n\nBody text."
        fm, body = _split_frontmatter_body(content)
        assert fm == {}
        assert body.strip() == "Body text."

    def test_frontmatter_with_empty_body(self):
        content = "---\ntitle: Note\n---\n"
        fm, body = _split_frontmatter_body(content)
        assert fm == {"title": "Note"}
        assert body.strip() == ""


class TestMergeFrontmatter:
    """Tests for _merge_frontmatter helper."""

    def test_source_adds_new_fields(self):
        source = {"author": "Alice", "tags": ["draft"]}
        dest = {"title": "Note"}
        merged = _merge_frontmatter(source, dest)
        assert merged == {"title": "Note", "author": "Alice", "tags": ["draft"]}

    def test_destination_wins_scalar_conflict(self):
        source = {"title": "Old Title", "author": "Alice"}
        dest = {"title": "New Title"}
        merged = _merge_frontmatter(source, dest)
        assert merged["title"] == "New Title"
        assert merged["author"] == "Alice"

    def test_list_fields_union_deduped(self):
        source = {"tags": ["a", "b", "c"]}
        dest = {"tags": ["b", "c", "d"]}
        merged = _merge_frontmatter(source, dest)
        assert merged["tags"] == ["b", "c", "d", "a"]

    def test_source_list_dest_scalar_dest_wins(self):
        source = {"status": ["draft", "review"]}
        dest = {"status": "published"}
        merged = _merge_frontmatter(source, dest)
        assert merged["status"] == "published"

    def test_both_empty(self):
        assert _merge_frontmatter({}, {}) == {}

    def test_source_empty(self):
        dest = {"title": "Keep"}
        assert _merge_frontmatter({}, dest) == {"title": "Keep"}

    def test_dest_empty(self):
        source = {"title": "Bring"}
        assert _merge_frontmatter(source, {}) == {"title": "Bring"}

    def test_identical_frontmatter_unchanged(self):
        fm = {"title": "Same", "tags": ["a", "b"]}
        merged = _merge_frontmatter(fm.copy(), fm.copy())
        assert merged == fm
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestSplitFrontmatterBody tests/test_tools_files.py::TestMergeFrontmatter -v`
Expected: ImportError — `_split_frontmatter_body` and `_merge_frontmatter` don't exist yet.

**Step 3: Write minimal implementation**

Add to `src/tools/files.py` after the `_parse_frontmatter` function (after line 148):

```python
import re

# (add `re` to imports at top of file)


def _split_frontmatter_body(content: str) -> tuple[dict, str]:
    """Split a markdown file's content into frontmatter dict and body string.

    Returns:
        Tuple of (frontmatter_dict, body_string). Frontmatter is empty dict
        if the file has no valid YAML frontmatter block.
    """
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return {}, content

    fm = yaml.safe_load(match.group(1)) or {}
    body = content[match.end():]
    return fm, body


def _merge_frontmatter(source_fm: dict, dest_fm: dict) -> dict:
    """Merge source frontmatter into destination frontmatter.

    Rules:
    - Fields only in source are added to result.
    - Fields only in destination are kept as-is.
    - Both are lists: union (destination order first, then unique source items).
    - Both exist but destination is scalar: destination wins.
    - Identical values: kept as-is.
    """
    merged = dict(dest_fm)
    for key, src_val in source_fm.items():
        if key not in merged:
            merged[key] = src_val
        elif isinstance(merged[key], list) and isinstance(src_val, list):
            # Union: dest order first, then items from source not in dest
            existing = set()
            for item in merged[key]:
                existing.add(item if not isinstance(item, list) else tuple(item))
            for item in src_val:
                hashable = item if not isinstance(item, list) else tuple(item)
                if hashable not in existing:
                    merged[key].append(item)
                    existing.add(hashable)
        # else: dest wins (scalar conflict, or type mismatch)
    return merged
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestSplitFrontmatterBody tests/test_tools_files.py::TestMergeFrontmatter -v`
Expected: All 12 tests PASS.

**Step 5: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add frontmatter split/merge helpers for file merge (#101)"
```

---

### Task 2: Body block splitting and dedup helpers + tests

**Files:**
- Modify: `src/tools/files.py` (add helpers)
- Modify: `tests/test_tools_files.py` (add tests)

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py`:

```python
from tools.files import _split_blocks, _merge_bodies


class TestSplitBlocks:
    """Tests for _split_blocks helper."""

    def test_split_by_headings(self):
        body = "# Intro\n\nParagraph.\n\n## Tasks\n\n- Item 1\n- Item 2\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 2
        assert blocks[0] == ("# Intro", "# Intro\n\nParagraph.\n")
        assert blocks[1] == ("## Tasks", "## Tasks\n\n- Item 1\n- Item 2\n")

    def test_content_before_first_heading(self):
        body = "Some intro text.\n\n# Heading\n\nContent.\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 2
        assert blocks[0] == (None, "Some intro text.\n")
        assert blocks[1] == ("# Heading", "# Heading\n\nContent.\n")

    def test_no_headings(self):
        body = "Just a paragraph.\n\nAnother paragraph.\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 1
        assert blocks[0] == (None, body)

    def test_empty_body(self):
        blocks = _split_blocks("")
        assert blocks == []

    def test_whitespace_only(self):
        blocks = _split_blocks("  \n\n  \n")
        assert blocks == []


class TestMergeBodies:
    """Tests for _merge_bodies helper."""

    def test_identical_bodies_no_change(self):
        body = "# Tasks\n\n- Item 1\n- Item 2\n"
        merged, stats = _merge_bodies(body, body)
        assert merged == body
        assert stats["blocks_added"] == 0

    def test_source_has_unique_block_under_existing_heading(self):
        source = "# Tasks\n\n- Item 1\n\n# Notes\n\nSource note.\n"
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, dest)
        assert "Source note." in merged
        assert "# Notes" in merged
        assert stats["blocks_added"] == 1

    def test_source_unique_block_appended_when_no_heading_match(self):
        source = "# Unrelated\n\nNew stuff.\n"
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, dest)
        assert "New stuff." in merged
        assert "# Unrelated" in merged
        # Original content preserved
        assert merged.startswith("# Tasks\n")
        assert stats["blocks_added"] == 1

    def test_duplicate_blocks_not_added(self):
        source = "# Tasks\n\n- Item 1\n\n# Notes\n\nShared note.\n"
        dest = "# Tasks\n\n- Item 1\n\n# Notes\n\nShared note.\n"
        merged, stats = _merge_bodies(source, dest)
        assert merged == dest
        assert stats["blocks_added"] == 0

    def test_partial_overlap(self):
        source = "# Tasks\n\n- Item 1\n\n# Log\n\nEntry A.\n"
        dest = "# Tasks\n\n- Item 1\n\n# Log\n\nEntry B.\n"
        merged, stats = _merge_bodies(source, dest)
        assert "Entry A." in merged
        assert "Entry B." in merged
        assert stats["blocks_added"] == 1

    def test_empty_source_body(self):
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies("", dest)
        assert merged == dest
        assert stats["blocks_added"] == 0

    def test_empty_dest_body(self):
        source = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, "")
        assert "# Tasks" in merged
        assert "- Item 1" in merged
        assert stats["blocks_added"] == 1
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestSplitBlocks tests/test_tools_files.py::TestMergeBodies -v`
Expected: ImportError — `_split_blocks` and `_merge_bodies` don't exist yet.

**Step 3: Write minimal implementation**

Add to `src/tools/files.py` after `_merge_frontmatter`:

```python
_HEADING_RE = re.compile(r"^(#+)\s+", re.MULTILINE)


def _split_blocks(body: str) -> list[tuple[str | None, str]]:
    """Split a markdown body into blocks by headings.

    Each block is a (heading, content) tuple where heading is the full heading
    line (e.g. "## Tasks") or None for content before the first heading.
    Content includes the heading line itself and all text until the next heading.

    Returns:
        List of (heading_context, block_content) tuples. Empty list for empty/whitespace body.
    """
    if not body or not body.strip():
        return []

    # Find all heading positions
    headings = [(m.start(), m) for m in _HEADING_RE.finditer(body)]

    if not headings:
        return [(None, body)]

    blocks = []
    # Content before first heading
    first_pos = headings[0][0]
    if first_pos > 0:
        pre_content = body[:first_pos]
        if pre_content.strip():
            blocks.append((None, pre_content))

    # Each heading starts a block that runs until the next heading
    for i, (pos, match) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(body)
        block_text = body[pos:end]
        heading_line = body[pos:body.index("\n", pos)] if "\n" in body[pos:] else body[pos:]
        blocks.append((heading_line, block_text))

    return blocks


def _normalize_block(text: str) -> str:
    """Normalize a block for comparison: strip, collapse whitespace."""
    return " ".join(text.split())


def _merge_bodies(source_body: str, dest_body: str) -> tuple[str, dict]:
    """Merge unique blocks from source into destination body.

    Blocks from source that already exist in destination (after normalization)
    are skipped. Unique source blocks are placed under matching headings in
    destination if possible, otherwise appended at the end.

    Returns:
        Tuple of (merged_body, stats_dict) where stats_dict has "blocks_added" count.
    """
    source_blocks = _split_blocks(source_body)
    dest_blocks = _split_blocks(dest_body)

    if not source_blocks:
        return dest_body, {"blocks_added": 0}

    # Build set of normalized dest block contents for dedup
    dest_normalized = {_normalize_block(content) for _, content in dest_blocks}

    # Find unique source blocks
    unique_blocks: list[tuple[str | None, str]] = []
    for heading, content in source_blocks:
        if _normalize_block(content) not in dest_normalized:
            unique_blocks.append((heading, content))

    if not unique_blocks:
        return dest_body, {"blocks_added": 0}

    # Build a map of dest heading -> index for insertion
    dest_heading_indices: dict[str, int] = {}
    for i, (heading, _) in enumerate(dest_blocks):
        if heading is not None:
            dest_heading_indices[heading.lower()] = i

    # Insert unique blocks: after matching heading section, or append
    # Work with dest_blocks as a mutable list, track insertions
    appended: list[tuple[str | None, str]] = []
    # Group insertions by dest index to maintain order
    insertions: dict[int, list[tuple[str | None, str]]] = {}

    for heading, content in unique_blocks:
        if heading is not None and heading.lower() in dest_heading_indices:
            idx = dest_heading_indices[heading.lower()]
            insertions.setdefault(idx, []).append((heading, content))
        else:
            appended.append((heading, content))

    # Rebuild: interleave dest blocks with insertions
    result_blocks: list[str] = []
    for i, (_, content) in enumerate(dest_blocks):
        result_blocks.append(content)
        if i in insertions:
            for _, ins_content in insertions[i]:
                result_blocks.append(ins_content)

    for _, content in appended:
        result_blocks.append(content)

    merged = "".join(result_blocks)
    return merged, {"blocks_added": len(unique_blocks)}
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestSplitBlocks tests/test_tools_files.py::TestMergeBodies -v`
Expected: All 12 tests PASS.

**Step 5: Run full file tests for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -v`
Expected: All existing tests still PASS.

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add body block split/merge helpers for file merge (#101)"
```

---

### Task 3: `merge_files` tool + tests

**Files:**
- Modify: `src/tools/files.py` (add `merge_files` function)
- Modify: `tests/test_tools_files.py` (add tests)

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py`:

```python
from tools.files import merge_files


class TestMergeFiles:
    """Tests for merge_files tool."""

    def test_identical_files_deletes_source(self, vault_config):
        """Identical files: source deleted, dest unchanged."""
        content = "---\ntitle: Note\n---\n\n# Body\n\nSame content.\n"
        (vault_config / "src.md").write_text(content)
        (vault_config / "dst.md").write_text(content)
        result = json.loads(merge_files("src.md", "dst.md"))
        assert result["success"] is True
        assert not (vault_config / "src.md").exists()
        assert (vault_config / "dst.md").read_text() == content
        assert result["action"] == "identical"

    def test_frontmatter_only_diff(self, vault_config):
        """Source has extra frontmatter fields, bodies identical."""
        src = "---\ntitle: Note\nauthor: Alice\n---\n\n# Body\n\nText.\n"
        dst = "---\ntitle: Note\n---\n\n# Body\n\nText.\n"
        (vault_config / "src.md").write_text(src)
        (vault_config / "dst.md").write_text(dst)
        result = json.loads(merge_files("src.md", "dst.md"))
        assert result["success"] is True
        assert not (vault_config / "src.md").exists()
        merged = (vault_config / "dst.md").read_text()
        assert "author: Alice" in merged
        assert "title: Note" in merged
        assert result["action"] == "frontmatter_merged"

    def test_body_diff_appended(self, vault_config):
        """Source has unique section, merged into dest."""
        src = "# Tasks\n\n- Item 1\n\n# Extra\n\nNew content.\n"
        dst = "# Tasks\n\n- Item 1\n"
        (vault_config / "src.md").write_text(src)
        (vault_config / "dst.md").write_text(dst)
        result = json.loads(merge_files("src.md", "dst.md"))
        assert result["success"] is True
        merged = (vault_config / "dst.md").read_text()
        assert "New content." in merged
        assert "# Extra" in merged
        assert result["action"] == "content_merged"
        assert result["blocks_added"] == 1

    def test_delete_source_false(self, vault_config):
        """delete_source=False preserves source file."""
        content = "# Same\n"
        (vault_config / "src.md").write_text(content)
        (vault_config / "dst.md").write_text(content)
        result = json.loads(merge_files("src.md", "dst.md", delete_source=False))
        assert result["success"] is True
        assert (vault_config / "src.md").exists()

    def test_source_not_found(self, vault_config):
        (vault_config / "dst.md").write_text("# Dest\n")
        result = json.loads(merge_files("nonexistent.md", "dst.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_destination_not_found(self, vault_config):
        (vault_config / "src.md").write_text("# Source\n")
        result = json.loads(merge_files("src.md", "nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_concat_strategy(self, vault_config):
        """Concat strategy concatenates without dedup."""
        (vault_config / "src.md").write_text("# Source\n\nSource body.\n")
        (vault_config / "dst.md").write_text("# Dest\n\nDest body.\n")
        result = json.loads(merge_files("src.md", "dst.md", strategy="concat"))
        assert result["success"] is True
        merged = (vault_config / "dst.md").read_text()
        assert "Dest body." in merged
        assert "Source body." in merged
        # Source kept by default for concat
        assert (vault_config / "src.md").exists()

    def test_frontmatter_list_merge(self, vault_config):
        """List fields in frontmatter are unioned."""
        src = "---\ntags:\n  - a\n  - b\n---\n\n# Body\n"
        dst = "---\ntags:\n  - b\n  - c\n---\n\n# Body\n"
        (vault_config / "src.md").write_text(src)
        (vault_config / "dst.md").write_text(dst)
        merge_files("src.md", "dst.md")
        merged = (vault_config / "dst.md").read_text()
        # dest order first, then source unique items
        assert "- b" in merged
        assert "- c" in merged
        assert "- a" in merged

    def test_merge_with_heading_positioning(self, vault_config):
        """Unique source block placed after matching heading section in dest."""
        src = "# Log\n\nEntry from source.\n\n# Tasks\n\n- Source task\n"
        dst = "# Tasks\n\n- Dest task\n\n# Log\n\nEntry from dest.\n"
        (vault_config / "src.md").write_text(src)
        (vault_config / "dst.md").write_text(dst)
        result = json.loads(merge_files("src.md", "dst.md"))
        merged = (vault_config / "dst.md").read_text()
        # Source's "# Log" block should appear near dest's "# Log" section
        log_pos = merged.index("Entry from dest.")
        source_log_pos = merged.index("Entry from source.")
        tasks_pos = merged.index("- Dest task")
        source_tasks_pos = merged.index("- Source task")
        # Source log entry near dest log entry, source task near dest task
        assert source_log_pos > log_pos
        assert source_tasks_pos > tasks_pos

    def test_invalid_strategy(self, vault_config):
        (vault_config / "src.md").write_text("# A\n")
        (vault_config / "dst.md").write_text("# B\n")
        result = json.loads(merge_files("src.md", "dst.md", strategy="invalid"))
        assert result["success"] is False
        assert "strategy" in result["error"].lower()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestMergeFiles -v`
Expected: ImportError — `merge_files` doesn't exist yet.

**Step 3: Write minimal implementation**

Add to `src/tools/files.py` after the helper functions:

```python
def merge_files(
    source: str,
    destination: str,
    strategy: str = "smart",
    delete_source: bool | None = None,
) -> str:
    """Merge a source file into a destination file.

    Args:
        source: Path to the source ("from") file.
        destination: Path to the destination ("to") file. Must exist.
        strategy: "smart" (content-aware dedup) or "concat" (simple concatenation).
        delete_source: Delete source after merge. Defaults to True for smart, False for concat.

    Returns:
        JSON response describing what happened.
    """
    if strategy not in ("smart", "concat"):
        return err(f"Invalid strategy: {strategy!r}. Must be 'smart' or 'concat'.")

    if delete_source is None:
        delete_source = strategy == "smart"

    source_path, src_err = resolve_file(source)
    if src_err:
        return err(src_err)

    dest_path, dst_err = resolve_file(destination)
    if dst_err:
        return err(dst_err)

    try:
        src_content = source_path.read_text(encoding="utf-8", errors="ignore")
        dst_content = dest_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading files: {e}")

    if strategy == "concat":
        return _merge_concat(source_path, dest_path, src_content, dst_content, delete_source, source)

    return _merge_smart(source_path, dest_path, src_content, dst_content, delete_source, source)


def _merge_concat(
    source_path, dest_path, src_content, dst_content, delete_source, source_rel,
) -> str:
    """Simple concatenation merge with filename separator."""
    separator = f"\n\n---\n\n*Merged from {source_rel}:*\n\n"
    merged = dst_content.rstrip() + separator + src_content.lstrip()

    try:
        dest_path.write_text(merged, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing merged file: {e}")

    if delete_source:
        source_path.unlink()

    dest_rel = str(get_relative_path(dest_path))
    return ok(
        f"Concatenated {source_rel} into {dest_rel}",
        action="concatenated",
        path=dest_rel,
    )


def _merge_smart(
    source_path, dest_path, src_content, dst_content, delete_source, source_rel,
) -> str:
    """Content-aware smart merge with dedup."""
    src_fm, src_body = _split_frontmatter_body(src_content)
    dst_fm, dst_body = _split_frontmatter_body(dst_content)

    # Check if bodies are identical (normalized)
    bodies_identical = _normalize_block(src_body) == _normalize_block(dst_body)

    # Merge frontmatter
    merged_fm = _merge_frontmatter(src_fm, dst_fm)
    fm_changed = merged_fm != dst_fm

    # Merge bodies
    if bodies_identical:
        merged_body = dst_body
        blocks_added = 0
    else:
        merged_body, stats = _merge_bodies(src_body, dst_body)
        blocks_added = stats["blocks_added"]

    # Determine action
    if not fm_changed and blocks_added == 0:
        action = "identical"
    elif fm_changed and blocks_added == 0:
        action = "frontmatter_merged"
    else:
        action = "content_merged"

    # Rebuild file content
    if merged_fm:
        fm_yaml = yaml.dump(merged_fm, default_flow_style=False, allow_unicode=True)
        new_content = f"---\n{fm_yaml}---\n{merged_body}"
    else:
        new_content = merged_body

    try:
        dest_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing merged file: {e}")

    if delete_source:
        source_path.unlink()

    dest_rel = str(get_relative_path(dest_path))
    return ok(
        f"Merged {source_rel} into {dest_rel} ({action})",
        action=action,
        path=dest_rel,
        blocks_added=blocks_added,
        frontmatter_changed=fm_changed,
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestMergeFiles -v`
Expected: All 10 tests PASS.

**Step 5: Run full test suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -v`
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add merge_files tool with smart and concat strategies (#101)"
```

---

### Task 4: `batch_merge_files` tool + tests

**Files:**
- Modify: `src/tools/files.py` (add `batch_merge_files`, add import for `compare_folders`)
- Modify: `tests/test_tools_files.py` (add tests)

**Context:** `compare_folders` (in `src/tools/links.py`) returns `in_both` as a list of dicts with `source_paths` and `target_paths` (both are lists, to handle recursive mode where multiple files may share a stem). For `batch_merge_files`, each `in_both` entry may produce multiple merge pairs. The simplest approach: merge the first source path into the first target path for each stem. If there are multiple source paths for one stem, merge each into the same target. Document this behavior.

**Step 1: Write the failing tests**

Add to `tests/test_tools_files.py`:

```python
from tools.files import batch_merge_files


class TestBatchMergeFiles:
    """Tests for batch_merge_files tool."""

    def _setup_folders(self, vault_config, pairs):
        """Create source/target folders with file pairs.

        pairs: list of (filename, source_content, target_content)
        """
        src_dir = vault_config / "import"
        dst_dir = vault_config / "Daily Notes"
        src_dir.mkdir(exist_ok=True)
        dst_dir.mkdir(exist_ok=True)
        for name, src_content, dst_content in pairs:
            (src_dir / name).write_text(src_content)
            (dst_dir / name).write_text(dst_content)
        return "import", "Daily Notes"

    def test_batch_merge_identical_files(self, vault_config):
        """All identical pairs: sources deleted, dests unchanged."""
        clear_pending_previews()
        src_folder, dst_folder = self._setup_folders(vault_config, [
            ("2022-01-01.md", "# Jan 1\n\nContent.\n", "# Jan 1\n\nContent.\n"),
            ("2022-01-02.md", "# Jan 2\n\nContent.\n", "# Jan 2\n\nContent.\n"),
        ])
        result = json.loads(batch_merge_files(src_folder, dst_folder))
        assert result["success"] is True
        assert result["merged"] == 2
        assert not (vault_config / "import" / "2022-01-01.md").exists()
        assert not (vault_config / "import" / "2022-01-02.md").exists()

    def test_batch_merge_with_diffs(self, vault_config):
        """Mixed: one identical, one with unique content."""
        clear_pending_previews()
        src_folder, dst_folder = self._setup_folders(vault_config, [
            ("same.md", "# Same\n", "# Same\n"),
            ("diff.md", "# Diff\n\nExtra.\n", "# Diff\n"),
        ])
        result = json.loads(batch_merge_files(src_folder, dst_folder))
        assert result["success"] is True
        assert result["merged"] == 2
        merged = (vault_config / "Daily Notes" / "diff.md").read_text()
        assert "Extra." in merged

    def test_batch_confirmation_gate(self, vault_config):
        """Should require confirmation when >5 pairs."""
        clear_pending_previews()
        src_dir = vault_config / "bulk_src"
        dst_dir = vault_config / "bulk_dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        for i in range(8):
            (src_dir / f"note{i}.md").write_text(f"# Note {i}\n")
            (dst_dir / f"note{i}.md").write_text(f"# Note {i}\n")

        result = json.loads(batch_merge_files("bulk_src", "bulk_dst"))
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert "8" in result["preview_message"]
        # No files should be merged yet
        for i in range(8):
            assert (src_dir / f"note{i}.md").exists()

    def test_batch_confirm_executes(self, vault_config):
        """Preview then confirm should execute the batch."""
        clear_pending_previews()
        src_dir = vault_config / "conf_src"
        dst_dir = vault_config / "conf_dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        for i in range(8):
            (src_dir / f"n{i}.md").write_text(f"# N {i}\n")
            (dst_dir / f"n{i}.md").write_text(f"# N {i}\n")

        # Preview
        batch_merge_files("conf_src", "conf_dst")
        # Confirm
        result = json.loads(batch_merge_files("conf_src", "conf_dst", confirm=True))
        assert result["success"] is True
        assert result["merged"] == 8

    def test_batch_reports_only_in_source(self, vault_config):
        """Files only in source should be reported but not touched."""
        clear_pending_previews()
        src_dir = vault_config / "report_src"
        dst_dir = vault_config / "report_dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "shared.md").write_text("# Shared\n")
        (dst_dir / "shared.md").write_text("# Shared\n")
        (src_dir / "orphan.md").write_text("# Orphan\n")

        result = json.loads(batch_merge_files("report_src", "report_dst"))
        assert result["success"] is True
        assert result["merged"] == 1
        assert result["skipped_source_only"] == 1
        assert (src_dir / "orphan.md").exists()  # untouched

    def test_batch_no_overlap(self, vault_config):
        """No overlapping files: nothing to merge."""
        clear_pending_previews()
        src_dir = vault_config / "no_overlap_src"
        dst_dir = vault_config / "no_overlap_dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "a.md").write_text("# A\n")
        (dst_dir / "b.md").write_text("# B\n")

        result = json.loads(batch_merge_files("no_overlap_src", "no_overlap_dst"))
        assert result["success"] is True
        assert result["merged"] == 0

    def test_batch_recursive(self, vault_config):
        """Recursive mode merges files in subfolders."""
        clear_pending_previews()
        src_dir = vault_config / "rec_src"
        dst_dir = vault_config / "rec_dst"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "sub").mkdir()
        (src_dir / "sub" / "deep.md").write_text("# Deep\n")
        (dst_dir / "deep.md").write_text("# Deep\n")

        # Non-recursive: no match
        result = json.loads(batch_merge_files("rec_src", "rec_dst"))
        assert result["merged"] == 0

        # Recursive: matches
        result = json.loads(batch_merge_files("rec_src", "rec_dst", recursive=True))
        assert result["merged"] == 1
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchMergeFiles -v`
Expected: ImportError — `batch_merge_files` doesn't exist yet.

**Step 3: Write minimal implementation**

Add import at top of `src/tools/files.py`:

```python
from tools.links import compare_folders as _compare_folders
```

Add `batch_merge_files` function after `merge_files`:

```python
def batch_merge_files(
    source_folder: str,
    destination_folder: str,
    recursive: bool = False,
    strategy: str = "smart",
    delete_source: bool | None = None,
    confirm: bool = False,
) -> str:
    """Merge duplicate files between two folders.

    Uses compare_folders to find files with matching names in both folders,
    then merges each source file into the corresponding destination file.
    Files only in source or only in destination are reported but not touched.

    Args:
        source_folder: Folder containing "from" files.
        destination_folder: Folder containing "to" files.
        recursive: Include subfolders. Default False.
        strategy: "smart" (content-aware dedup) or "concat".
        delete_source: Delete source after merge. Defaults to True for smart, False for concat.
        confirm: Must be true to execute when merging more than 5 file pairs.

    Returns:
        JSON response with merge results, or confirmation preview for large batches.
    """
    if strategy not in ("smart", "concat"):
        return err(f"Invalid strategy: {strategy!r}. Must be 'smart' or 'concat'.")

    if delete_source is None:
        delete_source = strategy == "smart"

    # Use compare_folders to find matching pairs
    comparison = json.loads(_compare_folders(source_folder, destination_folder, recursive=recursive))
    if not comparison.get("success"):
        return err(comparison.get("error", "Folder comparison failed"))

    in_both = comparison.get("in_both", [])
    only_in_source = comparison.get("only_in_source", [])
    only_in_target = comparison.get("only_in_target", [])

    if not in_both:
        return ok(
            f"No overlapping files between '{source_folder}' and '{destination_folder}'",
            merged=0,
            skipped_source_only=len(only_in_source),
            skipped_target_only=len(only_in_target),
        )

    # Build merge pairs: [(source_path, dest_path), ...]
    pairs = []
    for entry in in_both:
        source_paths = entry["source_paths"]
        target_paths = entry["target_paths"]
        # Merge each source into the first target
        target = target_paths[0]
        for src in source_paths:
            pairs.append((src, target))

    # Confirmation gate for large batches
    if len(pairs) > BATCH_CONFIRM_THRESHOLD:
        pair_keys = tuple((s, d) for s, d in pairs)
        key = ("batch_merge_files", pair_keys)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            files = [f"{s} → {d}" for s, d in pairs]
            return ok(
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                preview_message=f"This will merge {len(pairs)} file pairs from '{source_folder}' into '{destination_folder}'.",
                files=files,
            )

    # Execute merges
    results = []
    for src_path, dst_path in pairs:
        result_json = merge_files(src_path, dst_path, strategy=strategy, delete_source=delete_source)
        result = json.loads(result_json)
        results.append(result)

    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    return ok(
        f"Batch merge: {len(succeeded)} merged, {len(failed)} failed",
        merged=len(succeeded),
        failed=len(failed),
        skipped_source_only=len(only_in_source),
        skipped_target_only=len(only_in_target),
        details=[
            {"action": r.get("action"), "path": r.get("path")}
            for r in succeeded
        ],
        errors=[r.get("error") for r in failed],
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchMergeFiles -v`
Expected: All 7 tests PASS.

**Step 5: Run full test suite for regressions**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS.

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add batch_merge_files tool with confirmation gate (#101)"
```

---

### Task 5: Register tools in MCP server

**Files:**
- Modify: `src/mcp_server.py`

**Step 1: Update imports**

Change the files import block (line 17-23) to include the new tools:

```python
from tools.files import (
    append_to_file,
    batch_merge_files,
    batch_move_files,
    create_file,
    merge_files,
    move_file,
    read_file,
)
```

**Step 2: Register tools**

After `mcp.tool()(append_to_file)` (line 71), add:

```python
mcp.tool()(merge_files)
mcp.tool()(batch_merge_files)
```

**Step 3: Verify no import errors**

Run: `.venv/bin/python -c "import mcp_server; print('OK')"` (from `src/` directory)
Expected: `OK`

**Step 4: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: register merge_files and batch_merge_files in MCP server (#101)"
```

---

### Task 6: Update system prompt and CLAUDE.md

**Files:**
- Modify: `system_prompt.txt.example` (add merge tools to tool reference and decision tree)
- Modify: `CLAUDE.md` (add merge tools to MCP tools table)

**Step 1: Add merge tools to system prompt tool reference**

Find the tool reference section in `system_prompt.txt.example` and add entries for `merge_files` and `batch_merge_files`. Add a decision tree entry like:

```
- "Merge duplicate files" / "deduplicate imported notes" → batch_merge_files (uses compare_folders internally)
- "Merge two specific files" → merge_files
```

**Step 2: Add to CLAUDE.md MCP tools table**

Add rows:

| `merge_files` | Merge source into destination | `source`, `destination`, `strategy` ("smart"/"concat"), `delete_source` (bool) |
| `batch_merge_files` | Batch merge duplicates across folders | `source_folder`, `destination_folder`, `recursive`, `strategy`, `delete_source`, `confirm` |

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: add merge tools to system prompt and CLAUDE.md (#101)"
```

---

### Task 7: Final verification and cleanup

**Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS (existing ~449 + ~41 new = ~490).

**Step 2: Verify MCP server starts cleanly**

Run: `.venv/bin/python -c "import sys; sys.path.insert(0, 'src'); import mcp_server; print(f'Tools registered: OK')"`
Expected: No errors.

**Step 3: Manual smoke test (optional)**

Create two test files manually and verify `merge_files` works as expected via a quick Python script.

**Step 4: Create PR**

```bash
gh pr create --title "feat: add content-aware file merge tools" --body "$(cat <<'EOF'
## Summary
- Adds `merge_files` tool: merges a source file into a destination with content-aware dedup (smart strategy) or simple concatenation (concat strategy)
- Adds `batch_merge_files` tool: uses `compare_folders` internally to find duplicate filename stems across two folders, then merges each pair
- Smart merge: deduplicates identical bodies, unions frontmatter lists (dest wins scalar conflicts), positions unique source blocks under matching headings
- Batch follows existing `store_preview`/`consume_preview` confirmation gate for >5 file pairs

Closes #101

## Test plan
- [ ] `_split_frontmatter_body`: files with/without frontmatter, empty frontmatter, empty body
- [ ] `_merge_frontmatter`: new fields added, scalar conflict (dest wins), list union/dedup, empty inputs, identical inputs
- [ ] `_split_blocks`: heading-based splitting, content before first heading, no headings, empty body
- [ ] `_merge_bodies`: identical bodies, unique blocks under matching headings, unmatched blocks appended, partial overlap, empty inputs
- [ ] `merge_files`: identical (delete source), frontmatter-only diff, body diff, delete_source=False, concat strategy, heading positioning, error cases
- [ ] `batch_merge_files`: identical pairs, mixed diffs, confirmation gate, confirm execution, source-only reported, no overlap, recursive mode
- [ ] MCP server registration: no import errors
- [ ] Full test suite passes with no regressions
EOF
)"
```
