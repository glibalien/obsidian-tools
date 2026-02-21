"""Tests for tools/files.py - file operations."""

import json

import pytest

from services.vault import clear_pending_previews
from tools.files import (
    _merge_bodies,
    _merge_frontmatter,
    _split_blocks,
    _split_frontmatter_body,
    append_to_file,
    batch_move_files,
    create_file,
    move_file,
    read_file,
)


class TestReadFile:
    """Tests for read_file tool."""

    def test_read_existing_file(self, vault_config):
        """Should read content of existing file."""
        result = json.loads(read_file("note1.md"))
        assert result["success"] is True
        assert "# Note 1" in result["content"]
        assert "wikilink" in result["content"]

    def test_read_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(read_file("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_read_file_in_subdirectory(self, vault_config):
        """Should read file in subdirectory."""
        result = json.loads(read_file("projects/project1.md"))
        assert result["success"] is True
        assert "# Project 1" in result["content"]

    def test_short_file_no_markers(self, vault_config):
        """Short files should be returned in full with no markers."""
        result = json.loads(read_file("note3.md"))
        assert result["success"] is True
        assert "[... truncated" not in result["content"]
        assert "[Continuing from" not in result["content"]
        assert "# Note 3" in result["content"]

    def test_long_file_truncated_with_marker(self, vault_config):
        """Files longer than length should have a continuation marker."""
        long_content = "# Long Note\n\n" + "x" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = json.loads(read_file("long.md"))
        assert result["success"] is True
        assert result["content"].startswith("# Long Note")
        assert "[... truncated at char 3500 of" in result["content"]
        assert "Use offset=3500 to read more." in result["content"]

    def test_offset_pagination(self, vault_config):
        """Reading with offset should show continuation header and may show truncation marker."""
        long_content = "A" * 10000
        (vault_config / "long.md").write_text(long_content)
        result = json.loads(read_file("long.md", offset=3500))
        assert result["success"] is True
        assert "[Continuing from char 3500 of 10000]" in result["content"]
        assert "[... truncated at char 7000 of 10000" in result["content"]

    def test_offset_final_chunk(self, vault_config):
        """Reading the last chunk should have no truncation marker."""
        long_content = "B" * 5000
        (vault_config / "long.md").write_text(long_content)
        result = json.loads(read_file("long.md", offset=3500))
        assert result["success"] is True
        assert "[Continuing from char 3500 of 5000]" in result["content"]
        assert "[... truncated" not in result["content"]

    def test_offset_past_end(self, vault_config):
        """Offset past end of file should return error."""
        result = json.loads(read_file("note3.md", offset=99999))
        assert result["success"] is False
        assert "offset" in result["error"].lower()

    def test_read_file_utf8_encoding(self, vault_config):
        """Should handle UTF-8 content including non-ASCII characters."""
        content = "# Café\n\nRésumé with émojis: 🎉"
        (vault_config / "utf8.md").write_text(content, encoding="utf-8")
        result = json.loads(read_file("utf8.md"))
        assert result["success"] is True
        assert "Café" in result["content"]
        assert "🎉" in result["content"]

    def test_custom_length(self, vault_config):
        """Custom length parameter should control chunk size."""
        content = "C" * 500
        (vault_config / "custom.md").write_text(content)
        result = json.loads(read_file("custom.md", length=100))
        assert result["success"] is True
        assert "[... truncated at char 100 of 500" in result["content"]
        # Content before the truncation marker should be 100 chars
        before_marker = result["content"].split("\n\n[... truncated")[0]
        assert len(before_marker) == 100


class TestCreateFile:
    """Tests for create_file tool."""

    def test_create_simple_file(self, vault_config):
        """Should create a new file."""
        result = json.loads(create_file("new_note.md", "# New Note\n\nContent here."))
        assert result["success"] is True
        assert "new_note.md" in result["path"]

        # Verify file was created
        content = (vault_config / "new_note.md").read_text()
        assert "# New Note" in content

    def test_create_file_with_frontmatter_dict(self, vault_config):
        """Should create file with native dict frontmatter."""
        result = json.loads(create_file(
            "with_fm_dict.md",
            "Content",
            frontmatter={"tags": ["test"], "status": "draft"}
        ))
        assert result["success"] is True

        content = (vault_config / "with_fm_dict.md").read_text()
        assert "---" in content
        assert "tags:" in content
        assert "test" in content

    def test_create_file_with_frontmatter_json_string(self, vault_config):
        """Should create file with JSON string frontmatter for backward compatibility."""
        result = json.loads(create_file(
            "with_fm_json.md",
            "Content",
            frontmatter='{"tags": ["test"], "status": "draft"}'
        ))
        assert result["success"] is True

        content = (vault_config / "with_fm_json.md").read_text()
        assert "---" in content
        assert "tags:" in content
        assert "test" in content

    def test_create_file_already_exists(self, vault_config):
        """Should return error if file exists."""
        result = json.loads(create_file("note1.md", "content"))
        assert result["success"] is False
        assert "already exists" in result["error"].lower()

    def test_create_file_in_new_directory(self, vault_config):
        """Should create parent directories if needed."""
        result = json.loads(create_file("new_dir/nested/note.md", "content"))
        assert result["success"] is True
        assert (vault_config / "new_dir" / "nested" / "note.md").exists()

    def test_create_file_invalid_frontmatter(self, vault_config):
        """Should return error for invalid JSON frontmatter."""
        result = json.loads(create_file("test.md", "content", frontmatter="not valid json"))
        assert result["success"] is False
        assert "invalid frontmatter json" in result["error"].lower()

    def test_create_file_non_object_frontmatter_json(self, vault_config):
        """Should return error when frontmatter JSON is not an object."""
        result = json.loads(create_file("test.md", "content", frontmatter='["not", "an", "object"]'))
        assert result["success"] is False
        assert "expected a json object" in result["error"].lower()


class TestMoveFile:
    """Tests for move_file tool."""

    def test_move_file_success(self, vault_config):
        """Should move file to new location."""
        result = json.loads(move_file("note3.md", "archive/note3.md"))
        assert result["success"] is True
        assert "Moved" in result["message"]

        assert not (vault_config / "note3.md").exists()
        assert (vault_config / "archive" / "note3.md").exists()

    def test_move_file_source_not_found(self, vault_config):
        """Should return error for missing source."""
        result = json.loads(move_file("nonexistent.md", "destination.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_move_file_destination_exists(self, vault_config):
        """Should return error if destination exists."""
        result = json.loads(move_file("note1.md", "note2.md"))
        assert result["success"] is False
        assert "already exists" in result["error"].lower()

    def test_move_file_same_path(self, vault_config):
        """Should handle moving to same location."""
        result = json.loads(move_file("note1.md", "note1.md"))
        assert result["success"] is True
        assert "Already at destination" in result["message"]


class TestAppendToFile:
    """Tests for append_to_file tool."""

    def test_append_content(self, vault_config):
        """Should append content to file."""
        result = json.loads(append_to_file("note3.md", "\n## New Section\n\nAppended content."))
        assert result["success"] is True
        assert result["path"]

        content = (vault_config / "note3.md").read_text()
        assert "New Section" in content
        assert "Appended content" in content

    def test_append_to_nonexistent_file(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(append_to_file("nonexistent.md", "content"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestBatchMoveConfirmationGate:
    """Tests for batch_move_files confirmation requirement."""

    def _create_files(self, vault_config, count):
        """Create N test files and return move dicts."""
        (vault_config / "dest").mkdir(exist_ok=True)
        moves = []
        for i in range(count):
            name = f"move_test_{i}.md"
            (vault_config / name).write_text(f"# Note {i}\n")
            moves.append({"source": name, "destination": f"dest/{name}"})
        return moves

    def test_requires_confirmation_over_threshold(self, vault_config):
        """Should return preview when move count exceeds threshold."""
        clear_pending_previews()
        moves = self._create_files(vault_config, 10)
        result = json.loads(batch_move_files(moves=moves))
        assert result["success"] is True
        assert result["confirmation_required"] is True
        assert "10 files" in result["preview_message"]
        assert len(result["files"]) == 10
        # Verify no files were actually moved
        for move in moves:
            assert (vault_config / move["source"]).exists()
            assert not (vault_config / move["destination"]).exists()

    def test_executes_with_confirm_true(self, vault_config):
        """Should execute when confirm=True even over threshold."""
        clear_pending_previews()
        moves = self._create_files(vault_config, 10)
        # First call: store preview
        batch_move_files(moves=moves)
        # Second call: confirm execution
        result = json.loads(batch_move_files(moves=moves, confirm=True))
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "10 succeeded" in result["message"]

    def test_executes_under_threshold_without_confirm(self, vault_config):
        """Should execute without confirm when move count is at or below threshold."""
        moves = self._create_files(vault_config, 3)
        result = json.loads(batch_move_files(moves=moves))
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "3 succeeded" in result["message"]

    def test_confirm_true_without_preview_returns_preview(self, vault_config):
        """Passing confirm=True on first call should still return preview."""
        clear_pending_previews()
        moves = self._create_files(vault_config, 10)
        result = json.loads(batch_move_files(moves=moves, confirm=True))
        assert result["success"] is True
        assert result["confirmation_required"] is True
        # No files should be moved
        for move in moves:
            assert (vault_config / move["source"]).exists()
            assert not (vault_config / move["destination"]).exists()

    def test_two_step_confirmation_flow(self, vault_config):
        """Preview then confirm should execute the batch move."""
        clear_pending_previews()
        moves = self._create_files(vault_config, 8)
        # Step 1: preview
        preview = json.loads(batch_move_files(moves=moves))
        assert preview["confirmation_required"] is True
        # Step 2: confirm
        result = json.loads(batch_move_files(moves=moves, confirm=True))
        assert result["success"] is True
        assert "confirmation_required" not in result
        assert "8 succeeded" in result["message"]

    def test_batch_move_preview_has_preview_message(self, vault_config):
        """Preview should include separate preview_message for UI display."""
        clear_pending_previews()
        moves = []
        for i in range(10):
            path = f"move_preview_{i}.md"
            (vault_config / path).write_text(f"---\ntitle: test{i}\n---\n")
            moves.append({"source": path, "destination": f"dest/move_preview_{i}.md"})
        (vault_config / "dest").mkdir(exist_ok=True)
        result = json.loads(batch_move_files(moves=moves))
        assert "preview_message" in result
        assert "This will move 10 files" in result["preview_message"]
        assert "confirm=true" not in result["preview_message"]

    def test_confirmation_is_single_use(self, vault_config):
        """After execution, same confirm=True requires a new preview."""
        clear_pending_previews()
        moves = self._create_files(vault_config, 8)
        # Step 1: preview
        batch_move_files(moves=moves)
        # Step 2: confirm and execute
        batch_move_files(moves=moves, confirm=True)
        # Step 3: confirm again without new preview — should return preview
        # Recreate files since they were moved
        for move in moves:
            (vault_config / move["source"]).write_text("# Recreated\n")
        result = json.loads(batch_move_files(moves=moves, confirm=True))
        assert result["confirmation_required"] is True


class TestSplitFrontmatterBody:
    """Tests for _split_frontmatter_body helper."""

    def test_file_with_frontmatter(self):
        content = "---\ntitle: Test\ntags:\n  - a\n---\n\n# Body\n\nParagraph."
        fm, body = _split_frontmatter_body(content)
        assert fm == {"title": "Test", "tags": ["a"]}
        assert body.strip() == "# Body\n\nParagraph."

    def test_file_without_frontmatter(self):
        content = "# Just a heading\n\nSome text."
        fm, body = _split_frontmatter_body(content)
        assert fm == {}
        assert body == content

    def test_empty_frontmatter(self):
        content = "---\n---\n\nBody text."
        fm, body = _split_frontmatter_body(content)
        assert fm == {}
        assert body.strip() == "Body text."

    def test_frontmatter_with_empty_body(self):
        content = "---\ntitle: Note\n---\n"
        fm, body = _split_frontmatter_body(content)
        assert fm == {"title": "Note"}
        assert body.strip() == ""


class TestMergeFrontmatter:
    """Tests for _merge_frontmatter helper."""

    def test_source_adds_new_fields(self):
        source = {"author": "Alice", "tags": ["draft"]}
        dest = {"title": "Note"}
        merged = _merge_frontmatter(source, dest)
        assert merged == {"title": "Note", "author": "Alice", "tags": ["draft"]}

    def test_destination_wins_scalar_conflict(self):
        source = {"title": "Old Title", "author": "Alice"}
        dest = {"title": "New Title"}
        merged = _merge_frontmatter(source, dest)
        assert merged["title"] == "New Title"
        assert merged["author"] == "Alice"

    def test_list_fields_union_deduped(self):
        source = {"tags": ["a", "b", "c"]}
        dest = {"tags": ["b", "c", "d"]}
        merged = _merge_frontmatter(source, dest)
        assert merged["tags"] == ["b", "c", "d", "a"]

    def test_source_list_dest_scalar_dest_wins(self):
        source = {"status": ["draft", "review"]}
        dest = {"status": "published"}
        merged = _merge_frontmatter(source, dest)
        assert merged["status"] == "published"

    def test_both_empty(self):
        assert _merge_frontmatter({}, {}) == {}

    def test_source_empty(self):
        dest = {"title": "Keep"}
        assert _merge_frontmatter({}, dest) == {"title": "Keep"}

    def test_dest_empty(self):
        source = {"title": "Bring"}
        assert _merge_frontmatter(source, {}) == {"title": "Bring"}

    def test_identical_frontmatter_unchanged(self):
        fm = {"title": "Same", "tags": ["a", "b"]}
        merged = _merge_frontmatter(fm.copy(), fm.copy())
        assert merged == fm


class TestSplitBlocks:
    """Tests for _split_blocks helper."""

    def test_split_by_headings(self):
        body = "# Intro\n\nParagraph.\n\n## Tasks\n\n- Item 1\n- Item 2\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 2
        assert blocks[0] == ("# Intro", "# Intro\n\nParagraph.\n\n")
        assert blocks[1] == ("## Tasks", "## Tasks\n\n- Item 1\n- Item 2\n")

    def test_content_before_first_heading(self):
        body = "Some intro text.\n\n# Heading\n\nContent.\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 2
        assert blocks[0] == (None, "Some intro text.\n\n")
        assert blocks[1] == ("# Heading", "# Heading\n\nContent.\n")

    def test_no_headings(self):
        body = "Just a paragraph.\n\nAnother paragraph.\n"
        blocks = _split_blocks(body)
        assert len(blocks) == 1
        assert blocks[0] == (None, body)

    def test_empty_body(self):
        blocks = _split_blocks("")
        assert blocks == []

    def test_whitespace_only(self):
        blocks = _split_blocks("  \n\n  \n")
        assert blocks == []


class TestMergeBodies:
    """Tests for _merge_bodies helper."""

    def test_identical_bodies_no_change(self):
        body = "# Tasks\n\n- Item 1\n- Item 2\n"
        merged, stats = _merge_bodies(body, body)
        assert merged == body
        assert stats["blocks_added"] == 0

    def test_source_has_unique_block_under_existing_heading(self):
        source = "# Tasks\n\n- Item 1\n\n# Notes\n\nSource note.\n"
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, dest)
        assert "Source note." in merged
        assert "# Notes" in merged
        assert stats["blocks_added"] == 1

    def test_source_unique_block_appended_when_no_heading_match(self):
        source = "# Unrelated\n\nNew stuff.\n"
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, dest)
        assert "New stuff." in merged
        assert "# Unrelated" in merged
        assert merged.startswith("# Tasks\n")
        assert stats["blocks_added"] == 1

    def test_duplicate_blocks_not_added(self):
        source = "# Tasks\n\n- Item 1\n\n# Notes\n\nShared note.\n"
        dest = "# Tasks\n\n- Item 1\n\n# Notes\n\nShared note.\n"
        merged, stats = _merge_bodies(source, dest)
        assert merged == dest
        assert stats["blocks_added"] == 0

    def test_partial_overlap(self):
        source = "# Tasks\n\n- Item 1\n\n# Log\n\nEntry A.\n"
        dest = "# Tasks\n\n- Item 1\n\n# Log\n\nEntry B.\n"
        merged, stats = _merge_bodies(source, dest)
        assert "Entry A." in merged
        assert "Entry B." in merged
        assert stats["blocks_added"] == 1

    def test_empty_source_body(self):
        dest = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies("", dest)
        assert merged == dest
        assert stats["blocks_added"] == 0

    def test_empty_dest_body(self):
        source = "# Tasks\n\n- Item 1\n"
        merged, stats = _merge_bodies(source, "")
        assert "# Tasks" in merged
        assert "- Item 1" in merged
        assert stats["blocks_added"] == 1
