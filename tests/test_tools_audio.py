"""Tests for audio transcription tool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.audio import (
    AUDIO_EMBED_PATTERN,
    _extract_audio_embeds,
    _resolve_audio_file,
    transcribe_audio,
)


class TestExtractAudioEmbeds:
    """Tests for _extract_audio_embeds helper."""

    def test_extract_single_embed(self):
        content = "Some text ![[recording.m4a]] more text"
        result = _extract_audio_embeds(content)
        assert result == ["recording.m4a"]

    def test_extract_multiple_embeds(self):
        content = """
        ![[audio1.m4a]]
        Some text
        ![[audio2.mp3]]
        ![[audio3.wav]]
        """
        result = _extract_audio_embeds(content)
        assert result == ["audio1.m4a", "audio2.mp3", "audio3.wav"]

    def test_extract_all_supported_formats(self):
        content = """
        ![[a.m4a]] ![[b.webm]] ![[c.mp3]] ![[d.wav]] ![[e.ogg]]
        """
        result = _extract_audio_embeds(content)
        assert set(result) == {"a.m4a", "b.webm", "c.mp3", "d.wav", "e.ogg"}

    def test_case_insensitive_extension(self):
        content = "![[Recording.M4A]] ![[Voice.MP3]]"
        result = _extract_audio_embeds(content)
        assert result == ["Recording.M4A", "Voice.MP3"]

    def test_ignore_non_audio_embeds(self):
        content = """
        ![[image.png]]
        ![[document.pdf]]
        ![[audio.m4a]]
        ![[note]]
        """
        result = _extract_audio_embeds(content)
        assert result == ["audio.m4a"]

    def test_empty_content(self):
        result = _extract_audio_embeds("")
        assert result == []

    def test_no_audio_embeds(self):
        content = "Just regular text with [[wikilinks]] but no audio."
        result = _extract_audio_embeds(content)
        assert result == []

    def test_filename_with_spaces(self):
        content = "![[my recording file.m4a]]"
        result = _extract_audio_embeds(content)
        assert result == ["my recording file.m4a"]


class TestResolveAudioFile:
    """Tests for _resolve_audio_file helper."""

    def test_resolve_existing_file(self, vault_config):
        attachments = vault_config / "Attachments"
        (attachments / "test.m4a").write_bytes(b"audio data")

        path, error = _resolve_audio_file("test.m4a")
        assert error is None
        assert path is not None
        assert path.name == "test.m4a"

    def test_resolve_missing_file(self, vault_config):
        path, error = _resolve_audio_file("nonexistent.m4a")
        assert path is None
        assert "not found" in error


class TestTranscribeAudio:
    """Tests for transcribe_audio tool."""

    def test_no_api_key(self, vault_config, monkeypatch):
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        note = vault_config / "test.md"
        note.write_text("![[audio.m4a]]")

        result = json.loads(transcribe_audio("test.md"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    def test_file_not_found(self, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        result = json.loads(transcribe_audio("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_audio_embeds(self, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        note = vault_config / "plain.md"
        note.write_text("# Just text\n\nNo audio here.")

        result = json.loads(transcribe_audio("plain.md"))
        assert result["success"] is True
        assert "No audio embeds" in result["message"]
        assert result["results"] == []

    def test_audio_file_missing(self, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        note = vault_config / "missing_audio.md"
        note.write_text("![[nonexistent.m4a]]")

        result = json.loads(transcribe_audio("missing_audio.md"))
        assert result["success"] is False
        assert "All transcriptions failed" in result["error"]

    @patch("tools.audio.OpenAI")
    def test_successful_transcription(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        # Create note and audio file
        note = vault_config / "has_audio.md"
        note.write_text("# Recording\n\n![[test.m4a]]")
        attachments = vault_config / "Attachments"
        (attachments / "test.m4a").write_bytes(b"audio data")

        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "This is the transcribed text."
        mock_client.audio.transcriptions.create.return_value = mock_response

        result = json.loads(transcribe_audio("has_audio.md"))

        assert result["success"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["file"] == "test.m4a"
        assert result["results"][0]["transcript"] == "This is the transcribed text."

        # Verify API was called correctly
        mock_client.audio.transcriptions.create.assert_called_once()
        call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
        assert call_kwargs["model"] == "whisper-v3"
        assert call_kwargs["response_format"] == "verbose_json"
        assert call_kwargs["timestamp_granularities"] == ["word"]
        assert call_kwargs["extra_body"] == {"diarize": True}

    @patch("tools.audio.OpenAI")
    def test_multiple_audio_files(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        # Create note with multiple audio embeds
        note = vault_config / "multi_audio.md"
        note.write_text("![[first.m4a]]\n\n![[second.mp3]]")
        attachments = vault_config / "Attachments"
        (attachments / "first.m4a").write_bytes(b"audio1")
        (attachments / "second.mp3").write_bytes(b"audio2")

        # Mock responses
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response1 = MagicMock()
        mock_response1.text = "First transcript"
        mock_response2 = MagicMock()
        mock_response2.text = "Second transcript"
        mock_client.audio.transcriptions.create.side_effect = [mock_response1, mock_response2]

        result = json.loads(transcribe_audio("multi_audio.md"))

        assert result["success"] is True
        assert len(result["results"]) == 2
        assert result["results"][0]["file"] == "first.m4a"
        assert result["results"][0]["transcript"] == "First transcript"
        assert result["results"][1]["file"] == "second.mp3"
        assert result["results"][1]["transcript"] == "Second transcript"

    @patch("tools.audio.OpenAI")
    def test_partial_failure(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        # Create note with two embeds but only one audio file
        note = vault_config / "partial.md"
        note.write_text("![[exists.m4a]]\n\n![[missing.mp3]]")
        attachments = vault_config / "Attachments"
        (attachments / "exists.m4a").write_bytes(b"audio")

        # Mock successful transcription for existing file
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "Transcribed content"
        mock_client.audio.transcriptions.create.return_value = mock_response

        result = json.loads(transcribe_audio("partial.md"))

        assert result["success"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["file"] == "exists.m4a"
        assert "errors" in result
        assert any("missing.mp3" in e for e in result["errors"])

    @patch("tools.audio.OpenAI")
    def test_api_error(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")

        note = vault_config / "api_error.md"
        note.write_text("![[test.m4a]]")
        attachments = vault_config / "Attachments"
        (attachments / "test.m4a").write_bytes(b"audio")

        # Mock API failure
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = Exception("API rate limit")

        result = json.loads(transcribe_audio("api_error.md"))

        assert result["success"] is False
        assert "All transcriptions failed" in result["error"]
        assert "API rate limit" in result["error"]
