# Frontmatter Key Rename — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `"rename"` operation to `update_frontmatter` and `batch_update_frontmatter` that renames frontmatter keys while preserving their values.

**Architecture:** New `rename` boolean parameter on `update_file_frontmatter` (alongside existing `remove`/`append`). The rename pops the old key and inserts the new key with the same value, erroring if the source key is missing or the destination key already exists. Both tool-level functions validate that `value` is a non-empty string for rename and skip `_normalize_frontmatter_value`.

**Tech Stack:** Python, PyYAML, pytest

---

### Task 1: Add rename support to `update_file_frontmatter`

**Files:**
- Modify: `src/services/vault.py:251-309` (`update_file_frontmatter`)
- Test: `tests/test_vault_service.py`

**Step 1: Write the failing tests**

Add to the `TestFrontmatterUpdate` class in `tests/test_vault_service.py`:

```python
def test_update_file_frontmatter_rename(self, sample_frontmatter_file):
    """Should rename a frontmatter key, preserving its value."""
    update_file_frontmatter(sample_frontmatter_file, "author", "writer", rename=True)
    result = extract_frontmatter(sample_frontmatter_file)
    assert "author" not in result
    assert result["writer"] == "Test Author"

def test_update_file_frontmatter_rename_missing_key(self, sample_frontmatter_file):
    """Should raise ValueError when source key doesn't exist."""
    with pytest.raises(ValueError, match="not found"):
        update_file_frontmatter(sample_frontmatter_file, "nonexistent", "new_key", rename=True)

def test_update_file_frontmatter_rename_target_exists(self, sample_frontmatter_file):
    """Should raise ValueError when target key already exists."""
    with pytest.raises(ValueError, match="already exists"):
        update_file_frontmatter(sample_frontmatter_file, "author", "title", rename=True)

def test_update_file_frontmatter_rename_no_frontmatter(self, file_without_frontmatter):
    """Should raise ValueError when file has no frontmatter."""
    with pytest.raises(ValueError, match="no frontmatter"):
        update_file_frontmatter(file_without_frontmatter, "field", "new_field", rename=True)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestFrontmatterUpdate::test_update_file_frontmatter_rename tests/test_vault_service.py::TestFrontmatterUpdate::test_update_file_frontmatter_rename_missing_key tests/test_vault_service.py::TestFrontmatterUpdate::test_update_file_frontmatter_rename_target_exists tests/test_vault_service.py::TestFrontmatterUpdate::test_update_file_frontmatter_rename_no_frontmatter -v`
Expected: FAIL — `rename` parameter not recognized

**Step 3: Implement rename in `update_file_frontmatter`**

In `src/services/vault.py`, modify `update_file_frontmatter`:

1. Add `rename: bool = False` parameter.
2. In the no-frontmatter guard (line 279), also raise for `rename`:
   ```python
   if remove or rename:
       raise ValueError("File has no frontmatter")
   ```
3. Add rename branch before the `else` (set) branch:
   ```python
   elif rename:
       if field not in frontmatter:
           raise ValueError(f"Field '{field}' not found in frontmatter")
       new_key = value
       if new_key in frontmatter:
           raise ValueError(f"Field '{new_key}' already exists in frontmatter")
       frontmatter[new_key] = frontmatter.pop(field)
   ```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestFrontmatterUpdate -v`
Expected: All pass (new + existing)

**Step 5: Commit**

```bash
git add src/services/vault.py tests/test_vault_service.py
git commit -m "feat: add rename support to update_file_frontmatter"
```

---

### Task 2: Wire rename through `do_update_frontmatter`

**Files:**
- Modify: `src/services/vault.py:312-358` (`do_update_frontmatter`)

**Step 1: Write the failing test**

No new test file needed — this is covered by the tool-level tests in Task 3. The existing `do_update_frontmatter` tests are indirect (through `update_frontmatter` tool). Just modify the code.

**Step 2: Implement**

In `do_update_frontmatter`, update the `update_file_frontmatter` call and success message:

1. Add `rename=(operation == "rename")` to the `update_file_frontmatter` call:
   ```python
   update_file_frontmatter(
       file_path,
       field,
       parsed_value,
       remove=(operation == "remove"),
       append=(operation == "append"),
       rename=(operation == "rename"),
   )
   ```
2. Add success message for rename:
   ```python
   if operation == "remove":
       return True, f"Removed '{field}' from {path}"
   elif operation == "append":
       return True, f"Appended {parsed_value!r} to '{field}' in {path}"
   elif operation == "rename":
       return True, f"Renamed '{field}' to '{parsed_value}' in {path}"
   else:
       return True, f"Set '{field}' to {parsed_value!r} in {path}"
   ```

**Step 3: Run existing tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestFrontmatterUpdate -v`
Expected: All pass

**Step 4: Commit**

```bash
git add src/services/vault.py
git commit -m "feat: wire rename operation through do_update_frontmatter"
```

---

### Task 3: Add rename to `update_frontmatter` tool

**Files:**
- Modify: `src/tools/frontmatter.py:296-332` (`update_frontmatter`)
- Test: `tests/test_tools_frontmatter.py`

**Step 1: Write the failing tests**

Add a new `TestUpdateFrontmatterRename` class (or add to the existing update_frontmatter test area — there's a class around line 257):

```python
class TestUpdateFrontmatterRename:
    """Tests for update_frontmatter rename operation."""

    def test_rename_key(self, vault_config):
        """Should rename a frontmatter key."""
        result = json.loads(update_frontmatter(
            path="note1.md", field="tags", value="labels", operation="rename",
        ))
        assert result["success"] is True
        assert "Renamed" in result["message"]

        # Verify the actual file
        import yaml, re as _re
        content = (vault_config / "note1.md").read_text()
        match = _re.match(r"^---\n(.*?)\n---\n", content, _re.DOTALL)
        fm = yaml.safe_load(match.group(1))
        assert "tags" not in fm
        assert fm["labels"] == ["project", "work"]

    def test_rename_missing_source(self, vault_config):
        """Should error when source key doesn't exist."""
        result = json.loads(update_frontmatter(
            path="note1.md", field="nonexistent", value="new_key", operation="rename",
        ))
        assert result["success"] is False

    def test_rename_target_exists(self, vault_config):
        """Should error when target key already exists."""
        result = json.loads(update_frontmatter(
            path="note2.md", field="tags", value="company", operation="rename",
        ))
        assert result["success"] is False
        assert "already exists" in result["error"]

    def test_rename_value_required(self, vault_config):
        """Should error when value is missing for rename."""
        result = json.loads(update_frontmatter(
            path="note1.md", field="tags", operation="rename",
        ))
        assert result["success"] is False

    def test_rename_rejects_list_value(self, vault_config):
        """Should error when value is a list (key names must be strings)."""
        result = json.loads(update_frontmatter(
            path="note1.md", field="tags", value=["a", "b"], operation="rename",
        ))
        assert result["success"] is False

    def test_rename_skips_normalize(self, vault_config):
        """Rename value should not be JSON-parsed (it's a key name, not a YAML value)."""
        result = json.loads(update_frontmatter(
            path="note1.md", field="tags", value="[new_key]", operation="rename",
        ))
        # "[new_key]" should be treated as literal key name, not parsed as JSON list
        assert result["success"] is True
        import yaml, re as _re
        content = (vault_config / "note1.md").read_text()
        match = _re.match(r"^---\n(.*?)\n---\n", content, _re.DOTALL)
        fm = yaml.safe_load(match.group(1))
        assert "[new_key]" in fm
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestUpdateFrontmatterRename -v`
Expected: FAIL

**Step 3: Implement rename in `update_frontmatter`**

In `src/tools/frontmatter.py`, modify `update_frontmatter`:

1. Expand operation validation:
   ```python
   if operation not in ("set", "remove", "append", "rename"):
       return err(f"operation must be 'set', 'remove', 'append', or 'rename', got '{operation}'")
   ```
2. Add rename-specific validation (before the existing `set`/`append` value check):
   ```python
   if operation == "rename":
       if value is None or (isinstance(value, str) and not value.strip()):
           return err("value (new key name) is required for 'rename' operation")
       if not isinstance(value, str):
           return err("value must be a string (new key name) for 'rename' operation")
       # For rename, value is a key name — don't normalize as YAML value
       success, message = do_update_frontmatter(path, field, value, operation)
       if success:
           return ok(message)
       return err(message)
   ```
3. Keep existing `set`/`append` value check and `_normalize_frontmatter_value` path unchanged (only reached for non-rename).

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestUpdateFrontmatterRename -v`
Expected: All pass

**Step 5: Run full frontmatter test suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tools/frontmatter.py tests/test_tools_frontmatter.py
git commit -m "feat: add rename operation to update_frontmatter tool"
```

---

### Task 4: Add rename to `batch_update_frontmatter` tool

**Files:**
- Modify: `src/tools/frontmatter.py:443-508` (`batch_update_frontmatter`)
- Test: `tests/test_tools_frontmatter.py`

**Step 1: Write the failing tests**

Add to `tests/test_tools_frontmatter.py`:

```python
class TestBatchRenameFrontmatter:
    """Tests for batch_update_frontmatter with rename operation."""

    def test_batch_rename_explicit_paths(self, vault_config):
        """Should rename a key on multiple files."""
        result = json.loads(batch_update_frontmatter(
            paths=["note1.md", "note2.md"],
            field="tags",
            value="labels",
            operation="rename",
        ))
        assert result["success"] is True
        assert "2 succeeded" in result["message"]

        import yaml, re as _re
        for filename in ("note1.md", "note2.md"):
            content = (vault_config / filename).read_text()
            match = _re.match(r"^---\n(.*?)\n---\n", content, _re.DOTALL)
            fm = yaml.safe_load(match.group(1))
            assert "tags" not in fm
            assert "labels" in fm

    def test_batch_rename_partial_failure(self, vault_config):
        """Should report failures for files where target key exists."""
        # note2.md has 'company' field — renaming tags→company should fail for it
        result = json.loads(batch_update_frontmatter(
            paths=["note1.md", "note2.md"],
            field="tags",
            value="company",
            operation="rename",
        ))
        assert result["success"] is True
        assert "1 succeeded" in result["message"]
        assert "1 failed" in result["message"]

    def test_batch_rename_confirmation_gate(self, vault_config):
        """Should require confirmation for query-based rename."""
        from services.vault import clear_pending_previews
        clear_pending_previews()
        result = json.loads(batch_update_frontmatter(
            field="tags",
            value="labels",
            operation="rename",
            target_field="tags",
            target_match_type="exists",
        ))
        assert result["confirmation_required"] is True

    def test_batch_rename_invalid_value(self, vault_config):
        """Should reject non-string value for rename."""
        result = json.loads(batch_update_frontmatter(
            paths=["note1.md"],
            field="tags",
            value=["a", "b"],
            operation="rename",
        ))
        assert result["success"] is False

    def test_batch_rename_missing_value(self, vault_config):
        """Should require value for rename."""
        result = json.loads(batch_update_frontmatter(
            paths=["note1.md"],
            field="tags",
            operation="rename",
        ))
        assert result["success"] is False
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchRenameFrontmatter -v`
Expected: FAIL

**Step 3: Implement rename in `batch_update_frontmatter`**

In `src/tools/frontmatter.py`, modify `batch_update_frontmatter`:

1. Expand operation validation (same as `update_frontmatter`):
   ```python
   if operation not in ("set", "remove", "append", "rename"):
       return err(f"operation must be 'set', 'remove', 'append', or 'rename', got '{operation}'")
   ```
2. Add rename-specific validation alongside the existing `set`/`append` check:
   ```python
   if operation == "rename":
       if value is None or (isinstance(value, str) and not value.strip()):
           return err("value (new key name) is required for 'rename' operation")
       if not isinstance(value, str):
           return err("value must be a string (new key name) for 'rename' operation")
   elif operation in ("set", "append") and value is None:
       return err(f"value is required for '{operation}' operation")
   ```
3. Skip `_normalize_frontmatter_value` for rename. Change:
   ```python
   parsed_value = _normalize_frontmatter_value(value)
   ```
   to:
   ```python
   parsed_value = value if operation == "rename" else _normalize_frontmatter_value(value)
   ```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchRenameFrontmatter -v`
Expected: All pass

**Step 5: Run full test suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/tools/frontmatter.py tests/test_tools_frontmatter.py
git commit -m "feat: add rename operation to batch_update_frontmatter tool"
```

---

### Task 5: Update system prompt and CLAUDE.md

**Files:**
- Modify: `system_prompt.txt.example:159-160` (update_frontmatter description)
- Modify: `system_prompt.txt.example:168-180` (batch_update_frontmatter description)
- Modify: `CLAUDE.md` (tool table)

**Step 1: Update system prompt**

In `system_prompt.txt.example`, line 159-160, change:
```
- update_frontmatter: Modify note metadata. Parameters: path, field, value,
  operation ("set", "remove", or "append").
```
to:
```
- update_frontmatter: Modify note metadata. Parameters: path, field, value,
  operation ("set", "remove", "append", or "rename").
  For rename: field is the old key name, value is the new key name.
```

In the batch_update_frontmatter section (line 168), add `"rename"` to the description similarly.

**Step 2: Update CLAUDE.md**

In the MCP Tools table, update the `update_frontmatter` row's Key Parameters from `"set"/"remove"/"append"` to `"set"/"remove"/"append"/"rename"`. Same for `batch_update_frontmatter`.

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass (no code changes here, just docs)

**Step 4: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: document rename operation in system prompt and CLAUDE.md"
```

---

### Task 6: Final verification

**Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All 501+ tests pass

**Step 2: Verify no regressions in related tools**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py tests/test_tools_frontmatter.py -v`
Expected: All pass
