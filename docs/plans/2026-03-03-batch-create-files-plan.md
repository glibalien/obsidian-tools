# batch_create_files Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `batch_create_files` MCP tool that creates multiple vault files in a single call, with built-in existence checking and confirmation gate.

**Architecture:** Reuse existing `create_file()` internally. Pre-check existence to classify results into created/skipped/errors. Follow existing batch tool patterns for confirmation gate.

**Tech Stack:** Python, FastMCP, pytest

---

### Task 1: Write tests for batch_create_files

**Files:**
- Modify: `tests/test_tools_files.py`

**Step 1: Write the test class**

Add a new test class at the end of `tests/test_tools_files.py`:

```python
class TestBatchCreateFiles:
    """Tests for batch_create_files."""

    def setup_method(self):
        clear_pending_previews()

    def test_create_multiple_files(self, vault_config):
        """Create several files in one call."""
        files = [
            {"path": "People/Alice.md", "content": "Alice bio", "frontmatter": {"category": "person"}},
            {"path": "People/Bob.md", "content": "Bob bio"},
            {"path": "plain.md"},
        ]
        result = json.loads(batch_create_files(files=files))
        assert result["success"] is True
        assert len(result["created"]) == 3
        assert result["skipped"] == []
        assert result["errors"] == []
        assert "People/Alice.md" in result["created"]
        # Verify files on disk
        assert (vault_config / "People" / "Alice.md").exists()
        content = (vault_config / "People" / "Alice.md").read_text()
        assert "category: person" in content
        assert "Alice bio" in content
        assert (vault_config / "People" / "Bob.md").exists()
        assert (vault_config / "plain.md").exists()

    def test_skip_existing_true(self, vault_config):
        """Existing files are skipped when skip_existing=True."""
        (vault_config / "exists.md").write_text("existing content")
        files = [
            {"path": "exists.md", "content": "new content"},
            {"path": "new.md", "content": "new file"},
        ]
        result = json.loads(batch_create_files(files=files, skip_existing=True))
        assert result["success"] is True
        assert result["created"] == ["new.md"]
        assert result["skipped"] == ["exists.md"]
        # Existing file unchanged
        assert (vault_config / "exists.md").read_text() == "existing content"

    def test_skip_existing_false(self, vault_config):
        """Existing files are reported as errors when skip_existing=False."""
        (vault_config / "exists.md").write_text("existing content")
        files = [
            {"path": "exists.md", "content": "new content"},
            {"path": "new.md", "content": "new file"},
        ]
        result = json.loads(batch_create_files(files=files, skip_existing=False))
        assert result["success"] is True
        assert result["created"] == ["new.md"]
        assert result["skipped"] == []
        assert len(result["errors"]) == 1
        assert result["errors"][0]["path"] == "exists.md"

    def test_frontmatter_as_dict(self, vault_config):
        """Frontmatter is accepted as a native dict."""
        files = [
            {"path": "note.md", "content": "body", "frontmatter": {"tags": ["meeting", "work"], "status": "draft"}},
        ]
        result = json.loads(batch_create_files(files=files))
        assert result["success"] is True
        content = (vault_config / "note.md").read_text()
        assert "tags:" in content
        assert "status: draft" in content

    def test_confirmation_gate_triggers(self, vault_config):
        """Preview required when creating > BATCH_CONFIRM_THRESHOLD files."""
        files = [{"path": f"file_{i}.md", "content": f"content {i}"} for i in range(10)]
        result = json.loads(batch_create_files(files=files))
        assert result["confirmation_required"] is True
        assert "preview_message" in result
        assert "files" in result
        # No files created yet
        assert not (vault_config / "file_0.md").exists()

    def test_confirmation_gate_confirm(self, vault_config):
        """Files created after confirmation."""
        files = [{"path": f"file_{i}.md", "content": f"content {i}"} for i in range(10)]
        # First call: preview
        batch_create_files(files=files)
        # Second call: confirm
        result = json.loads(batch_create_files(files=files, confirm=True))
        assert result["success"] is True
        assert len(result["created"]) == 10
        assert (vault_config / "file_0.md").exists()

    def test_empty_files_list(self, vault_config):
        """Empty files list returns error."""
        result = json.loads(batch_create_files(files=[]))
        assert result["success"] is False

    def test_mixed_success_failure(self, vault_config):
        """Invalid paths fail, valid paths succeed."""
        files = [
            {"path": "good.md", "content": "ok"},
            {"path": "../../escape.md", "content": "bad"},
        ]
        result = json.loads(batch_create_files(files=files))
        assert result["success"] is True
        assert "good.md" in result["created"]
        assert len(result["errors"]) == 1
        assert "escape" in result["errors"][0]["path"]

    def test_directory_creation(self, vault_config):
        """Parent directories are created for nested paths."""
        files = [
            {"path": "deep/nested/dir/note.md", "content": "deep note"},
        ]
        result = json.loads(batch_create_files(files=files))
        assert result["success"] is True
        assert (vault_config / "deep" / "nested" / "dir" / "note.md").exists()

    def test_missing_path_key(self, vault_config):
        """Items without 'path' key are reported as errors."""
        files = [
            {"content": "no path"},
            {"path": "good.md", "content": "ok"},
        ]
        result = json.loads(batch_create_files(files=files))
        assert result["success"] is True
        assert "good.md" in result["created"]
        assert len(result["errors"]) == 1
```

Ensure the import at the top of the test file includes `batch_create_files` from `tools.files` and `clear_pending_previews` from `services.vault`.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchCreateFiles -v`
Expected: FAIL with `ImportError` (function doesn't exist yet)

**Step 3: Commit**

```bash
git add tests/test_tools_files.py
git commit -m "test: add tests for batch_create_files"
```

---

### Task 2: Implement batch_create_files

**Files:**
- Modify: `src/tools/files.py` (add function after `create_file` at ~line 475)

**Step 1: Write the implementation**

Add after `create_file` (around line 475), before `_parse_frontmatter`:

```python
def batch_create_files(
    files: list[dict],
    skip_existing: bool = True,
    confirm: bool = False,
) -> str:
    """Create multiple vault files in a single call.

    Args:
        files: List of file specifications, each a dict with:
            - path (required): Path for the new file (relative to vault).
            - content (optional): Body content of the note (markdown).
            - frontmatter (optional): Metadata dict, e.g. {"tags": ["meeting"]}.
        skip_existing: If True (default), silently skip files that already exist.
            If False, report existing files as errors.
        confirm: Must be true to execute when creating more than 5 files.

    Returns:
        Summary with created, skipped, and errors lists.
    """
    if not files:
        return err("files list is empty")

    # Validate all items have 'path' for the preview
    paths = []
    for i, item in enumerate(files):
        if not isinstance(item, dict) or "path" not in item:
            # We'll handle these in the execution loop; collect valid paths for preview
            continue
        paths.append(item["path"])

    # Confirmation gate for large batches
    if len(files) > BATCH_CONFIRM_THRESHOLD:
        key = ("batch_create_files", tuple(sorted(paths)))
        if not (confirm and consume_preview(key)):
            store_preview(key)
            return ok(
                "Describe this pending change to the user. "
                "They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=f"This will create {len(files)} files.",
                files=paths,
            )

    created = []
    skipped = []
    errors = []

    for i, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append({"path": f"item_{i}", "error": f"Expected dict, got {type(item).__name__}"})
            continue

        path = item.get("path")
        if not path:
            errors.append({"path": f"item_{i}", "error": "Missing 'path' key"})
            continue

        # Check existence before calling create_file
        try:
            file_path = resolve_vault_path(path)
        except ValueError as e:
            errors.append({"path": path, "error": str(e)})
            continue

        if file_path.exists():
            if skip_existing:
                skipped.append(path)
            else:
                errors.append({"path": path, "error": "File already exists"})
            continue

        # Convert frontmatter dict to JSON string for create_file
        fm = item.get("frontmatter")
        fm_str = json.dumps(fm) if fm is not None else None

        result_str = create_file(path, item.get("content", ""), fm_str)
        result = json.loads(result_str)

        if result.get("success"):
            created.append(result.get("path", path))
        else:
            errors.append({"path": path, "error": result.get("error", "Unknown error")})

    summary = f"Created {len(created)}, skipped {len(skipped)}, {len(errors)} errors"
    return ok(summary, created=created, skipped=skipped, errors=errors)
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchCreateFiles -v`
Expected: All 10 tests PASS

**Step 3: Commit**

```bash
git add src/tools/files.py
git commit -m "feat: add batch_create_files tool"
```

---

### Task 3: Register in MCP server and add compaction stub

**Files:**
- Modify: `src/mcp_server.py` (add import and registration)
- Modify: `src/services/compaction.py` (add stub builder)

**Step 1: Update mcp_server.py**

Add `batch_create_files` to the import from `tools.files` (line 17-25):

```python
from tools.files import (
    batch_create_files,
    batch_merge_files,
    ...
)
```

Add registration after `create_file` (around line 62):

```python
mcp.tool()(create_file)
mcp.tool()(batch_create_files)
```

**Step 2: Add compaction stub**

Add to `src/services/compaction.py` before the `_TOOL_STUB_BUILDERS` dict (around line 178):

```python
def _build_batch_create_files_stub(data: dict) -> str:
    """Compact batch_create_files: keep counts only."""
    stub = _base_stub(data)
    if "created" in data:
        stub["created_count"] = len(data["created"])
    if "skipped" in data:
        stub["skipped_count"] = len(data["skipped"])
    if "errors" in data:
        stub["error_count"] = len(data["errors"])
    return json.dumps(stub)
```

Add to `_TOOL_STUB_BUILDERS` dict:

```python
"batch_create_files": _build_batch_create_files_stub,
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (no regressions)

**Step 4: Commit**

```bash
git add src/mcp_server.py src/services/compaction.py
git commit -m "feat: register batch_create_files and add compaction stub"
```

---

### Task 4: Update system prompt

**Files:**
- Modify: `system_prompt.txt.example`

**Step 1: Add to decision tree**

In the decision tree table (around line 53), add after the `batch_merge_files` row:

```
| "Create multiple related files" / batch file creation | batch_create_files | Creates multiple files in one call. Frontmatter as native dict. |
```

**Step 2: Add to tool reference**

In the file management tools section (around line 139), add after the `create_file` entry:

```
- batch_create_files: Create multiple files in one call. Each item in files
  list has path (required), content (optional), frontmatter (optional, native
  dict). skip_existing (default true) skips existing files as "skipped".
  Returns created, skipped, errors lists.
```

**Step 3: Update batch confirmation note**

Update the batch confirmation line (around line 214) to include `batch_create_files`:

```
**Batch confirmation**: batch_create_files, batch_update_frontmatter, batch_move_files, and
batch_merge_files require confirmation when affecting >5 files.
```

**Step 4: Commit**

```bash
git add system_prompt.txt.example
git commit -m "docs: add batch_create_files to system prompt"
```
