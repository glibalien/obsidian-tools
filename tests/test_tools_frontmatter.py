"""Tests for tools/frontmatter.py - batch_update_frontmatter and search_by_date_range."""

import json
import os
import time

import pytest

from tools.frontmatter import (
    batch_update_frontmatter,
    list_files_by_frontmatter,
    search_by_date_range,
)


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
        assert len(result["successes"]) == 2
        assert result["failures"] == []

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
        assert len(result["successes"]) == 2
        assert result["failures"] == []

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
        assert len(result["successes"]) == 1
        assert len(result["failures"]) == 1

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
        assert len(result["successes"]) == 1
        assert result["failures"] == []

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


class TestCompoundFiltering:
    """Tests for list_files_by_frontmatter compound filtering."""

    def test_compound_filter_two_fields(self, vault_config):
        """Should return files matching both the primary field and filter."""
        (vault_config / "task_open.md").write_text(
            "---\nproject: '[[MyProject]]'\nstatus: open\ncategory: task\n---\n# Open task\n"
        )
        (vault_config / "task_done.md").write_text(
            "---\nproject: '[[MyProject]]'\nstatus: done\ncategory: task\n---\n# Done task\n"
        )
        (vault_config / "task_other.md").write_text(
            "---\nproject: '[[OtherProject]]'\nstatus: open\ncategory: task\n---\n# Other\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project",
                value="MyProject",
                filters='[{"field": "status", "value": "open"}]',
            )
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert any("task_open.md" in p for p in result["results"])
        assert not any("task_done.md" in p for p in result["results"])
        assert not any("task_other.md" in p for p in result["results"])

    def test_compound_filter_no_match(self, vault_config):
        """Should return empty when conditions match individually but not together."""
        (vault_config / "a.md").write_text(
            "---\nproject: '[[X]]'\nstatus: done\n---\n"
        )
        (vault_config / "b.md").write_text(
            "---\nproject: '[[Y]]'\nstatus: open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project",
                value="X",
                filters='[{"field": "status", "value": "open"}]',
            )
        )
        assert result["success"] is True
        assert result["total"] == 0
        assert result["results"] == []

    def test_compound_filter_with_match_type_equals(self, vault_config):
        """Should respect match_type in filter conditions."""
        (vault_config / "exact.md").write_text(
            "---\ncategory: meeting\nstatus: open\n---\n"
        )
        (vault_config / "partial.md").write_text(
            "---\ncategory: meeting\nstatus: open-review\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category",
                value="meeting",
                match_type="equals",
                filters='[{"field": "status", "value": "open", "match_type": "equals"}]',
            )
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert any("exact.md" in p for p in result["results"])

    def test_filters_invalid_json(self, vault_config):
        """Should return error for invalid JSON filters."""
        result = json.loads(
            list_files_by_frontmatter(
                field="category",
                value="task",
                filters="not valid json",
            )
        )
        assert result["success"] is False
        assert "json" in result["error"].lower()

    def test_filters_missing_field_key(self, vault_config):
        """Should return error when filter dict lacks required keys."""
        result = json.loads(
            list_files_by_frontmatter(
                field="category",
                value="task",
                filters='[{"value": "open"}]',
            )
        )
        assert result["success"] is False
        assert "field" in result["error"]


    def test_compound_filter_native_list(self, vault_config):
        """Should accept native list-of-dicts filters (not only JSON strings)."""
        (vault_config / "native_filter.md").write_text(
            "---\nproject: '[[MyProject]]'\nstatus: open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project",
                value="MyProject",
                filters=[{"field": "status", "value": "open", "match_type": "equals"}],
            )
        )
        assert result["success"] is True
        assert result["total"] >= 1
        assert any("native_filter.md" in p for p in result["results"])

    def test_filters_empty_list(self, vault_config):
        """Empty filters list should work same as no filters."""
        result_no_filters = json.loads(
            list_files_by_frontmatter(field="tags", value="test")
        )
        result_empty = json.loads(
            list_files_by_frontmatter(field="tags", value="test", filters="[]")
        )
        assert result_no_filters["total"] == result_empty["total"]


class TestIncludeFields:
    """Tests for list_files_by_frontmatter include_fields parameter."""

    def test_include_fields_returns_values(self, vault_config):
        """Results should be dicts with path and requested field values."""
        (vault_config / "task1.md").write_text(
            "---\ncategory: task\nstatus: open\nscheduled: '2026-03-01'\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="task", match_type="equals",
                include_fields='["status", "scheduled"]',
            )
        )
        assert result["success"] is True
        assert result["total"] >= 1
        item = next(r for r in result["results"] if "task1.md" in r["path"])
        assert item["status"] == "open"
        assert item["scheduled"] == "2026-03-01"

    def test_include_fields_null_for_missing(self, vault_config):
        """Fields not in frontmatter should return null."""
        (vault_config / "sparse.md").write_text(
            "---\ncategory: task\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="task", match_type="equals",
                include_fields='["status", "nonexistent"]',
            )
        )
        assert result["success"] is True
        item = next(r for r in result["results"] if "sparse.md" in r["path"])
        assert item["status"] is None
        assert item["nonexistent"] is None

    def test_include_fields_with_compound_filter(self, vault_config):
        """include_fields should work alongside compound filters."""
        (vault_config / "match.md").write_text(
            "---\nproject: '[[P1]]'\nstatus: open\ncontext: work\n---\n"
        )
        (vault_config / "nomatch.md").write_text(
            "---\nproject: '[[P1]]'\nstatus: done\ncontext: personal\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="P1",
                filters='[{"field": "status", "value": "open"}]',
                include_fields='["context"]',
            )
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert result["results"][0]["context"] == "work"

    def test_include_fields_empty_list(self, vault_config):
        """Empty include_fields should return plain paths like omitting it."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="test",
                include_fields="[]",
            )
        )
        assert result["success"] is True
        if result["total"] > 0:
            assert isinstance(result["results"][0], str)


    def test_include_fields_native_list(self, vault_config):
        """Should accept native list include_fields for structured tool calls."""
        (vault_config / "task_native_inc.md").write_text(
            "---\ncategory: task\nstatus: open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="task", match_type="equals",
                include_fields=["status"],
            )
        )
        assert result["success"] is True
        item = next(r for r in result["results"] if "task_native_inc.md" in r["path"])
        assert item["status"] == "open"

    def test_include_fields_invalid_json(self, vault_config):
        """Bad JSON should return error."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="test",
                include_fields="not json",
            )
        )
        assert result["success"] is False
        assert "json" in result["error"].lower()

    def test_without_include_fields_returns_strings(self, vault_config):
        """Without include_fields, results should be plain path strings."""
        result = json.loads(
            list_files_by_frontmatter(field="tags", value="test")
        )
        assert result["success"] is True
        if result["total"] > 0:
            assert isinstance(result["results"][0], str)


class TestBatchConfirmationGate:
    """Tests for batch_update_frontmatter confirmation requirement."""

    def _create_files(self, vault_config, count):
        """Create N test files in the vault."""
        paths = []
        for i in range(count):
            name = f"batch_test_{i}.md"
            (vault_config / name).write_text(f"---\ntags: [test]\n---\n# Note {i}\n")
            paths.append(name)
        return paths

    def test_requires_confirmation_over_threshold(self, vault_config):
        """Should return preview when file count exceeds threshold."""
        paths = self._create_files(vault_config, 10)
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="status",
                value="archived",
                operation="set",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert "10 files" in result["message"]
        assert all("path" in item for item in result["files"])
        assert [item["path"] for item in result["files"]] == paths
        # Verify no files were actually modified
        for path in paths:
            content = (vault_config / path).read_text()
            assert "status" not in content

    def test_executes_with_confirm_true(self, vault_config):
        """Should execute when confirm=True even over threshold."""
        paths = self._create_files(vault_config, 10)
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="status",
                value="archived",
                operation="set",
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "10 succeeded" in result["message"]

    def test_executes_under_threshold_without_confirm(self, vault_config):
        """Should execute without confirm when file count is at or below threshold."""
        paths = self._create_files(vault_config, 3)
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="status",
                value="done",
                operation="set",
            )
        )
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "3 succeeded" in result["message"]

    def test_confirmation_preview_includes_operation_details(self, vault_config):
        """Preview message should describe the operation."""
        paths = self._create_files(vault_config, 8)
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="context",
                value="work",
                operation="set",
            )
        )
        assert result["confirmation_required"] is True
        assert "context" in result["message"]
        assert "work" in result["message"]

    def test_confirmation_not_required_for_remove(self, vault_config):
        """Remove operations over threshold should also require confirmation."""
        paths = self._create_files(vault_config, 10)
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="tags",
                operation="remove",
            )
        )
        assert result["confirmation_required"] is True
        assert "remove" in result["message"]


class TestQueryBasedBatchUpdate:
    """Tests for batch_update_frontmatter with query-based targeting."""

    def test_query_target_finds_and_updates(self, vault_config):
        """Should find files by target criteria and update them."""
        (vault_config / "t1.md").write_text(
            "---\nproject: '[[Proj]]'\ncategory: task\n---\n"
        )
        (vault_config / "t2.md").write_text(
            "---\nproject: '[[Proj]]'\ncategory: task\n---\n"
        )
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="Proj",
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "2 succeeded" in result["message"]

        # Verify files were updated
        import re
        import yaml
        for name in ("t1.md", "t2.md"):
            content = (vault_config / name).read_text()
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            fm = yaml.safe_load(match.group(1))
            assert fm["context"] == "work"

    def test_query_target_requires_confirmation(self, vault_config):
        """Without confirm, should return preview with matched paths."""
        (vault_config / "qt1.md").write_text(
            "---\nproject: '[[QP]]'\n---\n"
        )
        result = json.loads(
            batch_update_frontmatter(
                field="status", value="archived", operation="set",
                target_field="project", target_value="QP",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert any("qt1.md" in f["path"] for f in result["files"])

    def test_query_target_with_filters(self, vault_config):
        """Compound targeting should narrow results."""
        (vault_config / "open.md").write_text(
            "---\nproject: '[[FP]]'\nstatus: open\n---\n"
        )
        (vault_config / "done.md").write_text(
            "---\nproject: '[[FP]]'\nstatus: done\n---\n"
        )
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="FP",
                target_filters='[{"field": "status", "value": "open"}]',
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "1 succeeded" in result["message"]
        assert len(result["successes"]) == 1
        assert result["failures"] == []


    def test_query_target_with_native_filter_list(self, vault_config):
        """Query targeting should accept native list target_filters."""
        (vault_config / "open_native.md").write_text(
            "---\nproject: '[[NP]]'\nstatus: open\n---\n"
        )
        (vault_config / "done_native.md").write_text(
            "---\nproject: '[[NP]]'\nstatus: done\n---\n"
        )
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="NP",
                target_filters=[{"field": "status", "value": "open", "match_type": "equals"}],
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "1 succeeded" in result["message"]

    def test_query_target_no_matches(self, vault_config):
        """Should return appropriate message when no files match."""
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="NonexistentProject999",
            )
        )
        assert result["success"] is True
        assert result["total"] == 0

    def test_paths_and_target_exclusive(self, vault_config):
        """Providing both paths and target_field should error."""
        result = json.loads(
            batch_update_frontmatter(
                field="status", value="done", operation="set",
                paths=["note1.md"],
                target_field="project", target_value="X",
            )
        )
        assert result["success"] is False
        assert "either" in result["error"].lower()


class TestCaseInsensitiveMatching:
    """Tests for case-insensitive frontmatter matching."""

    def test_contains_case_insensitive(self, vault_config):
        """Contains matching should be case-insensitive."""
        (vault_config / "ci1.md").write_text(
            "---\nstatus: Open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(field="status", value="open")
        )
        assert result["total"] >= 1
        assert any("ci1.md" in p for p in result["results"])

    def test_equals_case_insensitive(self, vault_config):
        """Equals matching should be case-insensitive."""
        (vault_config / "ci2.md").write_text(
            "---\ncategory: Meeting\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="meeting", match_type="equals"
            )
        )
        assert result["total"] >= 1
        assert any("ci2.md" in p for p in result["results"])

    def test_filter_case_insensitive(self, vault_config):
        """Compound filter values should also be case-insensitive."""
        (vault_config / "ci3.md").write_text(
            "---\nproject: '[[MyProj]]'\nstatus: OPEN\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="myproj",
                filters='[{"field": "status", "value": "open", "match_type": "equals"}]',
            )
        )
        assert result["total"] >= 1
        assert any("ci3.md" in p for p in result["results"])

    def test_list_values_case_insensitive(self, vault_config):
        """Contains in list values should be case-insensitive."""
        (vault_config / "ci4.md").write_text(
            "---\ntags:\n  - Project\n  - Work\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(field="tags", value="work")
        )
        assert result["total"] >= 1
        assert any("ci4.md" in p for p in result["results"])

    def test_field_name_case_insensitive(self, vault_config):
        """Field names should match case-insensitively (e.g. Project vs project)."""
        (vault_config / "ci_field.md").write_text(
            "---\nProject:\n- '[[MyProj]]'\nStatus: Open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="MyProj",
                filters='[{"field": "status", "value": "open"}]',
            )
        )
        assert result["total"] >= 1
        assert any("ci_field.md" in p for p in result["results"])

    def test_include_fields_case_insensitive_key(self, vault_config):
        """include_fields should find values even when field name case differs."""
        (vault_config / "ci_inc.md").write_text(
            "---\nProject:\n- '[[Proj]]'\nStatus: Open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="Proj",
                include_fields='["status"]',
            )
        )
        assert result["total"] >= 1
        item = next(r for r in result["results"] if "ci_inc.md" in r["path"])
        assert item["status"] == "Open"

    def test_non_string_field_value(self, vault_config):
        """Non-string field values (e.g. int) should be handled via str conversion."""
        (vault_config / "ci5.md").write_text(
            "---\npriority: 1\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="priority", value="1", match_type="equals"
            )
        )
        assert result["total"] >= 1
        assert any("ci5.md" in p for p in result["results"])


class TestWikilinkStripping:
    """Tests for wikilink bracket stripping in frontmatter matching."""

    def test_contains_strips_brackets_from_stored_value(self, vault_config):
        """'Agentic S2P' should match frontmatter containing '[[Agentic S2P]]'."""
        (vault_config / "wl1.md").write_text(
            "---\nProject:\n- '[[Agentic S2P]]'\nstatus: open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(field="project", value="Agentic S2P")
        )
        assert result["total"] >= 1
        assert any("wl1.md" in p for p in result["results"])

    def test_contains_strips_brackets_from_search_value(self, vault_config):
        """'[[Agentic S2P]]' search value should also match."""
        (vault_config / "wl2.md").write_text(
            "---\nProject:\n- '[[Agentic S2P]]'\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(field="project", value="[[Agentic S2P]]")
        )
        assert result["total"] >= 1
        assert any("wl2.md" in p for p in result["results"])

    def test_equals_with_wikilink_list(self, vault_config):
        """equals match_type should work with wikilinked list values."""
        (vault_config / "wl3.md").write_text(
            "---\nProject:\n- '[[Agentic S2P]]'\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="Agentic S2P", match_type="equals"
            )
        )
        assert result["total"] >= 1
        assert any("wl3.md" in p for p in result["results"])

    def test_equals_with_wikilink_string(self, vault_config):
        """equals should strip brackets from non-list string values too."""
        (vault_config / "wl4.md").write_text(
            "---\nproject: '[[Agentic S2P]]'\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="Agentic S2P", match_type="equals"
            )
        )
        assert result["total"] >= 1
        assert any("wl4.md" in p for p in result["results"])

    def test_aliased_wikilink(self, vault_config):
        """Aliased wikilinks like '[[Foo|Bar]]' should match on 'Foo'."""
        (vault_config / "wl5.md").write_text(
            "---\nproject: '[[Agentic S2P|S2P Project]]'\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(field="project", value="Agentic S2P")
        )
        assert result["total"] >= 1
        assert any("wl5.md" in p for p in result["results"])

    def test_compound_filter_strips_wikilinks(self, vault_config):
        """Wikilink stripping should also apply to compound filter values."""
        (vault_config / "wl6.md").write_text(
            "---\nProject:\n- '[[Agentic S2P]]'\nstatus: open\ncategory: task\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="task",
                filters='[{"field": "project", "value": "Agentic S2P"}]',
            )
        )
        assert result["total"] >= 1
        assert any("wl6.md" in p for p in result["results"])


class TestResultMessage:
    """Tests for explicit count message in results."""

    def test_found_message_present(self, vault_config):
        """Non-empty results should include a 'Found N' message."""
        result = json.loads(
            list_files_by_frontmatter(field="tags", value="project")
        )
        assert result["success"] is True
        assert result["total"] >= 1
        assert "Found" in result["message"]
        assert str(result["total"]) in result["message"]

    def test_no_results_message(self, vault_config):
        """Empty results should include a 'No files found' message."""
        result = json.loads(
            list_files_by_frontmatter(field="nonexistent", value="xyz")
        )
        assert result["total"] == 0
        assert "No files found" in result["message"]


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
