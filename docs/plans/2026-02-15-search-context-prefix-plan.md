# Search Context Prefix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve search ranking by prepending the note name to each chunk's indexed text, and guide the agent to use `read_file` for known-file queries.

**Architecture:** One-line change in `index_file()` to prefix chunk text with `[Note Name]`. System prompt addition for agent behavior guidance. No new dependencies.

**Tech Stack:** Python stdlib only.

---

### Task 1: Add note name prefix to indexed chunks and update tests

**Files:**
- Modify: `src/index_vault.py:271` (documents line in `index_file`)
- Modify: `tests/test_chunking.py:220-242` (TestIndexFileMetadata)

**Step 1: Update the test to verify note name prefix**

In `tests/test_chunking.py`, replace the `TestIndexFileMetadata` class with:

```python
class TestIndexFileMetadata:
    """Tests for enriched metadata in index_file."""

    @patch("index_vault.get_collection")
    def test_index_file_stores_heading_and_chunk_type(self, mock_get_collection, tmp_path):
        md_file = tmp_path / "test.md"
        md_file.write_text("---\ntags: test\n---\n\n# Title\n\nContent.\n\n## Section\n\nMore.")

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        from index_vault import index_file
        index_file(md_file)

        # Verify upsert was called with heading and chunk_type in metadata
        assert mock_collection.upsert.called
        for call in mock_collection.upsert.call_args_list:
            metadata = call[1]["metadatas"][0]
            assert "heading" in metadata
            assert "chunk_type" in metadata
            assert "source" in metadata
            assert "chunk" in metadata

    @patch("index_vault.get_collection")
    def test_index_file_prepends_note_name(self, mock_get_collection, tmp_path):
        md_file = tmp_path / "Obsidian Tools.md"
        md_file.write_text("# Title\n\nContent.")

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        from index_vault import index_file
        index_file(md_file)

        assert mock_collection.upsert.called
        for call in mock_collection.upsert.call_args_list:
            doc_text = call[1]["documents"][0]
            assert doc_text.startswith("[Obsidian Tools] ")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexFileMetadata::test_index_file_prepends_note_name -v`
Expected: FAIL — document text doesn't start with `[Obsidian Tools]`.

**Step 3: Update `index_file` in `src/index_vault.py`**

Change line 271 from:

```python
            documents=[chunk["text"]],
```

to:

```python
            documents=[f"[{md_file.stem}] {chunk['text']}"],
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: All tests pass.

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass, no regressions.

**Step 5: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: prepend note name to indexed chunk text for better search ranking"
```

---

### Task 2: Update system prompt and CLAUDE.md

**Files:**
- Modify: `system_prompt.txt.example` (Vault Navigation Strategy section, after the "answer directly from search results" paragraph)
- Modify: `CLAUDE.md` (index_vault.py component description)

**Step 1: Add read_file guidance to system prompt**

In `system_prompt.txt.example`, after the paragraph ending "Only use read_file when you need additional context beyond what search returned." (line 60), add:

```
When a user asks about a specific section of a known note (e.g. "tell me about
phase 5 of the obsidian tools project"), use read_file on that note directly
rather than search_vault. Search is best for discovery; read_file is best when
you already know which file contains the answer.
```

**Step 2: Update CLAUDE.md index_vault description**

In `CLAUDE.md`, find the `index_vault.py` component description and add mention of the note name prefix. Update it to:

```
- **index_vault.py**: Indexes vault content into ChromaDB using structure-aware chunking (splits by headings, paragraphs, sentences). Each chunk carries `heading` and `chunk_type` metadata and is prefixed with `[Note Name]` for search ranking. Runs via systemd, not manually. Use `--full` flag to force full reindex.
```

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: add read_file guidance and document note name prefix"
```

---

### Task 3: Final verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.
