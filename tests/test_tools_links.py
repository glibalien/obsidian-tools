"""Tests for tools/links.py - backlinks, outlinks, folder search."""

import json

import pytest

from tools.links import (
    compare_folders,
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


class TestFindBacklinksPagination:
    """Tests for find_backlinks pagination."""

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
        """Should extract wikilinks with resolved paths."""
        result = json.loads(find_outlinks("note2.md"))
        assert result["success"] is True
        names = [r["name"] for r in result["results"]]
        assert "note1" in names
        assert "note3" in names
        # Paths should resolve to existing vault files
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert by_name["note1"] == "note1.md"
        assert by_name["note3"] == "note3.md"

    def test_find_outlinks_unresolved_link(self, vault_config):
        """Should return null path for links to non-existent notes."""
        result = json.loads(find_outlinks("note1.md"))
        assert result["success"] is True
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert "wikilink" in by_name
        assert by_name["wikilink"] is None

    def test_find_outlinks_heading_suffix(self, vault_config):
        """Should resolve links with #heading suffixes."""
        (vault_config / "heading_links.md").write_text(
            "See [[note1#Section A]] for details."
        )
        result = json.loads(find_outlinks("heading_links.md"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "note1#Section A"
        assert link["path"] == "note1.md"

    def test_find_outlinks_subfolder_resolution(self, vault_config):
        """Should resolve links to notes in subfolders."""
        (vault_config / "links_to_project.md").write_text(
            "Check [[project1]] for status."
        )
        result = json.loads(find_outlinks("links_to_project.md"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "project1"
        assert link["path"] == "projects/project1.md"

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
        names = [r["name"] for r in result["results"]]
        assert names.count("same") == 1

    def test_find_outlinks_folder_qualified_with_colliding_stems(self, vault_config):
        """Should resolve folder-qualified links even when stems collide."""
        # Create two notes with the same stem in different folders
        (vault_config / "foo.md").write_text("# Root foo")
        sub = vault_config / "sub"
        sub.mkdir()
        (sub / "foo.md").write_text("# Sub foo")

        (vault_config / "qualifier_test.md").write_text(
            "[[foo]] and [[sub/foo]]"
        )
        result = json.loads(find_outlinks("qualifier_test.md"))
        by_name = {r["name"]: r["path"] for r in result["results"]}
        # Bare stem resolves to shortest path (root)
        assert by_name["foo"] == "foo.md"
        # Folder-qualified link resolves to the specific path
        assert by_name["sub/foo"] == "sub/foo.md"


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


class TestCompareFolders:
    """Tests for compare_folders tool."""

    def test_basic_comparison(self, vault_config):
        """Should categorize files into only_in_source, only_in_target, in_both."""
        source = vault_config / "folder_a"
        target = vault_config / "folder_b"
        source.mkdir()
        target.mkdir()
        (source / "shared.md").write_text("# Shared")
        (source / "only_a.md").write_text("# Only A")
        (target / "shared.md").write_text("# Shared copy")
        (target / "only_b.md").write_text("# Only B")

        result = json.loads(compare_folders("folder_a", "folder_b"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 1
        assert result["counts"]["only_in_target"] == 1
        assert result["counts"]["in_both"] == 1
        assert "folder_a/only_a.md" in result["only_in_source"]
        assert "folder_b/only_b.md" in result["only_in_target"]
        both_names = [m["name"] for m in result["in_both"]]
        assert "shared.md" in both_names

    def test_no_overlap(self, vault_config):
        """Should return empty in_both when folders are disjoint."""
        source = vault_config / "disjoint_a"
        target = vault_config / "disjoint_b"
        source.mkdir()
        target.mkdir()
        (source / "alpha.md").write_text("# A")
        (target / "beta.md").write_text("# B")

        result = json.loads(compare_folders("disjoint_a", "disjoint_b"))
        assert result["success"] is True
        assert result["counts"]["in_both"] == 0
        assert result["counts"]["only_in_source"] == 1
        assert result["counts"]["only_in_target"] == 1

    def test_complete_overlap(self, vault_config):
        """Should return empty only_in lists when folders have same filenames."""
        source = vault_config / "same_a"
        target = vault_config / "same_b"
        source.mkdir()
        target.mkdir()
        (source / "file1.md").write_text("# V1")
        (source / "file2.md").write_text("# V1")
        (target / "file1.md").write_text("# V2")
        (target / "file2.md").write_text("# V2")

        result = json.loads(compare_folders("same_a", "same_b"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 0
        assert result["counts"]["only_in_target"] == 0
        assert result["counts"]["in_both"] == 2

    def test_empty_source(self, vault_config):
        """Should handle empty source folder."""
        source = vault_config / "empty_src"
        target = vault_config / "nonempty"
        source.mkdir()
        target.mkdir()
        (target / "file.md").write_text("# File")

        result = json.loads(compare_folders("empty_src", "nonempty"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 0
        assert result["counts"]["only_in_target"] == 1
        assert result["counts"]["in_both"] == 0

    def test_empty_target(self, vault_config):
        """Should handle empty target folder."""
        source = vault_config / "nonempty_src"
        target = vault_config / "empty_tgt"
        source.mkdir()
        target.mkdir()
        (source / "file.md").write_text("# File")

        result = json.loads(compare_folders("nonempty_src", "empty_tgt"))
        assert result["success"] is True
        assert result["counts"]["only_in_source"] == 1
        assert result["counts"]["only_in_target"] == 0
        assert result["counts"]["in_both"] == 0

    def test_empty_both(self, vault_config):
        """Should handle both folders empty."""
        (vault_config / "empty1").mkdir()
        (vault_config / "empty2").mkdir()

        result = json.loads(compare_folders("empty1", "empty2"))
        assert result["success"] is True
        assert result["counts"] == {"only_in_source": 0, "only_in_target": 0, "in_both": 0}

    def test_case_insensitive_matching(self, vault_config):
        """Should match stems case-insensitively."""
        source = vault_config / "case_a"
        target = vault_config / "case_b"
        source.mkdir()
        target.mkdir()
        (source / "John Smith.md").write_text("# John")
        (target / "john smith.md").write_text("# John")

        result = json.loads(compare_folders("case_a", "case_b"))
        assert result["success"] is True
        assert result["counts"]["in_both"] == 1
        assert result["counts"]["only_in_source"] == 0

    def test_recursive(self, vault_config):
        """Should include subfolder files when recursive=True."""
        source = vault_config / "rec_a"
        target = vault_config / "rec_b"
        source.mkdir()
        target.mkdir()
        sub = source / "sub"
        sub.mkdir()
        (source / "top.md").write_text("# Top")
        (sub / "deep.md").write_text("# Deep")
        (target / "deep.md").write_text("# Deep copy")

        # Non-recursive: deep.md not scanned in source
        result = json.loads(compare_folders("rec_a", "rec_b"))
        assert result["counts"]["in_both"] == 0
        assert result["counts"]["only_in_source"] == 1  # top.md only

        # Recursive: deep.md found in both
        result = json.loads(compare_folders("rec_a", "rec_b", recursive=True))
        assert result["counts"]["in_both"] == 1
        both_names = [m["name"] for m in result["in_both"]]
        assert "deep.md" in both_names

    def test_same_folder_error(self, vault_config):
        """Should error when source and target are the same folder."""
        (vault_config / "same").mkdir()
        result = json.loads(compare_folders("same", "same"))
        assert result["success"] is False
        assert "same" in result["error"].lower()

    def test_invalid_folder_error(self, vault_config):
        """Should error when folder doesn't exist."""
        (vault_config / "exists").mkdir()
        result = json.loads(compare_folders("nonexistent", "exists"))
        assert result["success"] is False

        result = json.loads(compare_folders("exists", "nonexistent"))
        assert result["success"] is False

    def test_in_both_has_both_paths(self, vault_config):
        """in_both entries should include source_paths and target_paths."""
        source = vault_config / "paths_a"
        target = vault_config / "paths_b"
        source.mkdir()
        target.mkdir()
        (source / "shared.md").write_text("# A")
        (target / "shared.md").write_text("# B")

        result = json.loads(compare_folders("paths_a", "paths_b"))
        match = result["in_both"][0]
        assert match["name"] == "shared.md"
        assert match["source_paths"] == ["paths_a/shared.md"]
        assert match["target_paths"] == ["paths_b/shared.md"]

    def test_recursive_duplicate_stems(self, vault_config):
        """Should keep all files when multiple share a stem in recursive mode."""
        source = vault_config / "dup_src"
        target = vault_config / "dup_tgt"
        source.mkdir()
        target.mkdir()
        sub1 = source / "sub1"
        sub2 = source / "sub2"
        sub1.mkdir()
        sub2.mkdir()
        (sub1 / "report.md").write_text("# Report v1")
        (sub2 / "report.md").write_text("# Report v2")
        (target / "report.md").write_text("# Report target")

        result = json.loads(compare_folders("dup_src", "dup_tgt", recursive=True))
        assert result["counts"]["in_both"] == 1
        match = result["in_both"][0]
        assert match["name"] == "report.md"
        assert sorted(match["source_paths"]) == [
            "dup_src/sub1/report.md", "dup_src/sub2/report.md"
        ]
        assert match["target_paths"] == ["dup_tgt/report.md"]

    def test_results_sorted(self, vault_config):
        """Results should be sorted alphabetically."""
        source = vault_config / "sort_a"
        target = vault_config / "sort_b"
        source.mkdir()
        target.mkdir()
        for name in ["charlie.md", "alpha.md", "bravo.md"]:
            (source / name).write_text(f"# {name}")

        result = json.loads(compare_folders("sort_a", "sort_b"))
        assert result["only_in_source"] == [
            "sort_a/alpha.md", "sort_a/bravo.md", "sort_a/charlie.md"
        ]


class TestListToolPagination:
    """Tests for limit/offset pagination on list tools."""

    def test_find_outlinks_pagination(self, vault_config):
        """find_outlinks should respect limit and offset."""
        links = " ".join(f"[[note{i}]]" for i in range(10))
        (vault_config / "many_links.md").write_text(f"# Links\n\n{links}")

        result = json.loads(find_outlinks("many_links.md", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] == 10

        result2 = json.loads(find_outlinks("many_links.md", limit=3, offset=3))
        assert len(result2["results"]) == 3
        assert result2["total"] == 10

    def test_search_by_folder_pagination(self, vault_config):
        """search_by_folder should respect limit and offset."""
        for i in range(5):
            (vault_config / f"page_test_{i}.md").write_text(f"# Page {i}")

        result = json.loads(search_by_folder(".", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] >= 5

    def test_pagination_offset_beyond_results(self, vault_config):
        """Offset beyond results returns empty list with correct total."""
        result = json.loads(search_by_folder(".", limit=100, offset=9999))
        assert result["success"] is True
        assert result["results"] == []
        assert result["total"] >= 1

    def test_default_pagination_includes_total(self, vault_config):
        """Default call (no limit/offset) should still include total."""
        result = json.loads(find_outlinks("note1.md"))
        assert result["success"] is True
        assert "total" in result


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"offset": -1}, "offset must be >= 0"),
        ({"limit": 0}, "limit must be >= 1"),
        ({"limit": 2001}, "limit must be <= 2000"),
    ],
)
def test_paginated_link_tools_reject_invalid_pagination(vault_config, kwargs, expected_error):
    """Paginated links tools should return a consistent pagination validation error."""
    backlinks = json.loads(find_backlinks("note1", **kwargs))
    outlinks = json.loads(find_outlinks("note2.md", **kwargs))
    folder = json.loads(search_by_folder(".", **kwargs))

    for result in (backlinks, outlinks, folder):
        assert result["success"] is False
        assert expected_error in result["error"]
