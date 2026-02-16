"""Tests for structure-aware markdown chunking."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from index_vault import (
    _fixed_chunk_text,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
)


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

    def test_frontmatter_only_no_dict(self):
        """File with only frontmatter and no frontmatter dict returns empty list."""
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

    @patch("index_vault.get_collection")
    def test_index_file_prepends_note_name(self, mock_get_collection, tmp_path):
        md_file = tmp_path / "Obsidian Tools.md"
        md_file.write_text("# Title\n\nContent.")

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        from index_vault import index_file
        index_file(md_file)

        assert mock_collection.upsert.called
        for call in mock_collection.upsert.call_args_list:
            doc_text = call[1]["documents"][0]
            assert doc_text.startswith("[Obsidian Tools] ")


class TestSearchHeadingMetadata:
    """Tests for heading metadata in search results."""

    @patch("hybrid_search.get_collection")
    def test_semantic_search_includes_heading(self, mock_get_collection):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["Some content"]],
            "metadatas": [[{"source": "file.md", "heading": "## Notes", "chunk_type": "section"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test query", n_results=1)
        assert results[0]["heading"] == "## Notes"

    @patch("hybrid_search.get_collection")
    def test_semantic_search_missing_heading_defaults(self, mock_get_collection):
        """Old chunks without heading metadata should get a default."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["Some content"]],
            "metadatas": [[{"source": "file.md"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test query", n_results=1)
        assert results[0]["heading"] == ""

    @patch("hybrid_search.get_collection")
    def test_keyword_search_includes_heading(self, mock_get_collection):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["Some searchable content"],
            "metadatas": [{"source": "file.md", "heading": "## Tasks", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("searchable content", n_results=1)
        assert results[0]["heading"] == "## Tasks"


class TestChunkTypeFilter:
    """Tests for chunk_type filtering in search."""

    @patch("hybrid_search.get_collection")
    def test_semantic_search_with_chunk_type(self, mock_get_collection):
        """Semantic search passes chunk_type as where filter."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["frontmatter content"]],
            "metadatas": [[{"source": "file.md", "heading": "frontmatter", "chunk_type": "frontmatter"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("project tags", n_results=5, chunk_type="frontmatter")

        call_kwargs = mock_collection.query.call_args[1]
        assert call_kwargs["where"] == {"chunk_type": "frontmatter"}
        assert len(results) == 1

    @patch("hybrid_search.get_collection")
    def test_semantic_search_no_chunk_type(self, mock_get_collection):
        """Semantic search without chunk_type sends no where filter."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["content"]],
            "metadatas": [[{"source": "file.md", "heading": "", "chunk_type": "section"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        semantic_search("test", n_results=5)

        call_kwargs = mock_collection.query.call_args[1]
        assert "where" not in call_kwargs

    @patch("hybrid_search.get_collection")
    def test_keyword_search_with_chunk_type(self, mock_get_collection):
        """Keyword search passes chunk_type as where filter."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["frontmatter content"],
            "metadatas": [{"source": "file.md", "heading": "frontmatter", "chunk_type": "frontmatter"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("content", n_results=5, chunk_type="frontmatter")

        call_kwargs = mock_collection.get.call_args[1]
        assert call_kwargs["where"] == {"chunk_type": "frontmatter"}

    @patch("hybrid_search.get_collection")
    def test_keyword_search_no_chunk_type(self, mock_get_collection):
        """Keyword search without chunk_type sends no where filter."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["some content"],
            "metadatas": [{"source": "file.md", "heading": "", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("content", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        assert "where" not in call_kwargs

    @patch("hybrid_search.get_collection")
    def test_hybrid_search_passes_chunk_type(self, mock_get_collection):
        """Hybrid search passes chunk_type through to both sub-searches."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["content"]],
            "metadatas": [[{"source": "a.md", "heading": "", "chunk_type": "frontmatter"}]],
        }
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["content"],
            "metadatas": [{"source": "a.md", "heading": "", "chunk_type": "frontmatter"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import hybrid_search
        hybrid_search("test query", n_results=5, chunk_type="frontmatter")

        # Both semantic (query) and keyword (get) should have where filter
        query_kwargs = mock_collection.query.call_args[1]
        get_kwargs = mock_collection.get.call_args[1]
        assert query_kwargs["where"] == {"chunk_type": "frontmatter"}
        assert get_kwargs["where"] == {"chunk_type": "frontmatter"}


class TestKeywordSearchOptimization:
    """Tests for optimized single-query keyword search."""

    @patch("hybrid_search.get_collection")
    def test_single_term_no_or_wrapper(self, mock_get_collection):
        """Single-term query should use $contains directly, not $or."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["some content here"],
            "metadatas": [{"source": "a.md", "heading": "## H", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("content", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        assert call_kwargs["where_document"] == {"$contains": "content"}

    @patch("hybrid_search.get_collection")
    def test_multi_term_uses_or_query(self, mock_get_collection):
        """Multi-term query should combine terms with $or."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["alpha bravo content", "bravo only content"],
            "metadatas": [
                {"source": "a.md", "heading": "", "chunk_type": "section"},
                {"source": "b.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("alpha bravo", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        where_doc = call_kwargs["where_document"]
        assert "$or" in where_doc
        assert {"$contains": "alpha"} in where_doc["$or"]
        assert {"$contains": "bravo"} in where_doc["$or"]

    @patch("hybrid_search.get_collection")
    def test_multi_term_ranked_by_hit_count(self, mock_get_collection):
        """Results should be ranked by number of matching terms."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["alpha bravo content", "bravo only content"],
            "metadatas": [
                {"source": "a.md", "heading": "", "chunk_type": "section"},
                {"source": "b.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("alpha bravo", n_results=5)

        # a.md matches both terms, b.md matches one
        assert results[0]["source"] == "a.md"
        assert results[1]["source"] == "b.md"

    @patch("hybrid_search.get_collection")
    def test_query_uses_limit(self, mock_get_collection):
        """Query should include a limit parameter."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("something", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        assert "limit" in call_kwargs
        assert call_kwargs["limit"] == 200


class TestLinkIndex:
    """Tests for wikilink index building."""

    def test_build_link_index_basic(self, tmp_path):
        """Should extract wikilinks and build reverse index."""
        (tmp_path / "a.md").write_text("Link to [[B]] and [[C|alias]].")
        (tmp_path / "b.md").write_text("Link to [[C]].")
        (tmp_path / "c.md").write_text("No links here.")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"])

        assert sorted(index["b"]) == [str(tmp_path / "a.md")]
        assert sorted(index["c"]) == [str(tmp_path / "a.md"), str(tmp_path / "b.md")]

    def test_build_link_index_case_insensitive(self, tmp_path):
        """Link targets should be lowercased for case-insensitive lookup."""
        (tmp_path / "a.md").write_text("Link to [[MyNote]].")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md"])

        assert "mynote" in index

    def test_build_link_index_empty(self, tmp_path):
        """Files with no wikilinks produce empty index."""
        (tmp_path / "a.md").write_text("No links.")

        from index_vault import build_link_index
        index = build_link_index([tmp_path / "a.md"])

        assert index == {}


class TestStripWikilinkBrackets:
    """Tests for wikilink bracket stripping."""

    def test_simple_wikilink(self):
        assert _strip_wikilink_brackets("[[kerri del bene]]") == "kerri del bene"

    def test_aliased_wikilink(self):
        assert _strip_wikilink_brackets("[[kerri del bene|Kerri]]") == "Kerri"

    def test_no_wikilinks(self):
        assert _strip_wikilink_brackets("plain text") == "plain text"

    def test_multiple_wikilinks(self):
        result = _strip_wikilink_brackets("met [[alice]] and [[bob|Robert]]")
        assert result == "met alice and Robert"


class TestFormatFrontmatterForIndexing:
    """Tests for frontmatter-to-text conversion."""

    def test_simple_fields(self):
        fm = {"tags": "meeting", "company": "Acme"}
        result = format_frontmatter_for_indexing(fm)
        assert "tags: meeting" in result
        assert "company: Acme" in result

    def test_list_values(self):
        fm = {"tags": ["meeting", "project"]}
        result = format_frontmatter_for_indexing(fm)
        assert "tags: meeting, project" in result

    def test_wikilinks_stripped(self):
        fm = {"people": ["[[kerri del bene]]", "[[alice smith]]"]}
        result = format_frontmatter_for_indexing(fm)
        assert "kerri del bene" in result
        assert "[[" not in result

    def test_excluded_fields(self):
        fm = {"cssclass": "wide", "aliases": ["test"], "tags": "meeting"}
        result = format_frontmatter_for_indexing(fm)
        assert "cssclass" not in result
        assert "aliases" not in result
        assert "tags: meeting" in result

    def test_none_values_skipped(self):
        fm = {"title": None, "tags": "meeting"}
        result = format_frontmatter_for_indexing(fm)
        assert "title" not in result
        assert "tags: meeting" in result

    def test_empty_dict(self):
        assert format_frontmatter_for_indexing({}) == ""


class TestFrontmatterIndexing:
    """Tests for frontmatter chunk generation in chunk_markdown."""

    def test_frontmatter_chunk_created(self):
        text = "---\ntags: meeting\n---\n\n# Heading\n\nBody."
        fm = {"tags": "meeting"}
        chunks = chunk_markdown(text, frontmatter=fm)
        fm_chunks = [c for c in chunks if c["chunk_type"] == "frontmatter"]
        assert len(fm_chunks) == 1
        assert "tags: meeting" in fm_chunks[0]["text"]
        assert fm_chunks[0]["heading"] == "frontmatter"

    def test_frontmatter_chunk_is_first(self):
        text = "---\ntags: meeting\n---\n\n# Heading\n\nBody."
        fm = {"tags": "meeting"}
        chunks = chunk_markdown(text, frontmatter=fm)
        assert chunks[0]["chunk_type"] == "frontmatter"

    def test_wikilinks_stripped_in_frontmatter_chunk(self):
        text = "---\npeople:\n  - '[[kerri del bene]]'\n---\n\n# H\n\nBody."
        fm = {"people": ["[[kerri del bene]]"]}
        chunks = chunk_markdown(text, frontmatter=fm)
        fm_chunk = [c for c in chunks if c["chunk_type"] == "frontmatter"][0]
        assert "kerri del bene" in fm_chunk["text"]
        assert "[[" not in fm_chunk["text"]

    def test_frontmatter_only_file_indexed(self):
        text = "---\ntags: person\ncompany: Acme\n---\n"
        fm = {"tags": "person", "company": "Acme"}
        chunks = chunk_markdown(text, frontmatter=fm)
        assert len(chunks) == 1
        assert chunks[0]["chunk_type"] == "frontmatter"

    def test_no_frontmatter_param_no_extra_chunk(self):
        text = "# Heading\n\nBody."
        chunks = chunk_markdown(text)
        assert all(c["chunk_type"] != "frontmatter" for c in chunks)

    def test_none_frontmatter_no_extra_chunk(self):
        text = "# Heading\n\nBody."
        chunks = chunk_markdown(text, frontmatter=None)
        assert all(c["chunk_type"] != "frontmatter" for c in chunks)

    def test_empty_frontmatter_no_chunk(self):
        text = "# Heading\n\nBody."
        chunks = chunk_markdown(text, frontmatter={})
        assert all(c["chunk_type"] != "frontmatter" for c in chunks)

    def test_body_chunks_unchanged(self):
        """Body chunks are identical whether or not frontmatter is passed."""
        text = "---\ntags: meeting\n---\n\n# Heading\n\nBody text here."
        chunks_without = chunk_markdown(text)
        chunks_with = chunk_markdown(text, frontmatter={"tags": "meeting"})
        body_chunks = [c for c in chunks_with if c["chunk_type"] != "frontmatter"]
        assert body_chunks == chunks_without


class TestIndexFileBatching:
    """Tests for batched upsert in index_file."""

    @patch("index_vault.get_collection")
    def test_single_upsert_call_per_file(self, mock_get_collection):
        """index_file should call upsert once with all chunks, not once per chunk."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Section 1\n\nContent one.\n\n# Section 2\n\nContent two.\n")
            tmp_path = Path(f.name)

        try:
            from index_vault import index_file
            index_file(tmp_path)

            assert mock_collection.upsert.call_count == 1
            call_args = mock_collection.upsert.call_args[1]
            assert len(call_args["ids"]) >= 2
            assert len(call_args["documents"]) == len(call_args["ids"])
            assert len(call_args["metadatas"]) == len(call_args["ids"])
        finally:
            tmp_path.unlink()
