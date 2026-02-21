# Streaming Tool Status Events - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stream tool status events (tool_call, tool_result) from the API server to the Obsidian plugin during agent turns, replacing the static "Thinking..." indicator with live progress.

**Architecture:** Add an optional async callback to `agent_turn` that fires on tool calls/results. New `POST /chat/stream` SSE endpoint bridges callback events to the client via `asyncio.Queue`. Plugin switches from `requestUrl` to `fetch` + `ReadableStream` for SSE parsing.

**Tech Stack:** FastAPI `StreamingResponse`, SSE (`text/event-stream`), `asyncio.Queue`, browser `fetch` API

---

### Task 1: Add on_event callback to agent_turn

**Files:**
- Modify: `src/agent.py:188-291` (agent_turn function)
- Test: `tests/test_agent.py`

**Step 1: Write failing tests**

Add to `tests/test_agent.py`:

```python
@pytest.mark.anyio
async def test_agent_turn_on_event_tool_call():
    """on_event callback is called with tool_call events."""
    events = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.function.name = "search_vault"
    mock_tool_call.function.arguments = '{"query": "test"}'

    mock_msg_with_tool = MagicMock()
    mock_msg_with_tool.tool_calls = [mock_tool_call]
    mock_msg_with_tool.content = None
    mock_msg_with_tool.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_1", "function": {"name": "search_vault", "arguments": '{"query": "test"}'}, "type": "function"}],
    }

    mock_msg_final = MagicMock()
    mock_msg_final.tool_calls = None
    mock_msg_final.content = "Found results."
    mock_msg_final.model_dump.return_value = {"role": "assistant", "content": "Found results."}

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_with_tool)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_final)], usage=mock_usage),
    ]

    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text='{"success": true, "results": []}')]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "search"}]

    result = await agent_turn(mock_client, mock_session, messages, [], on_event=on_event)
    assert result == "Found results."

    # Should have: tool_call, tool_result, response
    event_types = [e[0] for e in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "response" in event_types

    # Verify tool_call event data
    tool_call_event = next(e for e in events if e[0] == "tool_call")
    assert tool_call_event[1]["tool"] == "search_vault"

    # Verify response event data
    response_event = next(e for e in events if e[0] == "response")
    assert response_event[1]["content"] == "Found results."


@pytest.mark.anyio
async def test_agent_turn_no_callback_unchanged():
    """agent_turn works exactly as before when no on_event is passed."""
    mock_msg_final = MagicMock()
    mock_msg_final.tool_calls = None
    mock_msg_final.content = "Hello"
    mock_msg_final.model_dump.return_value = {"role": "assistant", "content": "Hello"}

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=mock_msg_final)], usage=mock_usage,
    )

    mock_session = AsyncMock()
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "hi"}]

    result = await agent_turn(mock_client, mock_session, messages, [])
    assert result == "Hello"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_agent_turn_on_event_tool_call tests/test_agent.py::test_agent_turn_no_callback_unchanged -v`
Expected: FAIL — `on_event` is not a valid parameter

**Step 3: Implement the callback in agent_turn**

In `src/agent.py`, modify the `agent_turn` signature and add callback invocations:

```python
from typing import Callable, Awaitable

# Type alias for the event callback
EventCallback = Callable[[str, dict], Awaitable[None]]


async def agent_turn(
    client: OpenAI,
    session: ClientSession,
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = 20,
    on_event: EventCallback | None = None,
) -> str:
```

Add a helper at the top of agent_turn:

```python
    async def _emit(event_type: str, data: dict) -> None:
        if on_event is not None:
            await on_event(event_type, data)
```

Add callback invocations at these points:

1. **Before each tool execution** (inside the `for tool_call in assistant_message.tool_calls` loop, after argument parsing, before execution):
```python
            await _emit("tool_call", {"tool": tool_name, "args": arguments})
```

2. **After each tool execution** (after result is added to messages):
```python
            await _emit("tool_result", {"tool": tool_name})
```

3. **When final response is ready** (where `return assistant_message.content or ""` is, just before return):
```python
        if on_event is not None:
            await _emit("response", {"content": assistant_message.content or ""})
```

4. **When iteration cap is hit** (before the existing `return last_content + ...`):
```python
            content = last_content + "\n\n[Tool call limit reached]"
            await _emit("response", {"content": content})
            return content
```

Also add the import at the top of agent.py:
```python
from typing import Awaitable, Callable
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: ALL pass (new tests + existing tests unchanged)

**Step 5: Commit**

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: add on_event callback to agent_turn for streaming events (#62)"
```

---

### Task 2: Add POST /chat/stream SSE endpoint

**Files:**
- Modify: `src/api_server.py`
- Test: `tests/test_session_management.py`

**Step 1: Write failing test**

Add to `tests/test_session_management.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

class TestStreamEndpoint:
    """Tests for POST /chat/stream SSE endpoint."""

    def setup_method(self):
        file_sessions.clear()
        app.state.mcp_session = AsyncMock()
        app.state.llm_client = MagicMock()
        app.state.tools = []
        app.state.system_prompt = "test system prompt"

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_returns_sse_events(self, mock_agent_turn):
        """Stream endpoint returns SSE-formatted events."""
        async def fake_agent_turn(client, session, messages, tools, on_event=None):
            if on_event:
                await on_event("tool_call", {"tool": "search_vault", "args": {"query": "test"}})
                await on_event("tool_result", {"tool": "search_vault"})
                await on_event("response", {"content": "Found results."})
            return "Found results."

        mock_agent_turn.side_effect = fake_agent_turn

        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post(
                "/chat/stream",
                json={"message": "search", "active_file": "note.md"},
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            # Parse SSE events from response body
            lines = response.text.strip().split("\n")
            events = []
            for line in lines:
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            event_types = [e["type"] for e in events]
            assert "tool_call" in event_types
            assert "tool_result" in event_types
            assert "response" in event_types
            assert "done" in event_types

            # Verify response event has session_id
            response_event = next(e for e in events if e["type"] == "response")
            assert "session_id" in response_event

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_shares_sessions_with_chat(self, mock_agent_turn):
        """Stream endpoint shares the same session store as /chat."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            # Create session via /chat
            r1 = client.post("/chat", json={"message": "hi", "active_file": "shared.md"})
            sid1 = r1.json()["session_id"]

            # Continue via /chat/stream
            r2 = client.post(
                "/chat/stream",
                json={"message": "more", "active_file": "shared.md"},
            )
            # Parse session_id from SSE events
            events = []
            for line in r2.text.strip().split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            done_event = next(e for e in events if e["type"] == "done")
            assert done_event["session_id"] == sid1

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_error_sends_error_event(self, mock_agent_turn):
        """Errors during agent_turn produce an error SSE event."""
        mock_agent_turn.side_effect = Exception("LLM failed")

        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post(
                "/chat/stream",
                json={"message": "hi", "active_file": "err.md"},
            )
            assert response.status_code == 200  # SSE always returns 200

            events = []
            for line in response.text.strip().split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            event_types = [e["type"] for e in events]
            assert "error" in event_types
            assert "done" in event_types
```

Also add this import at the top of the file if not already present:
```python
from api_server import app, file_sessions, get_or_create_session, trim_messages
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestStreamEndpoint -v`
Expected: FAIL — 404 (endpoint doesn't exist)

**Step 3: Implement the streaming endpoint**

Add to `src/api_server.py`:

```python
import asyncio
import json as json_module

from fastapi.responses import StreamingResponse
```

Then add the endpoint after the existing `/chat` endpoint:

```python
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Process a chat message and stream events as SSE."""
    system_prompt = app.state.system_prompt
    preferences = load_preferences()
    if preferences:
        system_prompt += preferences

    session = get_or_create_session(request.active_file, system_prompt)
    messages = session.messages
    messages[0]["content"] = system_prompt

    compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
    for msg in messages:
        msg.pop("_compacted", None)

    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event_type: str, data: dict) -> None:
        await queue.put({"type": event_type, **data})

    def _restore_compacted_flags():
        for i in compacted_indices:
            if i < len(messages):
                messages[i]["_compacted"] = True

    async def run_agent():
        try:
            await agent_turn(
                app.state.llm_client,
                app.state.mcp_session,
                messages,
                app.state.tools,
                on_event=on_event,
            )
            _restore_compacted_flags()
            compact_tool_messages(messages)
            trim_messages(messages)
        except Exception as e:
            _restore_compacted_flags()
            messages.pop()
            await queue.put({"type": "error", "error": str(e)})
        finally:
            await queue.put({"type": "done", "session_id": session.session_id})
            await queue.put(None)  # sentinel

    async def event_generator():
        task = asyncio.create_task(run_agent())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json_module.dumps(event)}\n\n"
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Note: Import `json as json_module` to avoid conflict with the existing `json` usage in the file. Actually, check if `json` is already imported — it's not in api_server.py currently, so just `import json` is fine. Use `json.dumps` directly.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: ALL pass

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL pass

**Step 6: Commit**

```bash
git add src/api_server.py tests/test_session_management.py
git commit -m "feat: add POST /chat/stream SSE endpoint (#62)"
```

---

### Task 3: Update plugin to use streaming endpoint

**Files:**
- Modify: `plugin/src/ChatView.ts:100-143` (sendMessage method)
- Modify: `plugin/src/ChatView.ts:86-93` (showLoading method)

**Step 1: Update showLoading to return both the container and the text element**

Replace the `showLoading` method:

```typescript
private showLoading(): { container: HTMLElement; textEl: HTMLElement } {
    const loadingEl = this.messagesContainer.createDiv({
        cls: "chat-message chat-message-assistant chat-loading"
    });
    const textEl = loadingEl.createDiv({ cls: "chat-message-content", text: "Thinking..." });
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    return { container: loadingEl, textEl };
}
```

**Step 2: Replace sendMessage with SSE-based implementation**

Replace the `sendMessage` method:

```typescript
private async sendMessage(): Promise<void> {
    const message = this.inputField.value.trim();
    if (!message || this.isLoading) return;

    this.inputField.value = "";
    this.isLoading = true;
    this.sendButton.disabled = true;

    // Add user message
    await this.addMessage("user", message);

    // Show loading
    const { container: loadingEl, textEl: loadingText } = this.showLoading();

    try {
        const response = await fetch("http://127.0.0.1:8000/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: message,
                session_id: this.sessionId,
                active_file: this.getActiveFilePath()
            })
        });

        if (!response.ok || !response.body) {
            throw new Error(`Server returned ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                try {
                    const event = JSON.parse(line.slice(6));
                    switch (event.type) {
                        case "tool_call":
                            loadingText.setText(this.formatToolStatus(event.tool));
                            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
                            break;
                        case "tool_result":
                            // Could update status, but tool_call already shows what's happening
                            break;
                        case "response":
                            loadingEl.remove();
                            this.sessionId = event.session_id ?? this.sessionId;
                            await this.addMessage("assistant", event.content);
                            break;
                        case "error":
                            loadingEl.remove();
                            await this.addMessage("assistant", `Error: ${event.error}. Is the API server running?`);
                            break;
                        case "done":
                            this.sessionId = event.session_id ?? this.sessionId;
                            break;
                    }
                } catch {
                    // Skip malformed SSE lines
                }
            }
        }

        // If loading indicator is still showing (no response event received), remove it
        if (loadingEl.parentElement) {
            loadingEl.remove();
            await this.addMessage("assistant", "No response received from server.");
        }

    } catch (error) {
        if (loadingEl.parentElement) {
            loadingEl.remove();
        }
        const errorMessage = error instanceof Error ? error.message : "Failed to connect to server";
        await this.addMessage("assistant", `Error: ${errorMessage}. Is the API server running?`);
    } finally {
        this.isLoading = false;
        this.sendButton.disabled = false;
        this.inputField.focus();
    }
}
```

**Step 3: Add the formatToolStatus helper method**

Add this method to the `ChatView` class (after `getActiveFilePath`):

```typescript
private formatToolStatus(toolName: string): string {
    const labels: Record<string, string> = {
        search_vault: "Searching vault...",
        read_file: "Reading file...",
        find_backlinks: "Finding backlinks...",
        find_outlinks: "Finding outlinks...",
        search_by_folder: "Listing folder...",
        list_files_by_frontmatter: "Searching frontmatter...",
        web_search: "Searching the web...",
        create_file: "Creating file...",
        move_file: "Moving file...",
        update_frontmatter: "Updating frontmatter...",
        log_interaction: "Logging interaction...",
        transcribe_audio: "Transcribing audio...",
    };
    return labels[toolName] ?? `Running ${toolName}...`;
}
```

**Step 4: Remove the `requestUrl` import if no longer used**

Check line 1 of `ChatView.ts`. If `requestUrl` is no longer used anywhere in the file, remove it from the import:

```typescript
import { ItemView, MarkdownRenderer, WorkspaceLeaf } from "obsidian";
```

Note: This task depends on the markdown rendering changes from PR #65. If that PR hasn't been merged yet, the import will be `import { ItemView, WorkspaceLeaf } from "obsidian";` (without `MarkdownRenderer`). Adjust accordingly — just remove `requestUrl` from whatever the current import is.

**Step 5: Commit**

```bash
git add plugin/src/ChatView.ts
git commit -m "feat: plugin streams tool status events via SSE (#62)"
```

---

### Task 4: Add CSS for tool status indicator

**Files:**
- Modify: `plugin/styles.css`

**Step 1: Add tool status styles**

Add after the existing `.chat-loading .chat-message-content` rule:

```css
.chat-loading .chat-message-content {
	color: var(--text-muted);
	font-style: italic;
	transition: opacity 0.15s ease;
}
```

Replace the existing `.chat-loading .chat-message-content` block (lines 41-44) with the above (adds the transition for smooth text changes between tool statuses).

**Step 2: Commit**

```bash
git add plugin/styles.css
git commit -m "style: smooth transition for tool status text (#62)"
```

---

### Task 5: Build plugin and run full test suite

**Step 1: Build the plugin**

```bash
cd plugin && npm run build
```

Expected: Build succeeds with no errors.

**Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: ALL tests pass.

**Step 3: Commit build output (if not gitignored)**

Note: `plugin/main.js` is gitignored so no commit needed for build output.
