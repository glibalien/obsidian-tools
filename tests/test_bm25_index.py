"""Tests for BM25 index module."""

from unittest.mock import MagicMock, patch


class TestTokenize:
    """Tests for _tokenize helper."""

    def test_lowercases_text(self):
        from bm25_index import _tokenize
        tokens = _tokenize("Python Programming Language")
        assert "python" in tokens
        assert "programming" in tokens
        assert "language" in tokens

    def test_strips_punctuation(self):
        from bm25_index import _tokenize
        tokens = _tokenize("hello, world! (test)")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
        # No punctuation fragments
        for t in tokens:
            assert not any(c in t for c in ".,!?;:\"'()[]{}")

    def test_filters_stopwords(self):
        from bm25_index import _tokenize
        tokens = _tokenize("the quick brown fox is in the garden")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "in" not in tokens
        assert "quick" in tokens
        assert "brown" in tokens
        assert "garden" in tokens

    def test_filters_short_words(self):
        from bm25_index import _tokenize
        tokens = _tokenize("go to my house")
        assert "go" not in tokens
        assert "to" not in tokens
        assert "my" not in tokens
        assert "house" in tokens

    def test_empty_input(self):
        from bm25_index import _tokenize
        assert _tokenize("") == []

    def test_all_stopwords_input(self):
        from bm25_index import _tokenize
        assert _tokenize("the is in of and or to for") == []

    def test_mixed_punctuation_and_stopwords(self):
        from bm25_index import _tokenize
        tokens = _tokenize("it's a [complex] example!")
        # "it's" -> "it's" stripped -> "its" (length 3, not stopword) — wait,
        # stripping only removes leading/trailing punctuation chars
        # "it's" -> strip ".,!?;:\"'()[]{}" -> "it's" (apostrophe is ' which
        # is in the strip set, but strip only removes leading/trailing)
        # Actually "it's" -> stripped of ' at end? No, ' is in the middle.
        # Let's just check expected tokens
        assert "complex" in tokens
        assert "example" in tokens


class TestQueryIndex:
    """Tests for query_index."""

    def setup_method(self):
        import bm25_index
        bm25_index.invalidate()

    @patch("bm25_index.get_collection")
    def test_basic_query_returns_results(self, mock_get_collection):
        """Build index from multi-doc corpus and verify query returns results."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "python programming language guide",
                "javascript web development framework",
                "python machine learning tutorial",
            ],
            "metadatas": [
                {"source": "/vault/python.md", "heading": "Python", "chunk_type": "section"},
                {"source": "/vault/javascript.md", "heading": "JS", "chunk_type": "section"},
                {"source": "/vault/ml.md", "heading": "ML", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming")
        assert len(results) > 0
        # Python docs should rank higher than javascript
        sources = [r["source"] for r in results]
        assert "/vault/python.md" in sources

    @patch("bm25_index.get_collection")
    def test_bm25_ranking_order(self, mock_get_collection):
        """Documents with more matching terms should rank higher."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "unrelated document about cooking recipes",
                "python machine learning deep learning neural networks",
                "python programming python development python guide",
            ],
            "metadatas": [
                {"source": "/vault/cooking.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/ml.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/python.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming")
        assert len(results) >= 2
        # The doc with "python" repeated + "programming" should rank first
        assert results[0]["source"] == "/vault/python.md"

    @patch("bm25_index.get_collection")
    def test_return_fields(self, mock_get_collection):
        """Results should contain source, content, and heading fields."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "python programming tutorial",
                "cooking recipes for beginners",
                "music theory and composition",
            ],
            "metadatas": [
                {"source": "/vault/test.md", "heading": "Tutorial", "chunk_type": "section"},
                {"source": "/vault/cook.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/music.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming")
        assert len(results) >= 1
        result = results[0]
        assert set(result.keys()) == {"source", "content", "heading"}
        assert result["source"] == "/vault/test.md"
        assert result["content"] == "python programming tutorial"
        assert result["heading"] == "Tutorial"

    @patch("bm25_index.get_collection")
    def test_chunk_type_filtering(self, mock_get_collection):
        """chunk_type parameter filters results post-scoring."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "python programming in frontmatter",
                "python programming in section body",
                "cooking recipes for beginners guide",
            ],
            "metadatas": [
                {"source": "/vault/a.md", "heading": "frontmatter", "chunk_type": "frontmatter"},
                {"source": "/vault/b.md", "heading": "Code", "chunk_type": "section"},
                {"source": "/vault/c.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming", chunk_type="section")
        assert len(results) == 1
        assert results[0]["source"] == "/vault/b.md"

    @patch("bm25_index.get_collection")
    def test_empty_query_returns_empty(self, mock_get_collection):
        """Empty query should return empty list without building index."""
        import bm25_index
        results = bm25_index.query_index("")
        assert results == []

    @patch("bm25_index.get_collection")
    def test_empty_collection_returns_empty(self, mock_get_collection):
        """Empty ChromaDB collection should return empty results."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [],
            "metadatas": [],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python")
        assert results == []

    @patch("bm25_index.get_collection")
    def test_n_results_limiting(self, mock_get_collection):
        """n_results limits the number of returned results."""
        mock_collection = MagicMock()
        docs = [f"python programming example number {i}" for i in range(10)]
        metas = [
            {"source": f"/vault/doc{i}.md", "heading": "", "chunk_type": "section"}
            for i in range(10)
        ]
        mock_collection.get.return_value = {
            "documents": docs,
            "metadatas": metas,
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming", n_results=3)
        assert len(results) <= 3

    @patch("bm25_index.get_collection")
    def test_idf_weighting_rare_terms_rank_higher(self, mock_get_collection):
        """Rare terms should contribute more to ranking (IDF effect)."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "common word common word common word",
                "common word rare unique special term",
                "common word another document here",
                "common word yet another document",
                "common word still more documents",
            ],
            "metadatas": [
                {"source": f"/vault/doc{i}.md", "heading": "", "chunk_type": "section"}
                for i in range(5)
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        # Search for the rare term - the doc containing it should rank first
        results = bm25_index.query_index("unique special")
        assert len(results) >= 1
        assert results[0]["source"] == "/vault/doc1.md"

    @patch("bm25_index.get_collection")
    def test_case_insensitivity(self, mock_get_collection):
        """Query should match documents regardless of case."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "Python Programming LANGUAGE",
                "cooking recipes for beginners",
                "music theory and composition",
            ],
            "metadatas": [
                {"source": "/vault/test.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/cook.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/music.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming language")
        assert len(results) >= 1
        assert results[0]["source"] == "/vault/test.md"

    @patch("bm25_index.get_collection")
    def test_zero_score_results_excluded(self, mock_get_collection):
        """Results with BM25 score <= 0 should not be returned."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "completely unrelated cooking recipe instructions",
                "python programming tutorial guide",
                "music theory and composition basics",
            ],
            "metadatas": [
                {"source": "/vault/cooking.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/python.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/music.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming")
        # Only the matching doc should appear, not the unrelated ones
        sources = [r["source"] for r in results]
        assert "/vault/python.md" in sources
        assert "/vault/cooking.md" not in sources
        assert "/vault/music.md" not in sources

    @patch("bm25_index.get_collection")
    def test_missing_heading_defaults_to_empty(self, mock_get_collection):
        """When metadata has no heading key, default to empty string."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": [
                "python programming tutorial",
                "cooking recipes for beginners",
                "music theory and composition",
            ],
            "metadatas": [
                {"source": "/vault/test.md", "chunk_type": "section"},
                {"source": "/vault/cook.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/music.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        import bm25_index
        results = bm25_index.query_index("python programming")
        assert len(results) >= 1
        assert results[0]["heading"] == ""


class TestInvalidate:
    """Tests for invalidate() resetting the cached index."""

    def setup_method(self):
        import bm25_index
        bm25_index.invalidate()

    @patch("bm25_index.get_collection")
    def test_invalidate_forces_rebuild(self, mock_get_collection):
        """After invalidate(), next query should rebuild from ChromaDB."""
        mock_collection = MagicMock()

        # First call: return one doc
        first_data = {
            "documents": ["python programming tutorial"],
            "metadatas": [
                {"source": "/vault/v1.md", "heading": "", "chunk_type": "section"},
            ],
        }
        # Second call: return different doc (simulating reindex)
        second_data = {
            "documents": [
                "python programming tutorial",
                "python advanced concepts deep dive",
            ],
            "metadatas": [
                {"source": "/vault/v1.md", "heading": "", "chunk_type": "section"},
                {"source": "/vault/v2.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_collection.get.side_effect = [first_data, second_data]
        mock_get_collection.return_value = mock_collection

        import bm25_index

        # First query builds index
        results1 = bm25_index.query_index("python programming")
        assert mock_collection.get.call_count == 1

        # Query again without invalidate — should reuse cached index
        results2 = bm25_index.query_index("python programming")
        assert mock_collection.get.call_count == 1  # Still 1, no rebuild

        # Invalidate and query again — should rebuild
        bm25_index.invalidate()
        results3 = bm25_index.query_index("python concepts")
        assert mock_collection.get.call_count == 2  # Rebuilt

    @patch("bm25_index.get_collection")
    def test_invalidate_clears_state(self, mock_get_collection):
        """invalidate() should set internal state to None."""
        import bm25_index

        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "documents": ["test document content here"],
            "metadatas": [
                {"source": "/vault/test.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        # Build index
        bm25_index.query_index("test document")
        # Invalidate
        bm25_index.invalidate()
        assert bm25_index._bm25 is None
        assert bm25_index._doc_metadata is None


class TestInvalidateCalledByIndexer:
    """Verify index_vault calls bm25_index.invalidate()."""

    @patch("index_vault.embed_documents", return_value=[])
    @patch("index_vault.get_collection")
    @patch("index_vault.get_vault_files", return_value=[])
    @patch("index_vault.invalidate_bm25")
    def test_index_vault_invalidates_bm25(
        self, mock_invalidate, mock_files, mock_coll, mock_embed
    ):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "metadatas": []}
        mock_collection.count.return_value = 0
        mock_coll.return_value = mock_collection

        from index_vault import index_vault
        index_vault(full=True)

        mock_invalidate.assert_called_once()
