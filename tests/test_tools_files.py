"""Tests for tools/files.py - file operations."""

import json

import pytest

from services.vault import clear_pending_previews
from tools.files import (
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
        assert "10 files" in result["message"]
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
