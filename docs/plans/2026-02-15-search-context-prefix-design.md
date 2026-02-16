# Search Context Prefix Design

**Date:** 2026-02-15
**Status:** Approved

## Problem

When searching for a specific section of a note (e.g., "phase 5 of obsidian tools"), chunks from that note may not contain the note's name in their text, causing them to rank below the top N results. The note name is only in metadata, not searchable text.

## Solution

Two complementary fixes:

### A: Prepend note name to chunk text

In `index_file()`, prepend `[Note Name]` to each chunk's document text before storing in ChromaDB. This makes every chunk from a file matchable by keyword and semantic search when the query mentions the file name.

Applied in `index_file()`, not `chunk_markdown()` — chunking stays pure, the prefix is a storage concern.

Requires `--full` reindex.

### C: System prompt guidance

Add guidance telling the agent to use `read_file` directly when a user asks about a specific section of a known note, rather than relying on search.

## File Changes

- `src/index_vault.py`: Modify `index_file()` to prepend `md_file.stem` to document text
- `tests/test_chunking.py`: Update `TestIndexFileMetadata` to verify prefix
- `system_prompt.txt.example`: Add read_file-over-search guidance
- `CLAUDE.md`: Note the prefix behavior in index_vault docs
