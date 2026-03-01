"""Tests for tools/research.py - topic extraction."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.research import _extract_topics


class TestExtractTopics:
    """Tests for _extract_topics function."""

    def test_happy_path(self):
        """Should extract topics from content and return list of dicts."""
        topics = [
            {"topic": "Project planning", "context": "Q1 roadmap discussion", "type": "theme"},
            {"topic": "Budget review", "context": "Annual budget cycle", "type": "task"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some note content about projects.")

        assert len(result) == 2
        assert result[0]["topic"] == "Project planning"
        assert result[1]["type"] == "task"
        assert result[0]["context"] == "Q1 roadmap discussion"

        # Verify the LLM was called with the correct structure
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert any(m["role"] == "system" for m in messages)
        assert any(m["role"] == "user" for m in messages)

    def test_focus_included_in_prompt(self):
        """Should prepend focus guidance to the user message."""
        topics = [
            {"topic": "Timeline", "context": "Delivery dates", "type": "theme"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _extract_topics(mock_client, "Some content", focus="project timeline")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Focus especially on: project timeline" in user_msg["content"]

    def test_topic_cap(self):
        """Should truncate topics to MAX_RESEARCH_TOPICS."""
        # Generate more topics than the cap
        topics = [
            {"topic": f"Topic {i}", "context": f"Context {i}", "type": "theme"}
            for i in range(20)
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("tools.research.MAX_RESEARCH_TOPICS", 5):
            result = _extract_topics(mock_client, "Lots of content.")

        assert len(result) == 5
        # Should keep the first 5
        assert result[0]["topic"] == "Topic 0"
        assert result[4]["topic"] == "Topic 4"

    def test_llm_returns_none(self):
        """Should return empty list when LLM response content is None."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_llm_returns_invalid_json(self):
        """Should return empty list when LLM response is not valid JSON."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Here are some topics: blah blah"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_llm_exception_returns_empty_list(self):
        """Should return empty list when LLM call raises an exception."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_no_focus_omits_focus_line(self):
        """Without focus, user message should not contain focus guidance."""
        topics = [
            {"topic": "General", "context": "Overview", "type": "theme"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _extract_topics(mock_client, "Some content")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Focus especially on:" not in user_msg["content"]


class TestResearchNote:
    """Tests for research_note placeholder."""

    def test_raises_not_implemented(self):
        """research_note should raise NotImplementedError."""
        from tools.research import research_note

        with pytest.raises(NotImplementedError):
            research_note("some/path.md")
