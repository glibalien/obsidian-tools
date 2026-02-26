"""Tests for tools/editing.py - unified file editing."""

import json

import pytest

from tools.editing import edit_file


class TestEditFilePrepend:
    """Tests for edit_file with position='prepend'."""

    def test_prepend_with_frontmatter(self, vault_config):
        """Should prepend after frontmatter."""
        result = edit_file("note1.md", "**IMPORTANT NOTICE**", "prepend")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note1.md").read_text()
        assert content.index("IMPORTANT NOTICE") < content.index("# Note 1")
        assert content.index("---") < content.index("IMPORTANT NOTICE")

    def test_prepend_without_frontmatter(self, vault_config):
        """Should prepend at beginning when no frontmatter."""
        result = edit_file("note3.md", "Prepended content", "prepend")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note3.md").read_text()
        assert content.startswith("Prepended content")

    def test_prepend_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = edit_file("nonexistent.md", "content", "prepend")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestEditFileAppend:
    """Tests for edit_file with position='append'."""

    def test_append_content(self, vault_config):
        """Should append content to file."""
        result = json.loads(edit_file("note3.md", "\n## New Section\n\nAppended content.", "append"))
        assert result["success"] is True
        assert result["path"]

        content = (vault_config / "note3.md").read_text()
        assert "New Section" in content
        assert "Appended content" in content

    def test_append_to_nonexistent_file(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(edit_file("nonexistent.md", "content", "append"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestEditFileSectionReplace:
    """Tests for edit_file with position='section', mode='replace'."""

    def test_replace_section_basic(self, vault_config):
        """Should replace a section with new content."""
        result = edit_file("note2.md", "## Section A\n\nNew content for section A.", "section", heading="## Section A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "New content for section A" in content
        assert "Content in section A." not in content

    def test_replace_section_preserves_other_sections(self, vault_config):
        """Should not affect other sections."""
        result = edit_file("note2.md", "## Section A\n\nReplaced.", "section", heading="## Section A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "## Section B" in content
        assert "Content in section B" in content

    def test_replace_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = edit_file("note2.md", "content", "section", heading="## Nonexistent", mode="replace")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_replace_section_case_insensitive(self, vault_config):
        """Should match headings case-insensitively."""
        result = edit_file("note2.md", "## Section A\n\nReplaced.", "section", heading="## SECTION A", mode="replace")
        data = json.loads(result)
        assert data["success"] is True


class TestEditFileSectionAppend:
    """Tests for edit_file with position='section', mode='append'."""

    def test_append_to_section_basic(self, vault_config):
        """Should append content at end of section."""
        result = edit_file("note2.md", "Appended text.", "section", heading="## Section A", mode="append")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Content in section A" in content
        assert "Appended text" in content
        assert content.index("Content in section A") < content.index("Appended text")
        assert content.index("Appended text") < content.index("## Section B")

    def test_append_to_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = edit_file("note2.md", "content", "section", heading="## Missing", mode="append")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_append_to_last_section(self, vault_config):
        """Should append to section at end of file."""
        result = edit_file("note2.md", "Final content.", "section", heading="## Section B", mode="append")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Final content" in content


class TestEditFileValidation:
    """Tests for edit_file parameter validation."""

    def test_unknown_position(self, vault_config):
        """Should reject unknown position values."""
        result = json.loads(edit_file("note1.md", "content", "unknown"))
        assert result["success"] is False
        assert "unknown position" in result["error"].lower()

    def test_section_without_heading(self, vault_config):
        """Should require heading for section position."""
        result = json.loads(edit_file("note1.md", "content", "section"))
        assert result["success"] is False
        assert "heading" in result["error"].lower()

    def test_section_without_mode(self, vault_config):
        """Should require mode for section position."""
        result = json.loads(edit_file("note1.md", "content", "section", heading="## Test"))
        assert result["success"] is False
        assert "mode" in result["error"].lower()

    def test_section_invalid_mode(self, vault_config):
        """Should reject invalid mode values."""
        result = json.loads(edit_file("note1.md", "content", "section", heading="## Test", mode="delete"))
        assert result["success"] is False
        assert "mode" in result["error"].lower()
