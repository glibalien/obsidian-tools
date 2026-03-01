# Design: research_note MCP Tool

## Overview

An agentic research tool that reads a vault note, autonomously extracts key topics, researches them via web search and vault discovery, and appends a `## Research` section with synthesized findings. Runs its own inner LLM pipeline (no outer agent involvement beyond calling the tool).

## Tool Interface

```python
research_note(
    path: str,                # Path to the note to research
    depth: str = "shallow",   # "shallow" (snippets only) or "deep" (fetch + read web pages)
    focus: str | None = None  # Optional focus area to narrow topic extraction
)
```

- Path resolved via `resolve_file()`
- Text-safe allowlist: `.md`, `.txt`, `.markdown` only
- Returns `ok(path=..., topics_researched=N, preview=...)` — preview is first 500 chars of appended section
- If `## Research` already exists, replaces it (re-runnable)

## Architecture: 3-Stage Pipeline

### Stage 1 — Topic Extraction

- Read note content via `read_file` Python function (gets embed expansion)
- Send to LLM with extraction prompt; `focus` narrows scope if provided
- LLM returns structured JSON:

```json
[
  {"topic": "short label", "context": "what the note says", "type": "claim|concept|question"}
]
```

- Cap: 10 topics max
- Note content capped at `MAX_SUMMARIZE_CHARS` (200K)

### Stage 2 — Research Gathering

For each topic, run concurrently:

- **Web**: `web_search(topic)` via existing DuckDuckGo function
- **Vault**: `find_notes(query=topic, mode="hybrid", n_results=5)` via search infrastructure

In **deep** mode, additionally:

- Fetch top 2 web result URLs via `httpx` (10s timeout per page)
- Convert HTML to markdown via `html2text`
- Cap at 50K chars per page
- Send each page to LLM: "Extract information relevant to: {topic}"

All topic research runs concurrently via `ThreadPoolExecutor`.

### Stage 3 — Synthesis

Send all gathered material to a final LLM call with synthesis prompt:

- Organize findings by topic
- Cite sources: web URLs as markdown links, vault notes as `[[wikilinks]]`
- Flag contradictions between the note and external sources
- Highlight related vault content the user may not know about
- Keep concise — research supplement, not a thesis

Output appended/replaced as `## Research` via `edit_file(position="section", heading="Research", mode="replace")`.

## LLM Integration

- Own `openai.OpenAI` client pointed at `FIREWORKS_BASE_URL`
- New config constant: `RESEARCH_MODEL` (defaults to `FIREWORKS_MODEL`)
- Call count: 3 (shallow) to 3+N (deep, where N = topics with fetchable pages)
- Worst case ~11 calls for 8 topics in deep mode

## Resource Limits

| Resource | Limit |
|----------|-------|
| Topics | 10 max |
| URLs fetched per topic (deep) | 2 |
| Page content per URL | 50K chars |
| Note content to LLM | 200K chars (MAX_SUMMARIZE_CHARS) |
| Page fetch timeout | 10 seconds |

## Error Handling

- Stage fails entirely → return `err()` with description
- Individual topic/source fails → skip and continue with remaining material
- Page fetch 403/404/timeout → silently skip, topic still has snippets + vault results
- LLM returns malformed JSON in Stage 1 → retry once, then `err()`

## Dependencies

- `html2text` — new dependency for HTML-to-markdown conversion (deep mode)
- `httpx` — already available (transitive via FastAPI)

## System Integration

- **Module**: `src/tools/research.py`
- **MCP registration**: Added to `mcp_server.py`
- **Compaction stub**: `path` + `topics_researched`, drops `preview`
- **System prompt**: Add to tool reference and decision tree
- **Logging**: `logging.getLogger(__name__)`, INFO for LLM/search calls, DEBUG for intermediates, WARNING for failures
- **Timeout**: Bump `execute_tool_call` timeout for `research_note` (runs longer than 60s in deep mode)

## Testing

New file: `tests/test_tools_research.py`. Mock Fireworks client and search functions.

Test cases:
- Topic extraction prompt construction (with and without focus)
- Search dispatch (web + vault per topic)
- Deep mode page fetching and extraction
- Synthesis prompt construction with all gathered material
- Output written via edit_file
- Error degradation (failed topics/sources skipped gracefully)
- Replacement of existing `## Research` section
- Text-safe allowlist enforcement (rejects binary files)
- Topic cap enforcement (>10 topics truncated)
