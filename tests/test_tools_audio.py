"""Tests for audio file resolution via vault services."""

import pytest

import config
from services.vault import resolve_file


class TestResolveAudioFile:
    """Tests for audio file resolution via resolve_file with ATTACHMENTS_DIR."""

    def test_resolve_existing_file(self, vault_config):
        attachments = vault_config / "Attachments"
        (attachments / "test.m4a").write_bytes(b"audio data")

        path, error = resolve_file("test.m4a", base_path=config.ATTACHMENTS_DIR)
        assert error is None
        assert path is not None
        assert path.name == "test.m4a"

    def test_resolve_missing_file(self, vault_config):
        path, error = resolve_file("nonexistent.m4a", base_path=config.ATTACHMENTS_DIR)
        assert path is None
        assert "not found" in error.lower()

    def test_path_traversal_blocked(self, vault_config):
        """Path traversal attempts are rejected."""
        path, error = resolve_file("../../../etc/passwd", base_path=config.ATTACHMENTS_DIR)
        assert path is None
        assert "Path must be within vault" in error

    def test_path_traversal_with_dotdot(self, vault_config):
        """Dotdot in filename is rejected even if it resolves to a real file."""
        # Create a file outside Attachments but reachable via traversal
        (vault_config / "secret.m4a").write_bytes(b"secret")
        path, error = resolve_file("../secret.m4a", base_path=config.ATTACHMENTS_DIR)
        assert path is None
        assert "Path must be within vault" in error
