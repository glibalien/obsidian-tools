"""Tests for tools/files.py - file operations."""

import pytest

from tools.files import (
    append_to_file,
    create_file,
    move_file,
    read_file,
)


class TestReadFile:
    """Tests for read_file tool."""

    def test_read_existing_file(self, vault_config):
        """Should read content of existing file."""
        result = read_file("note1.md")
        assert "# Note 1" in result
        assert "wikilink" in result

    def test_read_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = read_file("nonexistent.md")
        assert "Error" in result
        assert "not found" in result.lower()

    def test_read_file_in_subdirectory(self, vault_config):
        """Should read file in subdirectory."""
        result = read_file("projects/project1.md")
        assert "# Project 1" in result

    def test_short_file_no_markers(self, vault_config):
        """Short files should be returned in full with no markers."""
        result = read_file("note3.md")
        assert "[... truncated" not in result
        assert "[Continuing from" not in result
        assert "# Note 3" in result

    def test_long_file_truncated_with_marker(self, vault_config):
        """Files longer than length should have a continuation marker."""
        long_content = "# Long Note\n\n" + "x" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md")
        assert result.startswith("# Long Note")
        assert "[... truncated at char 4000 of" in result
        assert "Use offset=4000 to read more." in result

    def test_offset_pagination(self, vault_config):
        """Reading with offset should show continuation header and may show truncation marker."""
        long_content = "A" * 10000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md", offset=4000)
        assert "[Continuing from char 4000 of 10000]" in result
        assert "[... truncated at char 8000 of 10000" in result

    def test_offset_final_chunk(self, vault_config):
        """Reading the last chunk should have no truncation marker."""
        long_content = "B" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = read_file("long.md", offset=4000)
        assert "[Continuing from char 4000 of 5000]" in result
        assert "[... truncated" not in result

    def test_offset_past_end(self, vault_config):
        """Offset past end of file should return error."""
        result = read_file("note3.md", offset=99999)
        assert "Error" in result
        assert "offset" in result.lower()

    def test_custom_length(self, vault_config):
        """Custom length parameter should control chunk size."""
        content = "C" * 500
        (vault_config / "custom.md").write_text(content)
        result = read_file("custom.md", length=100)
        assert "[... truncated at char 100 of 500" in result
        assert len(result.split("\n\n[... truncated")[0]) == 100


class TestCreateFile:
    """Tests for create_file tool."""

    def test_create_simple_file(self, vault_config):
        """Should create a new file."""
        result = create_file("new_note.md", "# New Note\n\nContent here.")
        assert "Created" in result
        assert "new_note.md" in result

        # Verify file was created
        content = (vault_config / "new_note.md").read_text()
        assert "# New Note" in content

    def test_create_file_with_frontmatter(self, vault_config):
        """Should create file with JSON frontmatter."""
        result = create_file(
            "with_fm.md",
            "Content",
            frontmatter='{"tags": ["test"], "status": "draft"}'
        )
        assert "Created" in result

        content = (vault_config / "with_fm.md").read_text()
        assert "---" in content
        assert "tags:" in content
        assert "test" in content

    def test_create_file_already_exists(self, vault_config):
        """Should return error if file exists."""
        result = create_file("note1.md", "content")
        assert "Error" in result
        assert "already exists" in result.lower()

    def test_create_file_in_new_directory(self, vault_config):
        """Should create parent directories if needed."""
        result = create_file("new_dir/nested/note.md", "content")
        assert "Created" in result
        assert (vault_config / "new_dir" / "nested" / "note.md").exists()

    def test_create_file_invalid_frontmatter(self, vault_config):
        """Should return error for invalid JSON frontmatter."""
        result = create_file("test.md", "content", frontmatter="not valid json")
        assert "Error" in result
        assert "Invalid frontmatter JSON" in result


class TestMoveFile:
    """Tests for move_file tool."""

    def test_move_file_success(self, vault_config):
        """Should move file to new location."""
        result = move_file("note3.md", "archive/note3.md")
        assert "Moved" in result

        assert not (vault_config / "note3.md").exists()
        assert (vault_config / "archive" / "note3.md").exists()

    def test_move_file_source_not_found(self, vault_config):
        """Should return error for missing source."""
        result = move_file("nonexistent.md", "destination.md")
        assert "Error" in result
        assert "not found" in result.lower()

    def test_move_file_destination_exists(self, vault_config):
        """Should return error if destination exists."""
        result = move_file("note1.md", "note2.md")
        assert "Error" in result
        assert "already exists" in result.lower()

    def test_move_file_same_path(self, vault_config):
        """Should handle moving to same location."""
        result = move_file("note1.md", "note1.md")
        assert "Already at destination" in result


class TestAppendToFile:
    """Tests for append_to_file tool."""

    def test_append_content(self, vault_config):
        """Should append content to file."""
        result = append_to_file("note3.md", "\n## New Section\n\nAppended content.")
        assert "Appended" in result

        content = (vault_config / "note3.md").read_text()
        assert "New Section" in content
        assert "Appended content" in content

    def test_append_to_nonexistent_file(self, vault_config):
        """Should return error for missing file."""
        result = append_to_file("nonexistent.md", "content")
        assert "Error" in result
        assert "not found" in result.lower()
