"""Tests for agent turn behavior: iteration cap and tool result truncation."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent import agent_turn, truncate_tool_result, MAX_TOOL_RESULT_CHARS
from services.compaction import compact_tool_messages


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
    # Last round's tool results stay uncompacted so the LLM can read them
    assert "_compacted" not in tool_msgs[0]
    assert "[truncated" in tool_msgs[0]["content"]
    assert "call_1" in tool_msgs[0]["content"]
    assert str(MAX_TOOL_RESULT_CHARS) in tool_msgs[0]["content"]


@pytest.mark.anyio
async def test_agent_turn_get_continuation():
    """Agent handles get_continuation for truncated results."""
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

    # MCP was called only once (transcribe_audio), NOT for get_continuation
    mock_session.call_tool.assert_called_once_with("transcribe_audio", {"path": "note.md"})

    # Verify tool messages
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    # First: truncated with marker
    assert "call_transcribe" in tool_msgs[0]["content"]
    # Second: continuation chunk contains B's
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
    assert "error" in parsed


def test_handle_get_continuation_valid():
    """Returns correct chunk from cache."""
    from agent import _handle_get_continuation, MAX_TOOL_RESULT_CHARS
    cache = {"call_1": "A" * 10000}
    result = _handle_get_continuation(cache, {"tool_call_id": "call_1", "offset": MAX_TOOL_RESULT_CHARS})
    assert result.startswith("A")
    assert "remaining" in result  # still has more (10000 - 4000 - 4000 = 2000 remaining)


def test_handle_get_continuation_final_chunk():
    """Final chunk has no truncation marker."""
    from agent import _handle_get_continuation, MAX_TOOL_RESULT_CHARS
    cache = {"call_1": "A" * 5000}
    result = _handle_get_continuation(cache, {"tool_call_id": "call_1", "offset": MAX_TOOL_RESULT_CHARS})
    assert "truncated" not in result
    assert len(result) == 1000  # 5000 - 4000


def test_handle_get_continuation_missing_id():
    """Returns error for unknown tool_call_id."""
    from agent import _handle_get_continuation
    result = _handle_get_continuation({}, {"tool_call_id": "bad"})
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
