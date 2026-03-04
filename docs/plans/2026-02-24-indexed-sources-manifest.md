# Indexed Sources Manifest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the O(N chunks) full-metadata scan in `prune_deleted_files` with an O(D) manifest-based approach that only touches the handful of files actually deleted between runs.

**Architecture:** Add `indexed_sources.json` to `CHROMA_PATH` (alongside `.last_indexed`). After each `index_vault()` run, write the full set of indexed source paths to it. On the next run, load the manifest, compute `manifest - valid_sources` to identify deleted files, and call `collection.delete(where={"source": ...})` only for those. Fall back to the existing full-scan path when no manifest exists (first run or `--full`).

**Tech Stack:** Python stdlib `json`, ChromaDB where-filter deletes

---

### Task 1: Add manifest helpers and tests

**Files:**
- Modify: `src/index_vault.py`
- Modify: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py` — new import at top:

```python
import json
```

Add to the existing imports from `index_vault`:

```python
from index_vault import (
    _fixed_chunk_text,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
    get_last_run,
    load_manifest,
    mark_run,
    save_manifest,
)
```

Append this class to `tests/test_chunking.py`:

```python
class TestManifest:
    """Tests for indexed_sources.json manifest helpers."""

    def test_load_manifest_no_file(self, tmp_path):
        """Returns None when no manifest exists."""
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = load_manifest()
        assert result is None

    def test_load_manifest_returns_set(self, tmp_path):
        """Returns a set of source paths from the manifest file."""
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps(["vault/a.md", "vault/b.md"]))
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = load_manifest()
        assert result == {"vault/a.md", "vault/b.md"}

    def test_load_manifest_corrupt_returns_none(self, tmp_path):
        """Returns None (and logs a warning) on corrupt manifest."""
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text("not valid json {{{")
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = load_manifest()
        assert result is None

    def test_save_manifest_writes_sorted(self, tmp_path):
        """Writes a sorted JSON array to indexed_sources.json."""
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            save_manifest({"vault/c.md", "vault/a.md", "vault/b.md"})
        content = json.loads((tmp_path / "indexed_sources.json").read_text())
        assert content == ["vault/a.md", "vault/b.md", "vault/c.md"]

    def test_save_manifest_creates_dir(self, tmp_path):
        """Creates CHROMA_PATH directory if it doesn't exist."""
        chroma_path = str(tmp_path / "new_chroma_dir")
        with patch("index_vault.CHROMA_PATH", chroma_path):
            save_manifest({"vault/a.md"})
        assert (Path(chroma_path) / "indexed_sources.json").exists()
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestManifest -v
```

Expected: FAIL — `load_manifest` and `save_manifest` are not defined yet.

**Step 3: Add `import json` and manifest helpers to `src/index_vault.py`**

Add `import json` to the stdlib imports block at the top (after `import hashlib`, before `import logging`):

```python
import json
```

Add `get_manifest_file`, `load_manifest`, and `save_manifest` functions after `mark_run` (around line 52):

```python
def get_manifest_file() -> str:
    """Return path to the indexed sources manifest file."""
    return os.path.join(CHROMA_PATH, "indexed_sources.json")


def load_manifest() -> set[str] | None:
    """Load set of previously indexed source paths.

    Returns None if no manifest exists or it cannot be read,
    triggering a full-scan fallback in prune_deleted_files.
    """
    path = get_manifest_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load indexed_sources manifest, falling back to full scan")
        return None


def save_manifest(sources: set[str]) -> None:
    """Save the current set of indexed source paths to disk."""
    os.makedirs(CHROMA_PATH, exist_ok=True)
    with open(get_manifest_file(), "w") as f:
        json.dump(sorted(sources), f)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestManifest -v
```

Expected: All 5 PASS.

**Step 5: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: add indexed_sources manifest helpers (#42)"
```

---

### Task 2: Update `prune_deleted_files` to use manifest

**Files:**
- Modify: `src/index_vault.py`
- Modify: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Append to `tests/test_chunking.py`:

```python
class TestPruneDeletedFiles:
    """Tests for the manifest-aware prune_deleted_files."""

    def test_fast_path_no_deletions(self):
        """Fast path: nothing to prune when indexed matches valid."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md", "b.md"},
                indexed_sources={"a.md", "b.md"},
            )
        mock_collection.delete.assert_not_called()
        assert result == 0

    def test_fast_path_deletes_removed_source(self):
        """Fast path: deletes by source filter for each removed file."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md", "b.md"},
                indexed_sources={"a.md", "b.md", "deleted.md"},
            )
        mock_collection.delete.assert_called_once_with(where={"source": "deleted.md"})
        assert result == 1

    def test_fast_path_multiple_deletions(self):
        """Fast path: calls delete once per deleted source."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md"},
                indexed_sources={"a.md", "del1.md", "del2.md"},
            )
        assert mock_collection.delete.call_count == 2
        assert result == 2

    def test_slow_path_when_no_manifest(self):
        """Slow path (indexed_sources=None): falls back to full metadata scan."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2", "id3"],
            "metadatas": [
                {"source": "kept.md"},
                {"source": "stale.md"},
                {"source": "stale.md"},
            ],
        }
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"kept.md"},
                indexed_sources=None,
            )
        mock_collection.get.assert_called_once_with(include=["metadatas"])
        assert result == 1  # 1 unique deleted source

    def test_slow_path_empty_collection(self):
        """Slow path: returns 0 immediately on empty collection."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files({"a.md"}, indexed_sources=None)
        mock_collection.delete.assert_not_called()
        assert result == 0
```

Also add `prune_deleted_files` to the imports at the top of the test file:

```python
from index_vault import (
    _fixed_chunk_text,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
    get_last_run,
    load_manifest,
    mark_run,
    prune_deleted_files,
    save_manifest,
)
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestPruneDeletedFiles -v
```

Expected: FAIL — `prune_deleted_files` doesn't accept `indexed_sources` yet.

**Step 3: Update `prune_deleted_files` in `src/index_vault.py`**

Replace the existing `prune_deleted_files` function:

```python
def prune_deleted_files(valid_sources: set[str], indexed_sources: set[str] | None = None) -> int:
    """Remove entries for files that no longer exist. Returns count of deleted sources.

    Uses a manifest-based fast path when indexed_sources is provided,
    falling back to a full metadata scan when it is None (first run or --full).
    """
    collection = get_collection()

    if indexed_sources is not None:
        # Fast path: only examine sources known to be indexed
        deleted_sources = indexed_sources - valid_sources
        for source in deleted_sources:
            collection.delete(where={"source": source})
        return len(deleted_sources)

    # Slow path: full metadata scan (no manifest available)
    all_entries = collection.get(include=["metadatas"])
    if not all_entries["ids"]:
        return 0

    ids_to_delete = []
    deleted_sources: set[str] = set()
    for doc_id, metadata in zip(all_entries["ids"], all_entries["metadatas"]):
        source = metadata.get("source", "")
        if source not in valid_sources:
            ids_to_delete.append(doc_id)
            deleted_sources.add(source)

    if ids_to_delete:
        batch_size = 5000
        for i in range(0, len(ids_to_delete), batch_size):
            collection.delete(ids=ids_to_delete[i:i + batch_size])

    return len(deleted_sources)
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestPruneDeletedFiles -v
```

Expected: All 5 PASS.

**Step 5: Run full suite to check for regressions**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: All pass.

**Step 6: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: manifest-based fast path for prune_deleted_files (#42)"
```

---

### Task 3: Wire manifest into `index_vault`

**Files:**
- Modify: `src/index_vault.py`
- Modify: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Append to `tests/test_chunking.py`:

```python
class TestIndexVaultManifest:
    """Tests that index_vault loads and saves the manifest correctly."""

    def test_saves_manifest_after_run(self, tmp_path):
        """index_vault saves valid_sources to manifest after each run."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault.index_file"), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        manifest_path = tmp_path / "indexed_sources.json"
        assert manifest_path.exists()
        content = json.loads(manifest_path.read_text())
        assert str(vault_file) in content

    def test_full_reindex_skips_manifest(self, tmp_path):
        """--full reindex passes indexed_sources=None to prune (forces slow path)."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault.index_file"), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0) as mock_prune, \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=True)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") is None

    def test_incremental_run_uses_manifest(self, tmp_path):
        """Incremental run loads manifest and passes it to prune."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps([str(vault_file), "/old/deleted.md"]))

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault.index_file"), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=1) as mock_prune, \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 4
            index_vault(full=False)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") == {str(vault_file), "/old/deleted.md"}
```

Also add `index_vault` to the imports:

```python
from index_vault import (
    _fixed_chunk_text,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
    get_last_run,
    index_vault,
    load_manifest,
    mark_run,
    prune_deleted_files,
    save_manifest,
)
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestIndexVaultManifest -v
```

Expected: FAIL.

**Step 3: Update `index_vault` in `src/index_vault.py`**

Replace the `index_vault` function:

```python
def index_vault(full: bool = False) -> None:
    """Index the vault, updating only changed files unless full=True."""
    scan_start = time.time()
    last_run = 0 if full else get_last_run()

    # Get all valid markdown files
    all_files = get_vault_files(VAULT_PATH)
    valid_sources = set(str(f) for f in all_files)

    # Load manifest for fast pruning (skip on --full to force full scan)
    indexed_sources = None if full else load_manifest()

    # Index new/modified files
    indexed = 0
    for md_file in all_files:
        try:
            modified = md_file.stat().st_mtime > last_run
        except FileNotFoundError:
            continue
        if modified:
            try:
                index_file(md_file)
            except FileNotFoundError:
                continue
            indexed += 1
            if indexed % 100 == 0:
                print(f"Indexed {indexed} files...")

    # Prune deleted files
    pruned = prune_deleted_files(valid_sources, indexed_sources=indexed_sources)

    # Save updated manifest
    save_manifest(valid_sources)

    mark_run(scan_start)
    collection = get_collection()
    print(f"Done. Indexed {indexed} new/modified files. Pruned {pruned} deleted source(s). Total chunks: {collection.count()}")
```

**Step 4: Run the new tests**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/test_chunking.py::TestIndexVaultManifest -v
```

Expected: All 3 PASS.

**Step 5: Run full suite**

```bash
cd /home/barry/projects/obsidian-tools && .venv/bin/python -m pytest tests/ -v 2>&1 | tail -5
```

Expected: All pass.

**Step 6: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: wire manifest into index_vault, skip on --full (#42)"
```

---

### Task 4: Open PR

```bash
git push -u origin fix/indexed-sources-manifest
gh pr create --title "perf: indexed sources manifest for O(D) pruning (#42)" --body "$(cat <<'EOF'
## Summary
- Adds `indexed_sources.json` to `CHROMA_PATH` (alongside `.last_indexed`)
- `prune_deleted_files` now accepts optional `indexed_sources`: when provided, diffs against `valid_sources` and calls `collection.delete(where={"source": ...})` only for deleted files — O(D) instead of O(N chunks)
- `index_vault` loads the manifest before each run and saves it after; `--full` passes `indexed_sources=None` to force the existing full-scan fallback
- First run (no manifest) uses the existing full metadata scan automatically

## Test plan
- [ ] `TestManifest` — load/save/corrupt-file handling
- [ ] `TestPruneDeletedFiles` — fast path (no deletions, single, multiple), slow path (no manifest, empty collection)
- [ ] `TestIndexVaultManifest` — manifest saved after run, `--full` skips manifest, incremental run uses manifest
- [ ] Full test suite passes

Closes #42
EOF
)"
```
