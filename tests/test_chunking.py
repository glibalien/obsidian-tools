"""Tests for structure-aware markdown chunking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from index_vault import _fixed_chunk_text, chunk_markdown


# --- _fixed_chunk_text tests ---


class TestFixedChunkText:
    """Tests for the character-based fallback chunker."""

    def test_basic_split(self):
        """Text longer than chunk_size is split into overlapping chunks."""
        text = "a" * 100
        chunks = _fixed_chunk_text(text, chunk_size=40, overlap=10)
        assert len(chunks) > 1
        # First chunk is 40 chars
        assert len(chunks[0]) == 40
        # Overlap: second chunk starts at position 30
        assert chunks[1][:10] == chunks[0][30:40]

    def test_short_text_single_chunk(self):
        """Text shorter than chunk_size returns a single chunk."""
        text = "Hello world"
        chunks = _fixed_chunk_text(text, chunk_size=500, overlap=50)
        assert chunks == ["Hello world"]

    def test_empty_text(self):
        """Empty text returns an empty list (no chunks)."""
        chunks = _fixed_chunk_text("", chunk_size=500, overlap=50)
        assert chunks == []


# --- chunk_markdown frontmatter handling ---


class TestChunkMarkdownFrontmatter:
    """Tests for YAML frontmatter stripping."""

    def test_frontmatter_stripped(self):
        """Frontmatter is not included in any chunk text."""
        text = "---\ntitle: Test\ntags:\n  - foo\n---\n\n# Heading\n\nBody text here."
        chunks = chunk_markdown(text)
        for chunk in chunks:
            assert "---" not in chunk["text"]
            assert "title: Test" not in chunk["text"]

    def test_no_frontmatter(self):
        """Text without frontmatter is chunked normally."""
        text = "# Heading\n\nSome body content."
        chunks = chunk_markdown(text)
        assert len(chunks) >= 1
        assert any("Some body content" in c["text"] for c in chunks)

    def test_frontmatter_only(self):
        """File with only frontmatter and no body returns empty list."""
        text = "---\ntitle: Empty\n---\n"
        chunks = chunk_markdown(text)
        assert chunks == []


# --- chunk_markdown heading splitting ---


class TestChunkMarkdownHeadings:
    """Tests for heading-based section splitting."""

    def test_splits_on_headings(self):
        """Each heading starts a new chunk."""
        text = "# First\n\nContent one.\n\n## Second\n\nContent two."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "# First" in headings
        assert "## Second" in headings

    def test_chunk_type_is_section(self):
        """Small sections that fit in one chunk have chunk_type 'section'."""
        text = "# Heading\n\nShort content."
        chunks = chunk_markdown(text)
        assert all(c["chunk_type"] == "section" for c in chunks)

    def test_top_level_content(self):
        """Content before the first heading gets heading='top-level'."""
        text = "Some intro text.\n\n# Heading\n\nBody."
        chunks = chunk_markdown(text)
        top = [c for c in chunks if c["heading"] == "top-level"]
        assert len(top) == 1
        assert "Some intro text" in top[0]["text"]

    def test_heading_in_backtick_code_fence_ignored(self):
        """Headings inside ``` code fences are not treated as section breaks."""
        text = "# Real Heading\n\nBefore code.\n\n```\n# Not a heading\ncode here\n```\n\nAfter code."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "# Not a heading" not in headings
        # All content is under the one real heading
        assert len(chunks) == 1

    def test_heading_in_tilde_code_fence_ignored(self):
        """Headings inside ~~~ code fences are not treated as section breaks."""
        text = "# Real Heading\n\nBefore.\n\n~~~\n## Fake\ncode\n~~~\n\nAfter."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "## Fake" not in headings
        assert len(chunks) == 1

    def test_nested_headings(self):
        """Different heading levels each produce separate sections."""
        text = "# H1\n\nContent.\n\n## H2\n\nMore.\n\n### H3\n\nDeep."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "# H1" in headings
        assert "## H2" in headings
        assert "### H3" in headings

    def test_heading_included_in_chunk_text(self):
        """The heading line itself is included in the chunk text for search context."""
        text = "## My Section\n\nSection body."
        chunks = chunk_markdown(text)
        assert len(chunks) == 1
        assert "## My Section" in chunks[0]["text"]

    def test_top_level_chunk_no_heading_prefix(self):
        """Top-level chunks do not have a heading line prepended."""
        text = "Just some intro.\n\n# Later\n\nBody."
        chunks = chunk_markdown(text)
        top = [c for c in chunks if c["heading"] == "top-level"]
        assert len(top) == 1
        # Should not start with a # heading
        assert not top[0]["text"].strip().startswith("#")


# --- chunk_markdown paragraph fallback ---


class TestChunkMarkdownParagraphFallback:
    """Tests for paragraph splitting of large sections."""

    def test_large_section_splits_on_paragraphs(self):
        """A section too large for one chunk is split on paragraph boundaries."""
        paragraphs = ["Paragraph number %d. " % i + "x" * 200 for i in range(10)]
        body = "\n\n".join(paragraphs)
        text = "# Big Section\n\n" + body
        chunks = chunk_markdown(text, max_chunk_size=500)
        # Should produce multiple chunks
        assert len(chunks) > 1
        # At least one should be paragraph type
        assert any(c["chunk_type"] == "paragraph" for c in chunks)
        # All should reference the heading
        assert all(c["heading"] == "# Big Section" for c in chunks)


# --- chunk_markdown sentence fallback ---


class TestChunkMarkdownSentenceFallback:
    """Tests for sentence splitting of large paragraphs."""

    def test_large_paragraph_splits_on_sentences(self):
        """A paragraph too large for one chunk is split on sentence boundaries."""
        sentences = ["This is sentence number %d." % i for i in range(50)]
        # Single paragraph (no double newlines)
        text = "# Heading\n\n" + " ".join(sentences)
        chunks = chunk_markdown(text, max_chunk_size=200)
        assert len(chunks) > 1
        assert any(c["chunk_type"] == "sentence" for c in chunks)


# --- chunk_markdown fragment fallback ---


class TestChunkMarkdownFragmentFallback:
    """Tests for character-split fallback when no boundaries exist."""

    def test_no_boundaries_falls_back_to_fixed_chunk(self):
        """A long string with no sentence boundaries falls back to fixed chunking."""
        # No periods, question marks, or exclamation marks — no sentence boundaries
        long_word = "x" * 3000
        text = "# Heading\n\n" + long_word
        chunks = chunk_markdown(text, max_chunk_size=500)
        assert len(chunks) > 1
        assert any(c["chunk_type"] == "fragment" for c in chunks)


# --- chunk_markdown metadata ---


class TestChunkMarkdownMetadata:
    """Tests for chunk metadata structure."""

    def test_required_keys_present(self):
        """Every chunk dict has text, heading, and chunk_type keys."""
        text = "# Hello\n\nWorld."
        chunks = chunk_markdown(text)
        for chunk in chunks:
            assert "text" in chunk
            assert "heading" in chunk
            assert "chunk_type" in chunk

    def test_empty_input(self):
        """Empty string returns empty list."""
        assert chunk_markdown("") == []

    def test_whitespace_only(self):
        """Whitespace-only input returns empty list."""
        assert chunk_markdown("   \n\n  \t  ") == []


# --- index_file integration ---


from unittest.mock import MagicMock, patch


class TestIndexFileMetadata:
    """Tests for enriched metadata in index_file."""

    @patch("index_vault.get_collection")
    def test_index_file_stores_heading_and_chunk_type(self, mock_get_collection, tmp_path):
        md_file = tmp_path / "test.md"
        md_file.write_text("---\ntags: test\n---\n\n# Title\n\nContent.\n\n## Section\n\nMore.")

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        from index_vault import index_file
        index_file(md_file)

        # Verify upsert was called with heading and chunk_type in metadata
        assert mock_collection.upsert.called
        for call in mock_collection.upsert.call_args_list:
            metadata = call[1]["metadatas"][0]
            assert "heading" in metadata
            assert "chunk_type" in metadata
            assert "source" in metadata
            assert "chunk" in metadata
