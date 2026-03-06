"""Tests for structure-aware markdown chunking."""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from chunking import (
    OVERLAP_SENTENCES,
    _fixed_chunk_text,
    _split_by_headings,
    _split_sentences,
    _strip_wikilink_brackets,
    _trailing_sentences,
    chunk_markdown,
    format_frontmatter_for_indexing,
)
from index_vault import (
    _prepare_file_chunks,
    get_last_run,
    index_vault,
    load_manifest,
    mark_run,
    prune_deleted_files,
    save_manifest,
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


# --- _split_sentences tests ---


class TestSplitSentences:
    """Tests for sentence boundary detection."""

    def test_basic_split(self):
        """Splits on standard sentence-ending punctuation."""
        assert _split_sentences("Hello world. How are you? Fine!") == [
            "Hello world.", "How are you?", "Fine!",
        ]

    def test_eg_ie(self):
        """e.g. and i.e. are not treated as sentence boundaries."""
        result = _split_sentences("Use tools e.g. grep or rg. Next.")
        assert result == ["Use tools e.g. grep or rg.", "Next."]

        result = _split_sentences("A format i.e. JSON works. Done.")
        assert result == ["A format i.e. JSON works.", "Done."]

    def test_abbreviations_split_normally(self):
        """All abbreviations (Dr., Mr., etc.) split like regular periods."""
        assert _split_sentences("Dr. Smith is here.") == ["Dr.", "Smith is here."]
        assert _split_sentences("Bring fruit, etc. Please hurry.") == [
            "Bring fruit, etc.", "Please hurry.",
        ]

    def test_decimal_numbers_no_space(self):
        """Decimals like 3.14 have no space after the period, so never match."""
        result = _split_sentences("Pi is 3.14 approximately. Next.")
        assert result == ["Pi is 3.14 approximately.", "Next."]

    def test_digit_led_sentence_splits(self):
        """Sentences starting with a digit split correctly."""
        result = _split_sentences("There were 10. 5 remained.")
        assert result == ["There were 10.", "5 remained."]

    def test_question_and_exclamation(self):
        """Question marks and exclamation points still split normally."""
        assert _split_sentences("Really? Yes! Okay.") == [
            "Really?", "Yes!", "Okay.",
        ]

    def test_no_boundaries(self):
        """Text with no sentence-ending punctuation returns as single item."""
        assert _split_sentences("just some text") == ["just some text"]

    def test_empty_string(self):
        """Empty string returns empty list."""
        assert _split_sentences("") == []


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
    @patch("index_vault.embed_documents", side_effect=lambda docs: [[0.1]] * len(docs))
    def test_index_file_prepends_note_name(self, mock_embed, mock_get_collection, tmp_path):
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
            assert doc_text.startswith("[Obsidian Tools > Title]")


class TestSearchHeadingMetadata:
    """Tests for heading metadata in search results."""

    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_semantic_search_includes_heading(self, mock_embed, mock_get_collection):
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
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_semantic_search_missing_heading_defaults(self, mock_embed, mock_get_collection):
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

    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1, 0.2])
    def test_semantic_search_uses_embed_query(self, mock_embed, mock_get_collection):
        """semantic_search uses embed_query and passes query_embeddings."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        semantic_search("test query")

        mock_embed.assert_called_once_with("test query")
        call_args = mock_collection.query.call_args[1]
        assert call_args["query_embeddings"] == [[0.1, 0.2]]


class TestChunkTypeFilter:
    """Tests for chunk_type filtering in search."""

    @patch("hybrid_search.embed_query", return_value=[0.1])
    @patch("hybrid_search.get_collection")
    def test_semantic_search_with_chunk_type(self, mock_get_collection, mock_embed):
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

    @patch("hybrid_search.embed_query", return_value=[0.1])
    @patch("hybrid_search.get_collection")
    def test_semantic_search_no_chunk_type(self, mock_get_collection, mock_embed):
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
    def test_single_term_includes_case_variants(self, mock_get_collection):
        """Single-term query should include case variants in $or."""
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
        where_doc = call_kwargs["where_document"]
        contains_values = [c["$contains"] for c in where_doc["$or"]]
        assert "content" in contains_values
        assert "Content" in contains_values

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
        contains_values = [c["$contains"] for c in where_doc["$or"]]
        assert "alpha" in contains_values
        assert "Alpha" in contains_values
        assert "bravo" in contains_values
        assert "Bravo" in contains_values

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



    @patch("hybrid_search.get_collection")
    def test_term_frequency_ranking(self, mock_get_collection):
        """Results ranked by term frequency, not just presence."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": [
                "project mentioned once",
                "project project project mentioned many times project",
            ],
            "metadatas": [
                {"source": "once.md", "heading": "", "chunk_type": "section"},
                {"source": "many.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("project", n_results=5)

        # many.md has 4 occurrences, once.md has 1
        assert results[0]["source"] == "many.md"
        assert results[1]["source"] == "once.md"

    @patch("hybrid_search.get_collection")
    def test_keyword_results_not_truncated(self, mock_get_collection):
        """Keyword results return full chunk content, not truncated to 500 chars."""
        long_content = "x" * 1000
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": [long_content],
            "metadatas": [{"source": "a.md", "heading": "", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("xxx", n_results=5)

        assert len(results[0]["content"]) == 1000

    def test_expanded_stopwords(self):
        """Common English words are filtered from queries."""
        from hybrid_search import _extract_query_terms
        terms = _extract_query_terms("this project has been about will")
        # "this", "has", "been", "about", "will" are stopwords
        assert terms == ["project"]

    def test_original_stopwords_still_filtered(self):
        """Original stopwords remain filtered."""
        from hybrid_search import _extract_query_terms
        terms = _extract_query_terms("the project for testing")
        assert "the" not in terms
        assert "for" not in terms
        assert "project" in terms
        assert "testing" in terms

    @pytest.mark.parametrize(
        ("query", "expected_variants"),
        [
            ("Adam Bird", ["adam", "Adam", "bird", "Bird"]),
            ("Adam", ["adam", "Adam"]),
            ("adam bird", ["adam", "Adam", "bird", "Bird"]),
        ],
        ids=["mixed_case_multi", "single_term", "lowercase_multi"],
    )
    @patch("hybrid_search.get_collection")
    def test_case_insensitive_contains(self, mock_get_collection, query, expected_variants):
        """Keyword search should include case variants in $contains query."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["Adam Bird is here"],
            "metadatas": [{"source": "a.md", "heading": "", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search(query, n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        where_doc = call_kwargs["where_document"]
        contains_values = [c["$contains"] for c in where_doc["$or"]]
        for variant in expected_variants:
            assert variant in contains_values


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

    @patch("index_vault.get_collection")
    @patch("index_vault.embed_documents", side_effect=lambda docs: [[0.1]] * len(docs))
    def test_index_file_passes_precomputed_embeddings(self, mock_embed, mock_get_collection, tmp_path):
        """index_file passes pre-computed embeddings and stores clean documents."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello\n\nSome content here.\n")

        from index_vault import index_file
        index_file(md_file)

        mock_embed.assert_called_once()
        call_args = mock_collection.upsert.call_args[1]
        # Documents should NOT have prefix (clean text)
        for doc in call_args["documents"]:
            assert not doc.startswith("search_document: ")
        # Embeddings should be passed
        assert "embeddings" in call_args


class TestMarkRun:
    """Tests for scan-start timestamp recording."""

    def test_mark_run_with_timestamp(self, tmp_path):
        """mark_run records the given timestamp, not current time."""
        import index_vault as iv
        original = iv.CHROMA_PATH
        try:
            iv.CHROMA_PATH = str(tmp_path)
            past_time = time.time() - 3600  # 1 hour ago
            mark_run(past_time)
            recorded = get_last_run()
            # Should be close to past_time, not current time
            assert abs(recorded - past_time) < 1.0
        finally:
            iv.CHROMA_PATH = original

    def test_mark_run_without_timestamp(self, tmp_path):
        """mark_run without timestamp uses current time (backward compat)."""
        import index_vault as iv
        original = iv.CHROMA_PATH
        try:
            iv.CHROMA_PATH = str(tmp_path)
            before = time.time()
            mark_run()
            recorded = get_last_run()
            assert recorded >= before - 1.0
        finally:
            iv.CHROMA_PATH = original

    def test_scan_start_prevents_race(self, tmp_path):
        """Files modified during indexing are not skipped on next run.

        Simulates the race condition: a file modified after scan_start
        should still be picked up on the next incremental run because
        mark_run uses scan_start, not completion time.
        """
        import index_vault as iv
        original = iv.CHROMA_PATH
        try:
            iv.CHROMA_PATH = str(tmp_path)

            scan_start = time.time()
            time.sleep(0.05)

            # File modified after scan_start
            modified_file = tmp_path / "note.md"
            modified_file.write_text("modified during indexing")
            file_mtime = modified_file.stat().st_mtime

            # mark_run with scan_start (before file was modified)
            mark_run(scan_start)
            last_run = get_last_run()

            # File should be newer than last_run
            assert file_mtime > last_run, (
                "File modified during indexing would be skipped on next run"
            )
        finally:
            iv.CHROMA_PATH = original


class TestManifest:
    """Tests for indexed_sources.json manifest helpers."""

    def test_load_manifest_no_file(self, tmp_path):
        """Returns None when no manifest exists."""
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = load_manifest()
        assert result is None

    def test_load_manifest_returns_set(self, tmp_path):
        """Returns a set of source paths from the manifest file."""
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps(["vault/a.md", "vault/b.md"]))
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = load_manifest()
        assert result == {"vault/a.md", "vault/b.md"}

    def test_load_manifest_corrupt_returns_none(self, tmp_path, caplog):
        """Returns None (and logs a warning) on corrupt manifest."""
        import logging
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text("not valid json {{{")
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                result = load_manifest()
        assert result is None
        assert any("Failed to load indexed_sources manifest" in r.message for r in caplog.records)

    def test_save_manifest_writes_sorted(self, tmp_path):
        """Writes a sorted JSON array to indexed_sources.json."""
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            save_manifest({"vault/c.md", "vault/a.md", "vault/b.md"})
        content = json.loads((tmp_path / "indexed_sources.json").read_text())
        assert content == ["vault/a.md", "vault/b.md", "vault/c.md"]

    def test_save_manifest_creates_dir(self, tmp_path):
        """Creates CHROMA_PATH directory if it doesn't exist."""
        chroma_path = str(tmp_path / "new_chroma_dir")
        with patch("index_vault.CHROMA_PATH", chroma_path):
            save_manifest({"vault/a.md"})
        assert (Path(chroma_path) / "indexed_sources.json").exists()

    def test_save_manifest_logs_on_write_error(self, tmp_path, caplog):
        """Logs a warning and returns False when the manifest file cannot be written."""
        import logging
        with patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("builtins.open", side_effect=OSError("disk full")):
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                result = save_manifest({"vault/a.md"})
        assert result is False
        assert any("Failed to save indexed_sources manifest" in r.message for r in caplog.records)

    def test_load_manifest_dirty_flag_returns_none(self, tmp_path, caplog):
        """Returns None when dirty sentinel exists (incomplete prior run)."""
        import logging
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps(["vault/a.md"]))
        dirty = tmp_path / ".indexing_in_progress"
        dirty.touch()
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                result = load_manifest()
        assert result is None
        assert any("incomplete" in r.message for r in caplog.records)

    def test_save_manifest_returns_true_on_success(self, tmp_path):
        """Returns True when manifest is saved successfully."""
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            result = save_manifest({"vault/a.md"})
        assert result is True

    def test_load_manifest_non_list_returns_none(self, tmp_path, caplog):
        """Returns None when manifest JSON is not a flat list (e.g. a dict)."""
        import logging
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text('{"source": "vault/a.md"}')
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                result = load_manifest()
        assert result is None
        assert any("unexpected schema" in r.message for r in caplog.records)

    def test_load_manifest_nested_list_returns_none(self, tmp_path, caplog):
        """Returns None when manifest JSON contains non-string elements."""
        import logging
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text('[["nested"]]')
        with patch("index_vault.CHROMA_PATH", str(tmp_path)):
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                result = load_manifest()
        assert result is None
        assert any("unexpected schema" in r.message for r in caplog.records)


class TestPruneDeletedFiles:
    """Tests for the manifest-aware prune_deleted_files."""

    def test_fast_path_no_deletions(self):
        """Fast path: nothing to prune when indexed matches valid."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md", "b.md"},
                indexed_sources={"a.md", "b.md"},
            )
        mock_collection.delete.assert_not_called()
        assert result == 0

    def test_fast_path_deletes_removed_source(self):
        """Fast path: deletes by source filter for each removed file."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md", "b.md"},
                indexed_sources={"a.md", "b.md", "deleted.md"},
            )
        mock_collection.delete.assert_called_once_with(where={"source": "deleted.md"})
        assert result == 1

    def test_fast_path_multiple_deletions(self):
        """Fast path: calls delete once per deleted source."""
        mock_collection = MagicMock()
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"a.md"},
                indexed_sources={"a.md", "del1.md", "del2.md"},
            )
        assert mock_collection.delete.call_count == 2
        assert result == 2

    def test_slow_path_when_no_manifest(self):
        """Slow path (indexed_sources=None): falls back to full metadata scan."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2", "id3"],
            "metadatas": [
                {"source": "kept.md"},
                {"source": "stale.md"},
                {"source": "stale.md"},
            ],
        }
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files(
                valid_sources={"kept.md"},
                indexed_sources=None,
            )
        mock_collection.get.assert_called_once_with(include=["metadatas"])
        assert result == 1  # 1 unique deleted source

    def test_slow_path_empty_collection(self):
        """Slow path: returns 0 immediately on empty collection."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}
        with patch("index_vault.get_collection", return_value=mock_collection):
            result = prune_deleted_files({"a.md"}, indexed_sources=None)
        mock_collection.delete.assert_not_called()
        assert result == 0


class TestIndexVaultManifest:
    """Tests that index_vault loads and saves the manifest correctly."""

    def test_saves_manifest_and_removes_sentinel_after_run(self, tmp_path):
        """index_vault saves manifest and removes the dirty sentinel on success."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        manifest_path = tmp_path / "indexed_sources.json"
        dirty_path = tmp_path / ".indexing_in_progress"
        assert manifest_path.exists()
        assert not dirty_path.exists()
        content = json.loads(manifest_path.read_text())
        assert str(vault_file) in content

    def test_dirty_sentinel_forces_slow_path(self, tmp_path):
        """If dirty sentinel exists, incremental run uses slow path (indexed_sources=None)."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")
        # Write a valid manifest AND the dirty sentinel (simulates incomplete prior run)
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps([str(vault_file)]))
        dirty = tmp_path / ".indexing_in_progress"
        dirty.touch()

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0) as mock_prune, \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") is None

    def test_sentinel_stays_if_save_manifest_fails(self, tmp_path):
        """Dirty sentinel is NOT removed if save_manifest fails."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.save_manifest", return_value=False), \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        dirty_path = tmp_path / ".indexing_in_progress"
        assert dirty_path.exists(), "Sentinel should remain when save_manifest fails"

    def test_full_reindex_skips_manifest(self, tmp_path):
        """--full reindex passes indexed_sources=None to prune (forces slow path)."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0) as mock_prune, \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=True)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") is None

    def test_incremental_run_uses_manifest(self, tmp_path):
        """Incremental run loads manifest and passes it to prune."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps([str(vault_file), "/old/deleted.md"]))

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=1) as mock_prune, \
             patch("index_vault.mark_run"):
            mock_coll.return_value.count.return_value = 4
            index_vault(full=False)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") == {str(vault_file), "/old/deleted.md"}

    def test_sentinel_write_failure_disables_manifest(self, tmp_path):
        """If sentinel write fails, indexed_sources is set to None (slow path forced)."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")
        # Pre-write a valid manifest
        manifest = tmp_path / "indexed_sources.json"
        manifest.write_text(json.dumps([str(vault_file)]))

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0) as mock_prune, \
             patch("index_vault.mark_run"), \
             patch("builtins.open", side_effect=OSError("disk full")):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        _, kwargs = mock_prune.call_args
        assert kwargs.get("indexed_sources") is None

    def test_sentinel_write_failure_deletes_manifest(self, tmp_path):
        """When sentinel write fails, any existing manifest is deleted to prevent stale fast-path."""
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")
        # Pre-write a manifest that would otherwise be used
        manifest_path = tmp_path / "indexed_sources.json"
        manifest_path.write_text(json.dumps([str(vault_file)]))

        # Patch open to fail only for the sentinel, not for read_manifest or save_manifest
        original_open = open
        sentinel_path = str(tmp_path / ".indexing_in_progress")

        def selective_open(path, *args, **kwargs):
            if str(path) == sentinel_path:
                raise OSError("permission denied")
            return original_open(path, *args, **kwargs)

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.save_manifest", return_value=False), \
             patch("builtins.open", side_effect=selective_open):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        assert not manifest_path.exists(), "Stale manifest should be deleted when sentinel write fails"

    def test_sentinel_removal_failure_logs_warning(self, tmp_path, caplog):
        """Logs a warning when the dirty sentinel cannot be removed after a successful run."""
        import logging
        vault_file = tmp_path / "note.md"
        vault_file.write_text("# Hello")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[vault_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.save_manifest", return_value=True), \
             patch("os.remove", side_effect=OSError("permission denied")):
            mock_coll.return_value.count.return_value = 5
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                index_vault(full=False)

        assert any("Failed to remove indexing sentinel" in r.message for r in caplog.records)


class TestIndexWorkers:
    """Tests for INDEX_WORKERS configuration."""

    def test_default_value(self):
        """INDEX_WORKERS defaults to 4."""
        import config
        assert config.INDEX_WORKERS == 4

    def test_env_override(self, monkeypatch):
        """INDEX_WORKERS can be set via environment variable."""
        import importlib
        import config
        monkeypatch.setenv("INDEX_WORKERS", "8")
        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
        try:
            assert config.INDEX_WORKERS == 8
        finally:
            monkeypatch.delenv("INDEX_WORKERS", raising=False)
            with patch("dotenv.load_dotenv"):
                importlib.reload(config)

    def test_minimum_value(self, monkeypatch):
        """INDEX_WORKERS has a minimum of 1."""
        import importlib
        import config
        monkeypatch.setenv("INDEX_WORKERS", "0")
        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
        try:
            assert config.INDEX_WORKERS == 1
        finally:
            monkeypatch.delenv("INDEX_WORKERS", raising=False)
            with patch("dotenv.load_dotenv"):
                importlib.reload(config)


class TestParallelIndexing:
    """Tests for parallel file indexing in index_vault."""

    def test_indexes_files_with_thread_pool(self, tmp_path):
        """index_vault uses ThreadPoolExecutor for file indexing."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# Note {f.stem}")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", return_value=None) as mock_prepare, \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 10
            index_vault(full=True)

        assert mock_prepare.call_count == 3
        indexed_files = {c.args[0] for c in mock_prepare.call_args_list}
        assert indexed_files == set(files)

    def test_file_error_does_not_stop_others(self, tmp_path):
        """A failing file doesn't prevent other files from being indexed."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        call_count = {"value": 0}
        def selective_index(f):
            call_count["value"] += 1
            if f.name == "bad.md":
                raise RuntimeError("index failed")
            return None

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file, bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=selective_index), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=True)

        assert call_count["value"] == 2

    def test_logs_error_for_failed_file(self, tmp_path, caplog):
        """Failed files are logged at ERROR level."""
        import logging
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=RuntimeError("boom")), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1):
            mock_coll.return_value.count.return_value = 0
            with caplog.at_level(logging.ERROR, logger="index_vault"):
                index_vault(full=True)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        assert any("Failed to index" in r.message for r in error_records)
        assert any("boom" in (r.exc_text or "") for r in error_records)

    def test_progress_logging(self, tmp_path, caplog):
        """Progress is logged every 100 files."""
        import logging
        files = [tmp_path / f"note{i}.md" for i in range(150)]
        for f in files:
            f.write_text("# Note")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 4):
            mock_coll.return_value.count.return_value = 500
            with caplog.at_level(logging.INFO, logger="index_vault"):
                index_vault(full=True)

        progress_msgs = [r for r in caplog.records if "Prepared" in r.message and "files" in r.message]
        assert len(progress_msgs) >= 1

    def test_skips_unmodified_files(self, tmp_path):
        """Incremental indexing only submits modified files to the pool."""
        old_file = tmp_path / "old.md"
        old_file.write_text("# Old")
        new_file = tmp_path / "new.md"
        new_file.write_text("# New")

        # Set old_file mtime to the past
        old_mtime = time.time() - 3600
        os.utime(old_file, (old_mtime, old_mtime))

        # last_run between old and new
        last_run_time = time.time() - 1800

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[old_file, new_file]), \
             patch("index_vault.get_last_run", return_value=last_run_time), \
             patch("index_vault._prepare_file_chunks", return_value=None) as mock_prepare, \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 2):
            mock_coll.return_value.count.return_value = 5
            index_vault(full=False)

        assert mock_prepare.call_count == 1
        assert mock_prepare.call_args.args[0] == new_file

    def test_file_not_found_is_not_a_failure(self, tmp_path):
        """FileNotFoundError (file deleted during indexing) doesn't count as failure."""
        gone_file = tmp_path / "gone.md"
        gone_file.write_text("# Gone")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[gone_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=FileNotFoundError("gone")), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run") as mock_mark, \
             patch("index_vault.INDEX_WORKERS", 1):
            mock_coll.return_value.count.return_value = 0
            index_vault(full=True)

        mock_mark.assert_called_once()

    def test_skips_mark_run_on_failure(self, tmp_path):
        """mark_run is not called when any file fails, so next run retries."""
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=RuntimeError("boom")), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run") as mock_mark, \
             patch("index_vault.INDEX_WORKERS", 1):
            mock_coll.return_value.count.return_value = 0
            index_vault(full=True)

        mock_mark.assert_not_called()

    def test_calls_mark_run_on_success(self, tmp_path):
        """mark_run is called when all files succeed."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run") as mock_mark, \
             patch("index_vault.INDEX_WORKERS", 1):
            mock_coll.return_value.count.return_value = 1
            index_vault(full=True)

        mock_mark.assert_called_once()


class TestBatchUpserts:
    """Tests for batched cross-file upserts in index_vault."""

    def _make_chunk_result(self, source: str, n_chunks: int = 2):
        """Helper to create a _prepare_file_chunks return value."""
        ids = [f"{source}_chunk_{i}" for i in range(n_chunks)]
        docs = [f"[{source}] content {i}" for i in range(n_chunks)]
        metas = [{"source": source, "chunk": i, "heading": "", "chunk_type": "body"} for i in range(n_chunks)]
        return source, ids, docs, metas

    def test_chunks_batched_into_single_upsert(self, tmp_path):
        """Multiple files' chunks are combined into a single upsert call when under batch size."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 6
            index_vault(full=True)

        # All 6 chunks (3 files x 2 chunks) in one upsert call
        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 1
        call_args = mock_collection.upsert.call_args[1]
        assert len(call_args["ids"]) == 6

    def test_upsert_respects_batch_size(self, tmp_path):
        """Chunks are split across multiple upsert calls when exceeding batch size."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 4):
            mock_coll.return_value.count.return_value = 6
            index_vault(full=True)

        # 6 chunks with batch_size=4 -> 2 upsert calls (4 + 2)
        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 2
        first_ids = mock_collection.upsert.call_args_list[0][1]["ids"]
        second_ids = mock_collection.upsert.call_args_list[1][1]["ids"]
        assert len(first_ids) == 4
        assert len(second_ids) == 2

    def test_stale_chunks_deleted_before_upsert(self, tmp_path):
        """Old chunks are deleted before new ones are upserted."""
        f = tmp_path / "note.md"
        f.write_text("# Note")

        result = self._make_chunk_result(str(f), 2)
        call_order = []

        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_collection.delete.side_effect = lambda **kw: call_order.append("delete")
        mock_collection.upsert.side_effect = lambda **kw: call_order.append("upsert")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[f]), \
             patch("index_vault._prepare_file_chunks", return_value=result), \
             patch("index_vault.get_collection", return_value=mock_collection), \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            index_vault(full=True)

        assert call_order[0] == "delete"
        assert call_order[-1] == "upsert"

    def test_failed_file_chunks_excluded_from_batch(self, tmp_path):
        """Chunks from files that failed preparation are not included in the batch upsert."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        good_result = self._make_chunk_result(str(good_file), 2)

        def selective_prepare(f):
            if f.name == "bad.md":
                raise RuntimeError("boom")
            return good_result

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file, bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=selective_prepare), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 2
            index_vault(full=True)

        mock_collection = mock_coll.return_value
        assert mock_collection.upsert.call_count == 1
        upserted_ids = mock_collection.upsert.call_args[1]["ids"]
        assert len(upserted_ids) == 2  # only good file's chunks

    def test_empty_results_skip_upsert(self, tmp_path):
        """When all files return None (empty), no upsert is called."""
        f = tmp_path / "empty.md"
        f.write_text("")

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[f]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 500):
            mock_coll.return_value.count.return_value = 0
            index_vault(full=True)

        mock_coll.return_value.upsert.assert_not_called()

    def test_phase_progress_logging(self, tmp_path, caplog):
        """Batch upsert logs phase-based progress."""
        import logging
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            mock_coll.return_value.count.return_value = 6
            with caplog.at_level(logging.INFO, logger="index_vault"):
                index_vault(full=True)

        messages = [r.message for r in caplog.records]
        # Should have preparation and upsert phase messages
        assert any("Prepared" in m for m in messages)
        assert any("Upserting" in m or "upsert" in m.lower() for m in messages)

    def test_delete_failure_excludes_source_from_upsert(self, tmp_path):
        """If deleting stale chunks fails for a source, its new chunks are excluded from upsert."""
        good_file = tmp_path / "good.md"
        good_file.write_text("# Good")
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# Bad")

        good_result = self._make_chunk_result(str(good_file), 2)
        bad_result = self._make_chunk_result(str(bad_file), 2)

        def selective_prepare(f):
            if f.name == "good.md":
                return good_result
            return bad_result

        def selective_delete(**kwargs):
            source = kwargs.get("where", {}).get("source", "")
            if "bad" in source:
                raise RuntimeError("delete failed")

        mock_collection = MagicMock()
        mock_collection.count.return_value = 4
        mock_collection.delete.side_effect = selective_delete

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[good_file, bad_file]), \
             patch("index_vault._prepare_file_chunks", side_effect=selective_prepare), \
             patch("index_vault.get_collection", return_value=mock_collection), \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run") as mock_mark, \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 1000):
            index_vault(full=True)

        # Only good file's chunks should be upserted
        assert mock_collection.upsert.call_count == 1
        upserted_ids = mock_collection.upsert.call_args[1]["ids"]
        assert len(upserted_ids) == 2
        assert all(str(good_file) in id_ for id_ in upserted_ids)
        # Should skip mark_run since there was a failure
        mock_mark.assert_not_called()

    def test_stale_chunks_deleted_for_now_empty_file(self, tmp_path):
        """When a file yields no chunks (e.g. emptied), its stale chunks are still deleted."""
        f = tmp_path / "was_content.md"
        f.write_text("")

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=[f]), \
             patch("index_vault._prepare_file_chunks", return_value=None), \
             patch("index_vault.get_collection", return_value=mock_collection), \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 500):
            index_vault(full=True)

        # Stale chunks should be deleted even though file now produces no chunks
        mock_collection.delete.assert_called_once_with(where={"source": str(f)})
        # No upsert since no new chunks
        mock_collection.upsert.assert_not_called()

    def test_upsert_failure_continues_remaining_batches(self, tmp_path):
        """A failed batch upsert doesn't abort the run; remaining batches still execute."""
        files = [tmp_path / f"note{i}.md" for i in range(3)]
        for f in files:
            f.write_text(f"# {f.stem}")

        results = {str(f): self._make_chunk_result(str(f), 2) for f in files}
        call_count = {"upsert": 0}

        def failing_upsert(**kwargs):
            call_count["upsert"] += 1
            if call_count["upsert"] == 1:
                raise RuntimeError("transient embedding error")

        mock_collection = MagicMock()
        mock_collection.count.return_value = 6
        mock_collection.upsert.side_effect = failing_upsert

        with patch("index_vault.VAULT_PATH", tmp_path), \
             patch("index_vault.CHROMA_PATH", str(tmp_path)), \
             patch("index_vault.get_vault_files", return_value=files), \
             patch("index_vault._prepare_file_chunks", side_effect=lambda f: results[str(f)]), \
             patch("index_vault.get_collection", return_value=mock_collection), \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run") as mock_mark, \
             patch("index_vault.INDEX_WORKERS", 1), \
             patch("index_vault.UPSERT_BATCH_SIZE", 4):
            index_vault(full=True)

        # Both batches attempted (4+2 chunks), first failed
        assert mock_collection.upsert.call_count == 2
        # mark_run skipped due to failure
        mock_mark.assert_not_called()


# --- _split_by_headings tests ---


class TestSplitByHeadings:
    """Tests for heading splitting with heading chain tracking."""

    def test_flat_headings(self):
        """Sequential same-level headings each get their own single-element chain."""
        text = "## Intro\nHello\n## Methods\nStuff\n## Results\nData"
        sections = _split_by_headings(text)
        assert len(sections) == 3
        # Each section: (heading, heading_chain, content)
        assert sections[0] == ("## Intro", ["Intro"], "Hello")
        assert sections[1] == ("## Methods", ["Methods"], "Stuff")
        assert sections[2] == ("## Results", ["Results"], "Data")

    def test_nested_headings(self):
        """Child headings include parent in their chain."""
        text = "# Parent\nIntro\n## Child\nBody"
        sections = _split_by_headings(text)
        assert len(sections) == 2
        assert sections[0] == ("# Parent", ["Parent"], "Intro")
        assert sections[1] == ("## Child", ["Parent", "Child"], "Body")

    def test_level_reset(self):
        """Same-or-higher level heading resets the stack."""
        text = "# First\nA\n## Sub\nB\n# Second\nC"
        sections = _split_by_headings(text)
        assert len(sections) == 3
        assert sections[0][1] == ["First"]
        assert sections[1][1] == ["First", "Sub"]
        # Second h1 resets - no parent
        assert sections[2][1] == ["Second"]

    def test_deeply_nested(self):
        """Full chain through 4 levels of headings."""
        text = "# A\na\n## B\nb\n### C\nc\n#### D\nd"
        sections = _split_by_headings(text)
        assert len(sections) == 4
        assert sections[0][1] == ["A"]
        assert sections[1][1] == ["A", "B"]
        assert sections[2][1] == ["A", "B", "C"]
        assert sections[3][1] == ["A", "B", "C", "D"]

    def test_top_level_content(self):
        """Content before first heading has empty chain."""
        text = "Some intro text\n# Heading\nBody"
        sections = _split_by_headings(text)
        assert len(sections) == 2
        assert sections[0] == ("top-level", [], "Some intro text")
        assert sections[1] == ("# Heading", ["Heading"], "Body")

    def test_no_headings(self):
        """Text with no headings returns one top-level section with empty chain."""
        text = "Just some plain text.\nAnother line."
        sections = _split_by_headings(text)
        assert len(sections) == 1
        assert sections[0] == ("top-level", [], "Just some plain text.\nAnother line.")

    def test_skip_level(self):
        """Skipping levels (h2 -> h4) still builds correct chain."""
        text = "## Parent\nA\n#### GrandChild\nB"
        sections = _split_by_headings(text)
        assert len(sections) == 2
        assert sections[0][1] == ["Parent"]
        assert sections[1][1] == ["Parent", "GrandChild"]

    def test_code_fence_headings_ignored(self):
        """Headings inside code fences are not treated as section breaks."""
        text = (
            "## Real Heading\n"
            "Some text\n"
            "```\n"
            "## Fake Heading\n"
            "code content\n"
            "```\n"
            "More text\n"
            "## Another Real\n"
            "End"
        )
        sections = _split_by_headings(text)
        # Should only have 2 sections: "Real Heading" and "Another Real"
        assert len(sections) == 2
        headings = [s[0] for s in sections]
        assert "## Real Heading" in headings
        assert "## Another Real" in headings
        # The fake heading inside the fence should be part of the first section's content
        assert "## Fake Heading" in sections[0][2]
        assert "code content" in sections[0][2]


class TestHeadingChainPropagation:
    """Tests for heading_chain propagation through chunk dicts."""

    def test_section_chunk_has_chain(self):
        """A small section under ## Architecture has heading_chain == ['Architecture']."""
        text = "## Architecture\nSmall section content."
        chunks = chunk_markdown(text)
        assert len(chunks) >= 1
        assert chunks[0]["heading_chain"] == ["Architecture"]

    def test_nested_chunk_has_full_chain(self):
        """## Parent then ### Child gives child chunk heading_chain ['Parent', 'Child']."""
        text = "## Parent\nParent content.\n### Child\nChild content."
        chunks = chunk_markdown(text)
        # Find the child chunk
        child_chunks = [c for c in chunks if "Child content" in c["text"]]
        assert len(child_chunks) >= 1
        assert child_chunks[0]["heading_chain"] == ["Parent", "Child"]

    def test_top_level_has_empty_chain(self):
        """Content before first heading has heading_chain == []."""
        text = "Top level content before any heading.\n## Heading\nSection content."
        chunks = chunk_markdown(text)
        top_chunks = [c for c in chunks if "Top level content" in c["text"]]
        assert len(top_chunks) >= 1
        assert top_chunks[0]["heading_chain"] == []

    def test_frontmatter_has_empty_chain(self):
        """Frontmatter chunk has heading_chain == []."""
        text = "---\ntitle: Test\n---\n## Heading\nContent."
        fm = {"title": "Test"}
        chunks = chunk_markdown(text, frontmatter=fm)
        fm_chunks = [c for c in chunks if c["chunk_type"] == "frontmatter"]
        assert len(fm_chunks) == 1
        assert fm_chunks[0]["heading_chain"] == []

    def test_paragraph_chunks_inherit_chain(self):
        """Large section split into paragraphs keeps heading_chain."""
        # Create a section large enough to be split into paragraphs
        para1 = "First paragraph. " * 60  # ~1020 chars
        para2 = "Second paragraph. " * 60  # ~1080 chars
        text = f"## Overview\n{para1}\n\n{para2}"
        chunks = chunk_markdown(text, max_chunk_size=1200)
        para_chunks = [c for c in chunks if c["chunk_type"] == "paragraph"]
        assert len(para_chunks) >= 2
        for chunk in para_chunks:
            assert chunk["heading_chain"] == ["Overview"]

    def test_sentence_chunks_inherit_chain(self):
        """Sentence-split chunks inherit heading_chain."""
        # Single paragraph (no double newlines) that's too large for one chunk
        long_text = ". ".join([f"Sentence number {i}" for i in range(80)])
        text = f"## Details\n{long_text}"
        chunks = chunk_markdown(text, max_chunk_size=500)
        sentence_chunks = [c for c in chunks if c["chunk_type"] == "sentence"]
        assert len(sentence_chunks) >= 2
        for chunk in sentence_chunks:
            assert chunk["heading_chain"] == ["Details"]


class TestPrepareFileChunksPrefix:
    """Tests for heading-chain-based document prefixes in _prepare_file_chunks."""

    def test_flat_heading_prefix(self, tmp_path):
        """File with ## Architecture -> doc starts with [My Note > Architecture]."""
        md = tmp_path / "My Note.md"
        md.write_text("## Architecture\nSome content here.")
        result = _prepare_file_chunks(md)
        assert result is not None
        _, _, documents, _ = result
        arch_docs = [d for d in documents if "Some content here" in d]
        assert arch_docs
        assert arch_docs[0].startswith("[My Note > Architecture]")

    def test_nested_heading_prefix(self, tmp_path):
        """## Architecture then ### Database -> doc starts with [My Note > Architecture > Database]."""
        md = tmp_path / "My Note.md"
        md.write_text("## Architecture\n### Database\nSchema details.")
        result = _prepare_file_chunks(md)
        assert result is not None
        _, _, documents, _ = result
        db_docs = [d for d in documents if "Schema details" in d]
        assert db_docs
        assert db_docs[0].startswith("[My Note > Architecture > Database]")

    def test_top_level_prefix(self, tmp_path):
        """Content before first heading -> doc starts with [My Note] (no chain)."""
        md = tmp_path / "My Note.md"
        md.write_text("Top level content before any heading.")
        result = _prepare_file_chunks(md)
        assert result is not None
        _, _, documents, _ = result
        top_docs = [d for d in documents if "Top level content" in d]
        assert top_docs
        assert top_docs[0].startswith("[My Note] ")

    def test_frontmatter_prefix(self, tmp_path):
        """File with frontmatter -> frontmatter doc starts with [My Note] (no chain)."""
        md = tmp_path / "My Note.md"
        md.write_text("---\ntags: [test]\n---\nSome body text.")
        result = _prepare_file_chunks(md)
        assert result is not None
        _, _, documents, _ = result
        # Frontmatter chunk should have [My Note] prefix
        fm_docs = [d for d in documents if "tags" in d]
        assert fm_docs
        assert fm_docs[0].startswith("[My Note] ")

    def test_level_reset_prefix(self, tmp_path):
        """## A then ### B then ## C -> C doc starts with [Note > C] (not [Note > A > C])."""
        md = tmp_path / "Note.md"
        md.write_text("## A\n### B\nNested content.\n## C\nReset content.")
        result = _prepare_file_chunks(md)
        assert result is not None
        _, _, documents, _ = result
        c_docs = [d for d in documents if "Reset content" in d]
        assert c_docs
        assert c_docs[0].startswith("[Note > C]")


class TestSentenceOverlap:
    """Tests for sentence carry-forward overlap in _chunk_sentences."""

    def test_overlap_between_chunks(self):
        """Last 2 sentences of chunk N appear at start of chunk N+1."""
        sentences = [f"Sentence {i} has some content here." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_markdown("## S\n\n" + text, max_chunk_size=200)
        sentence_chunks = [c for c in chunks if c["chunk_type"] == "sentence"]
        assert len(sentence_chunks) >= 2
        # Second chunk should start with overlap from first
        first_text = sentence_chunks[0]["text"]
        second_text = sentence_chunks[1]["text"]
        first_sentences = _split_sentences(first_text)
        overlap = first_sentences[-2:] if len(first_sentences) >= 2 else first_sentences[-1:]
        for sent in overlap:
            assert sent in second_text

    def test_first_chunk_no_overlap(self):
        """First chunk has no carry-forward prefix."""
        sentences = [f"Sentence {i} is here." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_markdown("## S\n\n" + text, max_chunk_size=200)
        assert chunks[0]["text"].startswith("## S")

    def test_single_chunk_no_overlap(self):
        """A section that fits in one chunk has no overlap artifacts."""
        text = "## S\n\nShort content here."
        chunks = chunk_markdown(text)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "## S\n\nShort content here."

    def test_fragment_keeps_own_overlap(self):
        """Oversized sentences fall back to _fixed_chunk_text with its own 50-char overlap."""
        giant = "x" * 3000
        text = "## S\n\n" + giant
        chunks = chunk_markdown(text, max_chunk_size=500)
        fragment_chunks = [c for c in chunks if c["chunk_type"] == "fragment"]
        assert len(fragment_chunks) >= 2
        # Fixed chunks have 50-char overlap
        assert fragment_chunks[0]["text"][-50:] == fragment_chunks[1]["text"][:50]

    def test_no_duplicate_carry_chunks(self):
        """Carry that can't fit with next sentence is dropped, not emitted as duplicate."""
        # Two long sentences that each take ~60% of max; carry of 2 = entire previous chunk
        s1 = "A" * 119 + "."
        s2 = "B" * 119 + "."
        s3 = "C" * 119 + "."
        text = f"## S\n\n{s1} {s2} {s3}"
        chunks = chunk_markdown(text, max_chunk_size=250)
        sentence_chunks = [c for c in chunks if c["chunk_type"] == "sentence"]
        # No two consecutive chunks should have identical text
        for i in range(len(sentence_chunks) - 1):
            assert sentence_chunks[i]["text"] != sentence_chunks[i + 1]["text"]

    def test_no_duplicate_carry_before_fragment(self):
        """Carry is dropped (not emitted) before oversized sentence fragment fallback."""
        s1 = "Short one."
        s2 = "Short two."
        giant = "".join(f"word{i} " for i in range(600))  # ~3600 chars, forces fragment
        text = f"## S\n\n{s1} {s2} {giant}"
        chunks = chunk_markdown(text, max_chunk_size=500)
        sentence_chunks = [c for c in chunks if c["chunk_type"] == "sentence"]
        # Carry ("Short one. Short two.") should NOT appear as a standalone chunk
        # after the first sentence chunk that already contains them
        for i in range(len(sentence_chunks) - 1):
            assert sentence_chunks[i]["text"] != sentence_chunks[i + 1]["text"]


class TestCrossSectionOverlap:
    """Tests for overlap between heading sections."""

    def test_overlap_between_sections(self):
        """First chunk of section N+1 contains trailing sentences from section N."""
        text = (
            "## First\n\nAlpha sentence one. Alpha sentence two. Alpha sentence three.\n\n"
            "## Second\n\nBeta content here."
        )
        chunks = chunk_markdown(text)
        second = [c for c in chunks if c["heading"] == "## Second"]
        assert len(second) == 1
        assert "Alpha sentence two." in second[0]["text"]
        assert "Alpha sentence three." in second[0]["text"]

    def test_no_overlap_after_frontmatter(self):
        """First body section does not get overlap from frontmatter."""
        text = "---\ntitle: Test\n---\n\n## First\n\nBody content."
        chunks = chunk_markdown(text, frontmatter={"title": "Test"})
        body = [c for c in chunks if c["heading"] == "## First"]
        assert len(body) == 1
        assert "title" not in body[0]["text"]

    def test_no_overlap_on_first_section(self):
        """Very first section has no overlap prefix."""
        text = "## Only\n\nJust content."
        chunks = chunk_markdown(text)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "## Only\n\nJust content."

    def test_overlap_across_three_sections(self):
        """Overlap chains across multiple sections."""
        text = (
            "## A\n\nA one. A two. A three.\n\n"
            "## B\n\nB one. B two. B three.\n\n"
            "## C\n\nC content here."
        )
        chunks = chunk_markdown(text)
        b_chunk = [c for c in chunks if c["heading"] == "## B"][0]
        c_chunk = [c for c in chunks if c["heading"] == "## C"][0]
        assert "A two." in b_chunk["text"]
        assert "A three." in b_chunk["text"]
        assert "B two." in c_chunk["text"]
        assert "B three." in c_chunk["text"]

    def test_single_sentence_section_overlap(self):
        """Section with only 1 sentence provides just that 1 sentence as overlap."""
        text = (
            "## A\n\nOnly one sentence here.\n\n"
            "## B\n\nB content."
        )
        chunks = chunk_markdown(text)
        b_chunk = [c for c in chunks if c["heading"] == "## B"][0]
        assert "Only one sentence here." in b_chunk["text"]

    def test_overlap_excludes_heading(self):
        """Heading line from previous section is not included in overlap text."""
        text = (
            "## Previous\n\nOne line of content\n\n"
            "## Next\n\nNext content."
        )
        chunks = chunk_markdown(text)
        next_chunk = [c for c in chunks if c["heading"] == "## Next"][0]
        # Should have body content as overlap, NOT the heading
        assert "One line of content" in next_chunk["text"]
        assert "## Previous" not in next_chunk["text"]

    def test_overlap_does_not_cascade(self):
        """Overlap from section A should not leak through B into C."""
        text = (
            "## A\n\nAlpha one. Alpha two. Alpha three.\n\n"
            "## B\n\nBravo content.\n\n"
            "## C\n\nCharlie content."
        )
        chunks = chunk_markdown(text)
        c_chunk = [c for c in chunks if c["heading"] == "## C"][0]
        # C should have B's trailing, NOT A's
        assert "Bravo content." in c_chunk["text"]
        assert "Alpha" not in c_chunk["text"]

    def test_overlap_skipped_when_oversize(self):
        """Cross-section overlap is skipped if it would exceed max_chunk_size."""
        # Section A content near the limit; section B content near the limit
        filler_a = "A word. " * 80  # ~640 chars
        filler_b = "B word. " * 80  # ~640 chars
        text = f"## A\n\n{filler_a}\n\n## B\n\n{filler_b}"
        chunks = chunk_markdown(text, max_chunk_size=700)
        b_chunks = [c for c in chunks if c["heading"] == "## B"]
        assert len(b_chunks) >= 1
        # No chunk should exceed max_chunk_size
        for c in b_chunks:
            assert len(c["text"]) <= 700

    def test_overlap_with_newline_terminated_text(self):
        """Sections with newline-terminated lines (no sentence punctuation) get line-based overlap."""
        text = (
            "## A\n\n- Item one\n- Item two\n- Item three\n\n"
            "## B\n\nB content."
        )
        chunks = chunk_markdown(text)
        b_chunk = [c for c in chunks if c["heading"] == "## B"][0]
        # Should get last 2 lines, not the entire section
        assert "- Item two" in b_chunk["text"]
        assert "- Item three" in b_chunk["text"]
        # Should NOT contain the heading from section A
        assert "## A" not in b_chunk["text"]


class TestTrailingSentences:
    """Tests for _trailing_sentences fallback behavior."""

    def test_sentence_split(self):
        """Normal prose with sentence punctuation splits on sentences."""
        text = "First sentence. Second sentence. Third sentence."
        result = _trailing_sentences(text, 2)
        assert "Second sentence." in result
        assert "Third sentence." in result
        assert "First" not in result

    def test_line_fallback(self):
        """Text without sentence punctuation falls back to line splitting."""
        text = "- Item one\n- Item two\n- Item three"
        result = _trailing_sentences(text, 2)
        assert "- Item two" in result
        assert "- Item three" in result
        assert "Item one" not in result

    def test_single_line_no_split(self):
        """Single line with no sentence punctuation returns the whole line."""
        text = "Just one line"
        result = _trailing_sentences(text, 2)
        assert result == "Just one line"

    def test_empty_text(self):
        """Empty text returns empty string."""
        assert _trailing_sentences("", 2) == ""

    def test_fewer_sentences_than_n(self):
        """Requesting more units than exist returns all available."""
        text = "Only one sentence."
        result = _trailing_sentences(text, 5)
        assert result == "Only one sentence."
