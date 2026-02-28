# Design: `summarize_file` MCP Tool

**Date**: 2026-02-27
**Status**: Approved

## Problem

When asked to summarize a note, the agent reads the file via `read_file`, produces a great conversational summary, but then writes a different (worse) summary when appending to the file via `edit_file`. The agent treats "explain to the user" and "write to a file" as different cognitive tasks, resulting in inconsistent quality.

## Solution

A purpose-built `summarize_file` MCP tool that:
1. Reads the file content internally (reusing `read_file` logic for embed expansion, audio transcription, image description)
2. Sends content to an LLM with a hardcoded summarization prompt
3. Appends the generated summary directly to the file
4. Returns a short confirmation to the agent

This decouples summary generation from the agent's conversational tendencies. The agent becomes a delivery mechanism ("I've added the summary to your note") rather than the generator.

## Tool Signature

```python
def summarize_file(
    path: str,
    focus: str | None = None,
) -> str:
```

**Parameters:**
- `path` — vault-relative file path (same as `read_file`)
- `focus` — optional guidance for what to emphasize (e.g. "budget discussion", "technical decisions")

**Returns:** `ok(path=..., summary_length=...)` on success, `err(...)` on failure.

## Internal Flow

1. **Read content** — call `read_file(path, offset=0, length=MAX_SUMMARIZE_CHARS)` internally. Gets full file with embeds expanded, audio transcribed, images described. Parse JSON result; bail with `err()` if failed.
2. **Call LLM** — send content to Fireworks via `OpenAI` client (same pattern as `handle_image` in `readers.py`) with hardcoded summarization prompt. `focus` param injected into prompt if provided.
3. **Append to file** — append `\n\n## Summary\n\n{llm_output}` to the file via `edit_file(path, content, position="append")` internally.
4. **Return confirmation** — `ok(path=relative_path, summary_length=len(summary_text))`

## Summarization Prompt

Hardcoded system+user prompt pair. System prompt instructs the LLM to:
- Produce a detailed, structured summary with subsections as warranted by the content
- Use tables where data is tabular
- End with `### Action Items` containing `- [ ]` checkboxes for any tasks, follow-ups, or commitments identified
- Omit the Action Items section if none exist
- Use markdown formatting appropriate for Obsidian

User message contains the file content, prefixed with the `focus` instruction if provided.

## Configuration

- `SUMMARIZE_MODEL` env var, defaulting to `FIREWORKS_MODEL` — separate knob if needed later, defaults to same model
- `MAX_SUMMARIZE_CHARS` constant in `config.py` — safety cap for content sent to the LLM (e.g. 200,000 chars). Files exceeding this get truncated with a note.

## Module Structure

New file: `src/tools/summary.py`. Cross-cutting feature (reads + LLM call + writes) that doesn't fit cleanly into `files.py` or `editing.py`.

## Compaction Stub

Register `summarize_file` in `_TOOL_STUB_BUILDERS` in `services/compaction.py`. Stub keeps `path` and `summary_length`, drops everything else (summary lives in the file, not conversation history).

## System Prompt Update

Add `summarize_file` to the tool reference table and decision tree in `system_prompt.txt.example`. Guide the agent: "When asked to summarize a note, use `summarize_file`. Do not summarize manually via `read_file` + `edit_file`."

## Testing

New file: `tests/test_tools_summary.py`.

Key test cases:
- **Happy path**: mock LLM response, verify summary appended to file with correct format
- **With focus param**: verify focus text appears in the prompt sent to the LLM
- **read_file failure**: file doesn't exist → returns `err()`
- **LLM failure**: API error → returns `err()`, file unchanged
- **Large content truncation**: content exceeding `MAX_SUMMARIZE_CHARS` is truncated with a note
- **Non-markdown file**: summarizing audio/office docs (read_file handles conversion)
- **File already has summary**: appending a second summary doesn't corrupt existing content

All LLM calls mocked via patching the OpenAI client. Tests use `temp_vault` + `vault_config` fixtures.

## Scope Exclusions

- No streaming — LLM call is synchronous, result appended atomically
- No interactive preview — summary appended directly, user reviews in file
- No undo — user removes section manually or via `edit_file`
- No multi-file summarization — one file per call
