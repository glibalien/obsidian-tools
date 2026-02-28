"""Tests for tools/summary.py - file summarization."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.summary import summarize_file


class TestSummarizeFile:
    """Tests for summarize_file tool."""

    def test_happy_path(self, vault_config):
        """Should read file, call LLM, append summary, return confirmation."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "This note covers project planning.\n\n"
            "### Action Items\n\n"
            "- [ ] Review budget\n"
            "- [ ] Schedule follow-up"
        )

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            result = json.loads(summarize_file("note1.md"))

        assert result["success"] is True
        assert result["path"]
        assert result["summary_length"] > 0
        assert "project planning" in result["preview"]

        content = (vault_config / "note1.md").read_text()
        assert "## Summary" in content
        assert "### Action Items" in content
        assert "- [ ] Review budget" in content

    def test_with_focus(self, vault_config):
        """Should include focus in the LLM prompt."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Focused summary."

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            summarize_file("note1.md", focus="project timeline")

            # Check the user message sent to LLM contains focus
            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"]
            user_msg = next(m for m in messages if m["role"] == "user")
            assert "project timeline" in user_msg["content"]

    def test_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(summarize_file("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_llm_failure(self, vault_config):
        """Should return error on LLM failure, file unchanged."""
        original = (vault_config / "note1.md").read_text()

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = Exception("API error")

            result = json.loads(summarize_file("note1.md"))

        assert result["success"] is False
        assert "API error" in result["error"]
        assert (vault_config / "note1.md").read_text() == original

    def test_no_api_key(self, vault_config):
        """Should return error when FIREWORKS_API_KEY is not set."""
        with patch("os.getenv", return_value=None):
            result = json.loads(summarize_file("note1.md"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    def test_large_content_truncated(self, vault_config):
        """Should slice content exceeding MAX_SUMMARIZE_CHARS before sending."""
        # Create a file with content larger than limit
        large_content = "# Big Note\n\n" + "x" * 300_000
        (vault_config / "large.md").write_text(large_content)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary of large file."

        with patch("tools.summary.MAX_SUMMARIZE_CHARS", 1000):
            with patch("tools.summary.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client
                mock_client.chat.completions.create.return_value = mock_response

                result = json.loads(summarize_file("large.md"))

        assert result["success"] is True
        # Verify the content sent to LLM was actually sliced
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "truncated" in user_msg["content"].lower()
        # Full user message should be much shorter than the 300K original
        assert len(user_msg["content"]) < 2000

    def test_llm_returns_none(self, vault_config):
        """Should return error when LLM response content is None."""
        original = (vault_config / "note1.md").read_text()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            result = json.loads(summarize_file("note1.md"))

        assert result["success"] is False
        assert "empty" in result["error"].lower()
        assert (vault_config / "note1.md").read_text() == original

    def test_binary_file_rejected(self, vault_config):
        """Should reject binary files to prevent corruption."""
        attachments = vault_config / "Attachments"
        (attachments / "recording.m4a").write_bytes(b"fake audio")

        result = json.loads(summarize_file("Attachments/recording.m4a"))
        assert result["success"] is False
        assert "markdown/text" in result["error"].lower()

    def test_pdf_file_rejected(self, vault_config):
        """Should reject PDFs and other non-text files not in blocklist."""
        (vault_config / "report.pdf").write_bytes(b"%PDF-1.4 fake")

        result = json.loads(summarize_file("report.pdf"))
        assert result["success"] is False
        assert ".pdf" in result["error"]

    def test_append_preserves_existing(self, vault_config):
        """Appending a second summary doesn't corrupt existing content."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "First summary."

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response
            summarize_file("note1.md")

        mock_response2 = MagicMock()
        mock_response2.choices = [MagicMock()]
        mock_response2.choices[0].message.content = "Second summary."

        with patch("tools.summary.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response2
            result = json.loads(summarize_file("note1.md"))

        assert result["success"] is True
        content = (vault_config / "note1.md").read_text()
        assert "First summary." in content
        assert "Second summary." in content
