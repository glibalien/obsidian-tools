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


class TestFindNotesVaultScan:
    """Tests for find_notes without semantic query (pure vault scan)."""

    def test_folder_only(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "scandir").mkdir(exist_ok=True)
        (temp_vault / "scandir" / "p1.md").write_text(
            "---\nstatus: active\n---\nProject 1"
        )
        (temp_vault / "scandir" / "p2.md").write_text(
            "---\nstatus: done\n---\nProject 2"
        )

        result = json.loads(find_notes(folder="scandir"))
        assert result["success"]
        assert len(result["results"]) == 2
        assert result["total"] == 2

    def test_frontmatter_only(self, temp_vault, vault_config):
        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        (temp_vault / "a.md").write_text("---\nmystatus: unique123\n---\nA")
        (temp_vault / "b.md").write_text("---\nmystatus: done\n---\nB")

        result = json.loads(
            find_notes(
                frontmatter=[FilterCondition(field="mystatus", value="unique123")],
            )
        )
        assert result["success"]
        assert result["total"] == 1
        assert "a.md" in result["results"][0]

    def test_date_only(self, dated_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(
            find_notes(
                date_start="2025-06-01",
                date_end="2025-06-30",
            )
        )
        assert result["success"]
        assert result["total"] == 1
        assert "recent-note.md" in result["results"][0]

    def test_folder_plus_frontmatter_plus_date(self, dated_vault, vault_config):
        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        result = json.loads(
            find_notes(
                folder=".",
                recursive=True,
                frontmatter=[FilterCondition(field="tags", value="active")],
                date_start="2025-01-01",
                date_end="2025-12-31",
            )
        )
        assert result["success"]
        assert result["total"] == 1
        assert "recent-note.md" in result["results"][0]

    def test_include_fields(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "note.md").write_text(
            "---\nstatus: active\ntags: [test]\n---\nContent"
        )

        result = json.loads(
            find_notes(
                folder=".",
                include_fields=["status", "tags"],
            )
        )
        assert result["success"]
        # Find our specific note among any vault files
        r = [
            x
            for x in result["results"]
            if isinstance(x, dict) and x.get("path", "").endswith("note.md")
        ][0]
        assert r["status"] == "active"

    def test_sort_by_name(self, temp_vault, vault_config):
        from tools.search import find_notes

        (temp_vault / "beta.md").write_text("B")
        (temp_vault / "alpha.md").write_text("A")

        result = json.loads(find_notes(folder=".", sort="name"))
        paths = result["results"]
        assert paths == sorted(paths)

    def test_sort_by_modified(self, dated_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(
            find_notes(
                folder=".",
                recursive=True,
                sort="modified",
                date_start="2025-01-01",
                date_end="2025-12-31",
            )
        )
        assert result["success"]
        assert result["total"] >= 2

    def test_pagination(self, temp_vault, vault_config):
        from tools.search import find_notes

        for i in range(5):
            (temp_vault / f"note{i}.md").write_text(f"Note {i}")

        result = json.loads(find_notes(folder=".", n_results=2, offset=0))
        assert len(result["results"]) == 2
        assert result["total"] >= 5

        result2 = json.loads(find_notes(folder=".", n_results=2, offset=2))
        assert len(result2["results"]) == 2

    def test_no_filters_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes())
        assert not result["success"]

    def test_sort_relevance_without_query_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(folder=".", sort="relevance"))
        assert not result["success"]
        assert "relevance" in result["error"].lower()

    def test_invalid_sort_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(folder=".", sort="invalid"))
        assert not result["success"]

    def test_invalid_date_format_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(find_notes(date_start="not-a-date"))
        assert not result["success"]

    def test_date_start_after_end_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(
            find_notes(date_start="2025-12-01", date_end="2025-01-01")
        )
        assert not result["success"]

    def test_invalid_date_type_error(self, temp_vault, vault_config):
        from tools.search import find_notes

        result = json.loads(
            find_notes(date_start="2025-01-01", date_type="invalid")
        )
        assert not result["success"]

    def test_query_mode_dispatches(self, temp_vault, vault_config):
        """Verify that query mode is dispatched (no longer a placeholder)."""
        from unittest.mock import patch

        from tools.search import find_notes

        with patch("tools.search.search_results", return_value=[]):
            result = json.loads(find_notes(query="test query", folder="."))
            assert result["success"]
            assert result["total"] == 0


class TestFindNotesQueryMode:
    """Tests for find_notes with semantic query."""

    def test_query_only(self, temp_vault, vault_config):
        from unittest.mock import patch

        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "note.md", "content": "some content", "heading": "Section"},
            ]
            result = json.loads(find_notes(query="test query"))
            assert result["success"]
            assert len(result["results"]) == 1
            assert result["results"][0]["source"] == "note.md"
            mock_search.assert_called_once()

    def test_query_with_folder_filter(self, temp_vault, vault_config):
        from unittest.mock import patch

        from tools.search import find_notes

        (temp_vault / "projects").mkdir(exist_ok=True)
        (temp_vault / "projects" / "p1.md").write_text("Project content")
        (temp_vault / "other.md").write_text("Other content")

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "projects/p1.md", "content": "Project content", "heading": ""},
                {"source": "other.md", "content": "Other content", "heading": ""},
            ]
            result = json.loads(find_notes(query="content", folder="projects"))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "projects/p1.md" in sources
            assert "other.md" not in sources

    def test_query_with_frontmatter_filter(self, temp_vault, vault_config):
        from unittest.mock import patch

        from tools.frontmatter import FilterCondition
        from tools.search import find_notes

        (temp_vault / "active.md").write_text("---\nstatus: active\n---\nActive note")
        (temp_vault / "done.md").write_text("---\nstatus: done\n---\nDone note")

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "active.md", "content": "Active note", "heading": ""},
                {"source": "done.md", "content": "Done note", "heading": ""},
            ]
            result = json.loads(find_notes(
                query="note",
                frontmatter=[FilterCondition(field="status", value="active")],
            ))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "active.md" in sources
            assert "done.md" not in sources

    def test_query_with_date_filter(self, dated_vault, vault_config):
        from unittest.mock import patch

        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": "old-note.md", "content": "Old", "heading": ""},
                {"source": "recent-note.md", "content": "Recent", "heading": ""},
            ]
            result = json.loads(find_notes(
                query="content",
                date_start="2025-06-01",
                date_end="2025-06-30",
            ))
            assert result["success"]
            sources = [r["source"] for r in result["results"]]
            assert "recent-note.md" in sources
            assert "old-note.md" not in sources

    def test_query_mode_pagination(self, temp_vault, vault_config):
        from unittest.mock import patch

        from tools.search import find_notes

        with patch("tools.search.search_results") as mock_search:
            mock_search.return_value = [
                {"source": f"note{i}.md", "content": f"Content {i}", "heading": ""}
                for i in range(10)
            ]
            result = json.loads(find_notes(query="test", n_results=3, offset=0))
            assert len(result["results"]) == 3
            assert result["total"] == 10

            result2 = json.loads(find_notes(query="test", n_results=3, offset=3))
            assert len(result2["results"]) == 3

    def test_query_search_failure(self, temp_vault, vault_config):
        from unittest.mock import patch

        from tools.search import find_notes

        with patch("tools.search.search_results", side_effect=Exception("DB error")):
            result = json.loads(find_notes(query="test"))
            assert not result["success"]
            assert "Search failed" in result["error"]
