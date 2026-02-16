# Design: Generic Tool Result Continuation

## Problem

Tool results exceeding `MAX_TOOL_RESULT_CHARS` (4000) are hard-truncated with no way for the agent to retrieve the remainder. This causes data loss for large results like audio transcripts (e.g., a 24,591-char transcript truncated to 16% of its content, producing an incomplete summary).

## Solution

Add a generic continuation mechanism to `agent_turn` in `agent.py`. When any tool result is truncated, the full result is cached and a synthetic `get_continuation` tool lets the agent retrieve subsequent chunks on demand.

## Scope

- **agent.py only** — no MCP, API server, or tool implementation changes
- Works in both CLI (`agent.py`) and HTTP (`api_server.py`) paths since both call `agent_turn`

## Design

### 1. Enhanced truncation marker

`truncate_tool_result` gains a `tool_call_id` parameter. Truncated results end with:

```
[truncated — showing 4000/24591 chars. Call get_continuation with tool_call_id="call_abc123" to read more]
```

### 2. Result cache

A `dict[str, str]` scoped to each `agent_turn` invocation:

```python
truncated_results: dict[str, str] = {}  # tool_call_id -> full result
```

Stored when truncation occurs. Freed automatically when the turn ends (goes out of scope).

### 3. Synthetic `get_continuation` tool

Injected into the tools list at the start of `agent_turn`:

```json
{
    "type": "function",
    "function": {
        "name": "get_continuation",
        "description": "Retrieve the next chunk of a truncated tool result. Use when a previous tool result shows [truncated].",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_call_id": {
                    "type": "string",
                    "description": "The tool_call_id from the truncation message"
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to read from (default: 4000)"
                }
            },
            "required": ["tool_call_id"]
        }
    }
}
```

### 4. Local handler in tool call loop

Before calling `execute_tool_call`, check if the tool is `get_continuation`. If so, serve the chunk directly from the cache — no MCP round-trip:

- Look up `tool_call_id` in `truncated_results`
- Return `full_result[offset:offset + MAX_TOOL_RESULT_CHARS]`
- If more remains, append the same style truncation marker with updated offset info
- If ID not found, return an error JSON

### 5. Iteration counting

Add `get_continuation` to `UNCOUNTED_TOOLS` so continuation calls don't count toward the 20-iteration agent loop cap.

## Data flow

```
User asks to summarize meeting
  -> agent calls transcribe_audio
  -> MCP returns 24,591 chars
  -> agent_turn caches full result, truncates to 4000 chars
  -> marker: [truncated - showing 4000/24591 chars. Call get_continuation...]
  -> LLM sees partial transcript, calls get_continuation(offset=4000)
  -> agent_turn serves chars 4000-8000 from cache
  -> LLM calls get_continuation(offset=8000) ... repeats as needed
  -> LLM has enough context, generates summary
```

## What doesn't change

- `api_server.py` — calls `agent_turn` the same way
- `mcp_server.py` — no new MCP tools
- `services/compaction.py` — continuation tool results are regular tool messages, compacted normally
- All existing tool implementations — unchanged

## Testing (in test_agent.py)

- `truncate_tool_result` with new marker format (tool_call_id embedded, char counts)
- `get_continuation` handler: valid ID, invalid ID, multiple chunks, final chunk without marker
- Integration: mock a tool returning >4000 chars, verify full result recoverable across chunks
