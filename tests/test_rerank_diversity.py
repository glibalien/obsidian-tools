"""Tests for cross-encoder reranking and source diversity."""

import importlib
from unittest.mock import patch


class TestRerankConfig:
    """Config constants for reranking and diversity."""

    def test_rerank_model_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_MODEL == "BAAI/bge-reranker-v2-m3"

    def test_rerank_enabled_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_ENABLED is True

    def test_rerank_enabled_false(self, monkeypatch):
        monkeypatch.setenv("RERANK_ENABLED", "false")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.RERANK_ENABLED is False

    def test_max_chunks_per_source_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 3

    def test_max_chunks_per_source_env(self, monkeypatch):
        monkeypatch.setenv("MAX_CHUNKS_PER_SOURCE", "5")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 5

    def test_max_chunks_per_source_zero_disables(self, monkeypatch):
        monkeypatch.setenv("MAX_CHUNKS_PER_SOURCE", "0")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.MAX_CHUNKS_PER_SOURCE == 0


from unittest.mock import MagicMock, patch


class TestRerank:
    """Tests for the rerank function in services/chroma.py."""

    @patch("services.chroma.get_reranker")
    def test_rerank_sorts_by_score(self, mock_get_reranker):
        """Rerank should sort results by cross-encoder score descending."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]
        mock_get_reranker.return_value = mock_model

        from services.chroma import rerank
        results = [
            {"source": "a.md", "content": "low relevance", "heading": ""},
            {"source": "b.md", "content": "high relevance", "heading": ""},
            {"source": "c.md", "content": "mid relevance", "heading": ""},
        ]
        ranked = rerank("test query", results)
        assert [r["source"] for r in ranked] == ["b.md", "c.md", "a.md"]

    @patch("services.chroma.get_reranker")
    def test_rerank_preserves_all_fields(self, mock_get_reranker):
        """Rerank should preserve all dict fields in results."""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.8]
        mock_get_reranker.return_value = mock_model

        from services.chroma import rerank
        results = [{"source": "a.md", "content": "text", "heading": "## H1", "extra": "val"}]
        ranked = rerank("query", results)
        assert ranked[0]["heading"] == "## H1"
        assert ranked[0]["extra"] == "val"

    @patch("services.chroma.get_reranker")
    def test_rerank_empty_results(self, mock_get_reranker):
        """Rerank on empty input returns empty list."""
        from services.chroma import rerank
        assert rerank("query", []) == []
        mock_get_reranker.assert_not_called()

    @patch("services.chroma.RERANK_ENABLED", False)
    def test_rerank_disabled_returns_unchanged(self):
        """When RERANK_ENABLED is False, return results unchanged."""
        from services.chroma import rerank
        results = [
            {"source": "a.md", "content": "text1", "heading": ""},
            {"source": "b.md", "content": "text2", "heading": ""},
        ]
        ranked = rerank("query", results)
        assert ranked == results

    @patch("services.chroma._reranker_failed", True)
    @patch("services.chroma.RERANK_ENABLED", True)
    def test_rerank_after_load_failure_returns_unchanged(self):
        """If reranker failed to load, skip reranking."""
        from services.chroma import rerank
        results = [{"source": "a.md", "content": "text", "heading": ""}]
        ranked = rerank("query", results)
        assert ranked == results


class TestDiversify:
    """Tests for source-level diversity filtering."""

    def test_caps_chunks_per_source(self):
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": f"chunk{i}", "heading": ""} for i in range(5)
        ]
        diverse = _diversify(results, max_per_source=3)
        assert len(diverse) == 3
        assert all(r["source"] == "a.md" for r in diverse)

    def test_backfills_from_other_sources(self):
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "a.md", "content": "a2", "heading": ""},
            {"source": "a.md", "content": "a3", "heading": ""},
            {"source": "a.md", "content": "a4", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "b.md", "content": "b2", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=2)
        sources = [r["source"] for r in diverse]
        assert sources.count("a.md") == 2
        assert sources.count("b.md") == 2

    def test_preserves_ranking_order(self):
        """Within the cap, original ranking order is preserved."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "a.md", "content": "a2", "heading": ""},
            {"source": "b.md", "content": "b2", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=2)
        assert [r["content"] for r in diverse] == ["a1", "b1", "a2", "b2"]

    def test_zero_max_disables(self):
        """max_per_source=0 disables diversity (pass-through)."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": f"chunk{i}", "heading": ""} for i in range(5)
        ]
        diverse = _diversify(results, max_per_source=0)
        assert len(diverse) == 5

    def test_negative_max_disables(self):
        """max_per_source=-1 disables diversity (pass-through, not empty)."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": f"chunk{i}", "heading": ""} for i in range(3)
        ]
        diverse = _diversify(results, max_per_source=-1)
        assert len(diverse) == 3

    def test_empty_results(self):
        from hybrid_search import _diversify
        assert _diversify([], max_per_source=3) == []

    def test_fewer_results_than_cap(self):
        """When all sources are under the cap, nothing is dropped."""
        from hybrid_search import _diversify
        results = [
            {"source": "a.md", "content": "a1", "heading": ""},
            {"source": "b.md", "content": "b1", "heading": ""},
            {"source": "c.md", "content": "c1", "heading": ""},
        ]
        diverse = _diversify(results, max_per_source=3)
        assert len(diverse) == 3


class TestHydeConfig:
    """Config constants for HyDE."""

    def test_hyde_enabled_default(self):
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.HYDE_ENABLED is True

    def test_hyde_enabled_false(self, monkeypatch):
        monkeypatch.setenv("HYDE_ENABLED", "false")
        with patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
        assert config.HYDE_ENABLED is False


class TestIsQuestion:
    """Tests for _is_question heuristic."""

    def test_question_mark(self):
        from hybrid_search import _is_question
        assert _is_question("what is this about?") is True

    def test_starts_with_question_word(self):
        from hybrid_search import _is_question
        assert _is_question("how does indexing work") is True
        assert _is_question("what are the main features") is True
        assert _is_question("why is this important") is True
        assert _is_question("where do the logs go") is True
        assert _is_question("when was this created") is True
        assert _is_question("who wrote this") is True
        assert _is_question("which model is used") is True

    def test_starts_with_auxiliary_verb(self):
        from hybrid_search import _is_question
        assert _is_question("is the server running") is True
        assert _is_question("are there any errors") is True
        assert _is_question("does this support PDF") is True
        assert _is_question("do we have tests for this") is True
        assert _is_question("can I search by date") is True
        assert _is_question("could this be improved") is True
        assert _is_question("would this work with images") is True
        assert _is_question("should I reindex") is True

    def test_non_question_keyword(self):
        from hybrid_search import _is_question
        assert _is_question("meeting notes 2024") is False
        assert _is_question("python programming") is False

    def test_question_word_not_at_start(self):
        from hybrid_search import _is_question
        assert _is_question("notes about what happened") is False

    def test_empty_query(self):
        from hybrid_search import _is_question
        assert _is_question("") is False


class TestSearchIntegration:
    """Tests that reranking and diversity are wired into search functions."""

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_semantic_search_calls_rerank(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["doc1", "doc2"]],
            "metadatas": [[
                {"source": "a.md", "heading": ""},
                {"source": "b.md", "heading": ""},
            ]],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r  # pass-through

        from hybrid_search import semantic_search
        semantic_search("test", n_results=2)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    def test_keyword_search_calls_rerank(self, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["1", "2"],
            "documents": ["keyword match one", "keyword match two"],
            "metadatas": [
                {"source": "a.md", "heading": ""},
                {"source": "b.md", "heading": ""},
            ],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import keyword_search
        keyword_search("keyword match", n_results=2)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_hybrid_search_calls_rerank(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["doc1"]],
            "metadatas": [[{"source": "a.md", "heading": ""}]],
        }
        mock_collection.get.return_value = {
            "ids": ["1"],
            "documents": ["doc1"],
            "metadatas": [{"source": "a.md", "heading": ""}],
        }
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import hybrid_search
        hybrid_search("test", n_results=1)
        mock_rerank.assert_called_once()

    @patch("hybrid_search.rerank")
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_hybrid_search_fetches_4x_candidates(self, mock_embed, mock_coll, mock_rerank):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
        mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_coll.return_value = mock_collection
        mock_rerank.side_effect = lambda q, r: r

        from hybrid_search import hybrid_search
        hybrid_search("test", n_results=5)
        # _semantic_retrieve should request 4x = 20 candidates
        mock_collection.query.assert_called_once()
        assert mock_collection.query.call_args[1]["n_results"] == 20

    @patch("hybrid_search.MAX_CHUNKS_PER_SOURCE", 3)
    @patch("hybrid_search.rerank", side_effect=lambda q, r: r)
    @patch("hybrid_search.get_collection")
    @patch("hybrid_search.embed_query", return_value=[0.1])
    def test_diversity_applied_after_rerank(self, mock_embed, mock_coll, mock_rerank):
        """Source diversity caps results even after reranking."""
        docs = [f"doc{i}" for i in range(5)]
        metas = [{"source": "a.md", "heading": ""} for _ in range(5)]
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [docs],
            "metadatas": [metas],
        }
        mock_coll.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test", n_results=5)
        # MAX_CHUNKS_PER_SOURCE=3, so capped at 3
        assert len(results) == 3
