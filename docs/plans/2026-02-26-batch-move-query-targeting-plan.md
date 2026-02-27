# Query-Based Targeting for batch_move_files Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add query-based frontmatter targeting to `batch_move_files` so the agent can move files matching criteria to a destination folder in one call, and extract shared targeting utilities into `services/vault.py`.

**Architecture:** Move `FilterCondition`, `_get_field_ci`, `_strip_wikilinks`, `_matches_field`, `_validate_filters`, `_get_file_date`, `_find_matching_files`, and related constants from `tools/frontmatter.py` to `services/vault.py`. Update all importers (`tools/frontmatter.py`, `tools/search.py`, `tools/__init__.py`, tests). Then add query-based parameters to `batch_move_files` in `tools/files.py`.

**Tech Stack:** Python, pydantic (for `FilterCondition`), pytest

---

### Task 1: Move targeting utilities to services/vault.py

**Files:**
- Modify: `src/services/vault.py` — add moved functions after the existing frontmatter section (~line 248)
- Modify: `src/tools/frontmatter.py` — remove moved functions, import them from services.vault
- Modify: `src/tools/search.py` — update imports
- Test: Run existing test suite

This is a pure mechanical extraction. No logic changes.

**Step 1: Add pydantic import and moved code to services/vault.py**

Add `from pydantic import BaseModel` to the imports at the top of `src/services/vault.py` (after `import yaml`, line 10).

Move the following from `tools/frontmatter.py` into `services/vault.py`, placing them in a new section after `get_file_creation_time` (after line 248). Preserve the exact function signatures and docstrings:

```python
# =============================================================================
# Frontmatter Matching & Vault Query Utilities
# =============================================================================


class FilterCondition(BaseModel):
    """A single frontmatter filter condition."""

    field: str
    value: str = ""
    match_type: str = "contains"


VALID_MATCH_TYPES = ("contains", "equals", "missing", "exists", "not_contains", "not_equals")
NO_VALUE_MATCH_TYPES = ("missing", "exists")

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _get_field_ci(frontmatter: dict, field: str):
    # ... exact copy from frontmatter.py lines 35-46


def _strip_wikilinks(text: str) -> str:
    # ... exact copy from frontmatter.py lines 56-58


def _matches_field(frontmatter: dict, field: str, value: str, match_type: str) -> bool:
    # ... exact copy from frontmatter.py lines 92-135


def _validate_filters(
    filters: list[FilterCondition] | None,
) -> tuple[list[dict], str | None]:
    # ... exact copy from frontmatter.py lines 138-163


def _get_file_date(
    md_file: Path, date_type: str, frontmatter: dict | None = None,
) -> datetime | None:
    # ... exact copy from frontmatter.py lines 166-191


def _find_matching_files(
    field: str | None,
    value: str,
    match_type: str,
    parsed_filters: list[dict],
    include_fields: list[str] | None = None,
    folder: Path | None = None,
    recursive: bool = False,
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    date_type: str = "modified",
) -> list[str | dict]:
    # ... exact copy from frontmatter.py lines 194-279
```

Note: `_find_matching_files` already calls `extract_frontmatter`, `get_relative_path`, `get_vault_files`, `_matches_field`, `_get_field_ci`, `_get_file_date` — all of which will now be in the same module (no imports needed). `_matches_field` calls `_get_field_ci` and `_strip_wikilinks` — also local. `_get_file_date` calls `extract_frontmatter`, `parse_frontmatter_date`, `get_file_creation_time` — all local. `_validate_filters` references `FilterCondition`, `VALID_MATCH_TYPES`, `NO_VALUE_MATCH_TYPES` — all local.

**Step 2: Update tools/frontmatter.py imports**

Remove from `tools/frontmatter.py`:
- `FilterCondition` class (lines 27-32)
- `_get_field_ci` function (lines 35-46)
- `_WIKILINK_RE` (line 49) — but keep `_JSON_SCALAR_RE` (line 50, used by `_normalize_frontmatter_value`)
- `VALID_MATCH_TYPES` and `NO_VALUE_MATCH_TYPES` (lines 52-53)
- `_strip_wikilinks` function (lines 56-58)
- `_matches_field` function (lines 92-135)
- `_validate_filters` function (lines 138-163)
- `_get_file_date` function (lines 166-191)
- `_find_matching_files` function (lines 194-279)

Add these to the existing `from services.vault import (...)` block:
```python
from services.vault import (
    # ... existing imports ...
    FilterCondition,
    VALID_MATCH_TYPES,
    NO_VALUE_MATCH_TYPES,
    _find_matching_files,
    _get_field_ci,
    _matches_field,
    _validate_filters,
)
```

Note: `_get_file_date` and `_strip_wikilinks` are NOT used in frontmatter.py's remaining code — don't import them. `_get_field_ci` IS still used by `_find_matching_files` (now in vault.py) but also in... let me check... No, it's only used by `_matches_field` and `_find_matching_files`, both of which moved. So `_get_field_ci` does NOT need to be imported back into frontmatter.py either.

Actually, looking more carefully at the remaining code in `frontmatter.py`:
- `_resolve_batch_targets` (line 359) calls `_find_matching_files`, `_validate_filters`, `VALID_MATCH_TYPES`, `NO_VALUE_MATCH_TYPES` — need imports
- `_needs_confirmation` (line 346) uses `consume_preview`, `store_preview` — already imported
- `_confirmation_preview` (line 333) uses `ok` — already imported
- `batch_update_frontmatter` (line 441) uses `FilterCondition` in its signature — need import

So the imports needed back in frontmatter.py are: `FilterCondition`, `VALID_MATCH_TYPES`, `NO_VALUE_MATCH_TYPES`, `_find_matching_files`, `_validate_filters`.

**Step 3: Update tools/search.py imports**

Change:
```python
from tools.frontmatter import (
    FilterCondition,
    _find_matching_files,
    _validate_filters,
)
```
To:
```python
from services.vault import (
    FilterCondition,
    _find_matching_files,
    _validate_filters,
)
```

And merge into the existing `from services.vault import err, ok, resolve_dir` block:
```python
from services.vault import (
    FilterCondition,
    _find_matching_files,
    _validate_filters,
    err,
    ok,
    resolve_dir,
)
```

**Step 4: Run tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All ~694 tests pass. Zero logic changed — only import paths.

**Step 5: Commit**

```bash
git add src/services/vault.py src/tools/frontmatter.py src/tools/search.py
git commit -m "refactor: move frontmatter matching utilities to services/vault.py (#139)"
```

---

### Task 2: Add query-based targeting to batch_move_files

**Files:**
- Modify: `src/tools/files.py:904-961` — rewrite `batch_move_files`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests for query-based batch_move_files**

Add these tests to `tests/test_tools_files.py` inside the existing `TestBatchMoveFiles` class (or create a new `TestBatchMoveFilesQuery` class after it):

```python
class TestBatchMoveFilesQuery:
    """Tests for query-based batch_move_files."""

    def test_query_move_by_frontmatter(self, vault_config):
        """Should move files matching frontmatter criteria to destination folder."""
        (vault_config / "src").mkdir(exist_ok=True)
        for name in ["alice.md", "bob.md", "charlie.md"]:
            (vault_config / "src" / name).write_text(
                f"---\ncategory: person\n---\n# {name}\n"
            )
        # Non-matching file should stay
        (vault_config / "src" / "project.md").write_text(
            "---\ncategory: project\n---\n# Project\n"
        )
        result = json.loads(batch_move_files(
            target_field="category",
            target_value="person",
            target_match_type="equals",
            destination_folder="People",
            folder="src",
            confirm=True,
        ))
        assert result["success"] is True
        assert "3 succeeded" in result["message"]
        assert (vault_config / "People" / "alice.md").exists()
        assert (vault_config / "People" / "bob.md").exists()
        assert (vault_config / "People" / "charlie.md").exists()
        # Non-matching file untouched
        assert (vault_config / "src" / "project.md").exists()

    def test_query_move_folder_only(self, vault_config):
        """Should move all files in a folder to destination."""
        (vault_config / "inbox").mkdir(exist_ok=True)
        for name in ["a.md", "b.md"]:
            (vault_config / "inbox" / name).write_text(f"# {name}\n")
        result = json.loads(batch_move_files(
            folder="inbox",
            destination_folder="archive",
            confirm=True,
        ))
        assert result["success"] is True
        assert "2 succeeded" in result["message"]
        assert (vault_config / "archive" / "a.md").exists()
        assert (vault_config / "archive" / "b.md").exists()

    def test_query_move_confirmation_flow(self, vault_config):
        """Query-based moves should require confirmation when >5 files."""
        clear_pending_previews()
        (vault_config / "bulk").mkdir(exist_ok=True)
        for i in range(8):
            (vault_config / "bulk" / f"note{i}.md").write_text(
                f"---\nstatus: draft\n---\n# Note {i}\n"
            )
        # Step 1: preview
        preview = json.loads(batch_move_files(
            target_field="status",
            target_value="draft",
            target_match_type="equals",
            destination_folder="published",
            folder="bulk",
        ))
        assert preview["confirmation_required"] is True
        assert "files" in preview
        assert len(preview["files"]) == 8
        # Step 2: confirm
        result = json.loads(batch_move_files(
            target_field="status",
            target_value="draft",
            target_match_type="equals",
            destination_folder="published",
            folder="bulk",
            confirm=True,
        ))
        assert result["success"] is True
        assert "8 succeeded" in result["message"]

    def test_query_move_no_matches(self, vault_config):
        """Should return early when no files match."""
        result = json.loads(batch_move_files(
            target_field="category",
            target_value="nonexistent",
            destination_folder="dest",
        ))
        assert result["success"] is True
        assert "No files matched" in result["message"]

    def test_moves_and_target_field_mutual_exclusion(self, vault_config):
        """Should error when both moves and target_field are provided."""
        result = json.loads(batch_move_files(
            moves=[{"source": "a.md", "destination": "b.md"}],
            target_field="category",
            target_value="person",
            destination_folder="People",
        ))
        assert result["success"] is False

    def test_moves_and_destination_folder_mutual_exclusion(self, vault_config):
        """Should error when both moves and destination_folder are provided."""
        result = json.loads(batch_move_files(
            moves=[{"source": "a.md", "destination": "b.md"}],
            destination_folder="People",
        ))
        assert result["success"] is False

    def test_query_without_destination_folder(self, vault_config):
        """Should error when query params are given without destination_folder."""
        result = json.loads(batch_move_files(
            target_field="category",
            target_value="person",
        ))
        assert result["success"] is False

    def test_query_move_with_target_filters(self, vault_config):
        """Should support additional target_filters for AND logic."""
        from services.vault import FilterCondition

        (vault_config / "mixed").mkdir(exist_ok=True)
        (vault_config / "mixed" / "active_person.md").write_text(
            "---\ncategory: person\nstatus: active\n---\n"
        )
        (vault_config / "mixed" / "draft_person.md").write_text(
            "---\ncategory: person\nstatus: draft\n---\n"
        )
        result = json.loads(batch_move_files(
            target_field="category",
            target_value="person",
            target_match_type="equals",
            target_filters=[FilterCondition(field="status", value="active", match_type="equals")],
            destination_folder="active_people",
            folder="mixed",
            confirm=True,
        ))
        assert result["success"] is True
        assert "1 succeeded" in result["message"]
        assert (vault_config / "active_people" / "active_person.md").exists()
        assert (vault_config / "mixed" / "draft_person.md").exists()

    def test_existing_moves_still_work(self, vault_config):
        """Explicit moves list should continue working as before."""
        (vault_config / "old.md").write_text("# Old\n")
        result = json.loads(batch_move_files(
            moves=[{"source": "old.md", "destination": "new/old.md"}],
        ))
        assert result["success"] is True
        assert (vault_config / "new" / "old.md").exists()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchMoveFilesQuery -v`
Expected: FAIL — `batch_move_files` doesn't accept the new parameters yet.

**Step 3: Implement query-based batch_move_files**

Replace `batch_move_files` in `src/tools/files.py` (lines 904-961). Add `FilterCondition` to the imports from `services.vault`:

Add to the existing `from services.vault import (...)` block in files.py:
```python
from services.vault import (
    # ... existing imports ...
    FilterCondition,
    VALID_MATCH_TYPES,
    NO_VALUE_MATCH_TYPES,
    _find_matching_files,
    _validate_filters,
    resolve_dir,
)
```

New implementation:

```python
def batch_move_files(
    moves: list[dict] | None = None,
    destination_folder: str | None = None,
    target_field: str | None = None,
    target_value: str | None = None,
    target_match_type: str = "contains",
    target_filters: list[FilterCondition] | None = None,
    folder: str | None = None,
    recursive: bool = False,
    confirm: bool = False,
) -> str:
    """Move multiple vault files to new locations.

    Two modes (mutually exclusive):
    - Explicit: provide 'moves' list of {source, destination} dicts.
    - Query-based: provide targeting params + 'destination_folder'.

    Args:
        moves: Explicit list of move operations, each with 'source' and 'destination'.
        destination_folder: Target folder for query-based moves (filenames preserved).
        target_field: Find files where this frontmatter field matches target_value.
        target_value: Value to match for target_field.
        target_match_type: How to match - 'contains', 'equals', 'missing', 'exists',
            'not_contains', or 'not_equals' (default 'contains').
        target_filters: Additional targeting conditions (AND logic).
        folder: Restrict targeting to files within this folder.
        recursive: Include subfolders when folder is set (default false).
        confirm: Must be true to execute when moving more than 5 files.

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    has_query = target_field is not None or folder is not None

    # Mutual exclusivity checks
    if moves is not None and has_query:
        return err("Provide either 'moves' or query parameters (target_field/folder), not both")
    if moves is not None and destination_folder is not None:
        return err("Cannot combine 'moves' with 'destination_folder'")
    if has_query and destination_folder is None:
        return err("'destination_folder' is required when using query-based targeting")

    if moves is not None:
        return _batch_move_explicit(moves, confirm)

    return _batch_move_query(
        destination_folder, target_field, target_value, target_match_type,
        target_filters, folder, recursive, confirm,
    )


def _batch_move_explicit(moves: list[dict], confirm: bool) -> str:
    """Execute explicit moves list (original behavior)."""
    if not moves:
        return err("moves list is empty")

    if len(moves) > BATCH_CONFIRM_THRESHOLD:
        move_keys = tuple(
            (m.get("source", ""), m.get("destination", ""))
            for m in moves if isinstance(m, dict)
        )
        key = ("batch_move_files", move_keys)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            files = []
            for m in moves:
                if isinstance(m, dict) and m.get("source"):
                    files.append(f"{m['source']} → {m.get('destination', '?')}")
            return ok(
                "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=f"This will move {len(moves)} files.",
                files=files,
            )

    results = []
    for i, move in enumerate(moves):
        if not isinstance(move, dict):
            results.append((False, f"Item {i}: expected dict, got {type(move).__name__}"))
            continue

        source = move.get("source")
        destination = move.get("destination")

        if not source:
            results.append((False, f"Item {i}: missing 'source' key"))
            continue
        if not destination:
            results.append((False, f"Item {i}: missing 'destination' key"))
            continue

        success, message = do_move_file(source, destination)
        results.append((success, message))

    return ok(format_batch_result("move", results))


def _batch_move_query(
    destination_folder: str,
    target_field: str | None,
    target_value: str | None,
    target_match_type: str,
    target_filters: list[FilterCondition] | None,
    folder: str | None,
    recursive: bool,
    confirm: bool,
) -> str:
    """Execute query-based batch move."""
    # Validate target_match_type
    if target_field is not None:
        if target_match_type not in VALID_MATCH_TYPES:
            return err(
                f"target_match_type must be one of {VALID_MATCH_TYPES}, "
                f"got '{target_match_type}'"
            )
        if target_match_type not in NO_VALUE_MATCH_TYPES and target_value is None:
            return err(f"target_value is required for target_match_type '{target_match_type}'")

    # Validate target_filters
    parsed_filters, filter_err = _validate_filters(target_filters)
    if filter_err:
        return err(filter_err)

    # Resolve folder
    folder_path = None
    if folder is not None:
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return err(folder_err)

    # Find matching files
    paths = _find_matching_files(
        target_field, target_value or "", target_match_type,
        parsed_filters, folder=folder_path, recursive=recursive,
    )

    if not paths:
        msg = "No files matched the targeting criteria"
        if folder is not None:
            msg += f" in folder '{folder}'"
        return ok(msg, results=[], total=0)

    # Build move pairs: source_path -> destination_folder/filename
    move_pairs = []
    for source_path in paths:
        filename = Path(source_path).name
        dest_path = str(Path(destination_folder) / filename)
        move_pairs.append((source_path, dest_path))

    # Confirmation gate
    if len(move_pairs) > BATCH_CONFIRM_THRESHOLD:
        pair_keys = tuple(sorted((s, d) for s, d in move_pairs))
        key = ("batch_move_files", destination_folder, pair_keys)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            files = [f"{s} → {d}" for s, d in move_pairs]
            context_parts = []
            if target_field is not None:
                context_parts.append(f"target_field='{target_field}', target_value='{target_value}'")
            if folder is not None:
                context_parts.append(f"folder='{folder}'")
            context = " matched by " + ", ".join(context_parts) if context_parts else ""
            return ok(
                "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=f"This will move {len(move_pairs)} files to '{destination_folder}'{context}.",
                files=files,
            )

    # Execute moves
    results = []
    for source_path, dest_path in move_pairs:
        success, message = do_move_file(source_path, dest_path)
        results.append((success, message))

    return ok(format_batch_result("move", results))
```

**Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchMoveFilesQuery -v`
Expected: All new tests pass.

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass (existing + new).

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add query-based targeting to batch_move_files (#139)"
```

---

### Task 3: Update MCP server registration and system prompt

**Files:**
- Modify: `src/mcp_server.py` — no changes needed (batch_move_files signature is backward-compatible since all new params have defaults)
- Modify: `system_prompt.txt.example` — update the batch_move_files tool reference if it exists there

**Step 1: Verify MCP registration still works**

The existing `mcp.tool()(batch_move_files)` in `mcp_server.py` should auto-detect the new parameters via introspection. No code change needed — just verify.

**Step 2: Update CLAUDE.md tool table**

Update the `batch_move_files` row in the MCP Tools table in `CLAUDE.md` to mention the new query-based parameters.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with query-based batch_move_files params (#139)"
```

---

### Task 4: Update test imports for moved functions (if any tests broke)

This is a cleanup task — if any tests in `test_find_notes.py` or `test_tools_frontmatter.py` import the moved functions from `tools.frontmatter`, they should still work since `tools/frontmatter.py` re-imports them. But update any test that imports private functions (`_find_matching_files`, `_get_file_date`) to import from `services.vault` instead for clarity.

**Files:**
- Modify: `tests/test_find_notes.py` — update `from tools.frontmatter import _find_matching_files` (6 occurrences) to `from services.vault import _find_matching_files`
- Modify: `tests/test_find_notes.py` — update `from tools.frontmatter import _get_file_date` (3 occurrences) to `from services.vault import _get_file_date`
- Modify: `tests/test_find_notes.py` — update `from tools.frontmatter import FilterCondition` (3 occurrences) to `from services.vault import FilterCondition`

**Step 1: Update imports**

Use find-and-replace across `tests/test_find_notes.py`:
- `from tools.frontmatter import _find_matching_files` → `from services.vault import _find_matching_files`
- `from tools.frontmatter import _get_file_date` → `from services.vault import _get_file_date`
- `from tools.frontmatter import FilterCondition` → `from services.vault import FilterCondition`

Also update `tests/test_tools_frontmatter.py` if it imports `FilterCondition`:
- `from tools.frontmatter import (FilterCondition,` — this still works via re-export, but update for clarity: `from services.vault import FilterCondition`

**Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass.

**Step 3: Commit**

```bash
git add tests/test_find_notes.py tests/test_tools_frontmatter.py
git commit -m "refactor: update test imports for moved vault utilities (#139)"
```
