# Parallel File Indexing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Parallelize file indexing in `index_vault` using `ThreadPoolExecutor` for 3-5x speedup on full reindexes.

**Architecture:** Replace the sequential `for md_file in all_files: index_file(md_file)` loop with `concurrent.futures.ThreadPoolExecutor`. Each file is an independent unit of work (read → chunk → upsert). ChromaDB's `PersistentClient` is thread-safe. A configurable `INDEX_WORKERS` env var controls concurrency.

**Tech Stack:** `concurrent.futures.ThreadPoolExecutor`, `threading` (for atomic counter)

---

### Task 1: Add `INDEX_WORKERS` config constant

**Files:**
- Modify: `src/config.py:51-52`
- Test: `tests/test_chunking.py` (existing `TestMarkRun` area — but config tests go in test_chunking since that's where index_vault tests live)

**Step 1: Write the failing test**

Add to `tests/test_chunking.py` imports and a new test class:

```python
# Add to imports at top:
# (already has: from index_vault import ...)
# No new imports needed for this test

class TestIndexWorkers:
    """Tests for INDEX_WORKERS configuration."""

    def test_default_value(self):
        """INDEX_WORKERS defaults to 4."""
        import config
        assert config.INDEX_WORKERS == 4

    def test_env_override(self, monkeypatch):
        """INDEX_WORKERS can be set via environment variable."""
        import importlib
        import config
        monkeypatch.setenv("INDEX_WORKERS", "8")
        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
        try:
            assert config.INDEX_WORKERS == 8
        finally:
            monkeypatch.delenv("INDEX_WORKERS", raising=False)
            with patch("dotenv.load_dotenv"):
                importlib.reload(config)

    def test_minimum_value(self, monkeypatch):
        """INDEX_WORKERS has a minimum of 1."""
        import importlib
        import config
        monkeypatch.setenv("INDEX_WORKERS", "0")
        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
        try:
            assert config.INDEX_WORKERS == 1
        finally:
            monkeypatch.delenv("INDEX_WORKERS", raising=False)
            with patch("dotenv.load_dotenv"):
                importlib.reload(config)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexWorkers -v`
Expected: FAIL — `config.INDEX_WORKERS` does not exist

**Step 3: Write minimal implementation**

Add to `src/config.py` after line 52 (`INDEX_INTERVAL`):

```python
INDEX_WORKERS = max(1, int(os.getenv("INDEX_WORKERS", "4")))
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexWorkers -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py tests/test_chunking.py
git commit -m "feat: add INDEX_WORKERS config constant"
```

---

### Task 2: Parallelize the indexing loop in `index_vault`

**Files:**
- Modify: `src/index_vault.py:1-11` (imports), `src/index_vault.py:467-524` (`index_vault` function)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py`:

```python
from unittest.mock import call

class TestParallelIndexing:
    """Tests for parallel file indexing in index_vault."""

    def test_indexes_files_with_thread_pool(self, tmp_path):
        """index_vault uses ThreadPoolExecutor for file indexing."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# Note {f.stem}")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault.index_file") as mock_index, \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 10
            index_vault(full=True)

        assert mock_index.call_count == 3
        indexed_files = {c.args[0] for c in mock_index.call_args_list}
        assert indexed_files == set(files)

    def test_file_error_does_not_stop_others(self, tmp_path):
        """A failing file doesn't prevent other files from being indexed."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        def selective_index(f):
            if f.name == "bad.md":
                raise RuntimeError("index failed")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file, bad_file]), \
             patch("index_vault.index_file", side_effect=selective_index) as mock_index, \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=True)

        assert mock_index.call_count == 2

    def test_logs_error_for_failed_file(self, tmp_path, caplog):
        """Failed files are logged at ERROR level."""
        import logging
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[bad_file]), \
             patch("index_vault.index_file", side_effect=RuntimeError("boom")), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1):
            mock_coll.return_value.count.return_value = 0
            with caplog.at_level(logging.ERROR, logger="index_vault"):
                index_vault(full=True)

        assert any("boom" in r.message for r in caplog.records)

    def test_progress_logging(self, tmp_path, caplog):
        """Progress is logged every 100 files."""
        import logging
        files = [tmp_path / f"note{i}.md" for i in range(150)]
        for f in files:
            f.write_text(f"# Note")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault.index_file"), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 4):
            mock_coll.return_value.count.return_value = 500
            with caplog.at_level(logging.INFO, logger="index_vault"):
                index_vault(full=True)

        progress_msgs = [r for r in caplog.records if "Indexed" in r.message and "files..." in r.message]
        assert len(progress_msgs) >= 1

    def test_skips_unmodified_files(self, tmp_path):
        """Incremental indexing only submits modified files to the pool."""
        old_file = tmp_path / "old.md"
        old_file.write_text("# Old")
        new_file = tmp_path / "new.md"
        new_file.write_text("# New")

        # Set old_file mtime to the past
        old_mtime = time.time() - 3600
        os.utime(old_file, (old_mtime, old_mtime))

        # last_run between old and new
        last_run_time = time.time() - 1800

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[old_file, new_file]), \
             patch("index_vault.get_last_run", return_value=last_run_time), \
             patch("index_vault.index_file") as mock_index, \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        assert mock_index.call_count == 1
        assert mock_index.call_args.args[0] == new_file
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestParallelIndexing -v`
Expected: FAIL — `index_vault` doesn't use ThreadPoolExecutor yet (tests should still pass with sequential, except the error handling ones may differ)

Actually — the tests are designed to work with the parallel implementation. The key behavioral change is error isolation: currently a `RuntimeError` in `index_file` would propagate and crash the loop. With the executor, errors are caught per-future. So `test_file_error_does_not_stop_others` should fail with the current sequential code.

**Step 3: Write the implementation**

Modify `src/index_vault.py`:

Add imports at top (after `import time`, before `from datetime`):
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
```

Add import of `INDEX_WORKERS` on the config import line:
```python
from config import VAULT_PATH, CHROMA_PATH, INDEX_WORKERS
```

Replace the indexing loop in `index_vault` (lines 494-508) with:

```python
    # Collect files to index
    to_index = []
    for md_file in all_files:
        try:
            modified = md_file.stat().st_mtime > last_run
        except FileNotFoundError:
            continue
        if modified:
            to_index.append(md_file)

    # Index files in parallel
    indexed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as executor:
        futures = {executor.submit(index_file, f): f for f in to_index}
        for future in as_completed(futures):
            md_file = futures[future]
            try:
                future.result()
                indexed += 1
                if indexed % 100 == 0:
                    logger.info("Indexed %s files...", indexed)
            except Exception:
                failed += 1
                logger.error("Failed to index %s", md_file, exc_info=True)
```

Update the final log line (line 523) to include failures:
```python
    logger.info("Done. Indexed %s new/modified files (%s failed). Pruned %s deleted source(s). Total chunks: %s",
                indexed, failed, pruned, collection.count())
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestParallelIndexing -v`
Expected: PASS

**Step 5: Run all tests to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: All PASS (existing `TestIndexVaultManifest` tests patch `index_file` so threading is transparent)

**Step 6: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: parallelize file indexing with ThreadPoolExecutor

Closes #51"
```

---

### Task 3: Update documentation

**Files:**
- Modify: `CLAUDE.md` (brief mention in Architecture > Key Components for index_vault)

**Step 1: Add parallel indexing note to CLAUDE.md**

In the `index_vault.py` line under Key Components or the config table, add `INDEX_WORKERS` to the configuration table:

| `INDEX_WORKERS` | `4` | Thread pool size for file indexing |

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add INDEX_WORKERS to config table"
```
