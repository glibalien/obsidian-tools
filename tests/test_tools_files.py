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
