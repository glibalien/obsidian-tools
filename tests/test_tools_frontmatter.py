"""Tests for tools/frontmatter.py - batch_update_frontmatter and search_by_date_range."""

import json
import os
import time

import pytest

from services.vault import clear_pending_previews
from tools.frontmatter import (
    FilterCondition,
    batch_update_frontmatter,
    list_files_by_frontmatter,
    search_by_date_range,
    update_frontmatter,
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

    def test_batch_set_native_list_value(self, vault_config):
        """Should accept a native list value without JSON string encoding."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note2.md"],
                field="tags",
                value=["meeting", "important", "q2"],
                operation="set",
            )
        )
        assert result["success"] is True

        import re
        import yaml

        content = (vault_config / "note2.md").read_text()
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match
        fm = yaml.safe_load(match.group(1))
        assert fm["tags"] == ["meeting", "important", "q2"]

    def test_batch_set_native_dict_value(self, vault_config):
        """Should accept a native dict value without JSON string encoding."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md"],
                field="metadata",
                value={"owner": "alice", "priority": 2},
                operation="set",
            )
        )
        assert result["success"] is True

        import re
        import yaml

        content = (vault_config / "note1.md").read_text()
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match
        fm = yaml.safe_load(match.group(1))
        assert fm["metadata"] == {"owner": "alice", "priority": 2}

    def test_batch_set_native_bool_value(self, vault_config):
        """Should accept a native bool value without JSON string encoding."""
        result = json.loads(
            batch_update_frontmatter(
                paths=["note1.md"],
                field="published",
                value=True,
                operation="set",
            )
        )
        assert result["success"] is True

        import re
        import yaml

        content = (vault_config / "note1.md").read_text()
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert match
        fm = yaml.safe_load(match.group(1))
        assert fm["published"] is True


class TestUpdateFrontmatterValueNormalization:
    """Tests for update_frontmatter value normalization behavior."""

    def test_update_frontmatter_accepts_native_bool(self, monkeypatch):
        """Native boolean values should pass through unchanged."""
        captured = {}

        def fake_update(path, field, value, operation):
            captured["value"] = value
            return True, "ok"

        monkeypatch.setattr("tools.frontmatter.do_update_frontmatter", fake_update)

        result = json.loads(
            update_frontmatter(path="note1.md", field="published", value=False, operation="set")
        )
        assert result["success"] is True
        assert captured["value"] is False

    def test_update_frontmatter_legacy_json_string_list(self, monkeypatch):
        """Legacy JSON string containers should still be parsed."""
        captured = {}

        def fake_update(path, field, value, operation):
            captured["value"] = value
            return True, "ok"

        monkeypatch.setattr("tools.frontmatter.do_update_frontmatter", fake_update)

        result = json.loads(
            update_frontmatter(
                path="note1.md",
                field="tags",
                value='["a", "b"]',
                operation="set",
            )
        )
        assert result["success"] is True
        assert captured["value"] == ["a", "b"]

    def test_update_frontmatter_accepts_native_list(self, monkeypatch):
        """Native list values should pass through unchanged (no JSON string required)."""
        captured = {}

        def fake_update(path, field, value, operation):
            captured["value"] = value
            return True, "ok"

        monkeypatch.setattr("tools.frontmatter.do_update_frontmatter", fake_update)

        result = json.loads(
            update_frontmatter(
                path="note1.md",
                field="category",
                value=["project"],
                operation="set",
            )
        )
        assert result["success"] is True
        assert captured["value"] == ["project"]

    def test_update_frontmatter_plain_string_remains_string(self, monkeypatch):
        """Plain strings should not be treated as JSON."""
        captured = {}

        def fake_update(path, field, value, operation):
            captured["value"] = value
            return True, "ok"

        monkeypatch.setattr("tools.frontmatter.do_update_frontmatter", fake_update)

        result = json.loads(
            update_frontmatter(path="note1.md", field="status", value="archived", operation="set")
        )
        assert result["success"] is True
        assert captured["value"] == "archived"


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
                filters=[FilterCondition(field="status", value="open")],
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
                filters=[FilterCondition(field="status", value="open")],
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
                filters=[FilterCondition(field="status", value="open", match_type="equals")],
            )
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert any("exact.md" in p for p in result["results"])

    def test_filters_empty_list(self, vault_config):
        """Empty filters list should work same as no filters."""
        result_no_filters = json.loads(
            list_files_by_frontmatter(field="tags", value="test")
        )
        result_empty = json.loads(
            list_files_by_frontmatter(field="tags", value="test", filters=[])
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
                include_fields=["status", "scheduled"],
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
                include_fields=["status", "nonexistent"],
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
                filters=[FilterCondition(field="status", value="open")],
                include_fields=["context"],
            )
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert result["results"][0]["context"] == "work"

    def test_include_fields_empty_returns_strings(self, vault_config):
        """Empty include_fields should return plain paths like omitting it."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="test",
                include_fields=[],
            )
        )
        assert result["success"] is True
        if result["total"] > 0:
            assert isinstance(result["results"][0], str)

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
        clear_pending_previews()
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
        assert result["files"] == paths
        # Verify no files were actually modified
        for path in paths:
            content = (vault_config / path).read_text()
            assert "status" not in content

    def test_executes_with_confirm_true(self, vault_config):
        """Should execute when confirm=True even over threshold."""
        clear_pending_previews()
        paths = self._create_files(vault_config, 10)
        # First call: store preview
        batch_update_frontmatter(
            paths=paths,
            field="status",
            value="archived",
            operation="set",
        )
        # Second call: confirm execution
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
        clear_pending_previews()
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
        clear_pending_previews()
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

    def test_confirm_true_without_preview_returns_preview(self, vault_config):
        """Passing confirm=True on first call should still return preview."""
        clear_pending_previews()
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
        assert result["confirmation_required"] is True
        # No files should be modified
        for path in paths:
            content = (vault_config / path).read_text()
            assert "status" not in content

    def test_two_step_confirmation_flow(self, vault_config):
        """Preview then confirm should execute the batch operation."""
        clear_pending_previews()
        paths = self._create_files(vault_config, 8)
        # Step 1: preview
        preview = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="status",
                value="done",
                operation="set",
            )
        )
        assert preview["confirmation_required"] is True
        # Step 2: confirm
        result = json.loads(
            batch_update_frontmatter(
                paths=paths,
                field="status",
                value="done",
                operation="set",
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "8 succeeded" in result["message"]

    def test_confirmation_is_single_use(self, vault_config):
        """After execution, same confirm=True requires a new preview."""
        clear_pending_previews()
        paths = self._create_files(vault_config, 8)
        # Step 1: preview
        batch_update_frontmatter(
            paths=paths, field="status", value="done", operation="set",
        )
        # Step 2: confirm and execute
        batch_update_frontmatter(
            paths=paths, field="status", value="done", operation="set",
            confirm=True,
        )
        # Step 3: confirm again without new preview — should return preview
        result = json.loads(
            batch_update_frontmatter(
                paths=paths, field="status", value="done", operation="set",
                confirm=True,
            )
        )
        assert result["confirmation_required"] is True


class TestQueryBasedBatchUpdate:
    """Tests for batch_update_frontmatter with query-based targeting."""

    def test_query_target_finds_and_updates(self, vault_config):
        """Should find files by target criteria and update them."""
        clear_pending_previews()
        (vault_config / "t1.md").write_text(
            "---\nproject: '[[Proj]]'\ncategory: task\n---\n"
        )
        (vault_config / "t2.md").write_text(
            "---\nproject: '[[Proj]]'\ncategory: task\n---\n"
        )
        # First call: store preview
        batch_update_frontmatter(
            field="context", value="work", operation="set",
            target_field="project", target_value="Proj",
        )
        # Second call: confirm execution
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
        clear_pending_previews()
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
        assert any("qt1.md" in f for f in result["files"])

    def test_query_target_with_filters(self, vault_config):
        """Compound targeting should narrow results."""
        clear_pending_previews()
        (vault_config / "open.md").write_text(
            "---\nproject: '[[FP]]'\nstatus: open\n---\n"
        )
        (vault_config / "done.md").write_text(
            "---\nproject: '[[FP]]'\nstatus: done\n---\n"
        )
        # First call: store preview
        batch_update_frontmatter(
            field="context", value="work", operation="set",
            target_field="project", target_value="FP",
            target_filters=[FilterCondition(field="status", value="open")],
        )
        # Second call: confirm execution
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="FP",
                target_filters=[FilterCondition(field="status", value="open")],
                confirm=True,
            )
        )
        assert result["success"] is True
        assert "1 succeeded" in result["message"]


    def test_query_target_with_filter_condition_list(self, vault_config):
        """Query targeting should accept FilterCondition list for target_filters."""
        clear_pending_previews()
        (vault_config / "open_native.md").write_text(
            "---\nproject: '[[NP]]'\nstatus: open\n---\n"
        )
        (vault_config / "done_native.md").write_text(
            "---\nproject: '[[NP]]'\nstatus: done\n---\n"
        )
        # First call: store preview
        batch_update_frontmatter(
            field="context", value="work", operation="set",
            target_field="project", target_value="NP",
            target_filters=[FilterCondition(field="status", value="open", match_type="equals")],
        )
        # Second call: confirm execution
        result = json.loads(
            batch_update_frontmatter(
                field="context", value="work", operation="set",
                target_field="project", target_value="NP",
                target_filters=[FilterCondition(field="status", value="open", match_type="equals")],
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

    @pytest.mark.parametrize(
        ("filename", "frontmatter", "field", "value", "match_type"),
        [
            ("ci1.md", "---\nstatus: Open\n---\n", "status", "open", "contains"),
            ("ci2.md", "---\ncategory: Meeting\n---\n", "category", "meeting", "equals"),
            ("ci4.md", "---\ntags:\n  - Project\n  - Work\n---\n", "tags", "work", "contains"),
            ("ci5.md", "---\npriority: 1\n---\n", "priority", "1", "equals"),
        ],
        ids=["contains", "equals", "list_values", "non_string"],
    )
    def test_simple_case_insensitive(self, vault_config, filename, frontmatter, field, value, match_type):
        """Case-insensitive matching should work for contains, equals, list values, and non-string types."""
        (vault_config / filename).write_text(frontmatter)
        result = json.loads(
            list_files_by_frontmatter(field=field, value=value, match_type=match_type)
        )
        assert result["total"] >= 1
        assert any(filename in p for p in result["results"])

    def test_filter_case_insensitive(self, vault_config):
        """Compound filter values should also be case-insensitive."""
        (vault_config / "ci3.md").write_text(
            "---\nproject: '[[MyProj]]'\nstatus: OPEN\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="myproj",
                filters=[FilterCondition(field="status", value="open", match_type="equals")],
            )
        )
        assert result["total"] >= 1
        assert any("ci3.md" in p for p in result["results"])

    def test_field_name_case_insensitive(self, vault_config):
        """Field names should match case-insensitively (e.g. Project vs project)."""
        (vault_config / "ci_field.md").write_text(
            "---\nProject:\n- '[[MyProj]]'\nStatus: Open\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="project", value="MyProj",
                filters=[FilterCondition(field="status", value="open")],
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
                include_fields=["status"],
            )
        )
        assert result["total"] >= 1
        item = next(r for r in result["results"] if "ci_inc.md" in r["path"])
        assert item["status"] == "Open"


class TestWikilinkStripping:
    """Tests for wikilink bracket stripping in frontmatter matching."""

    @pytest.mark.parametrize(
        ("filename", "frontmatter", "value", "match_type"),
        [
            ("wl1.md", "---\nProject:\n- '[[Agentic S2P]]'\nstatus: open\n---\n", "Agentic S2P", "contains"),
            ("wl2.md", "---\nProject:\n- '[[Agentic S2P]]'\n---\n", "[[Agentic S2P]]", "contains"),
            ("wl3.md", "---\nProject:\n- '[[Agentic S2P]]'\n---\n", "Agentic S2P", "equals"),
            ("wl4.md", "---\nproject: '[[Agentic S2P]]'\n---\n", "Agentic S2P", "equals"),
            ("wl5.md", "---\nproject: '[[Agentic S2P|S2P Project]]'\n---\n", "Agentic S2P", "contains"),
        ],
        ids=["stored_brackets", "search_brackets", "equals_list", "equals_string", "aliased"],
    )
    def test_wikilink_stripping(self, vault_config, filename, frontmatter, value, match_type):
        """Wikilink brackets should be stripped during frontmatter matching."""
        (vault_config / filename).write_text(frontmatter)
        result = json.loads(
            list_files_by_frontmatter(field="project", value=value, match_type=match_type)
        )
        assert result["total"] >= 1
        assert any(filename in p for p in result["results"])

    def test_compound_filter_strips_wikilinks(self, vault_config):
        """Wikilink stripping should also apply to compound filter values."""
        (vault_config / "wl6.md").write_text(
            "---\nProject:\n- '[[Agentic S2P]]'\nstatus: open\ncategory: task\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category", value="task",
                filters=[FilterCondition(field="project", value="Agentic S2P")],
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


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"offset": -1}, "offset must be >= 0"),
        ({"limit": 0}, "limit must be >= 1"),
        ({"limit": 501}, "limit must be <= 500"),
    ],
)
def test_frontmatter_paginated_tools_reject_invalid_pagination(vault_config, kwargs, expected_error):
    """Frontmatter paginated tools should return a consistent pagination validation error."""
    list_result = json.loads(
        list_files_by_frontmatter(field="tags", value="test", **kwargs)
    )
    date_result = json.loads(
        search_by_date_range(
            start_date="1990-01-01",
            end_date="2099-12-31",
            date_type="modified",
            **kwargs,
        )
    )

    for result in (list_result, date_result):
        assert result["success"] is False
        assert expected_error in result["error"]


class TestNegativeMatching:
    """Tests for negative and existence match types."""

    def test_missing_finds_files_without_field(self, vault_config):
        """match_type='missing' finds files where the field is absent."""
        # note3.md has no frontmatter at all — should match 'missing' on any field
        result = json.loads(
            list_files_by_frontmatter(field="company", match_type="missing")
        )
        assert result["success"] is True
        # note3.md has no frontmatter, note1.md has no company field
        assert any("note3.md" in p for p in result["results"])
        assert any("note1.md" in p for p in result["results"])
        # note2.md has company: Acme Corp — should NOT match
        assert not any("note2.md" in p for p in result["results"])

    def test_missing_excludes_files_with_field(self, vault_config):
        """match_type='missing' excludes files that have the field."""
        result = json.loads(
            list_files_by_frontmatter(field="tags", match_type="missing")
        )
        assert result["success"] is True
        # note1.md has tags — should NOT be in results
        assert not any("note1.md" in p for p in result["results"])
        # note3.md has no frontmatter — should be in results
        assert any("note3.md" in p for p in result["results"])

    def test_exists_finds_files_with_field(self, vault_config):
        """match_type='exists' finds files where the field is present."""
        result = json.loads(
            list_files_by_frontmatter(field="tags", match_type="exists")
        )
        assert result["success"] is True
        assert any("note1.md" in p for p in result["results"])
        assert any("note2.md" in p for p in result["results"])
        # note3.md has no frontmatter — should NOT match
        assert not any("note3.md" in p for p in result["results"])

    def test_not_contains_excludes_matching(self, vault_config):
        """match_type='not_contains' excludes files containing the value."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="project", match_type="not_contains"
            )
        )
        assert result["success"] is True
        # note1.md has tags: [project, work] — should be excluded
        assert not any("note1.md" in p for p in result["results"])
        # note2.md has tags: [meeting] — should be included
        assert any("note2.md" in p for p in result["results"])

    def test_not_contains_includes_missing_field(self, vault_config):
        """match_type='not_contains' includes files where the field is absent."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="project", match_type="not_contains"
            )
        )
        assert result["success"] is True
        # note3.md has no frontmatter — should be included
        assert any("note3.md" in p for p in result["results"])

    def test_not_equals(self, vault_config):
        """match_type='not_equals' excludes exact matches, includes the rest."""
        (vault_config / "ne1.md").write_text("---\nstatus: open\n---\n")
        (vault_config / "ne2.md").write_text("---\nstatus: closed\n---\n")
        (vault_config / "ne3.md").write_text("---\ntitle: no status\n---\n")
        result = json.loads(
            list_files_by_frontmatter(
                field="status", value="open", match_type="not_equals"
            )
        )
        assert result["success"] is True
        assert not any("ne1.md" in p for p in result["results"])
        assert any("ne2.md" in p for p in result["results"])
        # ne3.md has no status field — should be included
        assert any("ne3.md" in p for p in result["results"])

    def test_missing_in_filter(self, vault_config):
        """'missing' match_type works in compound FilterCondition."""
        (vault_config / "mf1.md").write_text("---\ncategory: task\n---\n")
        (vault_config / "mf2.md").write_text(
            "---\ncategory: task\nstatus: done\n---\n"
        )
        result = json.loads(
            list_files_by_frontmatter(
                field="category",
                value="task",
                match_type="equals",
                filters=[FilterCondition(field="status", match_type="missing")],
            )
        )
        assert result["success"] is True
        assert any("mf1.md" in p for p in result["results"])
        assert not any("mf2.md" in p for p in result["results"])

    def test_value_required_for_contains(self, vault_config):
        """match_type='contains' requires a non-empty value."""
        result = json.loads(
            list_files_by_frontmatter(field="tags", value="", match_type="contains")
        )
        assert result["success"] is False
        assert "value" in result["error"].lower()

    def test_invalid_match_type_rejected(self, vault_config):
        """Unknown match_type returns an error."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="test", match_type="regex"
            )
        )
        assert result["success"] is False
        assert "match_type" in result["error"]


class TestFolderFiltering:
    """Tests for folder parameter on frontmatter tools."""

    def test_list_with_folder(self, vault_config):
        """list_files_by_frontmatter with folder restricts to that directory."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="project", folder="projects"
            )
        )
        assert result["success"] is True
        assert result["total"] >= 1
        for path in result["results"]:
            assert path.startswith("projects/")

    def test_list_folder_with_missing(self, vault_config):
        """folder + match_type='missing' finds files in folder without the field."""
        # projects/project1.md has tags and status, but no company
        result = json.loads(
            list_files_by_frontmatter(
                field="company", match_type="missing", folder="projects"
            )
        )
        assert result["success"] is True
        assert any("project1.md" in p for p in result["results"])
        # Results should all be in the projects folder
        for path in result["results"]:
            assert path.startswith("projects/")

    def test_list_folder_excludes_other_folders(self, vault_config):
        """folder parameter should exclude files outside the folder."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="meeting", folder="projects"
            )
        )
        assert result["success"] is True
        # note2.md has tags: [meeting] but is in root, not projects/
        assert not any("note2.md" in p for p in result["results"])

    def test_list_folder_invalid(self, vault_config):
        """Nonexistent folder returns error."""
        result = json.loads(
            list_files_by_frontmatter(
                field="tags", value="test", folder="nonexistent"
            )
        )
        assert result["success"] is False

    def test_batch_folder_only(self, vault_config):
        """batch_update_frontmatter with folder-only returns confirmation preview."""
        clear_pending_previews()
        result = json.loads(
            batch_update_frontmatter(
                field="category",
                value="project",
                operation="set",
                folder="projects",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert all("projects/" in f for f in result["files"])

    def test_batch_folder_with_query(self, vault_config):
        """folder + target_field narrows to files in folder matching the query."""
        clear_pending_previews()
        (vault_config / "projects" / "active.md").write_text(
            "---\nstatus: active\n---\n"
        )
        (vault_config / "projects" / "archived.md").write_text(
            "---\nstatus: archived\n---\n"
        )
        result = json.loads(
            batch_update_frontmatter(
                field="category",
                value="project",
                operation="set",
                target_field="status",
                target_value="active",
                folder="projects",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert any("active.md" in f for f in result["files"])
        assert not any("archived.md" in f for f in result["files"])

    def test_batch_folder_with_paths_rejected(self, vault_config):
        """folder + paths returns error."""
        result = json.loads(
            batch_update_frontmatter(
                field="status",
                value="done",
                operation="set",
                paths=["note1.md"],
                folder="projects",
            )
        )
        assert result["success"] is False

    def test_batch_folder_not_contains_end_to_end(self, vault_config):
        """End-to-end: folder + not_contains finds and updates only missing files."""
        clear_pending_previews()
        # Create person files, some with and some without category
        persons = vault_config / "persons"
        persons.mkdir()
        (persons / "alice.md").write_text(
            "---\ncategory:\n  - person\n---\n# Alice\n"
        )
        (persons / "bob.md").write_text("---\ntags:\n  - colleague\n---\n# Bob\n")
        (persons / "carol.md").write_text("# Carol\n")  # no frontmatter

        # Query: find files in persons/ where category not_contains person
        result = json.loads(
            batch_update_frontmatter(
                field="category",
                value="person",
                operation="append",
                target_field="category",
                target_value="person",
                target_match_type="not_contains",
                folder="persons",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        # alice.md already has category: person — should NOT be in the list
        assert not any("alice.md" in f for f in result["files"])
        # bob.md and carol.md should be in the list
        assert any("bob.md" in f for f in result["files"])
        assert any("carol.md" in f for f in result["files"])

    def test_batch_folder_missing_match_type(self, vault_config):
        """batch with folder + target_match_type='missing' works without target_value."""
        clear_pending_previews()
        persons = vault_config / "persons2"
        persons.mkdir()
        (persons / "has_cat.md").write_text(
            "---\ncategory: person\n---\n"
        )
        (persons / "no_cat.md").write_text("---\ntags: [test]\n---\n")

        result = json.loads(
            batch_update_frontmatter(
                field="category",
                value="person",
                operation="set",
                target_field="category",
                target_match_type="missing",
                folder="persons2",
            )
        )
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert any("no_cat.md" in f for f in result["files"])
        assert not any("has_cat.md" in f for f in result["files"])
