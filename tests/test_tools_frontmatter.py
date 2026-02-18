"""Tests for tools/frontmatter.py - batch_update_frontmatter and search_by_date_range."""

import json
import os
import time

import pytest

from tools.frontmatter import batch_update_frontmatter, search_by_date_range


class TestBatchUpdateFrontmatter:
    """Tests for batch_update_frontmatter tool."""

    def test_batch_set_field(self, vault_config):
        """Should set a field on all specified files."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md", "note2.md"],
                field="status",
                value="archived",
                operation="set",
            )
        )
        assert result["success"] is True
        assert "2 succeeded" in result["message"]
        assert "0 failed" in result["message"]

        # Verify both files were actually updated
        import yaml

        for filename in ("note1.md", "note2.md"):
            content = (vault_config / filename).read_text()
            match = __import__("re").match(r"^---\n(.*?)\n---\n", content, __import__("re").DOTALL)
            assert match, f"{filename} should have frontmatter after update"
            fm = yaml.safe_load(match.group(1))
            assert fm["status"] == "archived", f"{filename} status should be 'archived'"

    def test_batch_append_to_list(self, vault_config):
        """Should append a tag to multiple files that already have a tags list."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md", "projects/project1.md"],
                field="tags",
                value="archived",
                operation="append",
            )
        )
        assert result["success"] is True
        assert "2 succeeded" in result["message"]
        assert "0 failed" in result["message"]

        # Verify tag was appended on both files
        import re
        import yaml

        for path in ("note1.md", "projects/project1.md"):
            content = (vault_config / path).read_text()
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            assert match
            fm = yaml.safe_load(match.group(1))
            assert "archived" in fm["tags"], f"{path} should have 'archived' tag"

    def test_batch_remove_field(self, vault_config):
        """Should remove a field from multiple files."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md", "note2.md"],
                field="tags",
                operation="remove",
            )
        )
        assert result["success"] is True
        assert "2 succeeded" in result["message"]
        assert "0 failed" in result["message"]

        # Verify tags field was removed
        import re
        import yaml

        for filename in ("note1.md", "note2.md"):
            content = (vault_config / filename).read_text()
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            assert match
            fm = yaml.safe_load(match.group(1))
            assert "tags" not in fm, f"{filename} should no longer have 'tags' field"

    def test_batch_empty_paths(self, vault_config):
        """Should return error when paths list is empty."""
        result = json.loads(
            batch_update_frontmatter(
                paths=[],
                field="status",
                value="archived",
                operation="set",
            )
        )
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_batch_invalid_operation(self, vault_config):
        """Should return error for an unrecognized operation."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md"],
                field="status",
                value="done",
                operation="delete",
            )
        )
        assert result["success"] is False
        assert "operation" in result["error"].lower()
        assert "delete" in result["error"]

    def test_batch_set_without_value(self, vault_config):
        """Should return error when value is missing for 'set' operation."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md"],
                field="status",
                value=None,
                operation="set",
            )
        )
        assert result["success"] is False
        assert "value is required" in result["error"].lower()

    def test_batch_append_without_value(self, vault_config):
        """Should return error when value is missing for 'append' operation."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md"],
                field="tags",
                value=None,
                operation="append",
            )
        )
        assert result["success"] is False
        assert "value is required" in result["error"].lower()

    def test_batch_partial_failure(self, vault_config):
        """Should continue processing after individual failures and report both outcomes."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md", "nonexistent.md"],
                field="status",
                value="archived",
                operation="set",
            )
        )
        assert result["success"] is True
        assert "1 succeeded" in result["message"]
        assert "1 failed" in result["message"]
        # Succeeded and failed sections should both appear in the summary
        assert "Succeeded:" in result["message"]
        assert "Failed:" in result["message"]

    def test_batch_json_value(self, vault_config):
        """Should parse a JSON list string and store it as a list in frontmatter."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note2.md"],
                field="tags",
                value='["meeting", "important", "q1"]',
                operation="set",
            )
        )
        assert result["success"] is True
        assert "1 succeeded" in result["message"]

        import re
        import yaml

        content = (vault_config / "note2.md").read_text()
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match
        fm = yaml.safe_load(match.group(1))
        assert isinstance(fm["tags"], list)
        assert "meeting" in fm["tags"]
        assert "important" in fm["tags"]
        assert "q1" in fm["tags"]


class TestSearchByDateRange:
    """Tests for search_by_date_range tool."""

    def test_date_range_modified(self, vault_config):
        """Should find files whose mtime falls within the specified date range."""
        # Set known mtime on note1.md and note2.md
        target_ts = time.mktime(time.strptime("2023-06-15", "%Y-%m-%d"))
        os.utime(vault_config / "note1.md", (target_ts, target_ts))
        os.utime(vault_config / "note2.md", (target_ts, target_ts))

        result = json.loads(
            search_by_date_range(
                start_date="2023-06-01",
                end_date="2023-06-30",
                date_type="modified",
            )
        )
        assert result["success"] is True
        assert result["total"] >= 2
        paths = result["results"]
        assert any("note1.md" in p for p in paths)
        assert any("note2.md" in p for p in paths)

    def test_date_range_modified_excludes_others(self, vault_config):
        """Files outside the date range should not appear in results."""
        # Set note1 inside the range and note2 outside
        inside_ts = time.mktime(time.strptime("2023-06-15", "%Y-%m-%d"))
        outside_ts = time.mktime(time.strptime("2022-01-01", "%Y-%m-%d"))
        os.utime(vault_config / "note1.md", (inside_ts, inside_ts))
        os.utime(vault_config / "note2.md", (outside_ts, outside_ts))

        result = json.loads(
            search_by_date_range(
                start_date="2023-06-01",
                end_date="2023-06-30",
                date_type="modified",
            )
        )
        assert result["success"] is True
        paths = result["results"]
        assert any("note1.md" in p for p in paths)
        assert not any("note2.md" in p for p in paths)

    def test_date_range_created_frontmatter(self, vault_config):
        """Should find files by frontmatter Date field when date_type='created'."""
        # note1.md has Date: 2024-01-15 in its frontmatter
        result = json.loads(
            search_by_date_range(
                start_date="2024-01-01",
                end_date="2024-01-31",
                date_type="created",
            )
        )
        assert result["success"] is True
        assert result["total"] >= 1
        paths = result["results"]
        assert any("note1.md" in p for p in paths)

    def test_date_range_created_wikilink_date(self, vault_config):
        """Should handle frontmatter Date in wikilink format [[YYYY-MM-DD]]."""
        (vault_config / "wikilink_date.md").write_text(
            "---\nDate: '[[2024-03-10]]'\n---\n\n# Wikilink Date Note\n"
        )
        result = json.loads(
            search_by_date_range(
                start_date="2024-03-01",
                end_date="2024-03-31",
                date_type="created",
            )
        )
        assert result["success"] is True
        assert result["total"] >= 1
        paths = result["results"]
        assert any("wikilink_date.md" in p for p in paths)

    def test_date_range_no_matches(self, vault_config):
        """Should return empty results when no files fall within the date range."""
        result = json.loads(
            search_by_date_range(
                start_date="1990-01-01",
                end_date="1990-01-31",
                date_type="modified",
            )
        )
        assert result["success"] is True
        assert result["results"] == []
        assert result["total"] == 0
        assert "No files found" in result["message"]

    def test_date_range_invalid_start(self, vault_config):
        """Should return error for a malformed start_date."""
        result = json.loads(
            search_by_date_range(
                start_date="15-01-2024",
                end_date="2024-01-31",
            )
        )
        assert result["success"] is False
        assert "start_date" in result["error"]

    def test_date_range_invalid_end(self, vault_config):
        """Should return error for a malformed end_date."""
        result = json.loads(
            search_by_date_range(
                start_date="2024-01-01",
                end_date="January 31 2024",
            )
        )
        assert result["success"] is False
        assert "end_date" in result["error"]

    def test_date_range_start_after_end(self, vault_config):
        """Should return error when start_date is later than end_date."""
        result = json.loads(
            search_by_date_range(
                start_date="2024-12-31",
                end_date="2024-01-01",
            )
        )
        assert result["success"] is False
        assert "after" in result["error"].lower()

    def test_date_range_invalid_date_type(self, vault_config):
        """Should return error for an unrecognized date_type."""
        result = json.loads(
            search_by_date_range(
                start_date="2024-01-01",
                end_date="2024-12-31",
                date_type="accessed",
            )
        )
        assert result["success"] is False
        assert "date_type" in result["error"]
        assert "accessed" in result["error"]

    def test_date_range_pagination(self, vault_config):
        """Should respect limit and offset and report correct total count."""
        # Set all markdown files to a known mtime inside our range
        target_ts = time.mktime(time.strptime("2025-05-20", "%Y-%m-%d"))
        for md_file in vault_config.rglob("*.md"):
            os.utime(md_file, (target_ts, target_ts))

        # First: get total without pagination
        full_result = json.loads(
            search_by_date_range(
                start_date="2025-05-01",
                end_date="2025-05-31",
                date_type="modified",
            )
        )
        assert full_result["success"] is True
        total = full_result["total"]
        assert total >= 3  # at least note1, note2, note3

        # Paginate: first page of 2
        page1 = json.loads(
            search_by_date_range(
                start_date="2025-05-01",
                end_date="2025-05-31",
                date_type="modified",
                limit=2,
                offset=0,
            )
        )
        assert page1["success"] is True
        assert page1["total"] == total
        assert len(page1["results"]) == 2

        # Second page: next 2 (or fewer if total <= 3)
        page2 = json.loads(
            search_by_date_range(
                start_date="2025-05-01",
                end_date="2025-05-31",
                date_type="modified",
                limit=2,
                offset=2,
            )
        )
        assert page2["success"] is True
        assert page2["total"] == total
        # Results across pages should be disjoint
        assert not set(page1["results"]) & set(page2["results"])
        # Combined pages should cover all results
        assert set(page1["results"]) | set(page2["results"]) == set(full_result["results"][:4])
