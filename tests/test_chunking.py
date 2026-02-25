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

from index_vault import (
    _fixed_chunk_text,
    _split_sentences,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
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

    def test_abbreviations_split_normally(self):
        """Title abbreviations split like any other period — no special handling."""
        result = _split_sentences("Dr. Smith is here. Next.")
        assert result == ["Dr.", "Smith is here.", "Next."]

    def test_eg_ie(self):
        """e.g. and i.e. are not treated as sentence boundaries."""
        result = _split_sentences("Use tools e.g. grep or rg. Next.")
        assert result == ["Use tools e.g. grep or rg.", "Next."]

        result = _split_sentences("A format i.e. JSON works. Done.")
        assert result == ["A format i.e. JSON works.", "Done."]

    def test_single_letter_initials(self):
        """Single-letter initials (J. K. Rowling) are preserved."""
        result = _split_sentences("J. K. Rowling wrote it. Next.")
        assert result == ["J. K. Rowling wrote it.", "Next."]

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

    def test_initials_preserved_in_sequence(self):
        """Adjacent initials (J. K.) are preserved, including the last one."""
        result = _split_sentences("By J. K. Rowling and others. Done.")
        assert result == ["By J. K. Rowling and others.", "Done."]

    def test_terminal_abbreviation_splits(self):
        """Non-title abbreviations at sentence end split correctly."""
        result = _split_sentences("Bring fruit, etc. Please hurry.")
        assert result == ["Bring fruit, etc.", "Please hurry."]

    def test_suffix_abbreviation_splits(self):
        """Name suffixes (Jr., Sr.) at sentence end split correctly."""
        result = _split_sentences("His name is John Doe Jr. He arrived.")
        assert result == ["His name is John Doe Jr.", "He arrived."]

    def test_single_letter_label_splits(self):
        """Single-letter labels (not initials) split correctly."""
        result = _split_sentences("Plan A. Plan B. Continue.")
        assert result == ["Plan A.", "Plan B.", "Continue."]

        result = _split_sentences("Option C. Continue.")
        assert result == ["Option C.", "Continue."]


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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
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
             patch("index_vault.index_file"), \
             patch("index_vault.get_collection") as mock_coll, \
             patch("index_vault.prune_deleted_files", return_value=0), \
             patch("index_vault.mark_run"), \
             patch("index_vault.save_manifest", return_value=True), \
             patch("os.remove", side_effect=OSError("permission denied")):
            mock_coll.return_value.count.return_value = 5
            with caplog.at_level(logging.WARNING, logger="index_vault"):
                index_vault(full=False)

        assert any("Failed to remove indexing sentinel" in r.message for r in caplog.records)
