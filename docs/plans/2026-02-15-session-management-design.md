# Hybrid Session Management Design

## Problem

Unbounded session history causes token explosion in the API server. Every tool result (search results, file contents) accumulates in the message list, growing the prompt size with each request. There is also no agent loop safeguard or tool result size cap.

## Solution

File-keyed session management with tool compaction, agent loop cap, and tool result truncation.

## Session Model

```python
@dataclass
class Session:
    session_id: str
    active_file: str | None
    messages: list[dict]
```

Storage: `file_sessions: dict[str | None, Session]` keyed by `active_file` (or `None` for no-file). The old UUID-based `sessions` dict is replaced. The `session_id` in responses remains a UUID for plugin compatibility, but routing is by file.

## Request Flow

```
POST /chat {message, session_id, active_file}
  |
  +- Lookup file_sessions[active_file]
  |   +- Found -> continue session
  |   +- Not found -> create new session (system prompt + user msg)
  |
  +- Append user message (with context prefix)
  +- Call agent_turn(messages, max_iterations=10)
  |   +- Each tool result truncated to MAX_TOOL_RESULT_CHARS (4000)
  |   +- If max iterations hit -> return partial + warning
  +- Compact tool messages in history
  +- Return response + session_id
```

## Tool Compaction

After `agent_turn` returns, walk messages and replace `role: "tool"` entries with compact stubs. Stubs contain:

- Tool name (parsed from the corresponding assistant tool_call)
- Success/failure status
- File paths from results
- Result count
- Error message if failed

Non-JSON results keep first ~200 chars as summary. A `_compacted` flag prevents re-processing. The `_compacted` key is stripped before sending to the LLM since it's internal bookkeeping.

Example stub:
```json
{"tool": "search_vault", "status": "success", "summary": "5 results", "files": ["Notes/foo.md", "Notes/bar.md"]}
```

## Agent Loop Cap

`agent_turn` gains a `max_iterations` parameter (default 10). If the loop hits the cap, it returns the last assistant content (if any) appended with `"\n\n[Tool call limit reached]"`.

## Tool Result Truncation

Before appending a tool result to the message list, truncate at `MAX_TOOL_RESULT_CHARS` (4000) with `"\n\n[truncated]"` marker.

## Plugin Changes

None required. The plugin already sends `active_file` (or `null`) on every request and updates `sessionId` from responses.

## Files Modified

| File | Changes |
|------|---------|
| `src/api_server.py` | Session dataclass, file-keyed routing, tool compaction, config constants |
| `src/agent.py` | `max_iterations` param, tool result truncation |
| `tests/test_session_management.py` | New test file |

## Testing

- Same file continues session
- Different file starts new session
- Switching back to previous file resumes it
- Tool compaction produces valid stubs
- Agent loop cap returns partial response
- Tool result truncation at boundary
- Compacted messages have `_compacted` stripped before LLM calls
