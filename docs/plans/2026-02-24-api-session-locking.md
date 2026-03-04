# API Session Locking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent race conditions when two requests arrive concurrently for the same session by serializing them with a per-session `asyncio.Lock`.

**Architecture:** Add `lock: asyncio.Lock` to the `Session` dataclass. Split `_prepare_turn` into `_build_system_prompt` (outside lock) and `_setup_turn` (inside lock). Both `/chat` and `/chat/stream` acquire `session.lock` before mutating messages or calling `agent_turn`. Requests for different sessions are unaffected.

**Tech Stack:** Python asyncio, FastAPI, httpx (already installed), pytest

---

### Task 1: Write failing concurrency tests

**Files:**
- Modify: `tests/test_session_management.py`

**Step 1: Add imports**

At the top of `tests/test_session_management.py`, add `asyncio` and `httpx` to the imports:

```python
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient
```

**Step 2: Add concurrency tests**

Append to `tests/test_session_management.py`:

```python
class TestConcurrentRequests:
    """Tests for per-session locking under concurrent requests."""

    def test_same_session_requests_are_serialized(self, mock_app, clear_sessions):
        """Two concurrent requests for the same session must not interleave."""
        call_log = []

        async def mock_agent_turn(*args, **kwargs):
            call_log.append("enter")
            await asyncio.sleep(0.05)
            call_log.append("exit")
            return "response"

        async def run():
            with patch("api_server.agent_turn", side_effect=mock_agent_turn), \
                 patch("api_server.ensure_interaction_logged", new_callable=AsyncMock):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await asyncio.gather(
                        client.post("/chat", json={"message": "first", "active_file": "test.md"}),
                        client.post("/chat", json={"message": "second", "active_file": "test.md"}),
                    )

        asyncio.run(run())
        # With serialization: enter, exit, enter, exit (never overlapping)
        # Without it: enter, enter, exit, exit
        assert call_log == ["enter", "exit", "enter", "exit"]

    def test_different_session_requests_run_in_parallel(self, mock_app, clear_sessions):
        """Concurrent requests for different sessions must not block each other."""
        counter = [0, 0]  # [currently_active, max_active]

        async def mock_agent_turn(*args, **kwargs):
            counter[0] += 1
            counter[1] = max(counter[1], counter[0])
            await asyncio.sleep(0.05)
            counter[0] -= 1
            return "response"

        async def run():
            with patch("api_server.agent_turn", side_effect=mock_agent_turn), \
                 patch("api_server.ensure_interaction_logged", new_callable=AsyncMock):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await asyncio.gather(
                        client.post("/chat", json={"message": "msg_a", "active_file": "a.md"}),
                        client.post("/chat", json={"message": "msg_b", "active_file": "b.md"}),
                    )

        asyncio.run(run())
        # Both agent_turn calls should have been active at the same time
        assert counter[1] == 2
```

**Step 3: Run tests to confirm they fail**

```bash
.venv/bin/python -m pytest tests/test_session_management.py::TestConcurrentRequests -v
```

Expected: `test_same_session_requests_are_serialized` FAILS (call_log shows interleaving). `test_different_session_requests_run_in_parallel` may pass or fail depending on event loop behaviour — that's fine.

**Step 4: Commit the failing tests**

```bash
git add tests/test_session_management.py
git commit -m "test: add failing concurrency tests for session locking (#37)"
```

---

### Task 2: Implement per-session locking

**Files:**
- Modify: `src/api_server.py`

**Step 1: Add lock field to Session**

In `src/api_server.py`, update the `Session` dataclass:

```python
@dataclass
class Session:
    """A chat session tied to an active file."""

    session_id: str
    active_file: str | None
    messages: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

**Step 2: Replace `_prepare_turn` with two helpers**

Remove `_prepare_turn` and replace with `_build_system_prompt` and `_setup_turn`:

```python
def _build_system_prompt() -> str:
    """Build system prompt with current user preferences appended."""
    system_prompt = app.state.system_prompt
    preferences = load_preferences()
    if preferences:
        system_prompt += preferences
    return system_prompt


def _setup_turn(session: Session, request: ChatRequest, system_prompt: str) -> set[int]:
    """Prepare turn messages. Must be called with session.lock held."""
    messages = session.messages
    messages[0]["content"] = system_prompt

    compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
    for msg in messages:
        msg.pop("_compacted", None)

    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    return compacted_indices
```

**Step 3: Update `/chat` handler**

Replace the `chat` handler with:

```python
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat message and return the agent's response."""
    system_prompt = _build_system_prompt()
    session = get_or_create_session(request.active_file, system_prompt)
    messages = session.messages

    async with session.lock:
        compacted_indices = _setup_turn(session, request, system_prompt)
        turn_start = len(messages) - 1
        try:
            response = await agent_turn(
                app.state.llm_client,
                app.state.mcp_session,
                messages,
                app.state.tools,
            )
            await ensure_interaction_logged(
                app.state.mcp_session, messages, turn_start, request.message, response,
            )
            _restore_compacted_flags(messages, compacted_indices)
            compact_tool_messages(messages)
            trim_messages(messages)
            return ChatResponse(response=response, session_id=session.session_id)
        except Exception as e:
            _restore_compacted_flags(messages, compacted_indices)
            messages.pop()
            raise HTTPException(status_code=500, detail=str(e))
```

**Step 4: Update `/chat/stream` handler**

Replace the `chat_stream` handler with:

```python
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Process a chat message and stream events as SSE."""
    system_prompt = _build_system_prompt()
    session = get_or_create_session(request.active_file, system_prompt)
    messages = session.messages

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event_type: str, data: dict) -> None:
        await queue.put({"type": event_type, **data})

    async def run_agent():
        try:
            async with session.lock:
                compacted_indices = _setup_turn(session, request, system_prompt)
                turn_start = len(messages) - 1
                try:
                    response = await agent_turn(
                        app.state.llm_client,
                        app.state.mcp_session,
                        messages,
                        app.state.tools,
                        on_event=on_event,
                    )
                    await ensure_interaction_logged(
                        app.state.mcp_session, messages, turn_start,
                        request.message, response,
                    )
                    _restore_compacted_flags(messages, compacted_indices)
                    compact_tool_messages(messages)
                    trim_messages(messages)
                except Exception as e:
                    _restore_compacted_flags(messages, compacted_indices)
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
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**Step 5: Run the new concurrency tests**

```bash
.venv/bin/python -m pytest tests/test_session_management.py::TestConcurrentRequests -v
```

Expected: Both PASS.

**Step 6: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -v
```

Expected: All pass. If any existing `test_session_management.py` tests import `_prepare_turn` directly, update them to use `_build_system_prompt`/`_setup_turn`.

**Step 7: Commit**

```bash
git add src/api_server.py
git commit -m "fix: add per-session asyncio.Lock to prevent concurrent request races (#37)"
```

---

### Task 3: Open PR

```bash
gh pr create --title "fix: per-session locking for concurrent requests (#37)" --body "$(cat <<'EOF'
## Summary
- Adds `lock: asyncio.Lock` to the `Session` dataclass
- Splits `_prepare_turn` into `_build_system_prompt` + `_setup_turn` so lock acquisition wraps only the session-mutating work
- Both `/chat` and `/chat/stream` acquire the session lock before touching `messages` or calling `agent_turn`
- Requests for different sessions are unaffected (no unnecessary serialization)

## Test plan
- [ ] `TestConcurrentRequests::test_same_session_requests_are_serialized` — verifies no message interleaving
- [ ] `TestConcurrentRequests::test_different_session_requests_run_in_parallel` — verifies different sessions don't block each other
- [ ] Full test suite passes

Closes #37
EOF
)"
```
