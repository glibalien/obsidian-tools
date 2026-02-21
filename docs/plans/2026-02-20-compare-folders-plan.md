# Compare Folders Tool — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `compare_folders` MCP tool that compares two vault folders by filename stem, returning files unique to each folder and files in both.

**Architecture:** Single function in `src/tools/links.py` using `resolve_dir()` + `glob()`/`rglob()` to scan both folders, set operations on lowercased stems to categorize files into only_in_source, only_in_target, in_both. Registered in `mcp_server.py`.

**Tech Stack:** Python, FastMCP, existing vault service helpers.

---

### Task 1: Write failing tests

**Files:**
- Modify: `tests/test_tools_links.py`

**Step 1: Add import for `compare_folders`**

At line 8, add `compare_folders` to the import:

```python
from tools.links import (
    compare_folders,
    find_backlinks,
    find_outlinks,
    search_by_folder,
)
```

**Step 2: Write test class**

Add after `TestSearchByFolder` (before `TestListToolPagination`):

```python
class TestCompareFolders:
    """Tests for compare_folders tool."""

    def test_basic_comparison(self, vault_config):
        """Should categorize files into only_in_source, only_in_target, in_both."""
        source = vault_config / "folder_a"
        target = vault_config / "folder_b"
        source.mkdir()
        target.mkdir()
        (source / "shared.md").write_text("# Shared")
        (source / "only_a.md").write_text("# Only A")
        (target / "shared.md").write_text("# Shared copy")
        (target / "only_b.md").write_text("# Only B")

        result = json.loads(compare_folders("folder_a", "folder_b"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 1
        assert result["counts"]["only_in_target"] == 1
        assert result["counts"]["in_both"] == 1
        assert "folder_a/only_a.md" in result["only_in_source"]
        assert "folder_b/only_b.md" in result["only_in_target"]
        both_names = [m["name"] for m in result["in_both"]]
        assert "shared.md" in both_names

    def test_no_overlap(self, vault_config):
        """Should return empty in_both when folders are disjoint."""
        source = vault_config / "disjoint_a"
        target = vault_config / "disjoint_b"
        source.mkdir()
        target.mkdir()
        (source / "alpha.md").write_text("# A")
        (target / "beta.md").write_text("# B")

        result = json.loads(compare_folders("disjoint_a", "disjoint_b"))
        assert result["success"] is True
        assert result["counts"]["in_both"] == 0
        assert result["counts"]["only_in_source"] == 1
        assert result["counts"]["only_in_target"] == 1

    def test_complete_overlap(self, vault_config):
        """Should return empty only_in lists when folders have same filenames."""
        source = vault_config / "same_a"
        target = vault_config / "same_b"
        source.mkdir()
        target.mkdir()
        (source / "file1.md").write_text("# V1")
        (source / "file2.md").write_text("# V1")
        (target / "file1.md").write_text("# V2")
        (target / "file2.md").write_text("# V2")

        result = json.loads(compare_folders("same_a", "same_b"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 0
        assert result["counts"]["only_in_target"] == 0
        assert result["counts"]["in_both"] == 2

    def test_empty_source(self, vault_config):
        """Should handle empty source folder."""
        source = vault_config / "empty_src"
        target = vault_config / "nonempty"
        source.mkdir()
        target.mkdir()
        (target / "file.md").write_text("# File")

        result = json.loads(compare_folders("empty_src", "nonempty"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 0
        assert result["counts"]["only_in_target"] == 1
        assert result["counts"]["in_both"] == 0

    def test_empty_both(self, vault_config):
        """Should handle both folders empty."""
        (vault_config / "empty1").mkdir()
        (vault_config / "empty2").mkdir()

        result = json.loads(compare_folders("empty1", "empty2"))
        assert result["success"] is True
        assert result["counts"] == {"only_in_source": 0, "only_in_target": 0, "in_both": 0}

    def test_case_insensitive_matching(self, vault_config):
        """Should match stems case-insensitively."""
        source = vault_config / "case_a"
        target = vault_config / "case_b"
        source.mkdir()
        target.mkdir()
        (source / "John Smith.md").write_text("# John")
        (target / "john smith.md").write_text("# John")

        result = json.loads(compare_folders("case_a", "case_b"))
        assert result["success"] is True
        assert result["counts"]["in_both"] == 1
        assert result["counts"]["only_in_source"] == 0

    def test_recursive(self, vault_config):
        """Should include subfolder files when recursive=True."""
        source = vault_config / "rec_a"
        target = vault_config / "rec_b"
        source.mkdir()
        target.mkdir()
        sub = source / "sub"
        sub.mkdir()
        (source / "top.md").write_text("# Top")
        (sub / "deep.md").write_text("# Deep")
        (target / "deep.md").write_text("# Deep copy")

        # Non-recursive: deep.md not scanned in source
        result = json.loads(compare_folders("rec_a", "rec_b"))
        assert result["counts"]["in_both"] == 0
        assert result["counts"]["only_in_source"] == 1  # top.md only

        # Recursive: deep.md found in both
        result = json.loads(compare_folders("rec_a", "rec_b", recursive=True))
        assert result["counts"]["in_both"] == 1
        both_names = [m["name"] for m in result["in_both"]]
        assert "deep.md" in both_names

    def test_same_folder_error(self, vault_config):
        """Should error when source and target are the same folder."""
        (vault_config / "same").mkdir()
        result = json.loads(compare_folders("same", "same"))
        assert result["success"] is False
        assert "same" in result["error"].lower()

    def test_invalid_folder_error(self, vault_config):
        """Should error when folder doesn't exist."""
        (vault_config / "exists").mkdir()
        result = json.loads(compare_folders("nonexistent", "exists"))
        assert result["success"] is False

        result = json.loads(compare_folders("exists", "nonexistent"))
        assert result["success"] is False

    def test_in_both_has_both_paths(self, vault_config):
        """in_both entries should include source_path and target_path."""
        source = vault_config / "paths_a"
        target = vault_config / "paths_b"
        source.mkdir()
        target.mkdir()
        (source / "shared.md").write_text("# A")
        (target / "shared.md").write_text("# B")

        result = json.loads(compare_folders("paths_a", "paths_b"))
        match = result["in_both"][0]
        assert match["name"] == "shared.md"
        assert match["source_path"] == "paths_a/shared.md"
        assert match["target_path"] == "paths_b/shared.md"

    def test_results_sorted(self, vault_config):
        """Results should be sorted alphabetically."""
        source = vault_config / "sort_a"
        target = vault_config / "sort_b"
        source.mkdir()
        target.mkdir()
        for name in ["charlie.md", "alpha.md", "bravo.md"]:
            (source / name).write_text(f"# {name}")

        result = json.loads(compare_folders("sort_a", "sort_b"))
        assert result["only_in_source"] == [
            "sort_a/alpha.md", "sort_a/bravo.md", "sort_a/charlie.md"
        ]
```

**Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py::TestCompareFolders -v`
Expected: ImportError — `compare_folders` doesn't exist yet.

**Step 4: Commit**

```bash
git add tests/test_tools_links.py
git commit -m "test: add compare_folders tests (red)"
```

---

### Task 2: Implement `compare_folders`

**Files:**
- Modify: `src/tools/links.py:195` (append after `search_by_folder`)

**Step 1: Write the implementation**

Add at end of `src/tools/links.py`:

```python
def compare_folders(
    source: str,
    target: str,
    recursive: bool = False,
) -> str:
    """Compare two vault folders by filename, showing overlap and differences.

    Args:
        source: Path to the source folder (relative to vault or absolute).
        target: Path to the target folder (relative to vault or absolute).
        recursive: If True, include files in subfolders. Default: False.

    Returns:
        JSON response with only_in_source, only_in_target, in_both lists and counts.
    """
    source_path, source_err = resolve_dir(source)
    if source_err:
        return err(source_err)

    target_path, target_err = resolve_dir(target)
    if target_err:
        return err(target_err)

    if source_path == target_path:
        return err("Source and target folders are the same")

    source_files = _scan_folder(source_path, recursive)
    target_files = _scan_folder(target_path, recursive)

    source_stems = {stem: path for stem, path in source_files}
    target_stems = {stem: path for stem, path in target_files}

    source_only_keys = sorted(source_stems.keys() - target_stems.keys())
    target_only_keys = sorted(target_stems.keys() - source_stems.keys())
    both_keys = sorted(source_stems.keys() & target_stems.keys())

    only_in_source = [source_stems[k] for k in source_only_keys]
    only_in_target = [target_stems[k] for k in target_only_keys]
    in_both = [
        {
            "name": source_stems[k].rsplit("/", 1)[-1],
            "source_path": source_stems[k],
            "target_path": target_stems[k],
        }
        for k in both_keys
    ]

    counts = {
        "only_in_source": len(only_in_source),
        "only_in_target": len(only_in_target),
        "in_both": len(in_both),
    }

    return ok(
        f"Compared '{source}' with '{target}': "
        f"{counts['only_in_source']} only in source, "
        f"{counts['only_in_target']} only in target, "
        f"{counts['in_both']} in both",
        only_in_source=only_in_source,
        only_in_target=only_in_target,
        in_both=in_both,
        counts=counts,
    )


def _scan_folder(folder_path, recursive: bool) -> list[tuple[str, str]]:
    """Scan a folder for .md files and return (lowercased_stem, relative_path) pairs."""
    pattern_func = folder_path.rglob if recursive else folder_path.glob
    results = []
    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        rel_path = get_relative_path(md_file)
        stem = md_file.stem.lower()
        results.append((stem, rel_path))
    return results
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py::TestCompareFolders -v`
Expected: All 12 tests PASS.

**Step 3: Run full link test suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py -v`
Expected: All existing tests still PASS.

**Step 4: Commit**

```bash
git add src/tools/links.py
git commit -m "feat: add compare_folders tool implementation"
```

---

### Task 3: Register tool in MCP server

**Files:**
- Modify: `src/mcp_server.py:30-34` (import) and `src/mcp_server.py:79-81` (registration)

**Step 1: Add import**

Update the links import block at line 30:

```python
from tools.links import (
    compare_folders,
    find_backlinks,
    find_outlinks,
    search_by_folder,
)
```

**Step 2: Add registration**

After `mcp.tool()(search_by_folder)` at line 81, add:

```python
mcp.tool()(compare_folders)
```

**Step 3: Commit**

```bash
git add src/mcp_server.py
git commit -m "feat: register compare_folders in MCP server"
```

---

### Task 4: Add `compare_folders` to pagination validation test

**Files:**
- Modify: `tests/test_tools_links.py`

The `test_paginated_link_tools_reject_invalid_pagination` parametrized test (line 241) validates that all paginated tools reject bad pagination. `compare_folders` has no pagination, so it does NOT need to be added there. No action needed.

However, verify the full test suite passes:

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS, no regressions.

**Step 1: Commit (squash the three implementation commits)**

This is the final state. All three prior commits can stay as-is or be squashed when creating the PR — user preference.
