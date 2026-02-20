"""Tests for API context handling."""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import format_context_prefix


def test_format_context_prefix_with_file():
    """Context prefix includes the file path."""
    result = format_context_prefix("Projects/Marketing.md")
    assert result == '[The user has a file open. Its exact path is: "Projects/Marketing.md"]\n\n'


def test_format_context_prefix_with_nested_path():
    """Context prefix works with deeply nested paths."""
    result = format_context_prefix("Work/Projects/2024/Q1/report.md")
    assert result == '[The user has a file open. Its exact path is: "Work/Projects/2024/Q1/report.md"]\n\n'


def test_format_context_prefix_without_file():
    """No context prefix when active_file is None."""
    result = format_context_prefix(None)
    assert result == ""


def test_format_context_prefix_empty_string():
    """Empty string is treated as no file (falsy)."""
    result = format_context_prefix("")
    assert result == ""
