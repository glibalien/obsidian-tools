# Consolidate Preference Tools Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 3 preference tools with a single `manage_preferences` tool using operation dispatch.

**Architecture:** Single `manage_preferences(operation, preference?, line_number?)` function with if/elif branches. Keeps `_read_preferences`/`_write_preferences` internal helpers. Registered as one MCP tool.

**Tech Stack:** Python, FastMCP, pytest

---

### Task 1: Rewrite preferences.py

**Files:**
- Modify: `src/tools/preferences.py:1-85`

**Step 1: Replace the three public functions with `manage_preferences`**

Replace the entire file content after `_write_preferences` (line 28) with:

```python
def manage_preferences(
    operation: str,
    preference: str | None = None,
    line_number: int | None = None,
) -> str:
    """Manage user preferences stored in Preferences.md.

    Args:
        operation: "list", "add", or "remove".
        preference: The preference text (required for "add").
        line_number: 1-indexed line number (required for "remove").
    """
    if operation == "list":
        preferences = _read_preferences()
        if not preferences:
            return ok("No preferences saved.", results=[])
        return ok(results=[f"{i}. {pref}" for i, pref in enumerate(preferences, start=1)])

    if operation == "add":
        if not preference or not preference.strip():
            return err("preference cannot be empty")
        preference = preference.strip()
        preferences = _read_preferences()
        preferences.append(preference)
        _write_preferences(preferences)
        return ok(f"Saved preference: {preference}")

    if operation == "remove":
        if line_number is None:
            return err("line_number is required for remove operation")
        preferences = _read_preferences()
        if not preferences:
            return err("No preferences to remove")
        if line_number < 1 or line_number > len(preferences):
            return err(f"Invalid line number. Must be between 1 and {len(preferences)}")
        removed = preferences.pop(line_number - 1)
        _write_preferences(preferences)
        return ok(f"Removed preference: {removed}")

    return err(f"Unknown operation: {operation}. Must be 'list', 'add', or 'remove'")
```

Also update the module docstring (line 1) to: `"""Preference tools - manage user preferences."""`

**Step 2: Run existing tests to confirm they fail (old API removed)**

Run: `.venv/bin/python -m pytest tests/test_tools_preferences.py -v`
Expected: FAIL — `ImportError` for `save_preference`, `list_preferences`, `remove_preference`

**Step 3: Commit**

```bash
git add src/tools/preferences.py
git commit -m "refactor: replace 3 preference functions with manage_preferences"
```

---

### Task 2: Update tests to new API

**Files:**
- Modify: `tests/test_tools_preferences.py:1-163`

**Step 1: Rewrite tests to use `manage_preferences`**

Update the import (line 9) from:
```python
from tools.preferences import save_preference, list_preferences, remove_preference
```
to:
```python
from tools.preferences import manage_preferences
```

Replace every call pattern:
- `save_preference("X")` → `manage_preferences(operation="add", preference="X")`
- `list_preferences()` → `manage_preferences(operation="list")`
- `remove_preference(N)` → `manage_preferences(operation="remove", line_number=N)`

Add two new tests:

```python
def test_manage_preferences_invalid_operation(prefs_file):
    """Unknown operation returns error."""
    result = json.loads(manage_preferences(operation="delete"))
    assert result["success"] is False
    assert "unknown operation" in result["error"].lower()


def test_manage_preferences_remove_missing_line_number(prefs_file):
    """Remove without line_number returns error."""
    manage_preferences(operation="add", preference="something")
    result = json.loads(manage_preferences(operation="remove"))
    assert result["success"] is False
    assert "line_number" in result["error"].lower()
```

**Step 2: Run tests to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_tools_preferences.py -v`
Expected: All PASS (15 tests — 13 existing + 2 new)

**Step 3: Commit**

```bash
git add tests/test_tools_preferences.py
git commit -m "test: update preference tests for manage_preferences API"
```

---

### Task 3: Update imports and MCP registration

**Files:**
- Modify: `src/tools/__init__.py:21-24,56-59`
- Modify: `src/mcp_server.py:38-42,93-96`

**Step 1: Update `src/tools/__init__.py`**

Replace the preferences import block (lines 21-25):
```python
from tools.preferences import (
    manage_preferences,
)
```

Replace the preferences `__all__` entries (lines 56-59):
```python
    "manage_preferences",
```

**Step 2: Update `src/mcp_server.py`**

Replace the preferences import block (lines 38-42):
```python
from tools.preferences import (
    manage_preferences,
)
```

Replace the preference tool registration (lines 93-96):
```python
# Preference tools
mcp.tool()(manage_preferences)
```

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add src/tools/__init__.py src/mcp_server.py
git commit -m "refactor: register single manage_preferences tool in MCP server"
```

---

### Task 4: Update documentation

**Files:**
- Modify: `system_prompt.txt.example` (lines ~194-197, the Preferences section)
- Modify: `CLAUDE.md` (tool table, preferences row)

**Step 1: Update `system_prompt.txt.example`**

Replace the Preferences section (lines 194-197):
```
### Preferences
- manage_preferences: List, add, or remove user preferences. Use operation="list", "add" (with preference=), or "remove" (with line_number=).
```

**Step 2: Update `CLAUDE.md`**

In the MCP Tools table, replace the three preference rows:
```
| `save_preference` / `list_preferences` / `remove_preference` | Manage Preferences.md | `preference` / (none) / `line_number` |
```

with:
```
| `manage_preferences` | List/add/remove preferences | `operation` ("list"/"add"/"remove"), `preference`, `line_number` |
```

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: update tool references for manage_preferences"
```

---

### Task 5: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

**Step 2: Verify no stale references**

Run: `grep -r "save_preference\|list_preferences\|remove_preference" src/ tests/ --include="*.py"`
Expected: No matches (only this plan file and git history)
