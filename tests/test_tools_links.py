"""Tests for tools/links.py - find_links, folder comparison."""

import json

import pytest

from tools.links import (
    compare_folders,
    find_links,
)


class TestFindBacklinks:
    """Tests for find_links with direction='backlinks'."""

    def test_find_backlinks_basic(self, vault_config):
        """Should find files that link to a note."""
        result = json.loads(find_links("note1.md", direction="backlinks"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_alias_links(self, vault_config):
        """Should find links with aliases."""
        # note2.md has [[note3|alias]]
        result = json.loads(find_links("note3.md", direction="backlinks"))
        assert result["success"] is True
        assert "note2.md" in result["results"]

    def test_find_backlinks_none_found(self, vault_config):
        """Should return message when no backlinks found."""
        (vault_config / "lonely.md").write_text("# No one links here")
        result = json.loads(find_links("lonely.md", direction="backlinks"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No backlinks found" in result["message"]

    def test_find_backlinks_folder_qualified_links(self, vault_config):
        """Should find folder-qualified wikilinks like [[sub/foo]]."""
        sub = vault_config / "sub"
        sub.mkdir(exist_ok=True)
        (sub / "target.md").write_text("# Target in subfolder")
        (vault_config / "linker.md").write_text("See [[sub/target]] for details.")
        result = json.loads(find_links("sub/target.md", direction="backlinks"))
        assert result["success"] is True
        assert "linker.md" in result["results"]

    def test_find_backlinks_bare_stem_when_no_collision(self, vault_config):
        """Should match bare [[stem]] when no other file shares the stem."""
        sub = vault_config / "sub"
        sub.mkdir(exist_ok=True)
        (sub / "unique_name.md").write_text("# Unique in sub")
        (vault_config / "bare_link.md").write_text("Link to [[unique_name]]")
        (vault_config / "qualified_link.md").write_text("Link to [[sub/unique_name]]")
        result = json.loads(find_links("sub/unique_name.md", direction="backlinks"))
        assert result["success"] is True
        assert "bare_link.md" in result["results"]
        assert "qualified_link.md" in result["results"]

    def test_find_backlinks_no_bare_stem_when_collision(self, vault_config):
        """Bare [[foo]] should NOT match sub/foo.md when foo.md also exists."""
        sub = vault_config / "sub"
        sub.mkdir(exist_ok=True)
        (vault_config / "foo.md").write_text("# Root foo")
        (sub / "foo.md").write_text("# Sub foo")
        (vault_config / "bare_link.md").write_text("Link to [[foo]]")
        (vault_config / "qualified_link.md").write_text("Link to [[sub/foo]]")
        # sub/foo.md should only get the qualified link, not the bare one
        result = json.loads(find_links("sub/foo.md", direction="backlinks"))
        assert result["success"] is True
        assert "qualified_link.md" in result["results"]
        assert "bare_link.md" not in result["results"]
        # foo.md (root) should get the bare link
        result2 = json.loads(find_links("foo.md", direction="backlinks"))
        assert result2["success"] is True
        assert "bare_link.md" in result2["results"]

    def test_find_backlinks_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(find_links("nonexistent.md", direction="backlinks"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestFindBacklinksPagination:
    """Tests for find_links backlinks pagination."""

    def test_pagination_limit(self, vault_config):
        """Should respect limit parameter."""
        result = json.loads(find_links("note1.md", direction="backlinks", limit=1))
        assert result["success"] is True
        assert len(result["results"]) <= 1
        assert result["total"] >= 1

    def test_pagination_offset(self, vault_config):
        """Should respect offset parameter."""
        # Get all results first
        full = json.loads(find_links("note1.md", direction="backlinks"))
        total = full["total"]

        # Offset past all results
        result = json.loads(find_links("note1.md", direction="backlinks", offset=total))
        assert result["results"] == [] or len(result["results"]) == 0


class TestFindOutlinks:
    """Tests for find_links with direction='outlinks'."""

    def test_find_outlinks_basic(self, vault_config):
        """Should extract wikilinks with resolved paths."""
        result = json.loads(find_links("note2.md", direction="outlinks"))
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
        result = json.loads(find_links("note1.md", direction="outlinks"))
        assert result["success"] is True
        by_name = {r["name"]: r["path"] for r in result["results"]}
        assert "wikilink" in by_name
        assert by_name["wikilink"] is None

    def test_find_outlinks_heading_suffix(self, vault_config):
        """Should resolve links with #heading suffixes."""
        (vault_config / "heading_links.md").write_text(
            "See [[note1#Section A]] for details."
        )
        result = json.loads(find_links("heading_links.md", direction="outlinks"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "note1#Section A"
        assert link["path"] == "note1.md"

    def test_find_outlinks_subfolder_resolution(self, vault_config):
        """Should resolve links to notes in subfolders."""
        (vault_config / "links_to_project.md").write_text(
            "Check [[project1]] for status."
        )
        result = json.loads(find_links("links_to_project.md", direction="outlinks"))
        assert result["success"] is True
        link = result["results"][0]
        assert link["name"] == "project1"
        assert link["path"] == "projects/project1.md"

    def test_find_outlinks_none_found(self, vault_config):
        """Should return message when no outlinks found."""
        # note3.md has no wikilinks
        result = json.loads(find_links("note3.md", direction="outlinks"))
        assert result["success"] is True
        assert result["results"] == []
        assert "No outlinks found" in result["message"]

    def test_find_outlinks_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(find_links("nonexistent.md", direction="outlinks"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_find_outlinks_deduplicates(self, vault_config):
        """Should return unique links only."""
        # Create file with duplicate links
        (vault_config / "dupes.md").write_text(
            "[[same]] and [[same]] and [[same|alias]]"
        )
        result = json.loads(find_links("dupes.md", direction="outlinks"))
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
        result = json.loads(find_links("qualifier_test.md", direction="outlinks"))
        by_name = {r["name"]: r["path"] for r in result["results"]}
        # Bare stem resolves to shortest path (root)
        assert by_name["foo"] == "foo.md"
        # Folder-qualified link resolves to the specific path
        assert by_name["sub/foo"] == "sub/foo.md"


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


class TestFindOutlinksPagination:
    """Tests for find_links outlinks pagination."""

    def test_find_outlinks_pagination(self, vault_config):
        """find_links outlinks should respect limit and offset."""
        links = " ".join(f"[[note{i}]]" for i in range(10))
        (vault_config / "many_links.md").write_text(f"# Links\n\n{links}")

        result = json.loads(find_links("many_links.md", direction="outlinks", limit=3, offset=0))
        assert result["success"] is True
        assert len(result["results"]) == 3
        assert result["total"] == 10

        result2 = json.loads(find_links("many_links.md", direction="outlinks", limit=3, offset=3))
        assert len(result2["results"]) == 3
        assert result2["total"] == 10

    def test_default_pagination_includes_total(self, vault_config):
        """Default call (no limit/offset) should still include total."""
        result = json.loads(find_links("note1.md", direction="outlinks"))
        assert result["success"] is True
        assert "total" in result


class TestFindLinksBoth:
    """Tests for find_links with direction='both'."""

    def test_both_returns_backlinks_and_outlinks(self, vault_config):
        """Should return both sections in one call."""
        result = json.loads(find_links("note2.md", direction="both"))
        assert result["success"] is True
        assert "backlinks" in result
        assert "outlinks" in result
        assert isinstance(result["backlinks"]["results"], list)
        assert isinstance(result["outlinks"]["results"], list)
        assert "total" in result["backlinks"]
        assert "total" in result["outlinks"]

    def test_both_pagination(self, vault_config):
        """Pagination should apply to both sections."""
        result = json.loads(find_links("note2.md", direction="both", limit=1))
        assert result["success"] is True
        assert len(result["backlinks"]["results"]) <= 1
        assert len(result["outlinks"]["results"]) <= 1

    def test_both_file_not_found(self, vault_config):
        """Should error for missing file in both mode."""
        result = json.loads(find_links("nonexistent.md", direction="both"))
        assert result["success"] is False

    def test_both_surfaces_outlink_read_error(self, vault_config):
        """Should return error if file becomes unreadable during outlink extraction."""
        note = vault_config / "unreadable.md"
        note.write_text("[[link]]")
        # Make file unreadable after resolve_file succeeds
        note.chmod(0o000)
        try:
            result = json.loads(find_links("unreadable.md", direction="both"))
            assert result["success"] is False
            assert "Reading file failed" in result["error"]
        finally:
            note.chmod(0o644)


class TestFindLinksValidation:
    """Tests for find_links input validation."""

    def test_invalid_direction(self, vault_config):
        """Should reject invalid direction values."""
        result = json.loads(find_links("note1.md", direction="invalid"))
        assert result["success"] is False
        assert "Invalid direction" in result["error"]

    def test_default_direction_is_both(self, vault_config):
        """Default direction should be 'both'."""
        result = json.loads(find_links("note2.md"))
        assert result["success"] is True
        assert "backlinks" in result
        assert "outlinks" in result


@pytest.mark.parametrize(
    ("kwargs", "expected_error"),
    [
        ({"offset": -1}, "offset must be >= 0"),
        ({"limit": 0}, "limit must be >= 1"),
        ({"limit": 2001}, "limit must be <= 2000"),
    ],
)
def test_paginated_link_tools_reject_invalid_pagination(vault_config, kwargs, expected_error):
    """find_links should return pagination validation errors for all directions."""
    for direction in ("backlinks", "outlinks", "both"):
        result = json.loads(find_links("note1.md", direction=direction, **kwargs))
        assert result["success"] is False
        assert expected_error in result["error"], f"Failed for direction={direction}"
