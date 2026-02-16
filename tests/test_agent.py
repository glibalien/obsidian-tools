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
