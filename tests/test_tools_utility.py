"""Tests for tools/utility.py - utility tools (logging, date)."""

import json
import re
from datetime import datetime
from unittest.mock import patch

import pytest

from tools.utility import get_current_date, log_interaction


class TestGetCurrentDate:
    """Tests for get_current_date tool."""

    def test_get_current_date_format(self):
        """Should return success with date in YYYY-MM-DD format."""
        result = json.loads(get_current_date())
        assert result["success"] is True
        assert "date" in result
        # Validate format with regex: YYYY-MM-DD
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["date"])

    def test_get_current_date_matches_today(self):
        """Should return exact current date when mocked."""
        mock_date = datetime(2025, 6, 15, 14, 30, 0)
        with patch("tools.utility.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_date
            result = json.loads(get_current_date())
            assert result["success"] is True
            assert result["date"] == "2025-06-15"

    def test_get_current_date_different_dates(self):
        """Should correctly format various dates."""
        test_cases = [
            (datetime(2024, 1, 1), "2024-01-01"),
            (datetime(2025, 12, 31), "2025-12-31"),
            (datetime(2026, 2, 17), "2026-02-17"),
        ]
        for mock_date, expected in test_cases:
            with patch("tools.utility.datetime") as mock_datetime:
                mock_datetime.now.return_value = mock_date
                result = json.loads(get_current_date())
                assert result["success"] is True
                assert result["date"] == expected


class TestLogInteraction:
    """Tests for log_interaction tool."""

    def test_log_interaction_success(self):
        """Should return success message with logged path."""
        with patch("tools.utility.log_chat") as mock_log_chat:
            mock_log_chat.return_value = "Daily Notes/2025-06-15.md"
            result = json.loads(
                log_interaction(
                    task_description="Test task",
                    query="Test query",
                    summary="Test summary",
                )
            )
            assert result["success"] is True
            assert "Logged to" in result["message"]
            assert "Daily Notes/2025-06-15.md" in result["message"]

    def test_log_interaction_with_files(self):
        """Should pass files list to log_chat."""
        with patch("tools.utility.log_chat") as mock_log_chat:
            mock_log_chat.return_value = "Daily Notes/2025-06-15.md"
            files = ["note1.md", "note2.md", "note3.md"]
            result = json.loads(
                log_interaction(
                    task_description="Test task",
                    query="Test query",
                    summary="Test summary",
                    files=files,
                )
            )
            assert result["success"] is True
            # Verify that log_chat was called with the files argument
            mock_log_chat.assert_called_once_with(
                "Test task", "Test query", "Test summary", files, None
            )

    def test_log_interaction_with_full_response(self):
        """Should pass full_response to log_chat."""
        with patch("tools.utility.log_chat") as mock_log_chat:
            mock_log_chat.return_value = "Daily Notes/2025-06-15.md"
            full_response = "This is the full conversational response."
            result = json.loads(
                log_interaction(
                    task_description="Test task",
                    query="Test query",
                    summary="n/a",
                    full_response=full_response,
                )
            )
            assert result["success"] is True
            # Verify that log_chat was called with the full_response argument
            mock_log_chat.assert_called_once_with(
                "Test task", "Test query", "n/a", None, full_response
            )

    def test_log_interaction_with_files_and_full_response(self):
        """Should pass both files and full_response to log_chat."""
        with patch("tools.utility.log_chat") as mock_log_chat:
            mock_log_chat.return_value = "Daily Notes/2025-06-15.md"
            files = ["file1.md", "file2.md"]
            full_response = "Complete response text."
            result = json.loads(
                log_interaction(
                    task_description="Research task",
                    query="Find related notes",
                    summary="n/a",
                    files=files,
                    full_response=full_response,
                )
            )
            assert result["success"] is True
            mock_log_chat.assert_called_once_with(
                "Research task", "Find related notes", "n/a", files, full_response
            )

    def test_log_interaction_error(self):
        """Should return error when log_chat raises an exception."""
        with patch("tools.utility.log_chat") as mock_log_chat:
            mock_log_chat.side_effect = ValueError("Vault path not found")
            result = json.loads(
                log_interaction(
                    task_description="Test task",
                    query="Test query",
                    summary="Test summary",
                )
            )
            assert result["success"] is False
            assert "Logging failed" in result["error"]
            assert "Vault path not found" in result["error"]

    def test_log_interaction_error_various_exceptions(self):
        """Should handle various exception types gracefully."""
        exceptions = [
            RuntimeError("Custom runtime error"),
            IOError("File I/O error"),
            Exception("Generic exception"),
        ]
        for exc in exceptions:
            with patch("tools.utility.log_chat") as mock_log_chat:
                mock_log_chat.side_effect = exc
                result = json.loads(
                    log_interaction(
                        task_description="Test",
                        query="Test",
                        summary="Test",
                    )
                )
                assert result["success"] is False
                assert "Logging failed" in result["error"]
