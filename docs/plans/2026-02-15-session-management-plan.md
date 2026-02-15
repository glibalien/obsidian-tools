# Hybrid Session Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate token explosion from unbounded session history by adding file-keyed sessions, tool compaction, agent loop caps, and tool result truncation.

**Architecture:** The API server routes sessions by `active_file` instead of UUID. After each agent turn, tool messages are compacted to lightweight stubs. The agent loop has an iteration cap and tool results are truncated before entering history.

**Tech Stack:** Python, FastAPI, pytest, dataclasses

---

### Task 0: Create GitHub issue and feature branch

**Step 1: Create GitHub issue**

```bash
gh issue create \
  --title "Hybrid session management to prevent token explosion" \
  --body "## Description
Unbounded session history causes token explosion. Tool results accumulate in message lists, growing prompt size with each request.

## Implementation Approach
- File-keyed sessions: route by active_file, one session per file, resume on switch-back
- Tool compaction: replace tool results with compact stubs after each turn
- Agent loop cap: max 10 iterations with graceful degradation
- Tool result truncation: 4000 char limit with [truncated] marker

## Success Criteria
- [ ] Same active_file continues existing session
- [ ] Different active_file starts/resumes separate session
- [ ] Tool messages compacted to stubs after agent_turn
- [ ] Agent loop stops at 10 iterations with warning
- [ ] Tool results truncated at 4000 chars
- [ ] All existing tests still pass
- [ ] No plugin changes needed

## Testing
- Unit tests for truncation, compaction, routing
- Integration tests for /chat endpoint session behavior"
```

**Step 2: Create feature branch**

```bash
git checkout -b feature/session-management
```

---

### Task 1: Agent loop cap and tool result truncation in `agent.py`

**Files:**
- Modify: `src/agent.py:132-199` (agent_turn and tool execution)
- Create: `tests/test_agent.py`

**Step 1: Write failing tests**

Create `tests/test_agent.py`:

```python
"""Tests for agent turn behavior: iteration cap and tool result truncation."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent import agent_turn, truncate_tool_result, MAX_TOOL_RESULT_CHARS


def test_truncate_tool_result_short():
    """Short results are returned unchanged."""
    result = "short result"
    assert truncate_tool_result(result) == "short result"


def test_truncate_tool_result_exact_limit():
    """Result exactly at limit is not truncated."""
    result = "x" * MAX_TOOL_RESULT_CHARS
    assert truncate_tool_result(result) == result


def test_truncate_tool_result_over_limit():
    """Result over limit is truncated with marker."""
    result = "x" * (MAX_TOOL_RESULT_CHARS + 100)
    truncated = truncate_tool_result(result)
    assert len(truncated) == MAX_TOOL_RESULT_CHARS + len("\n\n[truncated]")
    assert truncated.endswith("\n\n[truncated]")
    assert truncated.startswith("x" * MAX_TOOL_RESULT_CHARS)


@pytest.mark.anyio
async def test_agent_turn_max_iterations():
    """Agent turn stops after max_iterations and returns warning."""
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.function.name = "search_vault"
    mock_tool_call.function.arguments = '{"query": "test"}'

    mock_message = MagicMock()
    mock_message.tool_calls = [mock_tool_call]
    mock_message.content = "Searching..."
    mock_message.model_dump.return_value = {
        "role": "assistant",
        "content": "Searching...",
        "tool_calls": [{"id": "call_1", "function": {"name": "search_vault", "arguments": '{"query": "test"}'}, "type": "function"}],
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text='{"success": true, "results": []}')]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "hi"}]

    result = await agent_turn(mock_client, mock_session, messages, [], max_iterations=2)
    assert "[Tool call limit reached]" in result


@pytest.mark.anyio
async def test_agent_turn_tool_result_truncated():
    """Tool results exceeding MAX_TOOL_RESULT_CHARS are truncated in messages."""
    big_result = "x" * (MAX_TOOL_RESULT_CHARS + 500)

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.function.name = "read_file"
    mock_tool_call.function.arguments = '{"path": "note.md"}'

    mock_msg_with_tool = MagicMock()
    mock_msg_with_tool.tool_calls = [mock_tool_call]
    mock_msg_with_tool.content = None
    mock_msg_with_tool.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "note.md"}'}, "type": "function"}],
    }

    mock_msg_final = MagicMock()
    mock_msg_final.tool_calls = None
    mock_msg_final.content = "Done"
    mock_msg_final.model_dump.return_value = {"role": "assistant", "content": "Done"}

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
        isError=False, content=[MagicMock(text=big_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "read it"}]

    await agent_turn(mock_client, mock_session, messages, [])

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"].endswith("\n\n[truncated]")
    assert len(tool_msgs[0]["content"]) < len(big_result)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: ImportError for `truncate_tool_result` and `MAX_TOOL_RESULT_CHARS`

**Step 3: Implement truncation and iteration cap**

In `src/agent.py`, add constant and function after existing constants (after line 27):

```python
MAX_TOOL_RESULT_CHARS = 4000


def truncate_tool_result(result: str) -> str:
    """Truncate tool result if it exceeds the character limit."""
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result
    return result[:MAX_TOOL_RESULT_CHARS] + "\n\n[truncated]"
```

Replace `agent_turn` function (lines 132-199) with:

```python
async def agent_turn(
    client: OpenAI,
    session: ClientSession,
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = 10,
) -> str:
    """Execute one agent turn, handling tool calls until final response."""
    turn_prompt_tokens = 0
    turn_completion_tokens = 0
    llm_calls = 0
    last_content = ""

    while True:
        if llm_calls >= max_iterations:
            logger.warning("Agent hit max iterations (%d)", max_iterations)
            return (last_content or "") + "\n\n[Tool call limit reached]"

        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
        )

        llm_calls += 1
        usage = response.usage
        if usage:
            turn_prompt_tokens += usage.prompt_tokens
            turn_completion_tokens += usage.completion_tokens
            logger.info(
                "LLM call %d: prompt=%d completion=%d total=%d messages=%d",
                llm_calls,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                len(messages),
            )

        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))

        last_content = assistant_message.content or ""

        if not assistant_message.tool_calls:
            logger.info(
                "Turn complete: calls=%d prompt_total=%d completion_total=%d "
                "turn_total=%d",
                llm_calls,
                turn_prompt_tokens,
                turn_completion_tokens,
                turn_prompt_tokens + turn_completion_tokens,
            )
            return last_content

        # Execute each tool call
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            print(f"  [Calling {tool_name}...]")
            result = await execute_tool_call(session, tool_name, arguments)
            result = truncate_tool_result(result)
            logger.debug(
                "Tool %s result: %d chars", tool_name, len(result)
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add tests/test_agent.py src/agent.py
git commit -m "feat: add agent loop cap and tool result truncation"
```

---

### Task 2: Tool compaction utility in `api_server.py`

**Files:**
- Modify: `src/api_server.py` (add compaction functions)
- Create: `tests/test_session_management.py`

**Step 1: Write failing tests**

Create `tests/test_session_management.py`:

```python
"""Tests for session management: tool compaction, file-keyed routing."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import build_tool_stub, compact_tool_messages


class TestBuildToolStub:
    """Tests for build_tool_stub."""

    def test_search_vault_success(self):
        """Search results produce stub with file list and count."""
        content = json.dumps({
            "success": True,
            "results": [
                {"source": "Notes/foo.md", "content": "long content here..."},
                {"source": "Notes/bar.md", "content": "more content..."},
            ],
        })
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 2
        assert "Notes/foo.md" in parsed["files"]
        assert "Notes/bar.md" in parsed["files"]

    def test_error_response(self):
        """Error responses preserve the error message."""
        content = json.dumps({"success": False, "error": "File not found"})
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "error"
        assert parsed["error"] == "File not found"

    def test_success_with_path(self):
        """Success with path field (e.g., create_file, move_file)."""
        content = json.dumps({"success": True, "path": "new/note.md"})
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["path"] == "new/note.md"

    def test_success_with_message(self):
        """Success with message field (e.g., no results found)."""
        content = json.dumps({
            "success": True,
            "message": "No matching documents found",
            "results": [],
        })
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert "No matching" in parsed["message"]

    def test_non_json_content(self):
        """Non-JSON content is summarized to first 200 chars."""
        content = "x" * 500
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "unknown"
        assert len(parsed["summary"]) <= 200

    def test_plain_text_short(self):
        """Short plain text is kept as-is in summary."""
        content = "Tool error: connection refused"
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["summary"] == content


class TestCompactToolMessages:
    """Tests for compact_tool_messages."""

    def test_compacts_tool_messages(self):
        """Tool messages are replaced with stubs."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "search for X"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({"success": True, "results": [{"source": "a.md", "content": "..."}]})},
            {"role": "assistant", "content": "Found 1 result."},
        ]
        compact_tool_messages(messages)

        tool_msg = messages[3]
        assert tool_msg["_compacted"] is True
        parsed = json.loads(tool_msg["content"])
        assert parsed["result_count"] == 1

    def test_skips_already_compacted(self):
        """Already-compacted messages are not re-processed."""
        stub_content = json.dumps({"status": "success", "result_count": 1})
        messages = [
            {"role": "tool", "tool_call_id": "call_1",
             "content": stub_content, "_compacted": True},
        ]
        compact_tool_messages(messages)
        assert messages[0]["content"] == stub_content

    def test_preserves_non_tool_messages(self):
        """System, user, and assistant messages are untouched."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        original = [m.copy() for m in messages]
        compact_tool_messages(messages)
        assert messages == original
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: ImportError for `build_tool_stub` and `compact_tool_messages`

**Step 3: Implement compaction functions**

In `src/api_server.py`, add `import json` to the imports, then add before the `app` definition:

```python
# --- Tool compaction ---


def build_tool_stub(content: str) -> str:
    """Build a compact stub from a tool result string.

    Parses JSON tool results and extracts key metadata (status, file paths,
    result count, errors). Non-JSON content is summarized to 200 chars.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        summary = content[:200] if len(content) > 200 else content
        return json.dumps({"status": "unknown", "summary": summary})

    stub: dict = {}

    if "success" in data:
        stub["status"] = "success" if data["success"] else "error"
    else:
        stub["status"] = "unknown"

    if "error" in data:
        stub["error"] = data["error"]

    if "message" in data:
        stub["message"] = data["message"]

    if "path" in data:
        stub["path"] = data["path"]

    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        files = [
            r["source"]
            for r in data["results"]
            if isinstance(r, dict) and "source" in r
        ]
        if files:
            stub["files"] = files

    return json.dumps(stub)


def compact_tool_messages(messages: list[dict]) -> None:
    """Replace tool results with compact stubs in-place."""
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and not msg.get("_compacted"):
            messages[i] = {
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": build_tool_stub(msg["content"]),
                "_compacted": True,
            }
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: All 9 tests PASS

**Step 5: Commit**

```bash
git add src/api_server.py tests/test_session_management.py
git commit -m "feat: add tool compaction for session history"
```

---

### Task 3: File-keyed session routing in `api_server.py`

**Files:**
- Modify: `src/api_server.py` (replace session storage, update `/chat` endpoint)
- Modify: `tests/test_session_management.py` (add routing tests)

**Step 1: Write failing tests for session routing**

Append to `tests/test_session_management.py`:

```python
from api_server import Session, get_or_create_session, file_sessions


class TestSessionRouting:
    """Tests for file-keyed session routing."""

    def setup_method(self):
        file_sessions.clear()

    def test_new_session_created(self):
        """First request for a file creates a new session."""
        session = get_or_create_session("notes/foo.md", "system prompt")
        assert session.active_file == "notes/foo.md"
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "system"

    def test_same_file_returns_existing(self):
        """Second request for same file returns the same session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s1.messages.append({"role": "user", "content": "hello"})

        s2 = get_or_create_session("notes/foo.md", "system prompt")
        assert s2.session_id == s1.session_id
        assert len(s2.messages) == 2

    def test_different_file_creates_new(self):
        """Different file creates a separate session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s2 = get_or_create_session("notes/bar.md", "system prompt")
        assert s1.session_id != s2.session_id
        assert s1.active_file != s2.active_file

    def test_switch_back_resumes(self):
        """Switching back to a previously used file resumes that session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s1.messages.append({"role": "user", "content": "first"})
        original_id = s1.session_id

        get_or_create_session("notes/bar.md", "system prompt")
        s3 = get_or_create_session("notes/foo.md", "system prompt")

        assert s3.session_id == original_id
        assert len(s3.messages) == 2

    def test_null_file_creates_session(self):
        """None active_file gets its own session."""
        session = get_or_create_session(None, "system prompt")
        assert session.active_file is None
        assert session.session_id is not None
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestSessionRouting -v`
Expected: ImportError for `Session`, `get_or_create_session`, `file_sessions`

**Step 3: Implement Session dataclass and routing**

In `src/api_server.py`, add imports and types after existing imports:

```python
from dataclasses import dataclass, field
```

Replace `sessions: dict[str, list[dict]] = {}` with:

```python
@dataclass
class Session:
    """A chat session tied to an active file."""

    session_id: str
    active_file: str | None
    messages: list[dict] = field(default_factory=list)


# File-keyed session storage: active_file -> Session
file_sessions: dict[str | None, Session] = {}


def get_or_create_session(active_file: str | None, system_prompt: str) -> Session:
    """Get existing session for a file or create a new one."""
    if active_file in file_sessions:
        return file_sessions[active_file]

    session = Session(
        session_id=str(uuid.uuid4()),
        active_file=active_file,
        messages=[{"role": "system", "content": system_prompt}],
    )
    file_sessions[active_file] = session
    return session
```

Replace the `/chat` endpoint:

```python
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat message and return the agent's response."""
    session = get_or_create_session(request.active_file, app.state.system_prompt)
    messages = session.messages

    # Strip _compacted flags before LLM call (compact_tool_messages re-adds after)
    for msg in messages:
        msg.pop("_compacted", None)

    # Add user message with context prefix
    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    try:
        response = await agent_turn(
            app.state.llm_client,
            app.state.mcp_session,
            messages,
            app.state.tools,
        )
        compact_tool_messages(messages)
        return ChatResponse(response=response, session_id=session.session_id)
    except Exception as e:
        messages.pop()
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: All 14 tests PASS

**Step 5: Run existing tests for regressions**

Run: `.venv/bin/python -m pytest tests/test_api_context.py -v`
Expected: All 4 tests PASS

**Step 6: Commit**

```bash
git add src/api_server.py tests/test_session_management.py
git commit -m "feat: file-keyed session routing with compaction integration"
```

---

### Task 4: Integration tests for /chat endpoint

**Files:**
- Modify: `tests/test_session_management.py` (add endpoint tests)

**Step 1: Write integration tests**

Append to `tests/test_session_management.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient

from api_server import app


class TestChatEndpointIntegration:
    """Integration tests for /chat with file-keyed sessions."""

    def setup_method(self):
        file_sessions.clear()
        app.state.mcp_session = AsyncMock()
        app.state.llm_client = MagicMock()
        app.state.tools = []
        app.state.system_prompt = "test system prompt"

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_same_file_continues_session(self, mock_agent_turn):
        """Two requests with same active_file share a session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "note.md"})
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "more", "active_file": "note.md"})
            sid2 = r2.json()["session_id"]

        assert sid1 == sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_different_file_new_session(self, mock_agent_turn):
        """Different active_file gets a different session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "a.md"})
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "hi", "active_file": "b.md"})
            sid2 = r2.json()["session_id"]

        assert sid1 != sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_null_file_works(self, mock_agent_turn):
        """Null active_file creates and continues a session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi"})
            assert r1.status_code == 200
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "more"})
            sid2 = r2.json()["session_id"]

        assert sid1 == sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_switch_back_resumes_session(self, mock_agent_turn):
        """Switching away and back resumes the original session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "a.md"})
            sid_a = r1.json()["session_id"]

            client.post("/chat", json={"message": "hi", "active_file": "b.md"})

            r3 = client.post("/chat", json={"message": "back", "active_file": "a.md"})
            sid_a2 = r3.json()["session_id"]

        assert sid_a == sid_a2
```

**Step 2: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_session_management.py::TestChatEndpointIntegration -v`
Expected: All 4 tests PASS

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add tests/test_session_management.py
git commit -m "test: add integration tests for file-keyed session routing"
```

---

### Task 5: Final review and merge

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Review all changes**

```bash
git diff master..feature/session-management --stat
git log --oneline master..feature/session-management
```

**Step 3: Merge and close issue**

```bash
git checkout master
git merge feature/session-management
git push
gh issue close <issue-number>
```
