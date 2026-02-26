"""Tests for agent turn behavior: iteration cap and tool result truncation."""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent import (
    _parse_tool_arguments,
    _simplify_schema,
    agent_turn,
    ensure_interaction_logged,
    truncate_tool_result,
    MAX_TOOL_RESULT_CHARS,
)
from services.compaction import compact_tool_messages


class TestParseToolArguments:
    """Tests for _parse_tool_arguments: robust parsing of model-generated args."""

    def test_valid_json(self):
        assert _parse_tool_arguments('{"query": "test"}') == {"query": "test"}

    def test_empty_string(self):
        assert _parse_tool_arguments("") == {}

    def test_none_like(self):
        assert _parse_tool_arguments("   ") == {}

    def test_single_quotes(self):
        """Python-style single-quoted dict."""
        assert _parse_tool_arguments("{'path': 'Daily Notes/2026-02-18.md'}") == {
            "path": "Daily Notes/2026-02-18.md"
        }

    def test_python_booleans(self):
        """Python True/False instead of JSON true/false."""
        result = _parse_tool_arguments("{'confirm': True, 'field': 'status'}")
        assert result == {"confirm": True, "field": "status"}

    def test_trailing_comma(self):
        result = _parse_tool_arguments('{"query": "test", "n_results": 5,}')
        assert result == {"query": "test", "n_results": 5}

    def test_nested_objects(self):
        raw = '{"field": "project", "filters": [{"field": "status", "value": "open"}]}'
        result = _parse_tool_arguments(raw)
        assert result["field"] == "project"
        assert result["filters"][0]["field"] == "status"

    def test_strips_control_tokens(self):
        """gpt-oss-120b appends \\t<|call|> after JSON."""
        raw = '{\n"field": "project",\n"value": "Agentic S2P"\n}\t<|call|>'
        result = _parse_tool_arguments(raw)
        assert result == {"field": "project", "value": "Agentic S2P"}

    def test_strips_empty_args_with_control_token(self):
        """get_current_date with no real args, just {}<|call|>."""
        result = _parse_tool_arguments("{}<|call|>")
        assert result == {}

    def test_strips_multiple_control_tokens(self):
        raw = '{"query": "test"}<|call|><|end|>'
        result = _parse_tool_arguments(raw)
        assert result == {"query": "test"}


class TestSimplifySchema:
    """Tests for _simplify_schema: inlines $ref, flattens anyOf nullable."""

    def test_resolves_ref(self):
        """$ref entries are replaced with the referenced definition."""
        schema = {
            "$defs": {
                "Thing": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                }
            },
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Thing"},
                }
            },
            "type": "object",
        }
        result = _simplify_schema(schema)
        assert "$defs" not in result
        assert "$ref" not in json.dumps(result)
        assert result["properties"]["items"]["items"]["type"] == "object"
        assert "name" in result["properties"]["items"]["items"]["properties"]

    def test_simplifies_anyof_nullable(self):
        """anyOf[T, null] collapses to just T, keeping default/title."""
        schema = {
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "title": "Value",
                }
            },
            "type": "object",
        }
        result = _simplify_schema(schema)
        prop = result["properties"]["value"]
        assert "anyOf" not in prop
        assert prop["type"] == "string"
        assert prop["default"] is None
        assert prop["title"] == "Value"

    def test_combined_ref_and_anyof(self):
        """Real-world pattern: anyOf[$ref array, null] fully flattened."""
        schema = {
            "$defs": {
                "Filter": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["field", "value"],
                }
            },
            "properties": {
                "filters": {
                    "anyOf": [
                        {"items": {"$ref": "#/$defs/Filter"}, "type": "array"},
                        {"type": "null"},
                    ],
                    "default": None,
                }
            },
            "type": "object",
        }
        result = _simplify_schema(schema)
        filters = result["properties"]["filters"]
        assert "anyOf" not in filters
        assert "$ref" not in json.dumps(filters)
        assert filters["type"] == "array"
        assert filters["items"]["type"] == "object"
        assert "field" in filters["items"]["properties"]

    def test_passthrough_simple_schema(self):
        """Schemas without $ref or anyOf pass through unchanged."""
        schema = {
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
            "type": "object",
        }
        result = _simplify_schema(schema)
        assert result == schema

    def test_does_not_mutate_original(self):
        """Original schema dict is not modified."""
        schema = {
            "$defs": {"X": {"type": "string"}},
            "properties": {"a": {"$ref": "#/$defs/X"}},
            "type": "object",
        }
        original = json.dumps(schema)
        _simplify_schema(schema)
        assert json.dumps(schema) == original


@pytest.mark.parametrize(
    ("result", "result_id"),
    [
        ("short result", None),
        ("x" * MAX_TOOL_RESULT_CHARS, None),
        ("short", "1"),
    ],
    ids=["short", "exact_limit", "short_with_id"],
)
def test_truncate_tool_result_not_truncated(result, result_id):
    """Results at or under the limit are returned unchanged."""
    assert truncate_tool_result(result, result_id=result_id) == result


def test_truncate_tool_result_over_limit():
    """Result over limit is truncated with marker."""
    result = "x" * (MAX_TOOL_RESULT_CHARS + 100)
    truncated = truncate_tool_result(result)
    assert len(truncated) == MAX_TOOL_RESULT_CHARS + len("\n\n[truncated]")
    assert truncated.endswith("\n\n[truncated]")
    assert truncated.startswith("x" * MAX_TOOL_RESULT_CHARS)


def test_truncate_tool_result_with_result_id():
    """Truncated results include simple result_id and char counts in marker."""
    result = "x" * (MAX_TOOL_RESULT_CHARS + 500)
    truncated = truncate_tool_result(result, result_id="1")
    assert truncated.startswith("x" * MAX_TOOL_RESULT_CHARS)
    assert 'id="1"' in truncated
    assert "tool_call_id" not in truncated  # no longer uses tool_call_id
    assert str(MAX_TOOL_RESULT_CHARS) in truncated
    assert str(MAX_TOOL_RESULT_CHARS + 500) in truncated


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
    big_result = "x" * (MAX_TOOL_RESULT_CHARS + 5000)

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
    # Last round's tool results stay uncompacted so the LLM can read them
    assert "_compacted" not in tool_msgs[0]
    assert "[truncated" in tool_msgs[0]["content"]
    assert 'id="1"' in tool_msgs[0]["content"]  # simple numeric ID, not tool_call_id
    assert str(MAX_TOOL_RESULT_CHARS) in tool_msgs[0]["content"]


@pytest.mark.anyio
async def test_agent_turn_get_continuation():
    """Agent handles get_continuation for truncated results."""
    # Must exceed MAX_TOOL_RESULT_CHARS (100K) to trigger truncation
    big_result = "A" * 60000 + "B" * 60000 + "C" * 30000  # 150K chars total

    # LLM call 1: calls read_file, gets truncated result
    mock_tool_call_1 = MagicMock()
    mock_tool_call_1.id = "call_transcribe"
    mock_tool_call_1.function.name = "read_file"
    mock_tool_call_1.function.arguments = '{"path": "large_file.md"}'

    mock_msg_1 = MagicMock()
    mock_msg_1.tool_calls = [mock_tool_call_1]
    mock_msg_1.content = None
    mock_msg_1.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_transcribe", "function": {"name": "read_file", "arguments": '{"path": "large_file.md"}'}, "type": "function"}],
    }

    # LLM call 2: calls get_continuation with simple numeric id
    mock_tool_call_2 = MagicMock()
    mock_tool_call_2.id = "call_cont"
    mock_tool_call_2.function.name = "get_continuation"
    mock_tool_call_2.function.arguments = json.dumps({"id": "1", "offset": MAX_TOOL_RESULT_CHARS})

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

    # MCP was called only once (read_file), NOT for get_continuation
    mock_session.call_tool.assert_called_once_with("read_file", {"path": "large_file.md"})

    # Verify tool messages
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    # First: truncated with simple numeric id in marker
    assert 'id="1"' in tool_msgs[0]["content"]
    # Second: continuation chunk contains B's
    assert "B" in tool_msgs[1]["content"]


@pytest.mark.anyio
async def test_agent_turn_get_continuation_invalid_id():
    """get_continuation with unknown tool_call_id returns error."""
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_cont"
    mock_tool_call.function.name = "get_continuation"
    mock_tool_call.function.arguments = json.dumps({"id": "99", "offset": 0})

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
    assert "error" in parsed


def test_handle_get_continuation_valid():
    """Returns correct chunk from cache."""
    from agent import _handle_get_continuation, MAX_TOOL_RESULT_CHARS
    size = MAX_TOOL_RESULT_CHARS * 3  # large enough to have remaining after first chunk
    cache = {"1": "A" * size}
    result = _handle_get_continuation(cache, {"id": "1", "offset": MAX_TOOL_RESULT_CHARS})
    assert result.startswith("A")
    assert "remaining" in result


def test_handle_get_continuation_final_chunk():
    """Final chunk has no truncation marker."""
    from agent import _handle_get_continuation, MAX_TOOL_RESULT_CHARS
    extra = 1000
    cache = {"1": "A" * (MAX_TOOL_RESULT_CHARS + extra)}
    result = _handle_get_continuation(cache, {"id": "1", "offset": MAX_TOOL_RESULT_CHARS})
    assert "truncated" not in result
    assert len(result) == extra


def test_handle_get_continuation_missing_id():
    """Returns error for unknown id."""
    from agent import _handle_get_continuation
    result = _handle_get_continuation({}, {"id": "99"})
    parsed = json.loads(result)
    assert "error" in parsed


def test_load_preferences_reloaded_each_turn(tmp_path):
    """Preferences are re-read from disk so mid-session changes take effect."""
    from agent import load_preferences, SYSTEM_PROMPT
    import agent as agent_module

    prefs_file = tmp_path / "Preferences.md"
    original_prefs_file = agent_module.PREFERENCES_FILE

    try:
        agent_module.PREFERENCES_FILE = prefs_file

        # No file yet → None
        assert load_preferences() is None

        # Create preferences mid-session
        prefs_file.write_text("- Always respond in French")
        result = load_preferences()
        assert result is not None
        assert "Always respond in French" in result

        # Update preferences mid-session
        prefs_file.write_text("- Always respond in Spanish")
        result = load_preferences()
        assert "Always respond in Spanish" in result
    finally:
        agent_module.PREFERENCES_FILE = original_prefs_file


def test_build_system_prompt_includes_date(tmp_path):
    """_build_system_prompt should append the current date."""
    import agent as agent_module
    from agent import _build_system_prompt
    from datetime import datetime

    original_prefs_file = agent_module.PREFERENCES_FILE

    try:
        agent_module.PREFERENCES_FILE = tmp_path / "Preferences.md"
        prompt = _build_system_prompt()
        today = datetime.now().strftime("%Y-%m-%d")
        assert f"Current date: {today}" in prompt
    finally:
        agent_module.PREFERENCES_FILE = original_prefs_file


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

    # Verify tool_result event data
    tool_result_event = next(e for e in events if e[0] == "tool_result")
    assert tool_result_event[1]["tool"] == "search_vault"
    assert "success" in tool_result_event[1]

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


@pytest.mark.anyio
async def test_agent_turn_breaks_on_confirmation_required():
    """After confirmation_required, agent gets one text-only call (no tools)."""
    # First LLM call: agent decides to call batch_update_frontmatter
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_batch"
    mock_tool_call.function.name = "batch_update_frontmatter"
    mock_tool_call.function.arguments = '{"field": "status", "value": "done", "folder": "projects"}'

    mock_msg_with_tool = MagicMock()
    mock_msg_with_tool.tool_calls = [mock_tool_call]
    mock_msg_with_tool.content = "Let me update those files."
    mock_msg_with_tool.model_dump.return_value = {
        "role": "assistant",
        "content": "Let me update those files.",
        "tool_calls": [{"id": "call_batch", "function": {"name": "batch_update_frontmatter", "arguments": "{}"}, "type": "function"}],
    }

    # Second LLM call: text-only response presenting the preview
    mock_msg_present = MagicMock()
    mock_msg_present.tool_calls = None
    mock_msg_present.content = "This will update 7 files. Shall I proceed?"
    mock_msg_present.model_dump.return_value = {
        "role": "assistant",
        "content": "This will update 7 files. Shall I proceed?",
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_with_tool)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_present)], usage=mock_usage),
    ]

    # Tool returns confirmation_required
    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "This will set 'status' on 7 files. Show the file list to the user.",
        "files": ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "g.md"],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "update all"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    # Agent presents the preview to the user
    assert result == "This will update 7 files. Shall I proceed?"
    # LLM called exactly twice: tool call + text-only presentation
    assert mock_client.chat.completions.create.call_count == 2
    # Second call forced text-only via tool_choice="none"
    second_call = mock_client.chat.completions.create.call_args_list[1]
    assert second_call.kwargs.get("tool_choice") == "none"


@pytest.mark.anyio
async def test_confirmation_preview_emitted_after_response():
    """confirmation_preview SSE event is emitted after response, not before."""
    events = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_batch"
    mock_tool_call.function.name = "batch_update_frontmatter"
    mock_tool_call.function.arguments = '{"field": "status", "value": "done", "folder": "projects"}'

    mock_msg_with_tool = MagicMock()
    mock_msg_with_tool.tool_calls = [mock_tool_call]
    mock_msg_with_tool.content = None
    mock_msg_with_tool.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_batch", "function": {"name": "batch_update_frontmatter", "arguments": "{}"}, "type": "function"}],
    }

    mock_msg_present = MagicMock()
    mock_msg_present.tool_calls = None
    mock_msg_present.content = "This will update 7 files. Shall I proceed?"
    mock_msg_present.model_dump.return_value = {
        "role": "assistant",
        "content": "This will update 7 files. Shall I proceed?",
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_with_tool)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_present)], usage=mock_usage),
    ]

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "Confirm to proceed.",
        "preview_message": "This will set 'status' = 'done' on 7 files.",
        "files": ["a.md", "b.md"],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "update all"}]

    await agent_turn(mock_client, mock_session, messages, [], on_event=on_event)

    event_types = [e[0] for e in events]
    assert "response" in event_types
    assert "confirmation_preview" in event_types
    response_idx = event_types.index("response")
    preview_idx = event_types.index("confirmation_preview")
    assert response_idx < preview_idx, (
        f"response (index {response_idx}) should come before "
        f"confirmation_preview (index {preview_idx})"
    )


@pytest.mark.anyio
async def test_agent_turn_strips_tool_calls_with_content():
    """If model ignores tool_choice='none' but includes text, strip calls and return text."""
    # First LLM call: agent calls batch_move_files → confirmation_required
    mock_tool_call_preview = MagicMock()
    mock_tool_call_preview.id = "call_preview"
    mock_tool_call_preview.function.name = "batch_move_files"
    mock_tool_call_preview.function.arguments = '{"moves": [], "confirm": false}'

    mock_msg_preview = MagicMock()
    mock_msg_preview.tool_calls = [mock_tool_call_preview]
    mock_msg_preview.content = None
    mock_msg_preview.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_preview", "function": {"name": "batch_move_files", "arguments": "{}"}, "type": "function"}],
    }

    # Second LLM call: model ignores tool_choice="none" but includes text content
    mock_tool_call_confirm = MagicMock()
    mock_tool_call_confirm.id = "call_confirm"
    mock_tool_call_confirm.function.name = "batch_move_files"
    mock_tool_call_confirm.function.arguments = '{"moves": [], "confirm": true}'

    mock_msg_confirm = MagicMock()
    mock_msg_confirm.tool_calls = [mock_tool_call_confirm]
    mock_msg_confirm.content = "Here are the files to move:"
    mock_msg_confirm.model_dump.return_value = {
        "role": "assistant",
        "content": "Here are the files to move:",
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_preview)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_confirm)], usage=mock_usage),
    ]

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "This will move 10 files.",
        "files": [f"file{i}.md" for i in range(10)],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "move files"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    # Tool calls were stripped — turn ends with the text content, NOT executing the confirm
    assert result == "Here are the files to move:"
    # LLM called exactly twice (preview + stripped response)
    assert mock_client.chat.completions.create.call_count == 2
    # Tool was only called once (the preview), NOT twice (confirm was stripped)
    assert mock_session.call_tool.call_count == 1


@pytest.mark.anyio
async def test_agent_turn_retries_when_stripped_response_has_no_content():
    """If stripped response has no content, retry instead of returning empty string."""
    # First LLM call: agent calls batch_move_files → confirmation_required
    mock_tool_call_preview = MagicMock()
    mock_tool_call_preview.id = "call_preview"
    mock_tool_call_preview.function.name = "batch_move_files"
    mock_tool_call_preview.function.arguments = '{"moves": [], "confirm": false}'

    mock_msg_preview = MagicMock()
    mock_msg_preview.tool_calls = [mock_tool_call_preview]
    mock_msg_preview.content = None
    mock_msg_preview.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_preview", "function": {"name": "batch_move_files", "arguments": "{}"}, "type": "function"}],
    }

    # Second LLM call: model ignores tool_choice="none", tool-only, NO content
    mock_tool_call_confirm = MagicMock()
    mock_tool_call_confirm.id = "call_confirm"
    mock_tool_call_confirm.function.name = "batch_move_files"
    mock_tool_call_confirm.function.arguments = '{"moves": [], "confirm": true}'

    mock_msg_no_content = MagicMock()
    mock_msg_no_content.tool_calls = [mock_tool_call_confirm]
    mock_msg_no_content.content = None

    # Third LLM call: model finally returns text-only
    mock_msg_text = MagicMock()
    mock_msg_text.tool_calls = None
    mock_msg_text.content = "This will move 10 files. Shall I proceed?"
    mock_msg_text.model_dump.return_value = {
        "role": "assistant",
        "content": "This will move 10 files. Shall I proceed?",
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_preview)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_no_content)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_text)], usage=mock_usage),
    ]

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "This will move 10 files.",
        "files": [f"file{i}.md" for i in range(10)],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "move files"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    # Retried and got the text response
    assert result == "This will move 10 files. Shall I proceed?"
    # LLM called 3 times: tool call + stripped-no-content retry + text response
    assert mock_client.chat.completions.create.call_count == 3
    # Tool was only called once (the preview)
    assert mock_session.call_tool.call_count == 1


@pytest.mark.anyio
async def test_agent_turn_fallback_after_max_text_only_retries():
    """After MAX_TEXT_ONLY_RETRIES, use preview message as fallback instead of looping."""
    # First LLM call: agent calls batch_move_files → confirmation_required
    mock_tool_call_preview = MagicMock()
    mock_tool_call_preview.id = "call_preview"
    mock_tool_call_preview.function.name = "batch_move_files"
    mock_tool_call_preview.function.arguments = '{"moves": [], "confirm": false}'

    mock_msg_preview = MagicMock()
    mock_msg_preview.tool_calls = [mock_tool_call_preview]
    mock_msg_preview.content = None
    mock_msg_preview.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_preview", "function": {"name": "batch_move_files", "arguments": "{}"}, "type": "function"}],
    }

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_usage.total_tokens = 150

    def make_tool_only_msg():
        tc = MagicMock()
        tc.id = "call_confirm"
        tc.function.name = "batch_move_files"
        tc.function.arguments = '{"moves": [], "confirm": true}'
        msg = MagicMock()
        msg.tool_calls = [tc]
        msg.content = None
        return msg

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_preview)], usage=mock_usage),
    ] + [
        MagicMock(choices=[MagicMock(message=make_tool_only_msg())], usage=mock_usage)
        for _ in range(3)  # MAX_TEXT_ONLY_RETRIES
    ]

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "Confirm to proceed.",
        "preview_message": "This will move 10 files.",
        "files": [f"file{i}.md" for i in range(10)],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "move files"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    # Falls back to preview message after retries exhausted
    assert result == "This will move 10 files."
    # 1 (tool call) + 3 (retries) + no more = 4 total LLM calls
    assert mock_client.chat.completions.create.call_count == 4
    # The empty stripped message was NOT appended to history
    assert not any(
        msg.get("role") == "assistant" and msg.get("content") is None and "tool_calls" not in msg
        for msg in messages
    )


@pytest.mark.anyio
async def test_agent_turn_dedup_consecutive_tool_calls():
    """Identical consecutive tool calls are intercepted without executing."""
    # LLM call 1: calls update_frontmatter
    mock_tool_call_1 = MagicMock()
    mock_tool_call_1.id = "call_1"
    mock_tool_call_1.function.name = "update_frontmatter"
    mock_tool_call_1.function.arguments = '{"path": "note.md", "field": "category", "value": "person", "operation": "set"}'

    mock_msg_1 = MagicMock()
    mock_msg_1.tool_calls = [mock_tool_call_1]
    mock_msg_1.content = None
    mock_msg_1.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_1", "function": {"name": "update_frontmatter", "arguments": mock_tool_call_1.function.arguments}, "type": "function"}],
    }

    # LLM call 2: retries exact same call
    mock_tool_call_2 = MagicMock()
    mock_tool_call_2.id = "call_2"
    mock_tool_call_2.function.name = "update_frontmatter"
    mock_tool_call_2.function.arguments = '{"path": "note.md", "field": "category", "value": "person", "operation": "set"}'

    mock_msg_2 = MagicMock()
    mock_msg_2.tool_calls = [mock_tool_call_2]
    mock_msg_2.content = None
    mock_msg_2.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": "call_2", "function": {"name": "update_frontmatter", "arguments": mock_tool_call_2.function.arguments}, "type": "function"}],
    }

    # LLM call 3: final text response
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
        MagicMock(choices=[MagicMock(message=mock_msg_1)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_2)], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_final)], usage=mock_usage),
    ]

    tool_result = json.dumps({"success": True, "message": "Set 'category' to 'person' in note.md"})
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=tool_result)]
    )

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "set category"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    assert result == "Done"
    # Tool only executed once — second call was deduped
    assert mock_session.call_tool.call_count == 1
    # The dedup message was injected into the second tool result
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert "Duplicate call" in tool_msgs[1]["content"]
    assert "different approach" in tool_msgs[1]["content"]


@pytest.mark.anyio
async def test_agent_turn_allows_retry_after_failed_tool_call():
    """Identical consecutive tool call is allowed if the previous one failed."""
    args_json = '{"path": "note.md", "field": "category", "value": "person", "operation": "set"}'

    def make_tool_call(call_id):
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = "update_frontmatter"
        tc.function.arguments = args_json
        return tc

    def make_assistant_msg(call_id):
        tc = make_tool_call(call_id)
        msg = MagicMock()
        msg.tool_calls = [tc]
        msg.content = None
        msg.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [{"id": call_id, "function": {"name": "update_frontmatter", "arguments": args_json}, "type": "function"}],
        }
        return msg

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
        MagicMock(choices=[MagicMock(message=make_assistant_msg("call_1"))], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=make_assistant_msg("call_2"))], usage=mock_usage),
        MagicMock(choices=[MagicMock(message=mock_msg_final)], usage=mock_usage),
    ]

    # First call fails, second call succeeds
    error_result = "Tool error: connection timeout"
    success_result = json.dumps({"success": True, "message": "Set 'category' to 'person' in note.md"})
    mock_session = AsyncMock()
    mock_session.call_tool.side_effect = [
        MagicMock(isError=False, content=[MagicMock(text=error_result)]),
        MagicMock(isError=False, content=[MagicMock(text=success_result)]),
    ]

    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "set category"}]

    result = await agent_turn(mock_client, mock_session, messages, [])

    assert result == "Done"
    # Tool executed twice — retry was allowed because first call failed
    assert mock_session.call_tool.call_count == 2
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert "Duplicate call" not in tool_msgs[1]["content"]


class TestAgentCompaction:
    """Tests for tool message compaction in agent context."""

    def test_compact_tool_messages_after_tool_round(self):
        """Tool messages should be compacted after execution."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({"success": True, "results": [{"source": "a.md", "content": "long..."}]})},
        ]
        compact_tool_messages(messages)

        tool_msg = messages[3]
        assert tool_msg["_compacted"] is True
        parsed = json.loads(tool_msg["content"])
        assert parsed["status"] == "success"

    @pytest.mark.anyio
    async def test_cli_compacts_after_agent_turn(self):
        """CLI chat_loop compacts tool messages after each agent_turn."""
        # Simulate what chat_loop does: strip flags, call agent_turn, restore + compact
        search_result = json.dumps({
            "success": True,
            "results": [
                {"source": "note.md", "content": "A very long search result " * 50, "heading": "## Intro"}
            ],
        })
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "search for something"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": search_result},
            {"role": "assistant", "content": "Found results."},
        ]

        # First turn: compact (simulating end of chat_loop turn)
        compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
        for msg in messages:
            msg.pop("_compacted", None)
        # (agent_turn would run here)
        for i in compacted_indices:
            messages[i]["_compacted"] = True
        compact_tool_messages(messages)

        # Tool message should now be compacted
        tool_msg = messages[3]
        assert tool_msg["_compacted"] is True
        parsed = json.loads(tool_msg["content"])
        assert "snippet" in parsed["results"][0]  # search_vault stub format
        original_content = search_result
        assert len(tool_msg["content"]) < len(original_content)

        # Second turn: add new tool call, compact again — old stub should survive
        messages.append({"role": "user", "content": "read that file"})
        messages.append({
            "role": "assistant", "content": None, "tool_calls": [
                {"id": "call_2", "function": {"name": "read_file"}, "type": "function"}
            ],
        })
        messages.append({
            "role": "tool", "tool_call_id": "call_2",
            "content": json.dumps({"success": True, "content": "File body " * 100, "path": "note.md"}),
        })
        messages.append({"role": "assistant", "content": "Here is the file."})

        # Strip, "run agent_turn", restore, compact
        compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
        for msg in messages:
            msg.pop("_compacted", None)
        for i in compacted_indices:
            messages[i]["_compacted"] = True
        compact_tool_messages(messages)

        # Old search stub should still have snippet (not re-compacted/degraded)
        old_stub = json.loads(messages[3]["content"])
        assert "snippet" in old_stub["results"][0]
        # New read_file should now be compacted too
        new_stub = json.loads(messages[7]["content"])
        assert "content_preview" in new_stub  # read_file stub format
        assert messages[7]["_compacted"] is True


class TestEnsureInteractionLogged:
    """Tests for ensure_interaction_logged auto-logging safety net."""

    @pytest.mark.anyio
    async def test_noop_when_log_interaction_was_called(self):
        """No auto-log when agent already called log_interaction."""
        mock_session = AsyncMock()
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "find recipes"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "search_vault", "arguments": "{}"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c2", "function": {"name": "log_interaction", "arguments": "{}"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "c2", "content": '{"success": true}'},
            {"role": "assistant", "content": "Here are your recipes."},
        ]
        await ensure_interaction_logged(
            mock_session, messages, turn_start=1,
            user_query="find recipes", response="Here are your recipes.",
        )
        mock_session.call_tool.assert_not_called()

    @pytest.mark.anyio
    async def test_auto_logs_when_tools_used_but_no_log_interaction(self):
        """Auto-logs when agent used tools but forgot log_interaction."""
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(
            isError=False, content=[MagicMock(text='{"success": true}')]
        )
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "find recipes"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "search_vault", "arguments": "{}"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
            {"role": "assistant", "content": "Here are your recipes."},
        ]
        await ensure_interaction_logged(
            mock_session, messages, turn_start=1,
            user_query="find recipes", response="Here are your recipes.",
        )
        mock_session.call_tool.assert_called_once_with("log_interaction", {
            "task_description": "(auto-logged)",
            "query": "find recipes",
            "summary": "Here are your recipes.",
        })

    @pytest.mark.anyio
    async def test_noop_when_no_tools_called(self):
        """No auto-log for conversation-only turns (no tool calls)."""
        mock_session = AsyncMock()
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        await ensure_interaction_logged(
            mock_session, messages, turn_start=1,
            user_query="hello", response="Hi there!",
        )
        mock_session.call_tool.assert_not_called()

    @pytest.mark.anyio
    async def test_logs_error_on_failed_auto_log(self, caplog):
        """Logs error when auto-log MCP call fails."""
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(
            isError=True, content=[MagicMock(text="write failed")]
        )
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "query"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "search_vault", "arguments": "{}"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
            {"role": "assistant", "content": "Done."},
        ]
        with caplog.at_level(logging.ERROR, logger="agent"):
            await ensure_interaction_logged(
                mock_session, messages, turn_start=1,
                user_query="query", response="Done.",
            )
        assert any("Auto-log failed" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_truncates_long_response(self):
        """Auto-log truncates response summary to 2000 chars."""
        mock_session = AsyncMock()
        mock_session.call_tool.return_value = MagicMock(
            isError=False, content=[MagicMock(text='{"success": true}')]
        )
        long_response = "x" * 5000
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "query"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"success": true}'},
            {"role": "assistant", "content": long_response},
        ]
        await ensure_interaction_logged(
            mock_session, messages, turn_start=1,
            user_query="query", response=long_response,
        )
        call_args = mock_session.call_tool.call_args[0][1]
        assert len(call_args["summary"]) == 2000


@pytest.mark.anyio
async def test_confirmation_preview_data_returned():
    """_process_tool_calls returns preview data for caller to emit after response."""
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_batch"
    mock_tool_call.function.name = "batch_update_frontmatter"
    mock_tool_call.function.arguments = '{"field": "status", "value": "done"}'

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "Show the file list to the user and call again with confirm=true to proceed.",
        "preview_message": "This will set 'status' = 'done' on 7 files.",
        "files": ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "g.md"],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}]

    from src.agent import _process_tool_calls
    _, confirmation_required, preview_data = await _process_tool_calls(
        [mock_tool_call], mock_session, messages, {}, 0, None,
    )

    assert confirmation_required is True
    assert preview_data is not None
    assert preview_data["tool"] == "batch_update_frontmatter"
    assert preview_data["message"] == "This will set 'status' = 'done' on 7 files."
    assert preview_data["files"] == ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "g.md"]
