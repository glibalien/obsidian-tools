# Design: Heading Hierarchy + Chunk Overlap (#153 + #162)

## Overview

Two search quality improvements batched together (both require `--full` reindex):
1. **#153**: Add heading hierarchy to chunk prefixes for richer embedding context
2. **#162**: Add sentence-level overlap at chunk boundaries to preserve continuity

## 1. Heading Hierarchy (#153)

### `_split_by_headings` changes

- Parse heading level from `#` count
- Maintain a stack of `(level, clean_name)` tuples
- When heading level N appears: pop stack entries with level >= N, push new
- Return `(heading, heading_chain, content)` where `heading_chain` is `list[str]` of clean names (no `#` markers)

### Chunk dict flow

- All chunk dicts get a new `heading_chain` key (list of strings)
- Flows through `_chunk_text_block` -> `_chunk_sentences` -> `chunk_markdown`
- **Not stored in ChromaDB metadata** (metadata only supports scalars) -- used solely for prefix construction in `_prepare_file_chunks`

### Prefix construction in `_prepare_file_chunks`

```python
chain = [md_file.stem] + chunk["heading_chain"]
prefix = " > ".join(chain)
documents.append(f"[{prefix}] {chunk['text']}")
```

### Examples

| Context | Prefix |
|---------|--------|
| Frontmatter | `[My Note]` |
| Before first heading | `[My Note]` |
| Under `## Architecture` | `[My Note > Architecture]` |
| Under `## Architecture` then `### Database` | `[My Note > Architecture > Database]` |
| `## API` after `### Database` | `[My Note > API]` (stack pops Database + Architecture) |

## 2. Chunk Overlap (#162)

### Sentence-level overlap in `_chunk_sentences`

- Refactor to track sentences in a list instead of a concatenated string
- When flushing: carry forward the last 2 sentences as the start of the next buffer
- First chunk has no carry-forward
- Fragments from `_fixed_chunk_text` fallback keep their own 50-char overlap -- no double-overlap

### Cross-section overlap in `chunk_markdown`

- After chunking each section, extract the last 2 sentences from the final chunk's text
- Prepend to the next section's first chunk text (newline separator)
- Skip when previous section is frontmatter or there is no previous section
- Allow slight oversize from overlap (~50-200 chars on a 1500 limit)

### Not covered (by design)

- Paragraph-accumulated chunks within a section share the same heading context
- User chose sentence + cross-section overlap, not all-levels

### Constant

- `OVERLAP_SENTENCES = 2` in `chunking.py`

## 3. Testing

- Heading chain construction from `_split_by_headings` (flat, nested, level resets)
- Prefix format in indexed documents via `_prepare_file_chunks`
- Sentence overlap between consecutive chunks from `_chunk_sentences`
- Cross-section overlap at heading boundaries
- No overlap on first chunk or after frontmatter
- Edge cases: single-chunk sections, deeply nested headings, empty sections

## 4. Deployment

- Requires `--full` reindex
- No new dependencies
- No new config env vars
