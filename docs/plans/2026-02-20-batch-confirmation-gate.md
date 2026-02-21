# Batch Confirmation Gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent LLM agents from bypassing the batch confirmation gate by adding server-side hash-based confirmation tracking.

**Architecture:** Add confirmation helpers to `services/vault.py` that compute a SHA-256 hash of operation parameters and store pending confirmations in a module-level dict. Both `batch_update_frontmatter` and `batch_move_files` use these helpers so that `confirm=True` only works when a matching preview was already issued. Records are single-use and expire after 5 minutes.

**Tech Stack:** Python stdlib (`hashlib`, `json`, `time`)

---

### Task 1: Add confirmation helpers to vault.py

**Files:**
- Modify: `src/services/vault.py`
- Test: `tests/test_vault_service.py`

**Step 1: Write failing tests for the confirmation helpers**

Add to `tests/test_vault_service.py`:

```python
class TestConfirmationHelpers:
    """Tests for hash-based batch confirmation tracking."""

    def test_compute_op_hash_deterministic(self):
        """Same params produce same hash."""
        params = {"field": "status", "value": "done", "paths": ["b.md", "a.md"]}
        h1 = compute_op_hash(params)
        h2 = compute_op_hash(params)
        assert h1 == h2

    def test_compute_op_hash_sorts_paths(self):
        """Path order doesn't affect hash."""
        h1 = compute_op_hash({"paths": ["b.md", "a.md"]})
        h2 = compute_op_hash({"paths": ["a.md", "b.md"]})
        assert h1 == h2

    def test_compute_op_hash_different_params(self):
        """Different params produce different hashes."""
        h1 = compute_op_hash({"field": "status", "value": "done"})
        h2 = compute_op_hash({"field": "status", "value": "open"})
        assert h1 != h2

    def test_store_and_check_confirmation(self):
        """Stored confirmation can be checked and is single-use."""
        clear_pending_confirmations()
        h = compute_op_hash({"field": "x"})
        store_confirmation(h)
        assert check_confirmation(h) is True
        # Single-use: second check fails
        assert check_confirmation(h) is False

    def test_check_confirmation_missing(self):
        """Non-existent hash returns False."""
        clear_pending_confirmations()
        assert check_confirmation("nonexistent") is False

    def test_check_confirmation_expired(self):
        """Expired confirmation returns False."""
        clear_pending_confirmations()
        h = compute_op_hash({"field": "x"})
        store_confirmation(h)
        # Manually expire the record
        from services.vault import _pending_confirmations, CONFIRM_EXPIRY_SECONDS
        _pending_confirmations[h]["created"] = time.time() - CONFIRM_EXPIRY_SECONDS - 1
        assert check_confirmation(h) is False

    def test_store_cleans_expired(self):
        """Storing a new confirmation cleans up expired ones."""
        clear_pending_confirmations()
        h_old = compute_op_hash({"field": "old"})
        store_confirmation(h_old)
        from services.vault import _pending_confirmations, CONFIRM_EXPIRY_SECONDS
        _pending_confirmations[h_old]["created"] = time.time() - CONFIRM_EXPIRY_SECONDS - 1
        h_new = compute_op_hash({"field": "new"})
        store_confirmation(h_new)
        assert h_old not in _pending_confirmations
        assert h_new in _pending_confirmations
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestConfirmationHelpers -v`
Expected: ImportError / FAIL

**Step 3: Implement the confirmation helpers**

Add to `src/services/vault.py` after the existing imports, add `import hashlib` and `import time` to the import block. Then add a new section before the Batch Operations section:

```python
# =============================================================================
# Batch Confirmation Tracking
# =============================================================================

CONFIRM_EXPIRY_SECONDS = 300  # 5 minutes

_pending_confirmations: dict[str, dict] = {}


def compute_op_hash(params: dict) -> str:
    """Compute a SHA-256 hash of canonical operation parameters.

    Lists under 'paths' and 'moves' keys are sorted for order-independence.
    """
    canonical = {}
    for key, val in sorted(params.items()):
        if key == "paths" and isinstance(val, list):
            canonical[key] = sorted(val)
        elif key == "moves" and isinstance(val, list):
            canonical[key] = sorted(
                (json.dumps(m, sort_keys=True) for m in val)
            )
        else:
            canonical[key] = val
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def store_confirmation(op_hash: str) -> None:
    """Store a pending confirmation and clean up expired records."""
    now = time.time()
    expired = [
        k for k, v in _pending_confirmations.items()
        if now - v["created"] > CONFIRM_EXPIRY_SECONDS
    ]
    for k in expired:
        del _pending_confirmations[k]
    _pending_confirmations[op_hash] = {"created": now}


def check_confirmation(op_hash: str) -> bool:
    """Check and consume a pending confirmation. Returns True if valid."""
    record = _pending_confirmations.pop(op_hash, None)
    if record is None:
        return False
    if time.time() - record["created"] > CONFIRM_EXPIRY_SECONDS:
        return False
    return True


def clear_pending_confirmations() -> None:
    """Clear all pending confirmations. For testing only."""
    _pending_confirmations.clear()
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestConfirmationHelpers -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/services/vault.py tests/test_vault_service.py
git commit -m "feat: add hash-based confirmation helpers to vault service

Server-side tracking for batch operation confirmations.
Implements compute_op_hash, store_confirmation, check_confirmation
with 5-minute expiry and single-use semantics."
```

---

### Task 2: Wire confirmation into batch_update_frontmatter

**Files:**
- Modify: `src/tools/frontmatter.py`
- Test: `tests/test_tools_frontmatter.py`

**Step 1: Write failing tests**

Update existing tests and add new ones in `tests/test_tools_frontmatter.py`:

```python
# In TestBatchConfirmationGate:

def test_confirm_true_without_preview_returns_preview(self, vault_config):
    """confirm=True on first call should still return preview (no pending hash)."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    result = json.loads(
        batch_update_frontmatter(
            paths=paths, field="status", value="archived",
            operation="set", confirm=True,
        )
    )
    assert result["confirmation_required"] is True
    # Files not modified
    for path in paths:
        assert "status" not in (vault_config / path).read_text()

def test_two_step_confirmation_flow(self, vault_config):
    """Preview then confirm with same params should execute."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    kwargs = dict(paths=paths, field="status", value="archived", operation="set")
    # Step 1: preview
    r1 = json.loads(batch_update_frontmatter(**kwargs))
    assert r1["confirmation_required"] is True
    # Step 2: confirm
    r2 = json.loads(batch_update_frontmatter(**kwargs, confirm=True))
    assert r2["success"] is True
    assert "confirmation_required" not in r2
    assert "10 succeeded" in r2["message"]

def test_changed_params_between_preview_and_confirm(self, vault_config):
    """Changing params between preview and confirm returns new preview."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    # Preview with value="archived"
    batch_update_frontmatter(
        paths=paths, field="status", value="archived", operation="set",
    )
    # Confirm with value="draft" — different params, new preview
    result = json.loads(
        batch_update_frontmatter(
            paths=paths, field="status", value="draft",
            operation="set", confirm=True,
        )
    )
    assert result["confirmation_required"] is True

def test_confirmation_is_single_use(self, vault_config):
    """After executing, same confirm call starts a new preview cycle."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    kwargs = dict(paths=paths, field="status", value="archived", operation="set")
    # Preview + confirm
    batch_update_frontmatter(**kwargs)
    batch_update_frontmatter(**kwargs, confirm=True)
    # Second confirm without new preview → new preview
    result = json.loads(batch_update_frontmatter(**kwargs, confirm=True))
    assert result["confirmation_required"] is True

def test_expired_confirmation(self, vault_config):
    """Expired confirmation returns error message."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    kwargs = dict(paths=paths, field="status", value="archived", operation="set")
    batch_update_frontmatter(**kwargs)
    # Expire the record
    from services.vault import _pending_confirmations, CONFIRM_EXPIRY_SECONDS
    for v in _pending_confirmations.values():
        v["created"] = time.time() - CONFIRM_EXPIRY_SECONDS - 1
    result = json.loads(batch_update_frontmatter(**kwargs, confirm=True))
    # Should get a fresh preview (expired hash not found)
    assert result["confirmation_required"] is True
```

Also update the existing `test_executes_with_confirm_true` test — it currently expects `confirm=True` to bypass the gate directly. It now needs the two-step flow:

```python
def test_executes_with_confirm_true(self, vault_config):
    """Should execute via two-step flow: preview then confirm."""
    clear_pending_confirmations()
    paths = self._create_files(vault_config, 10)
    kwargs = dict(paths=paths, field="status", value="archived", operation="set")
    # Step 1: preview
    batch_update_frontmatter(**kwargs)
    # Step 2: confirm
    result = json.loads(batch_update_frontmatter(**kwargs, confirm=True))
    assert result["success"] is True
    assert "confirmation_required" not in result
    assert "10 succeeded" in result["message"]
```

Similarly update `test_query_target_requires_confirmation` and tests that call with `confirm=True` to use two-step flow.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchConfirmationGate -v`
Expected: FAIL (old tests pass `confirm=True` directly and succeed)

**Step 3: Implement the gate in frontmatter.py**

Modify `src/tools/frontmatter.py`:

1. Add imports:
```python
from services.vault import (
    ...existing imports...,
    check_confirmation,
    compute_op_hash,
    store_confirmation,
)
```

2. Modify `_resolve_batch_targets` — replace the confirmation logic. The function currently checks `if not confirm` in three places. Replace each with hash-based logic:

For query-based targeting (after resolving paths from target_field):
```python
    if target_field is not None:
        # ...existing path resolution...
        op_hash = compute_op_hash({
            "tool": "batch_update_frontmatter",
            "field": field, "value": value, "operation": operation,
            "paths": paths,
        })
        if confirm and check_confirmation(op_hash):
            pass  # Fall through to execution
        else:
            store_confirmation(op_hash)
            folder_note = f" in folder '{folder}'" if folder else ""
            context = (
                f" matched by target_field='{target_field}', "
                f"target_value='{target_value}'{folder_note}"
            )
            return None, _confirmation_preview(operation, field, value, paths, context)
```

Same pattern for folder-based and explicit paths > threshold.

**Important**: The `_resolve_batch_targets` function is shared logic but includes the `"tool"` key in the hash to namespace it. However, since `batch_move_files` doesn't use `_resolve_batch_targets`, this is fine — it will compute its own hash with `"tool": "batch_move_files"`.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/tools/frontmatter.py tests/test_tools_frontmatter.py
git commit -m "feat: hash-based confirmation gate for batch_update_frontmatter

confirm=True now only executes when a matching preview was already
issued. Prevents LLM agents from bypassing the two-step flow."
```

---

### Task 3: Wire confirmation into batch_move_files

**Files:**
- Modify: `src/tools/files.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests**

Update `tests/test_tools_files.py`:

```python
# Update TestBatchMoveConfirmationGate:

def test_confirm_true_without_preview_returns_preview(self, vault_config):
    """confirm=True on first call should still return preview."""
    clear_pending_confirmations()
    moves = self._create_files(vault_config, 10)
    result = json.loads(batch_move_files(moves=moves, confirm=True))
    assert result["confirmation_required"] is True

def test_two_step_confirmation_flow(self, vault_config):
    """Preview then confirm with same params should execute."""
    clear_pending_confirmations()
    moves = self._create_files(vault_config, 10)
    # Step 1: preview
    batch_move_files(moves=moves)
    # Step 2: confirm
    result = json.loads(batch_move_files(moves=moves, confirm=True))
    assert result["success"] is True
    assert "confirmation_required" not in result
    assert "10 succeeded" in result["message"]

def test_changed_params_returns_new_preview(self, vault_config):
    """Changing moves between preview and confirm returns new preview."""
    clear_pending_confirmations()
    moves = self._create_files(vault_config, 10)
    batch_move_files(moves=moves)
    # Change destinations
    altered = [{"source": m["source"], "destination": f"other/{m['source']}"} for m in moves]
    result = json.loads(batch_move_files(moves=altered, confirm=True))
    assert result["confirmation_required"] is True
```

Update existing `test_executes_with_confirm_true` to use two-step flow.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestBatchMoveConfirmationGate -v`
Expected: FAIL

**Step 3: Implement the gate in files.py**

Modify `src/tools/files.py`:

1. Add imports:
```python
from services.vault import (
    ...existing imports...,
    check_confirmation,
    compute_op_hash,
    store_confirmation,
)
```

2. In `batch_move_files`, replace the threshold check:
```python
    if len(moves) > BATCH_CONFIRM_THRESHOLD:
        op_hash = compute_op_hash({"tool": "batch_move_files", "moves": moves})
        if confirm and check_confirmation(op_hash):
            pass  # Fall through to execution
        else:
            store_confirmation(op_hash)
            files = []
            for m in moves:
                if isinstance(m, dict) and m.get("source"):
                    files.append(f"{m['source']} → {m.get('destination', '?')}")
            return ok(
                f"This will move {len(moves)} files. "
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                files=files,
            )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: hash-based confirmation gate for batch_move_files

Same server-side hash tracking as batch_update_frontmatter.
Closes #77."
```

---

### Task 4: Run full test suite and verify

**Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass, no regressions

**Step 2: Commit any fixups if needed**

---

### Task 5: Update system prompt (if needed)

**Files:**
- Review: `system_prompt.txt.example`

**Step 1: Check if system prompt wording needs changes**

The issue says "No behavioral change from the LLM's perspective" — the prompt already says to preview then confirm. Review `system_prompt.txt.example` to verify the batch tool guidance is still accurate. Only edit if something is misleading.

**Step 2: Commit if changed**
