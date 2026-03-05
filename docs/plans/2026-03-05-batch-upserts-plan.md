# Batch ChromaDB Upserts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace per-file ChromaDB upserts with batch upserts across files to speed up indexing.

**Architecture:** Refactor `index_vault()` into three phases: (1) parallel chunk preparation (existing), (2) bulk delete of stale chunks, (3) bulk upsert in fixed-size batches. Add `UPSERT_BATCH_SIZE` constant to config.py.

**Tech Stack:** Python, ChromaDB, ThreadPoolExecutor (existing)

---

### Task 1: Add UPSERT_BATCH_SIZE constant

**Files:**
- Modify: `src/config.py:54` (after INDEX_WORKERS)

**Step 1: Add the constant**

Add after the `INDEX_WORKERS` line (line 54):

```python
UPSERT_BATCH_SIZE = 500
```

**Step 2: Commit**

```bash
git add src/config.py
git commit -m "feat: add UPSERT_BATCH_SIZE constant to config"
```

---

### Task 2: Write tests for batch upsert behavior

Tests go in `tests/test_chunking.py` in a new `TestBatchUpserts` class after the existing `TestParallelIndexing` class (line 1402). All tests follow the existing pattern: patch `index_vault.*`, call `index_vault(full=True)`, assert on mock collection calls.

**Files:**
- Modify: `tests/test_chunking.py` (add after `TestParallelIndexing` class, ~line 1402)

**Step 1: Write failing tests**

Add the following test class at the end of the file:

```python
class TestBatchUpserts:
    """Tests for batched cross-file upserts in index_vault."""

    def _make_chunk_result(self, source: str, n_chunks: int = 2):
        """Helper to create a _prepare_file_chunks return value."""
        ids = [f"{source}_chunk_{i}" for i in range(n_chunks)]
        docs = [f"[{source}] content {i}" for i in range(n_chunks)]
        metas = [{"source": source, "chunk": i, "heading": "", "chunk_type": "body"} for i in range(n_chunks)]
        return source, ids, docs, metas

    def test_chunks_batched_into_single_upsert(self, tmp_path):
        """Multiple files' chunks are combined into a single upsert call when under batch size."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 6
            index_vault(full=True)

        # All 6 chunks (3 files x 2 chunks) in one upsert call
        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 1
        call_args = mock_collection.upsert.call_args[1]
        assert len(call_args["ids"]) == 6

    def test_upsert_respects_batch_size(self, tmp_path):
        """Chunks are split across multiple upsert calls when exceeding batch size."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 4):
            mock_coll.return_value.count.return_value = 6
            index_vault(full=True)

        # 6 chunks with batch_size=4 -> 2 upsert calls (4 + 2)
        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 2
        first_ids = mock_collection.upsert.call_args_list[0][1]["ids"]
        second_ids = mock_collection.upsert.call_args_list[1][1]["ids"]
        assert len(first_ids) == 4
        assert len(second_ids) == 2

    def test_stale_chunks_deleted_before_upsert(self, tmp_path):
        """Old chunks are deleted before new ones are upserted."""
        f = tmp_path / "note.md"
        f.write_text("# Note")

        result = self._make_chunk_result(str(f), 2)
        call_order = []

        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_collection.delete.side_effect = lambda **kw: call_order.append("delete")
        mock_collection.upsert.side_effect = lambda **kw: call_order.append("upsert")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[f]), \
             patch("index_vault._prepare_file_chunks", return_value=result), \
             patch("index_vault.get_collection", return_value=mock_collection), \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            index_vault(full=True)

        assert call_order[0] == "delete"
        assert call_order[-1] == "upsert"

    def test_failed_file_chunks_excluded_from_batch(self, tmp_path):
        """Chunks from files that failed preparation are not included in the batch upsert."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        good_result = self._make_chunk_result(str(good_file), 2)

        def selective_prepare(f):
            if f.name == "bad.md":
                raise RuntimeError("boom")
            return good_result

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file, bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=selective_prepare), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 2
            index_vault(full=True)

        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 1
        upserted_ids = mock_collection.upsert.call_args[1]["ids"]
        assert len(upserted_ids) == 2  # only good file's chunks

    def test_empty_results_skip_upsert(self, tmp_path):
        """When all files return None (empty), no upsert is called."""
        f = tmp_path / "empty.md"
        f.write_text("")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[f]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 500):
            mock_coll.return_value.count.return_value = 0
            index_vault(full=True)

        mock_coll.return_value.upsert.assert_not_called()

    def test_phase_progress_logging(self, tmp_path, caplog):
        """Batch upsert logs phase-based progress."""
        import logging
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 6
            with caplog.at_level(logging.INFO, logger="index_vault"):
                index_vault(full=True)

        messages = [r.message for r in caplog.records]
        # Should have preparation and upsert phase messages
        assert any("Prepared" in m for m in messages)
        assert any("Upserting" in m or "upsert" in m.lower() for m in messages)
```

**Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestBatchUpserts -v`

Expected: Most tests FAIL because `index_vault()` still does per-file upserts and doesn't read `UPSERT_BATCH_SIZE`.

**Step 3: Commit the failing tests**

```bash
git add tests/test_chunking.py
git commit -m "test: add tests for batch cross-file upserts (#121)"
```

---

### Task 3: Refactor index_vault() to use batch upserts

**Files:**
- Modify: `src/index_vault.py:17` (add config import)
- Modify: `src/index_vault.py:220-244` (replace the ThreadPoolExecutor + per-file upsert block)

**Step 1: Add UPSERT_BATCH_SIZE to the config import**

Change line 17:

```python
from config import VAULT_PATH, CHROMA_PATH, INDEX_WORKERS, UPSERT_BATCH_SIZE, setup_logging
```

**Step 2: Replace the indexing loop in index_vault()**

Replace lines 220-244 (from `collection = get_collection()` through the end of the `with` block) with the three-phase approach:

```python
    # Phase 1: Prepare chunks in parallel (pure Python in worker threads).
    prepared: list[tuple[str, list[str], list[str], list[dict]]] = []
    indexed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as executor:
        futures = {executor.submit(_prepare_file_chunks, f): f for f in to_index}
        for future in as_completed(futures):
            md_file = futures[future]
            try:
                result = future.result()
                if result is not None:
                    prepared.append(result)
                indexed += 1
                if indexed % 100 == 0:
                    logger.info("Prepared %s/%s files...", indexed, len(to_index))
            except FileNotFoundError:
                logger.debug("File disappeared during indexing: %s", md_file)
                valid_sources.discard(str(md_file))
            except Exception:
                failed += 1
                logger.error("Failed to index %s", md_file, exc_info=True)

    logger.info("Prepared %s files (%s with chunks, %s failed)",
                indexed, len(prepared), failed)

    # Phase 2: Delete stale chunks for files that will be re-upserted.
    collection = get_collection()
    sources_to_upsert = {src for src, _, _, _ in prepared}
    for source in sources_to_upsert:
        collection.delete(where={"source": source})
    if sources_to_upsert:
        logger.info("Deleted stale chunks for %s files", len(sources_to_upsert))

    # Phase 3: Bulk upsert in batches.
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    for _, ids, documents, metadatas in prepared:
        all_ids.extend(ids)
        all_docs.extend(documents)
        all_metas.extend(metadatas)

    total_chunks = len(all_ids)
    if total_chunks > 0:
        n_batches = (total_chunks + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE
        for batch_idx in range(n_batches):
            start = batch_idx * UPSERT_BATCH_SIZE
            end = start + UPSERT_BATCH_SIZE
            logger.info("Upserting batch %s/%s (%s chunks)...",
                        batch_idx + 1, n_batches, min(UPSERT_BATCH_SIZE, total_chunks - start))
            collection.upsert(
                ids=all_ids[start:end],
                documents=all_docs[start:end],
                metadatas=all_metas[start:end],
            )
```

The rest of `index_vault()` (pruning, manifest save, mark_run) stays unchanged.

**Step 3: Run the new batch tests**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestBatchUpserts -v`

Expected: All 6 tests PASS.

**Step 4: Run all existing tests to check for regressions**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`

Expected: All tests pass. Some existing `TestParallelIndexing` tests may need minor adjustments if they assert on `mock_collection.upsert` or `mock_collection.delete` call counts — these will now reflect batch behavior. Fix any that fail.

**Step 5: Commit**

```bash
git add src/index_vault.py src/config.py
git commit -m "feat: batch ChromaDB upserts across files for faster indexing (#121)"
```

---

### Task 4: Update existing tests for new behavior

Some existing tests in `TestParallelIndexing` and `TestIndexFileBatching` may break because they assert per-file upsert/delete patterns. The `TestIndexFileBatching.test_single_upsert_call_per_file` test validates `index_file()` which is unchanged, so it should still pass. But `TestParallelIndexing` tests that check `mock_collection.upsert` or `mock_collection.delete` calls may need updating.

**Files:**
- Modify: `tests/test_chunking.py` (update assertions in `TestParallelIndexing` if needed)

**Step 1: Run full test suite and identify failures**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v 2>&1 | grep -E "FAILED|PASSED|ERROR"`

**Step 2: Fix any failing assertions**

Existing tests that patch `_prepare_file_chunks` to return `None` won't trigger upserts (no chunks), so most should be fine. Tests that return actual chunk data and assert on upsert patterns may need batch-size patches added (e.g., `patch("index_vault.UPSERT_BATCH_SIZE", 1000)`).

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: All tests pass.

**Step 4: Commit if any test changes were needed**

```bash
git add tests/test_chunking.py
git commit -m "test: update existing index tests for batch upsert behavior"
```

---

### Task 5: Final verification and cleanup

**Step 1: Run the complete test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: All tests pass with no regressions.

**Step 2: Review the diff**

Run: `git diff master --stat` and `git log --oneline master..HEAD`

Verify only the expected files changed: `src/config.py`, `src/index_vault.py`, `tests/test_chunking.py`, `docs/plans/`.
