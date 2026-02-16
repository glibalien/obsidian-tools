"""Tests for tools/links.py - backlinks, outlinks, folder search."""

import json

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
        result = json.loads(find_backlinks("note1"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_with_extension(self, vault_config):
        """Should work with .md extension provided."""
        result = json.loads(find_backlinks("note1.md"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_alias_links(self, vault_config):
        """Should find links with aliases."""
        # note2.md has [[note3|alias]]
        result = json.loads(find_backlinks("note3"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_none_found(self, vault_config):
        """Should return message when no backlinks found."""
        result = json.loads(find_backlinks("nonexistent_note"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No backlinks found" in result["message"]

    def test_find_backlinks_empty_name(self, vault_config):
        """Should return error for empty note name."""
        result = json.loads(find_backlinks(""))
        assert result["success"] is False
        assert "cannot be empty" in result["error"]


class TestFindBacklinksWithIndex:
    """Tests for find_backlinks using link index."""

    def test_uses_index_when_available(self, vault_config):
        """Should read from link index file instead of scanning vault."""
        import json as json_mod
        import os
        from config import CHROMA_PATH

        # Write a fake link index with absolute paths
        vault_resolved = vault_config.resolve()
        index = {"note1": [str(vault_resolved / "note2.md")]}
        index_path = os.path.join(CHROMA_PATH, "link_index.json")
        os.makedirs(CHROMA_PATH, exist_ok=True)
        with open(index_path, "w") as f:
            json_mod.dump(index, f)

        try:
            result = json.loads(find_backlinks("note1"))
            assert result["success"] is True
            assert len(result["results"]) >= 1
            assert any("note2.md" in r for r in result["results"])
        finally:
            os.remove(index_path)

    def test_falls_back_without_index(self, vault_config):
        """Should fall back to vault scan if index file missing."""
        import os
        from config import CHROMA_PATH
        index_path = os.path.join(CHROMA_PATH, "link_index.json")
        if os.path.exists(index_path):
            os.remove(index_path)

        result = json.loads(find_backlinks("note1"))
        assert result["success"] is True
        assert any("note2.md" in r for r in result["results"])

    def test_pagination_limit(self, vault_config):
        """Should respect limit parameter."""
        result = json.loads(find_backlinks("note1", limit=1))
        assert result["success"] is True
        assert len(result["results"]) <= 1
        assert result["total"] >= 1

    def test_pagination_offset(self, vault_config):
        """Should respect offset parameter."""
        # Get all results first
        full = json.loads(find_backlinks("note1"))
        total = full["total"]

        # Offset past all results
        result = json.loads(find_backlinks("note1", offset=total))
        assert result["results"] == [] or len(result["results"]) == 0


class TestFindOutlinks:
    """Tests for find_outlinks tool."""

    def test_find_outlinks_basic(self, vault_config):
        """Should extract wikilinks from file."""
        result = json.loads(find_outlinks("note2.md"))
        assert result["success"] is True
        assert "note1" in result["results"]
        assert "note3" in result["results"]

    def test_find_outlinks_simple_link(self, vault_config):
        """Should find simple wikilinks."""
        result = json.loads(find_outlinks("note1.md"))
        assert result["success"] is True
        assert "wikilink" in result["results"]

    def test_find_outlinks_none_found(self, vault_config):
        """Should return message when no outlinks found."""
        # note3.md has no wikilinks
        result = json.loads(find_outlinks("note3.md"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No outlinks found" in result["message"]

    def test_find_outlinks_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(find_outlinks("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_find_outlinks_deduplicates(self, vault_config):
        """Should return unique links only."""
        # Create file with duplicate links
        (vault_config / "dupes.md").write_text(
            "[[same]] and [[same]] and [[same|alias]]"
        )
        result = json.loads(find_outlinks("dupes.md"))
        assert result["success"] is True
        assert result["results"].count("same") == 1


class TestSearchByFolder:
    """Tests for search_by_folder tool."""

    def test_search_by_folder_basic(self, vault_config):
        """Should list markdown files in folder."""
        result = json.loads(search_by_folder("projects"))
        assert result["success"] is True
        assert any("project1.md" in f for f in result["results"])

    def test_search_by_folder_recursive(self, vault_config):
        """Should include subfolders when recursive=True."""
        result = json.loads(search_by_folder(".", recursive=True))
        assert result["success"] is True
        assert any("note1.md" in f for f in result["results"])
        assert any("project1.md" in f for f in result["results"])

    def test_search_by_folder_non_recursive(self, vault_config):
        """Should not include subfolders when recursive=False."""
        result = json.loads(search_by_folder(".", recursive=False))
        assert result["success"] is True
        assert any("note1.md" in f for f in result["results"])
        # project1.md is in projects/ subfolder, should not appear
        assert not any("projects/" in f for f in result["results"])

    def test_search_by_folder_not_found(self, vault_config):
        """Should return error for missing folder."""
        result = json.loads(search_by_folder("nonexistent"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_search_by_folder_empty(self, vault_config):
        """Should return message for empty folder."""
        # Create empty folder
        (vault_config / "empty_folder").mkdir()
        result = json.loads(search_by_folder("empty_folder"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No markdown files found" in result["message"]
