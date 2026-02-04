"""Tests for tools/links.py - backlinks, outlinks, folder search."""

import pytest

from tools.links import (
    find_backlinks,
    find_outlinks,
    search_by_folder,
)


class TestFindBacklinks:
    """Tests for find_backlinks tool."""

    def test_find_backlinks_basic(self, vault_config):
        """Should find files that link to a note."""
        result = find_backlinks("note1")
        # note2.md links to note1
        assert "note2.md" in result

    def test_find_backlinks_with_extension(self, vault_config):
        """Should work with .md extension provided."""
        result = find_backlinks("note1.md")
        assert "note2.md" in result

    def test_find_backlinks_alias_links(self, vault_config):
        """Should find links with aliases."""
        # note2.md has [[note3|alias]]
        result = find_backlinks("note3")
        assert "note2.md" in result

    def test_find_backlinks_none_found(self, vault_config):
        """Should return message when no backlinks found."""
        result = find_backlinks("nonexistent_note")
        assert "No backlinks found" in result

    def test_find_backlinks_empty_name(self, vault_config):
        """Should return error for empty note name."""
        result = find_backlinks("")
        assert "Error" in result
        assert "empty" in result.lower()


class TestFindOutlinks:
    """Tests for find_outlinks tool."""

    def test_find_outlinks_basic(self, vault_config):
        """Should extract wikilinks from file."""
        result = find_outlinks("note2.md")
        assert "note1" in result
        assert "note3" in result

    def test_find_outlinks_simple_link(self, vault_config):
        """Should find simple wikilinks."""
        result = find_outlinks("note1.md")
        assert "wikilink" in result

    def test_find_outlinks_none_found(self, vault_config):
        """Should return message when no outlinks found."""
        # note3.md has no wikilinks
        result = find_outlinks("note3.md")
        assert "No outlinks found" in result

    def test_find_outlinks_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = find_outlinks("nonexistent.md")
        assert "Error" in result
        assert "not found" in result.lower()

    def test_find_outlinks_deduplicates(self, vault_config):
        """Should return unique links only."""
        # Create file with duplicate links
        (vault_config / "dupes.md").write_text(
            "[[same]] and [[same]] and [[same|alias]]"
        )
        result = find_outlinks("dupes.md")
        # Should only have "same" once
        lines = result.strip().split("\n")
        assert lines.count("same") == 1


class TestSearchByFolder:
    """Tests for search_by_folder tool."""

    def test_search_by_folder_basic(self, vault_config):
        """Should list markdown files in folder."""
        result = search_by_folder("projects")
        assert "project1.md" in result

    def test_search_by_folder_recursive(self, vault_config):
        """Should include subfolders when recursive=True."""
        result = search_by_folder(".", recursive=True)
        assert "note1.md" in result
        assert "project1.md" in result or "projects/project1.md" in result

    def test_search_by_folder_non_recursive(self, vault_config):
        """Should not include subfolders when recursive=False."""
        result = search_by_folder(".", recursive=False)
        # Root files should be present
        assert "note1.md" in result
        # project1.md is in projects/ subfolder, should not be in non-recursive
        # But actually projects/project1.md might appear if we're searching root
        # Let's be more specific
        lines = result.strip().split("\n")
        assert not any("projects/" in line for line in lines)

    def test_search_by_folder_not_found(self, vault_config):
        """Should return error for missing folder."""
        result = search_by_folder("nonexistent")
        assert "Error" in result
        assert "not found" in result.lower()

    def test_search_by_folder_empty(self, vault_config):
        """Should return message for empty folder."""
        # Create empty folder
        (vault_config / "empty_folder").mkdir()
        result = search_by_folder("empty_folder")
        assert "No markdown files found" in result
