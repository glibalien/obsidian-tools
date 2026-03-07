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
