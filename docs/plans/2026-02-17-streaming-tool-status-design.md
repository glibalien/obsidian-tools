# Streaming Tool Status Events - Design

**Issue:** #62
**Date:** 2026-02-17

## Problem

The chat plugin shows a static "Thinking..." indicator for the entire duration of an agent turn (30-60+ seconds for multi-tool queries). No feedback about what the agent is doing.

## Scope (v1)

Tool status events only — stream `tool_call` and `tool_result` events as the agent works. Final text still arrives all at once. Token-level streaming deferred to a future iteration.

## Architecture

Callback-based: `agent_turn` accepts an optional `on_event` async callback. The API server's streaming endpoint wraps this to yield SSE events. Existing `/chat` endpoint unchanged (passes no callback).

### Event Types

- `tool_call` — agent is calling a tool (`{"tool": "search_vault", "args": {"query": "..."}}`)
- `tool_result` — tool returned (`{"tool": "search_vault"}`)
- `response` — final assistant text (`{"content": "..."}`)
- `error` — something went wrong (`{"error": "..."}`)

### Data Flow

```
Plugin                    API Server                    agent_turn
  |                          |                              |
  |-- POST /chat/stream ---->|                              |
  |                          |-- agent_turn(callback=fn) -->|
  |                          |                              |-- LLM call
  |                          |                              |-- tool_call: search_vault
  |                          |<-- callback(tool_call) ------|
  |<-- SSE: tool_call -------|                              |
  |                          |                              |-- execute tool
  |                          |<-- callback(tool_result) ----|
  |<-- SSE: tool_result -----|                              |
  |                          |                              |-- LLM call (final)
  |                          |<-- callback(response) -------|
  |<-- SSE: response --------|                              |
  |<-- SSE: done ------------|                              |
```

Callback signature: `async def on_event(event_type: str, data: dict) -> None`

SSE endpoint uses `StreamingResponse` with `text/event-stream`. An `asyncio.Queue` bridges the callback to the SSE generator.

## Changes by File

### `src/agent.py`

Add optional `on_event` callback to `agent_turn`. Invoke before each tool call, after each tool result, and with the final response. No-op when None.

### `src/api_server.py`

Add `POST /chat/stream`. Same session management as `/chat`. Creates `asyncio.Queue`, defines callback that enqueues events, runs `agent_turn` in background task, returns `StreamingResponse` reading from queue. Compaction/trim after agent_turn completes.

### `plugin/src/ChatView.ts`

Replace `requestUrl` with `fetch` + `ReadableStream`. Parse SSE events. Update loading indicator on `tool_call` events, render final message on `response`.

### `plugin/styles.css`

Minor: style for tool status text in loading indicator.

## Error Handling

- `agent_turn` exceptions → `error` event on queue, then `done`
- Client disconnect → background task continues (agent_turn mutates session in place, must finish)

## Testing

- Unit test: `agent_turn` callback invoked with correct events during mocked turn
- Integration test: `/chat/stream` endpoint with `httpx` async client, verify SSE event sequence
- Existing 249 tests must pass unchanged (callback defaults to None)
