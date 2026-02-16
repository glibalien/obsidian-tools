# Medium-Priority Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 4 medium-priority issues: read_file UTF-8 encoding, batch upserts for indexing, link index for O(1) backlink lookups, and pagination on list tools.

**Architecture:** Four mostly independent fixes. Task 1 (encoding) is a one-liner. Task 2 (batch upserts) rewrites the chunk loop in index_file. Task 3 (link index) adds a new build step to index_vault and rewrites find_backlinks to use it. Task 4 (pagination) adds limit/offset to 5 list tools with a shared slicing pattern.

**Tech Stack:** Python, ChromaDB, pytest

---

### Task 1: Fix read_file UTF-8 Encoding

**Files:**
- Modify: `src/tools/files.py:35`
- Modify: `tests/test_tools_files.py`

**Step 1: Write a test that catches the encoding issue**

Add to `tests/test_tools_files.py` in `TestReadFile`:

```python
def test_read_file_utf8_encoding(self, vault_config):
    """Should handle UTF-8 content including non-ASCII characters."""
    content = "# Café\n\nRésumé with émojis: 🎉"
    (vault_config / "utf8.md").write_text(content, encoding="utf-8")
    result = json.loads(read_file("utf8.md"))
    assert result["success"] is True
    assert "Café" in result["content"]
    assert "🎉" in result["content"]
```

**Step 2: Fix the encoding**

In `src/tools/files.py:35`, change:
```python
content = file_path.read_text()
```
to:
```python
content = file_path.read_text(encoding="utf-8", errors="ignore")
```

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "fix: add UTF-8 encoding to read_file (2b)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Batch Upserts in index_file

**Files:**
- Modify: `src/index_vault.py:237-264` (the `index_file` function)
- Test: `tests/test_chunking.py`

**Step 1: Write test for batch upsert**

Add to `tests/test_chunking.py`:

```python
class TestIndexFileBatching:
    """Tests for batched upsert in index_file."""

    @patch("index_vault.get_collection")
    def test_single_upsert_call_per_file(self, mock_get_collection):
        """index_file should call upsert once with all chunks, not once per chunk."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        # Create a temp file with multiple sections (will produce multiple chunks)
        import tempfile
        from pathlib import Path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Section 1\n\nContent one.\n\n# Section 2\n\nContent two.\n")
            tmp_path = Path(f.name)

        try:
            from index_vault import index_file
            index_file(tmp_path)

            # Should be exactly 1 upsert call (batched), not 2+ individual calls
            assert mock_collection.upsert.call_count == 1

            # The single call should contain multiple chunks
            call_args = mock_collection.upsert.call_args[1]
            assert len(call_args["ids"]) >= 2
            assert len(call_args["documents"]) == len(call_args["ids"])
            assert len(call_args["metadatas"]) == len(call_args["ids"])
        finally:
            tmp_path.unlink()
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexFileBatching -v`
Expected: FAIL (current code calls upsert once per chunk)

**Step 3: Rewrite index_file to batch**

Replace the chunk loop in `src/index_vault.py` `index_file` function (lines 252-264):

```python
def index_file(md_file: Path) -> None:
    """Index a single markdown file, replacing any existing chunks."""
    collection = get_collection()

    # Delete existing chunks for this file
    existing = collection.get(
        where={"source": str(md_file)},
        include=[]
    )
    if existing['ids']:
        collection.delete(ids=existing['ids'])

    # Read and chunk the file
    content = md_file.read_text(encoding='utf-8', errors='ignore')
    chunks = chunk_markdown(content)

    if not chunks:
        return

    # Batch upsert all chunks at once
    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        ids.append(hashlib.md5(f"{md_file}_{i}".encode()).hexdigest())
        documents.append(f"[{md_file.stem}] {chunk['text']}")
        metadatas.append({
            "source": str(md_file),
            "chunk": i,
            "heading": chunk["heading"],
            "chunk_type": chunk["chunk_type"],
        })

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Then: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 5: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "perf: batch upserts in index_file (1e)

Collect all chunks for a file and upsert in a single ChromaDB call
instead of one call per chunk.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Build Link Index for find_backlinks

**Files:**
- Modify: `src/index_vault.py` (add `build_link_index` function, call from `index_vault`)
- Modify: `src/tools/links.py` (rewrite `find_backlinks` to use index)
- Test: `tests/test_chunking.py` (index building tests)
- Test: `tests/test_tools_links.py` (backlinks tests)

**Step 1: Write test for link index building**

Add to `tests/test_chunking.py`:

```python
class TestLinkIndex:
    """Tests for wikilink index building."""

    def test_build_link_index_basic(self, tmp_path):
        """Should extract wikilinks and build reverse index."""
        (tmp_path / "a.md").write_text("Link to [[B]] and [[C|alias]].")
        (tmp_path / "b.md").write_text("Link to [[C]].")
        (tmp_path / "c.md").write_text("No links here.")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"])

        assert sorted(index["b"]) == [str(tmp_path / "a.md")]
        assert sorted(index["c"]) == [str(tmp_path / "a.md"), str(tmp_path / "b.md")]

    def test_build_link_index_case_insensitive(self, tmp_path):
        """Link targets should be lowercased for case-insensitive lookup."""
        (tmp_path / "a.md").write_text("Link to [[MyNote]].")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md"])

        assert "mynote" in index

    def test_build_link_index_empty(self, tmp_path):
        """Files with no wikilinks produce empty index."""
        (tmp_path / "a.md").write_text("No links.")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md"])

        assert index == {}
```

**Step 2: Implement build_link_index**

Add to `src/index_vault.py`:

```python
import json as _json

LINK_INDEX_FILE = os.path.join(CHROMA_PATH, "link_index.json")

def _extract_wikilinks(text: str) -> list[str]:
    """Extract wikilink targets from text, lowercased."""
    # Matches [[target]] and [[target|alias]]
    return [m.lower() for m in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)]


def build_link_index(files: list[Path]) -> dict[str, list[str]]:
    """Build a reverse link index: {target_name_lower: [source_paths]}.

    Scans wikilinks from each file and builds a mapping from link target
    to the files that contain that link.
    """
    index: dict[str, list[str]] = {}
    for md_file in files:
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        targets = _extract_wikilinks(content)
        for target in set(targets):  # dedupe within same file
            index.setdefault(target, []).append(str(md_file))
    return index
```

**Step 3: Call from index_vault and save to disk**

Add to the end of `index_vault()`, after `pruned = prune_deleted_files(valid_sources)` and before `mark_run()`:

```python
    # Build and save link index
    link_index = build_link_index(all_files)
    with open(LINK_INDEX_FILE, "w", encoding="utf-8") as f:
        _json.dump(link_index, f)
```

**Step 4: Write test for find_backlinks using index**

Add to `tests/test_tools_links.py`:

```python
class TestFindBacklinksWithIndex:
    """Tests for find_backlinks using link index."""

    def test_uses_index_when_available(self, vault_config, tmp_path):
        """Should read from link index file instead of scanning vault."""
        import json as json_mod
        from config import CHROMA_PATH

        # Write a fake link index
        index = {"note1": [str(vault_config / "note2.md")]}
        index_path = os.path.join(CHROMA_PATH, "link_index.json")
        os.makedirs(CHROMA_PATH, exist_ok=True)
        with open(index_path, "w") as f:
            json_mod.dump(index, f)

        result = json.loads(find_backlinks("note1"))
        assert result["success"] is True
        assert len(result["results"]) == 1
        assert "note2.md" in result["results"][0]

    def test_falls_back_without_index(self, vault_config):
        """Should fall back to vault scan if index file missing."""
        import os
        from config import CHROMA_PATH
        index_path = os.path.join(CHROMA_PATH, "link_index.json")
        if os.path.exists(index_path):
            os.remove(index_path)

        # This should still work via the O(n) fallback
        result = json.loads(find_backlinks("note1"))
        assert result["success"] is True
        # note2.md links to note1 in the temp vault fixtures
        assert "note2.md" in result["results"]
```

**Step 5: Rewrite find_backlinks to use index**

In `src/tools/links.py`, rewrite `find_backlinks`:

```python
import json as _json
import os

def find_backlinks(note_name: str, limit: int = 100, offset: int = 0) -> str:
    """Find all vault files that contain wikilinks to a given note."""
    if not note_name or not note_name.strip():
        return err("note_name cannot be empty")

    note_name = note_name.strip()
    if note_name.endswith(".md"):
        note_name = note_name[:-3]

    # Try link index first
    from config import CHROMA_PATH
    index_path = os.path.join(CHROMA_PATH, "link_index.json")

    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                link_index = _json.load(f)
            sources = link_index.get(note_name.lower(), [])
            vault_resolved = VAULT_PATH.resolve()
            all_results = sorted(
                str(Path(s).relative_to(vault_resolved))
                for s in sources
                if Path(s).exists()
            )
        except Exception:
            all_results = _scan_backlinks(note_name)
    else:
        all_results = _scan_backlinks(note_name)

    if not all_results:
        return ok(f"No backlinks found to [[{note_name}]]", results=[], total=0)

    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _scan_backlinks(note_name: str) -> list[str]:
    """Fallback: scan all vault files for backlinks (O(n))."""
    pattern = rf"\[\[{re.escape(note_name)}(?:\|[^\]]+)?\]\]"
    backlinks = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in get_vault_files():
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(pattern, content, re.IGNORECASE):
            rel_path = md_file.relative_to(vault_resolved)
            backlinks.append(str(rel_path))

    return sorted(backlinks)
```

Note: This also adds `limit`/`offset` (Task 4 overlap). Add `from pathlib import Path` to imports if not already present.

**Step 6: Update existing backlinks tests**

The existing tests check `result["results"]` as a list — they should still work since the format hasn't changed, just need to account for the new `total` field. Verify they pass.

**Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py tests/test_chunking.py -v`
Then: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 8: Commit**

```bash
git add src/index_vault.py src/tools/links.py tests/test_chunking.py tests/test_tools_links.py
git commit -m "perf: add link index for O(1) backlink lookups (1b)

Build {target: [sources]} index during vault indexing, saved as JSON.
find_backlinks reads the index instead of scanning all files. Falls
back to O(n) scan if index doesn't exist.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Add Pagination to List Tools

**Files:**
- Modify: `src/tools/links.py` (find_outlinks, search_by_folder — find_backlinks already done in Task 3)
- Modify: `src/tools/frontmatter.py` (list_files_by_frontmatter, search_by_date_range)
- Modify: `tests/test_tools_links.py`

**Step 1: Write pagination tests**

Add to `tests/test_tools_links.py`:

```python
class TestPagination:
    """Tests for limit/offset pagination on list tools."""

    def test_find_outlinks_pagination(self, vault_config):
        """find_outlinks should respect limit and offset."""
        # Create file with many links
        links = " ".join(f"[[note{i}]]" for i in range(10))
        (vault_config / "many_links.md").write_text(f"# Links\n\n{links}")

        result = json.loads(find_outlinks("many_links.md", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] == 10

        result2 = json.loads(find_outlinks("many_links.md", limit=3, offset=3))
        assert len(result2["results"]) == 3
        assert result2["total"] == 10

    def test_search_by_folder_pagination(self, vault_config):
        """search_by_folder should respect limit and offset."""
        # vault_config has files in root — create several
        for i in range(5):
            (vault_config / f"page_test_{i}.md").write_text(f"# Page {i}")

        result = json.loads(search_by_folder(".", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] >= 5

    def test_pagination_offset_beyond_results(self, vault_config):
        """Offset beyond results returns empty list with correct total."""
        result = json.loads(search_by_folder(".", limit=100, offset=9999))
        assert result["success"] is True
        assert result["results"] == []
        assert result["total"] >= 1

    def test_default_limit_is_100(self, vault_config):
        """Default limit should be 100."""
        result = json.loads(find_outlinks("note1.md"))
        assert result["success"] is True
        assert "total" in result
```

**Step 2: Add pagination to find_outlinks**

In `src/tools/links.py`, update `find_outlinks`:

```python
def find_outlinks(path: str, limit: int = 100, offset: int = 0) -> str:
    """Extract all wikilinks from a vault file."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Reading file failed: {e}")

    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    if not matches:
        return ok(f"No outlinks found in {path}", results=[], total=0)

    all_results = sorted(set(matches))
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
```

**Step 3: Add pagination to search_by_folder**

```python
def search_by_folder(folder: str, recursive: bool = False, limit: int = 100, offset: int = 0) -> str:
    """List all markdown files in a vault folder."""
    folder_path, error = resolve_dir(folder)
    if error:
        return err(error)

    pattern_func = folder_path.rglob if recursive else folder_path.glob

    files = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        rel_path = md_file.relative_to(vault_resolved)
        files.append(str(rel_path))

    if not files:
        mode = "recursively " if recursive else ""
        return ok(f"No markdown files found {mode}in {folder}", results=[], total=0)

    all_results = sorted(files)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
```

**Step 4: Add pagination to list_files_by_frontmatter**

In `src/tools/frontmatter.py`:

```python
def list_files_by_frontmatter(field: str, value: str, match_type: str = "contains", limit: int = 100, offset: int = 0) -> str:
```

At the end, replace:
```python
    if not matching:
        return ok(f"No files found where {field} {match_type} '{value}'", results=[], total=0)

    all_results = sorted(matching)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
```

**Step 5: Add pagination to search_by_date_range**

In `src/tools/frontmatter.py`:

```python
def search_by_date_range(start_date: str, end_date: str, date_type: str = "modified", limit: int = 100, offset: int = 0) -> str:
```

At the end, replace:
```python
    if not matching:
        return ok(f"No files found with {date_type} date between {start_date} and {end_date}", results=[], total=0)

    all_results = sorted(matching)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
```

**Step 6: Update existing tests for `total` field**

Existing tests that check `result["results"]` should still pass since the list content is unchanged. But add `total` assertions where appropriate. Check that tests expecting `results=[]` also work with the new `total=0`.

**Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 8: Commit**

```bash
git add src/tools/links.py src/tools/frontmatter.py tests/test_tools_links.py
git commit -m "feat: add limit/offset pagination to list tools (5c)

Add limit (default 100) and offset (default 0) parameters to
find_backlinks, find_outlinks, search_by_folder,
list_files_by_frontmatter, and search_by_date_range. Responses
include total count for paging.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
