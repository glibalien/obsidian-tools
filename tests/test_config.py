"""Tests for src/config.py.

config.py executes module-level code at import time, so env vars must be set
BEFORE the module is (re-)imported. We use importlib.reload(config) with
monkeypatch to control env vars and re-evaluate module-level assignments.
"""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def reload_config():
    """Reload config after each test to restore module state."""
    import src.config as config
    yield config
    importlib.reload(config)


def _reload(monkeypatch) -> object:
    """Reload config with load_dotenv disabled so .env doesn't override monkeypatched env."""
    import src.config as config
    with patch("dotenv.load_dotenv"):
        importlib.reload(config)
    return config


# ---------------------------------------------------------------------------
# VAULT_PATH
# ---------------------------------------------------------------------------


def test_vault_path_default(monkeypatch):
    monkeypatch.delenv("VAULT_PATH", raising=False)
    config = _reload(monkeypatch)
    expected = Path("~/Documents/obsidian-vault").expanduser()
    assert config.VAULT_PATH == expected


def test_vault_path_custom(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    config = _reload(monkeypatch)
    assert config.VAULT_PATH == tmp_path


# ---------------------------------------------------------------------------
# CHROMA_PATH
# ---------------------------------------------------------------------------


def test_chroma_path_relative(monkeypatch):
    monkeypatch.delenv("CHROMA_PATH", raising=False)
    config = _reload(monkeypatch)
    # Project root is the parent of src/ which is the parent of config.py
    import src.config as cfg_module
    project_root = Path(cfg_module.__file__).parent.parent
    expected = str(project_root / "./.chroma_db")
    assert config.CHROMA_PATH == expected


def test_chroma_path_absolute(monkeypatch, tmp_path):
    abs_path = str(tmp_path / "chroma")
    monkeypatch.setenv("CHROMA_PATH", abs_path)
    config = _reload(monkeypatch)
    assert config.CHROMA_PATH == abs_path


# ---------------------------------------------------------------------------
# EXCLUDED_DIRS
# ---------------------------------------------------------------------------


def test_excluded_dirs_contents(monkeypatch):
    config = _reload(monkeypatch)
    assert config.EXCLUDED_DIRS == {'.venv', '.chroma_db', '.trash', '.obsidian', '.git'}


# ---------------------------------------------------------------------------
# PREFERENCES_FILE and ATTACHMENTS_DIR
# ---------------------------------------------------------------------------


def test_preferences_file_under_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    config = _reload(monkeypatch)
    assert config.PREFERENCES_FILE == tmp_path / "Preferences.md"


def test_attachments_dir_under_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    config = _reload(monkeypatch)
    assert config.ATTACHMENTS_DIR == tmp_path / "Attachments"


# ---------------------------------------------------------------------------
# API_PORT
# ---------------------------------------------------------------------------


def test_api_port_default(monkeypatch):
    monkeypatch.delenv("API_PORT", raising=False)
    config = _reload(monkeypatch)
    assert config.API_PORT == 8000


def test_api_port_custom(monkeypatch):
    monkeypatch.setenv("API_PORT", "9090")
    config = _reload(monkeypatch)
    assert config.API_PORT == 9090


# ---------------------------------------------------------------------------
# MAX_SESSIONS
# ---------------------------------------------------------------------------


def test_max_sessions_default(monkeypatch):
    monkeypatch.delenv("MAX_SESSIONS", raising=False)
    config = _reload(monkeypatch)
    assert config.MAX_SESSIONS == 20


def test_max_sessions_clamped(monkeypatch):
    monkeypatch.setenv("MAX_SESSIONS", "0")
    config = _reload(monkeypatch)
    assert config.MAX_SESSIONS == 1


# ---------------------------------------------------------------------------
# MAX_SESSION_MESSAGES
# ---------------------------------------------------------------------------


def test_max_session_messages_default(monkeypatch):
    monkeypatch.delenv("MAX_SESSION_MESSAGES", raising=False)
    config = _reload(monkeypatch)
    assert config.MAX_SESSION_MESSAGES == 50


def test_max_session_messages_clamped(monkeypatch):
    monkeypatch.setenv("MAX_SESSION_MESSAGES", "1")
    config = _reload(monkeypatch)
    assert config.MAX_SESSION_MESSAGES == 2


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


def test_fireworks_model_default(monkeypatch):
    monkeypatch.delenv("FIREWORKS_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = _reload(monkeypatch)
    assert config.FIREWORKS_MODEL == "accounts/fireworks/models/gpt-oss-120b"


def test_fireworks_model_env(monkeypatch):
    monkeypatch.setenv("FIREWORKS_MODEL", "accounts/fireworks/models/custom-model")
    config = _reload(monkeypatch)
    assert config.FIREWORKS_MODEL == "accounts/fireworks/models/custom-model"


def test_fireworks_model_llm_fallback(monkeypatch):
    monkeypatch.delenv("FIREWORKS_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "accounts/fireworks/models/fallback-model")
    config = _reload(monkeypatch)
    assert config.FIREWORKS_MODEL == "accounts/fireworks/models/fallback-model"


def test_llm_model_alias(monkeypatch):
    monkeypatch.setenv("FIREWORKS_MODEL", "accounts/fireworks/models/some-model")
    config = _reload(monkeypatch)
    assert config.LLM_MODEL == config.FIREWORKS_MODEL


# ---------------------------------------------------------------------------
# INDEX_INTERVAL
# ---------------------------------------------------------------------------


def test_index_interval_default(monkeypatch):
    monkeypatch.delenv("INDEX_INTERVAL", raising=False)
    config = _reload(monkeypatch)
    assert config.INDEX_INTERVAL == 60
