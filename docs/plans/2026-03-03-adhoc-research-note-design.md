# Ad-hoc Topic Research for research_note

**Date:** 2026-03-03
**Issue:** #150

## Problem

`research_note` requires an existing vault note — there's no way to research a topic from scratch without first creating a stub note.

## Design

### Approach: Single entry point

Add `topic: str | None` param to `research_note`, mutually exclusive with `path`.

### Parameters

- `path` provided → existing note-based flow (unchanged)
- `topic` provided → ad-hoc flow (new)
- Both or neither → error
- `depth` and `focus` work identically in both modes

### Ad-hoc flow

1. **Extract sub-topics**: Pass topic string to `_extract_topics` as content — LLM breaks it into sub-topics
2. **Gather research**: `_gather_research` runs as today
3. **Synthesize**: `_synthesize_research` with topic string as "note content" context
4. **Generate title**: New `_generate_title(client, topic, synthesis)` — LLM call for a clean note title. Fallback: title-cased topic string if LLM fails
5. **Sanitize filename**: Strip filesystem-unsafe chars, append `.md`
6. **Create file**: `create_file(path="{title}.md", content=synthesis)` in vault root. Error if file exists.

### Return value

Same shape: `ok(path=..., topics_researched=N, preview=...)`

### Unchanged

- Entire note-based flow (`path` param)
- `_gather_research`, `_research_topic`, `_synthesize_research`, SSRF infra
- `_extract_topics` (called with topic string instead of file content)
- Compaction stub (works for both modes)
- MCP tool count stays at 18

### Decisions

- Output location: vault root (no configurable RESEARCH_DIR)
- Sub-topics: run `_extract_topics` on the topic string to break into sub-topics
- Filename: LLM-generated title with fallback to title-cased topic string
