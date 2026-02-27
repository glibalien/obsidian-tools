"""Tests for find_notes unified discovery tool."""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture
def dated_vault(temp_vault):
    """Create vault files with known modification times."""
    old_file = temp_vault / "old-note.md"
    old_file.write_text("---\ntags: archive\n---\nOld content")
    old_time = datetime(2025, 1, 15).timestamp()
    os.utime(old_file, (old_time, old_time))

    recent_file = temp_vault / "recent-note.md"
    recent_file.write_text("---\ntags: active\n---\nRecent content")
    recent_time = datetime(2025, 6, 15).timestamp()
    os.utime(recent_file, (recent_time, recent_time))

    future_file = temp_vault / "future-note.md"
    future_file.write_text("---\ntags: draft\n---\nFuture content")
    future_time = datetime(2025, 12, 15).timestamp()
    os.utime(future_file, (future_time, future_time))

    return temp_vault


class TestFindMatchingFilesDateFilter:
    """Tests for date filtering in _find_matching_files."""

    def test_date_filter_modified(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2025, 6, 1)
        end = datetime(2025, 6, 30)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 1
        assert "recent-note.md" in results[0]

    def test_date_filter_with_frontmatter(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2025, 1, 1)
        end = datetime(2025, 12, 31)
        results = _find_matching_files(
            "tags", "active", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 1
        assert "recent-note.md" in results[0]

    def test_date_filter_no_matches(self, dated_vault, vault_config):
        from tools.frontmatter import _find_matching_files

        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_end=end, date_type="modified",
        )
        assert len(results) == 0

    def test_date_filter_start_only(self, dated_vault, vault_config):
        """date_start without date_end filters from start onwards."""
        from tools.frontmatter import _find_matching_files

        start = datetime(2025, 6, 1)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_type="modified",
        )
        names = [Path(r).name for r in results]
        assert "recent-note.md" in names
        assert "future-note.md" in names
        assert "old-note.md" not in names

    def test_date_filter_end_only(self, dated_vault, vault_config):
        """date_end without date_start filters up to end."""
        from tools.frontmatter import _find_matching_files

        end = datetime(2025, 6, 30)
        results = _find_matching_files(
            None, "", "contains", [],
            date_end=end, date_type="modified",
        )
        names = [Path(r).name for r in results]
        assert "old-note.md" in names
        assert "recent-note.md" in names
        assert "future-note.md" not in names

    def test_date_filter_created_type(self, temp_vault, vault_config):
        """date_type="created" with no frontmatter filters uses on-demand parse."""
        from tools.frontmatter import _find_matching_files

        note = temp_vault / "dated.md"
        note.write_text("---\nDate: 2025-03-15\n---\nContent")

        start = datetime(2025, 3, 1)
        end = datetime(2025, 3, 31)
        results = _find_matching_files(
            None, "", "contains", [],
            date_start=start, date_end=end, date_type="created",
        )
        assert len(results) == 1
        assert "dated.md" in results[0]


class TestGetFileDate:
    """Tests for the _get_file_date helper."""

    def test_modified_date(self, dated_vault, vault_config):
        from tools.frontmatter import _get_file_date

        recent = dated_vault / "recent-note.md"
        result = _get_file_date(recent, "modified")
        assert result is not None
        assert result.year == 2025
        assert result.month == 6

    def test_created_date_from_frontmatter(self, temp_vault, vault_config):
        from tools.frontmatter import _get_file_date

        # note1.md has Date: 2024-01-15 in frontmatter
        note1 = temp_vault / "note1.md"
        result = _get_file_date(note1, "created")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_created_date_no_frontmatter(self, dated_vault, vault_config):
        """Falls back to filesystem creation time when no Date field."""
        from tools.frontmatter import _get_file_date

        recent = dated_vault / "recent-note.md"
        result = _get_file_date(recent, "created")
        # Should return something (filesystem fallback), not None
        assert result is not None
