# Chunked File Reading Design

**Date:** 2026-02-15
**Status:** Approved

## Problem

`read_file` returns full file content, which gets truncated to 4,000 chars by the agent's tool result truncation. For long notes (10K+ chars), the agent can't access anything past the first ~4,000 characters and doesn't know content is missing.

## Solution

Add `offset` and `length` parameters to `read_file` so the agent can page through long files. Truncation markers tell the agent when more content is available and what offset to use.

## Design

### `read_file(path, offset=0, length=4000)`

- If entire file fits within `length` and `offset` is 0: return as-is (current behavior)
- Slice `content[offset:offset + length]`
- If `offset > 0`: prepend `[Continuing from char {offset} of {total}]\n\n`
- If `offset + length < total`: append `\n\n[... truncated at char {offset + length} of {total}. Use offset={offset + length} to read more.]`
- If `offset >= total`: return error message

Returns plain text (not JSON envelope). Markers are inline text the agent reads naturally.

### File Changes

- `src/tools/files.py`: Add parameters and chunking logic to `read_file()`
- `tests/test_tools_files.py`: Add tests for chunking, markers, pagination, edge cases
- `system_prompt.txt.example`: Update `read_file` description
- `CLAUDE.md`: Update `read_file` tool documentation
