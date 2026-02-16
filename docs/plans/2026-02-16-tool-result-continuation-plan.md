# Tool Result Continuation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a generic continuation mechanism so the agent can retrieve the full content of truncated tool results in chunks.

**Architecture:** A result cache (`dict[str, str]`) scoped to each `agent_turn` call stores full results when truncation occurs. A synthetic `get_continuation` tool is injected into the tool list and handled locally (no MCP round-trip). The truncation marker includes the tool_call_id and char counts so the LLM can decide whether to continue.

**Tech Stack:** Python, pytest, unittest.mock

---

### Task 1: Update `truncate_tool_result` signature and marker

**Files:**
- Modify: `src/agent.py:34-38`
- Test: `tests/test_agent.py`

**Step 1: Write failing tests for new truncation behavior**

Add to `tests/test_agent.py`:

```python
def test_truncate_tool_result_with_tool_call_id():
    """Truncated results include tool_call_id and char counts in marker."""
    result = "x" * (MAX_TOOL_RESULT_CHARS + 500)
    truncated = truncate_tool_result(result, tool_call_id="call_abc")
    assert truncated.startswith("x" * MAX_TOOL_RESULT_CHARS)
    assert "call_abc" in truncated
    assert "4000" in truncated
    assert str(MAX_TOOL_RESULT_CHARS + 500) in truncated


def test_truncate_tool_result_no_id_still_works():
    """Without tool_call_id, truncation marker omits continuation hint."""
    result = "x" * (MAX_TOOL_RESULT_CHARS + 100)
    truncated = truncate_tool_result(result)
    assert truncated.endswith("\n\n[truncated]")


def test_truncate_tool_result_short_with_id():
    """Short results unchanged even when tool_call_id provided."""
    result = "short"
    assert truncate_tool_result(result, tool_call_id="call_1") == "short"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_truncate_tool_result_with_tool_call_id tests/test_agent.py::test_truncate_tool_result_no_id_still_works tests/test_agent.py::test_truncate_tool_result_short_with_id -v`
Expected: FAIL — `truncate_tool_result() got unexpected keyword argument 'tool_call_id'`

**Step 3: Update `truncate_tool_result` in `src/agent.py`**

Replace lines 34-38:

```python
def truncate_tool_result(result: str, tool_call_id: str | None = None) -> str:
    """Truncate tool result if it exceeds the character limit.

    When tool_call_id is provided, the truncation marker includes it
    so the LLM can call get_continuation to retrieve more.
    """
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result
    truncated = result[:MAX_TOOL_RESULT_CHARS]
    if tool_call_id:
        truncated += (
            f"\n\n[truncated — showing {MAX_TOOL_RESULT_CHARS}/{len(result)} chars. "
            f'Call get_continuation with tool_call_id="{tool_call_id}" to read more]'
        )
    else:
        truncated += "\n\n[truncated]"
    return truncated
```

**Step 4: Update existing tests that check exact truncation format**

The test `test_truncate_tool_result_over_limit` checks `len(truncated) == MAX_TOOL_RESULT_CHARS + len("\n\n[truncated]")`. This still passes because the no-id path is unchanged.

The test `test_agent_turn_tool_result_truncated` checks `"[truncated]" in tool_msgs[0]["content"]`. The new marker still contains `[truncated`, so this still passes. But the tool_call_id is now passed, so the marker will be the longer form. Update this assertion:

In `test_agent_turn_tool_result_truncated`, change:
```python
    assert "[truncated]" in tool_msgs[0]["content"]
```
to:
```python
    assert "[truncated" in tool_msgs[0]["content"]
```

**Step 5: Run all agent tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: add tool_call_id to truncation marker for continuation support"
```

---

### Task 2: Add result cache and `get_continuation` handler to `agent_turn`

**Files:**
- Modify: `src/agent.py:124-214` (the `agent_turn` function)
- Test: `tests/test_agent.py`

**Step 1: Write failing test for get_continuation**

Add to `tests/test_agent.py`:

```python
@pytest.mark.anyio
async def test_agent_turn_get_continuation():
    """Agent handles get_continuation for truncated results."""
    # First result is large (will be truncated)
    big_result = "A" * 3000 + "B" * 3000 + "C" * 2000  # 8000 chars total

    # LLM call 1: calls transcribe_audio, gets truncated result
    mock_tool_call_1 = MagicMock()
    mock_tool_call_1.id = "call_transcribe"
    mock_tool_call_1.function.name = "transcribe_audio"
    mock_tool_call_1.function.arguments = '{"path": "note.md"}'

    mock_msg_1 = MagicMock()
    mock_msg_1.tool_calls = [mock_tool_call_1]
    mock_msg_1.content = None
    mock_msg_1.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_transcribe", "function": {"name": "transcribe_audio", "arguments": '{"path": "note.md"}'}, "type": "function"}],
    }

    # LLM call 2: calls get_continuation to get the rest
    mock_tool_call_2 = MagicMock()
    mock_tool_call_2.id = "call_cont"
    mock_tool_call_2.function.name = "get_continuation"
    mock_tool_call_2.function.arguments = json.dumps({"tool_call_id": "call_transcribe", "offset": MAX_TOOL_RESULT_CHARS})

    mock_msg_2 = MagicMock()
    mock_msg_2.tool_calls = [mock_tool_call_2]
    mock_msg_2.content = None
    mock_msg_2.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_cont", "function": {"name": "get_continuation", "arguments": mock_tool_call_2.function.arguments}, "type": "function"}],
    }

    # LLM call 3: final response
    mock_msg_final = MagicMock()
    mock_msg_final.tool_calls = None
    mock_msg_final.content = "Here is the summary."
    mock_msg_final.model_dump.return_value = {"role": "assistant", "content": "Here is the summary."}

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_1)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_2)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_final)], usage=mock_usage),
    ]

    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=big_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "summarize"}]

    result = await agent_turn(mock_client, mock_session, messages, [])
    assert result == "Here is the summary."

    # Verify: MCP was called only once (transcribe_audio), not for get_continuation
    mock_session.call_tool.assert_called_once_with("transcribe_audio", {"path": "note.md"})

    # Verify: the continuation result contains the second chunk
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    # First tool msg is the truncated first chunk
    assert "call_transcribe" in tool_msgs[0]["content"]
    # Second tool msg is the continuation chunk — should contain B's and C's
    assert "B" in tool_msgs[1]["content"]


@pytest.mark.anyio
async def test_agent_turn_get_continuation_invalid_id():
    """get_continuation with unknown tool_call_id returns error."""
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_cont"
    mock_tool_call.function.name = "get_continuation"
    mock_tool_call.function.arguments = json.dumps({"tool_call_id": "nonexistent", "offset": 0})

    mock_msg_1 = MagicMock()
    mock_msg_1.tool_calls = [mock_tool_call]
    mock_msg_1.content = None
    mock_msg_1.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_cont", "function": {"name": "get_continuation", "arguments": mock_tool_call.function.arguments}, "type": "function"}],
    }

    mock_msg_final = MagicMock()
    mock_msg_final.tool_calls = None
    mock_msg_final.content = "No cached result."
    mock_msg_final.model_dump.return_value = {"role": "assistant", "content": "No cached result."}

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_1)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_final)], usage=mock_usage),
    ]

    mock_session = AsyncMock()
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "continue"}]

    await agent_turn(mock_client, mock_session, messages, [])

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    parsed = json.loads(tool_msgs[0]["content"])
    assert parsed["error"] is not None
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_agent_turn_get_continuation tests/test_agent.py::test_agent_turn_get_continuation_invalid_id -v`
Expected: FAIL — `get_continuation` not handled

**Step 3: Implement the cache and handler in `agent_turn`**

In `src/agent.py`, modify `agent_turn`:

a) Define the synthetic tool definition as a module-level constant:

```python
GET_CONTINUATION_TOOL = {
    "type": "function",
    "function": {
        "name": "get_continuation",
        "description": (
            "Retrieve the next chunk of a truncated tool result. "
            "Use when a previous tool result shows [truncated]."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_call_id": {
                    "type": "string",
                    "description": "The tool_call_id from the truncation message",
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to read from",
                },
            },
            "required": ["tool_call_id"],
        },
    },
}
```

b) At the top of `agent_turn`, add the cache and inject the synthetic tool:

```python
    truncated_results: dict[str, str] = {}
    all_tools = tools + [GET_CONTINUATION_TOOL]
```

c) Use `all_tools` instead of `tools` in the `client.chat.completions.create` call:

```python
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=all_tools if all_tools else None,
            tool_choice="auto" if all_tools else None,
        )
```

d) Add `get_continuation` to `UNCOUNTED_TOOLS`:

```python
    UNCOUNTED_TOOLS = {"log_interaction", "get_continuation"}
```

e) In the tool call loop, handle `get_continuation` before `execute_tool_call`:

```python
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            logger.info("Tool call: %s args=%s", tool_name, arguments)

            if tool_name == "get_continuation":
                result = _handle_get_continuation(
                    truncated_results, arguments
                )
            else:
                result = await execute_tool_call(session, tool_name, arguments)
                raw_len = len(result)
                result = truncate_tool_result(result, tool_call_id=tool_call.id)
                if len(result) < raw_len + len(result) - raw_len:
                    # Cache the full result when truncation occurred
                    pass
                # Simpler approach: check if result was truncated
                if raw_len > MAX_TOOL_RESULT_CHARS:
                    truncated_results[tool_call.id] = result_before_truncation
```

Actually, cleaner to restructure:

```python
            if tool_name == "get_continuation":
                result = _handle_get_continuation(truncated_results, arguments)
            else:
                result = await execute_tool_call(session, tool_name, arguments)
                raw_len = len(result)
                if raw_len > MAX_TOOL_RESULT_CHARS:
                    truncated_results[tool_call.id] = result
                    result = truncate_tool_result(result, tool_call_id=tool_call.id)
                logger.info(
                    "Tool result: %s chars=%d truncated=%s",
                    tool_name, raw_len, raw_len > MAX_TOOL_RESULT_CHARS,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
```

f) Add the handler function (module-level, above `agent_turn`):

```python
def _handle_get_continuation(
    cache: dict[str, str], arguments: dict
) -> str:
    """Serve the next chunk of a cached truncated tool result."""
    tc_id = arguments.get("tool_call_id", "")
    offset = arguments.get("offset", MAX_TOOL_RESULT_CHARS)

    full_result = cache.get(tc_id)
    if full_result is None:
        return json.dumps({"error": f"No cached result for tool_call_id '{tc_id}'"})

    chunk = full_result[offset : offset + MAX_TOOL_RESULT_CHARS]
    if not chunk:
        return json.dumps({"error": "Offset beyond end of result"})

    end = offset + len(chunk)
    remaining = len(full_result) - end
    if remaining > 0:
        chunk += (
            f"\n\n[truncated — showing {offset}-{end}/{len(full_result)} chars. "
            f'{remaining} chars remaining. Call get_continuation with '
            f'tool_call_id="{tc_id}" offset={end} to read more]'
        )

    return chunk
```

**Step 4: Run all agent tests**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All PASS (no regressions)

**Step 6: Commit**

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: add get_continuation tool for retrieving truncated results"
```

---

### Task 3: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add documentation about the continuation mechanism**

In the `agent.py` description section and the "Token management — tool result truncation" section, add:

Under the `agent.py` bullet in Architecture:
- `agent.py` — CLI chat client, 4000-char tool result truncation, synthetic `get_continuation` tool for retrieving truncated results in chunks

In the "Token management — tool result truncation" section, add after the existing paragraph:

```markdown
**Tool result continuation:**
When a tool result is truncated, the full result is cached in-memory for the duration of the `agent_turn` call. The truncation marker includes the `tool_call_id` and character counts, and instructs the LLM to call `get_continuation(tool_call_id, offset)` if it needs more. This synthetic tool is handled directly by `agent_turn` (no MCP round-trip). The LLM can call it repeatedly with increasing offsets to retrieve the full result in 4000-char chunks. `get_continuation` calls are excluded from the iteration cap count.
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document get_continuation mechanism in CLAUDE.md"
```
