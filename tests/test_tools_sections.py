"""Tests for tools/sections.py - section manipulation."""

import json

import pytest

from tools.sections import (
    append_to_section,
    prepend_to_file,
    replace_section,
)


class TestPrependToFile:
    """Tests for prepend_to_file tool."""

    def test_prepend_with_frontmatter(self, vault_config):
        """Should prepend after frontmatter."""
        result = prepend_to_file("note1.md", "**IMPORTANT NOTICE**")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note1.md").read_text()
        # Should be after frontmatter but before original content
        assert content.index("IMPORTANT NOTICE") < content.index("# Note 1")
        assert content.index("---") < content.index("IMPORTANT NOTICE")

    def test_prepend_without_frontmatter(self, vault_config):
        """Should prepend at beginning when no frontmatter."""
        result = prepend_to_file("note3.md", "Prepended content")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note3.md").read_text()
        assert content.startswith("Prepended content")

    def test_prepend_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = prepend_to_file("nonexistent.md", "content")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()


class TestReplaceSection:
    """Tests for replace_section tool."""

    def test_replace_section_basic(self, vault_config):
        """Should replace a section with new content."""
        result = replace_section("note2.md", "## Section A", "## Section A\n\nNew content for section A.")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "New content for section A" in content
        assert "Content in section A." not in content  # Old content removed

    def test_replace_section_preserves_other_sections(self, vault_config):
        """Should not affect other sections."""
        result = replace_section("note2.md", "## Section A", "## Section A\n\nReplaced.")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "## Section B" in content
        assert "Content in section B" in content

    def test_replace_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = replace_section("note2.md", "## Nonexistent", "content")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_replace_section_case_insensitive(self, vault_config):
        """Should match headings case-insensitively."""
        result = replace_section("note2.md", "## SECTION A", "## Section A\n\nReplaced.")
        data = json.loads(result)
        assert data["success"] is True


class TestAppendToSection:
    """Tests for append_to_section tool."""

    def test_append_to_section_basic(self, vault_config):
        """Should append content at end of section."""
        result = append_to_section("note2.md", "## Section A", "Appended text.")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Content in section A" in content  # Original preserved
        assert "Appended text" in content
        # Appended should be after original content but before Section B
        assert content.index("Content in section A") < content.index("Appended text")
        assert content.index("Appended text") < content.index("## Section B")

    def test_append_to_section_not_found(self, vault_config):
        """Should return error for missing section."""
        result = append_to_section("note2.md", "## Missing", "content")
        data = json.loads(result)
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_append_to_last_section(self, vault_config):
        """Should append to section at end of file."""
        result = append_to_section("note2.md", "## Section B", "Final content.")
        data = json.loads(result)
        assert data["success"] is True

        content = (vault_config / "note2.md").read_text()
        assert "Final content" in content
