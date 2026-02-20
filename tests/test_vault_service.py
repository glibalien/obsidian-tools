"""Tests for services/vault.py - path resolution, response helpers, and utilities."""

import json
import time
from pathlib import Path

import pytest

from services.vault import (
    CONFIRM_EXPIRY_SECONDS,
    _pending_confirmations,
    check_confirmation,
    clear_pending_confirmations,
    compute_op_hash,
    err,
    extract_frontmatter,
    find_section,
    get_relative_path,
    get_vault_files,
    is_fence_line,
    ok,
    parse_frontmatter_date,
    resolve_dir,
    resolve_file,
    resolve_vault_path,
    store_confirmation,
    update_file_frontmatter,
)


class TestResponseHelpers:
    """Tests for ok() and err() response helpers."""

    def test_ok_with_message(self):
        result = json.loads(ok("Operation successful"))
        assert result["success"] is True
        assert result["message"] == "Operation successful"

    def test_ok_with_data_dict(self):
        result = json.loads(ok({"key": "value"}))
        assert result["success"] is True
        assert result["data"] == {"key": "value"}

    def test_ok_with_data_list(self):
        result = json.loads(ok([1, 2, 3]))
        assert result["success"] is True
        assert result["data"] == [1, 2, 3]

    def test_ok_with_kwargs(self):
        result = json.loads(ok("Done", path="test.md", count=5))
        assert result["success"] is True
        assert result["message"] == "Done"
        assert result["path"] == "test.md"
        assert result["count"] == 5

    def test_ok_empty(self):
        result = json.loads(ok())
        assert result["success"] is True
        assert "message" not in result
        assert "data" not in result

    def test_err_basic(self):
        result = json.loads(err("Something went wrong"))
        assert result["success"] is False
        assert result["error"] == "Something went wrong"

    def test_err_with_kwargs(self):
        result = json.loads(err("File not found", path="missing.md"))
        assert result["success"] is False
        assert result["error"] == "File not found"
        assert result["path"] == "missing.md"


class TestPathResolution:
    """Tests for path resolution functions."""

    def test_resolve_vault_path_relative(self, vault_config):
        """Relative path should resolve within vault."""
        result = resolve_vault_path("note1.md")
        assert result == vault_config / "note1.md"

    def test_resolve_vault_path_absolute(self, vault_config):
        """Absolute path within vault should work."""
        abs_path = str(vault_config / "note1.md")
        result = resolve_vault_path(abs_path)
        assert result == vault_config / "note1.md"

    def test_resolve_vault_path_traversal_rejected(self, vault_config):
        """Path traversal should be rejected."""
        with pytest.raises(ValueError, match="Path must be within vault"):
            resolve_vault_path("../../../etc/passwd")

    def test_resolve_vault_path_excluded_dir_rejected(self, vault_config):
        """Excluded directories should be rejected."""
        # Create .git directory
        git_dir = vault_config / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("test")

        with pytest.raises(ValueError, match="Cannot access excluded directory"):
            resolve_vault_path(".git/config")

    def test_resolve_file_success(self, vault_config):
        """resolve_file should return path for existing file."""
        path, error = resolve_file("note1.md")
        assert error is None
        assert path == vault_config / "note1.md"

    @pytest.mark.parametrize(
        ("input_path", "expected_error"),
        [
            ("nonexistent.md", "File not found"),
            ("projects", "Not a file"),
        ],
        ids=["missing", "is_directory"],
    )
    def test_resolve_file_error(self, vault_config, input_path, expected_error):
        """resolve_file should return error for invalid targets."""
        path, error = resolve_file(input_path)
        assert path is None
        assert expected_error in error

    def test_resolve_dir_success(self, vault_config):
        """resolve_dir should return path for existing directory."""
        path, error = resolve_dir("projects")
        assert error is None
        assert path == vault_config / "projects"

    @pytest.mark.parametrize(
        ("input_path", "expected_error"),
        [
            ("nonexistent", "Folder not found"),
            ("note1.md", "Not a folder"),
        ],
        ids=["missing", "is_file"],
    )
    def test_resolve_dir_error(self, vault_config, input_path, expected_error):
        """resolve_dir should return error for invalid targets."""
        path, error = resolve_dir(input_path)
        assert path is None
        assert expected_error in error


class TestFrontmatter:
    """Tests for frontmatter operations."""

    def test_extract_frontmatter_with_yaml(self, sample_frontmatter_file):
        """Should extract YAML frontmatter."""
        result = extract_frontmatter(sample_frontmatter_file)
        assert result["title"] == "Test Note"
        assert result["tags"] == ["test", "sample"]
        assert result["author"] == "Test Author"

    def test_extract_frontmatter_empty_file(self, empty_file):
        """Should return empty dict for empty file."""
        result = extract_frontmatter(empty_file)
        assert result == {}

    def test_extract_frontmatter_no_yaml(self, file_without_frontmatter):
        """Should return empty dict for file without frontmatter."""
        result = extract_frontmatter(file_without_frontmatter)
        assert result == {}

    def test_parse_frontmatter_date_iso(self):
        """Should parse ISO date format."""
        result = parse_frontmatter_date("2024-01-15")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_parse_frontmatter_date_wikilink(self):
        """Should parse date in wikilink format."""
        result = parse_frontmatter_date("[[2024-01-15]]")
        assert result is not None
        assert result.year == 2024

    def test_parse_frontmatter_date_invalid(self):
        """Should return None for invalid date."""
        result = parse_frontmatter_date("not a date")
        assert result is None

    def test_parse_frontmatter_date_none(self):
        """Should return None for None input."""
        result = parse_frontmatter_date(None)
        assert result is None

    def test_update_file_frontmatter_set(self, sample_frontmatter_file):
        """Should set a frontmatter field."""
        update_file_frontmatter(sample_frontmatter_file, "status", "published")
        result = extract_frontmatter(sample_frontmatter_file)
        assert result["status"] == "published"

    def test_update_file_frontmatter_remove(self, sample_frontmatter_file):
        """Should remove a frontmatter field."""
        update_file_frontmatter(sample_frontmatter_file, "author", None, remove=True)
        result = extract_frontmatter(sample_frontmatter_file)
        assert "author" not in result

    def test_update_file_frontmatter_append(self, sample_frontmatter_file):
        """Should append to a list field."""
        update_file_frontmatter(sample_frontmatter_file, "tags", "new-tag", append=True)
        result = extract_frontmatter(sample_frontmatter_file)
        assert "new-tag" in result["tags"]

    def test_update_file_frontmatter_append_no_duplicates(self, sample_frontmatter_file):
        """Should not add duplicate values when appending."""
        update_file_frontmatter(sample_frontmatter_file, "tags", "test", append=True)
        result = extract_frontmatter(sample_frontmatter_file)
        assert result["tags"].count("test") == 1


class TestSectionFinding:
    """Tests for find_section function."""

    def test_find_section_basic(self, sample_markdown_with_sections):
        """Should find a section by heading."""
        lines = sample_markdown_with_sections.split("\n")
        start, end, error = find_section(lines, "## Section One")
        assert error is None
        assert start is not None
        assert end is not None
        # Section should contain "Content of section one"
        section_content = "\n".join(lines[start:end])
        assert "Content of section one" in section_content

    def test_find_section_case_insensitive(self, sample_markdown_with_sections):
        """Should match headings case-insensitively."""
        lines = sample_markdown_with_sections.split("\n")
        start, end, error = find_section(lines, "## SECTION ONE")
        assert error is None
        assert start is not None

    def test_find_section_not_found(self, sample_markdown_with_sections):
        """Should return error for missing heading."""
        lines = sample_markdown_with_sections.split("\n")
        start, end, error = find_section(lines, "## Nonexistent")
        assert start is None
        assert end is None
        assert "Heading not found" in error

    def test_find_section_ignores_code_blocks(self, sample_markdown_with_sections):
        """Should ignore headings inside code blocks."""
        lines = sample_markdown_with_sections.split("\n")
        # "## This is not a heading" is inside a code block
        start, end, error = find_section(lines, "## This is not a heading")
        assert start is None
        assert "Heading not found" in error

    def test_find_section_invalid_format(self):
        """Should return error for invalid heading format."""
        lines = ["# Test"]
        start, end, error = find_section(lines, "No hash prefix")
        assert start is None
        assert "Invalid heading format" in error


class TestVaultScanning:
    """Tests for vault file scanning."""

    def test_get_vault_files(self, vault_config):
        """Should return all markdown files."""
        files = get_vault_files()
        filenames = [f.name for f in files]
        assert "note1.md" in filenames
        assert "note2.md" in filenames
        assert "note3.md" in filenames
        assert "project1.md" in filenames

    def test_get_vault_files_excludes_dirs(self, vault_config):
        """Should exclude files in excluded directories."""
        # Create .git directory with a file
        git_dir = vault_config / ".git"
        git_dir.mkdir()
        (git_dir / "test.md").write_text("test")

        files = get_vault_files()
        paths = [str(f) for f in files]
        assert not any(".git" in p for p in paths)

    def test_get_relative_path(self, vault_config):
        """Should return path relative to vault root."""
        abs_path = vault_config / "projects" / "project1.md"
        # Need to patch VAULT_PATH for this test
        import config
        result = get_relative_path(abs_path)
        assert result == "projects/project1.md"


class TestIsFenceLine:
    """Tests for is_fence_line helper."""

    def test_backtick_fence(self):
        assert is_fence_line("```") is True

    def test_backtick_fence_with_language(self):
        assert is_fence_line("```python") is True

    def test_tilde_fence(self):
        assert is_fence_line("~~~") is True

    def test_tilde_fence_with_language(self):
        assert is_fence_line("~~~markdown") is True

    def test_four_backticks(self):
        assert is_fence_line("````") is True

    def test_indented_fence(self):
        assert is_fence_line("  ```") is True

    def test_not_a_fence_two_backticks(self):
        assert is_fence_line("``not a fence``") is False

    def test_not_a_fence_plain_text(self):
        assert is_fence_line("hello world") is False

    def test_empty_line(self):
        assert is_fence_line("") is False


class TestConfirmationHelpers:
    """Tests for batch confirmation tracking helpers."""

    def test_compute_op_hash_deterministic(self):
        params = {"field": "status", "value": "done", "paths": ["b.md", "a.md"]}
        assert compute_op_hash(params) == compute_op_hash(params)

    def test_compute_op_hash_sorts_paths(self):
        h1 = compute_op_hash({"paths": ["b.md", "a.md"]})
        h2 = compute_op_hash({"paths": ["a.md", "b.md"]})
        assert h1 == h2

    def test_compute_op_hash_preserves_move_order(self):
        """Move order matters (chained renames), so different order = different hash."""
        h1 = compute_op_hash({"moves": [{"source": "b.md", "destination": "x/"}, {"source": "a.md", "destination": "y/"}]})
        h2 = compute_op_hash({"moves": [{"source": "a.md", "destination": "y/"}, {"source": "b.md", "destination": "x/"}]})
        assert h1 != h2

    def test_compute_op_hash_different_params(self):
        h1 = compute_op_hash({"field": "status", "value": "done"})
        h2 = compute_op_hash({"field": "status", "value": "open"})
        assert h1 != h2

    def test_store_and_check_confirmation(self):
        clear_pending_confirmations()
        h = compute_op_hash({"field": "x"})
        store_confirmation(h)
        assert check_confirmation(h) is True
        assert check_confirmation(h) is False  # single-use

    def test_check_confirmation_missing(self):
        clear_pending_confirmations()
        assert check_confirmation("nonexistent") is False

    def test_check_confirmation_expired(self):
        clear_pending_confirmations()
        h = compute_op_hash({"field": "x"})
        store_confirmation(h)
        _pending_confirmations[h]["created"] = time.time() - CONFIRM_EXPIRY_SECONDS - 1
        assert check_confirmation(h) is False

    def test_store_cleans_expired(self):
        clear_pending_confirmations()
        h_old = compute_op_hash({"field": "old"})
        store_confirmation(h_old)
        _pending_confirmations[h_old]["created"] = time.time() - CONFIRM_EXPIRY_SECONDS - 1
        h_new = compute_op_hash({"field": "new"})
        store_confirmation(h_new)
        assert h_old not in _pending_confirmations
        assert h_new in _pending_confirmations
