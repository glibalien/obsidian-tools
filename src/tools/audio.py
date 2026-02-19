"""Audio transcription tool - transcribe audio files embedded in vault notes."""

import os
import re
from pathlib import Path

from openai import OpenAI

from config import ATTACHMENTS_DIR, FIREWORKS_BASE_URL, WHISPER_MODEL
from services.vault import err, ok, resolve_file

# Pattern to match Obsidian audio embeds: ![[filename.ext]]
# Supports common audio formats: m4a, webm, mp3, wav, ogg
AUDIO_EMBED_PATTERN = re.compile(
    r"!\[\[([^\]]+\.(?:m4a|webm|mp3|wav|ogg))\]\]",
    re.IGNORECASE,
)


def _extract_audio_embeds(content: str) -> list[str]:
    """Extract audio embed filenames from markdown content.

    Args:
        content: Markdown content to parse.

    Returns:
        List of audio filenames found in ![[file.ext]] embeds.
    """
    return AUDIO_EMBED_PATTERN.findall(content)


def _resolve_audio_file(filename: str) -> tuple[Path | None, str | None]:
    """Resolve an audio filename to its path in the Attachments folder.

    Args:
        filename: Audio filename (e.g., "recording.m4a").

    Returns:
        Tuple of (resolved_path, None) on success, or (None, error_message) on failure.
    """
    try:
        audio_path = (ATTACHMENTS_DIR / filename).resolve()
        audio_path.relative_to(ATTACHMENTS_DIR.resolve())
    except (ValueError, OSError, RuntimeError):
        return None, f"Invalid audio file path: {filename}"

    if not audio_path.exists():
        return None, f"Audio file not found: {filename}"

    if not audio_path.is_file():
        return None, f"Not a file: {filename}"

    return audio_path, None


def _transcribe_single_file(
    client: OpenAI,
    audio_path: Path,
) -> tuple[str | None, str | None]:
    """Transcribe a single audio file using Whisper API.

    Args:
        client: OpenAI client configured for Fireworks.
        audio_path: Path to the audio file.

    Returns:
        Tuple of (transcript, None) on success, or (None, error_message) on failure.
    """
    try:
        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                extra_body={"diarize": True},
            )
        return response.text, None
    except Exception as e:
        return None, f"Transcription failed for {audio_path.name}: {e}"


def transcribe_audio(path: str) -> str:
    """Transcribe audio files embedded in a vault note.

    Parses Obsidian audio embeds (![[file.m4a]]) from the note content,
    resolves audio files to the Attachments folder, and transcribes them
    using Fireworks Whisper API.

    Args:
        path: Path to the note containing audio embeds.

    Returns:
        JSON with transcripts: {"success": true, "results": [{"file": "...", "transcript": "..."}]}
        On partial failure: {"success": true, "results": [...], "errors": [...]}
        On total failure: {"success": false, "error": "..."}
    """
    # Check API key
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    # Resolve and read the note
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Failed to read file: {e}")

    # Extract audio embeds
    audio_files = _extract_audio_embeds(content)
    if not audio_files:
        return ok("No audio embeds found", results=[])

    # Initialize Whisper client
    client = OpenAI(
        api_key=api_key,
        base_url=FIREWORKS_BASE_URL,
    )

    # Transcribe each audio file
    results = []
    errors = []

    for filename in audio_files:
        audio_path, resolve_error = _resolve_audio_file(filename)
        if resolve_error:
            errors.append(resolve_error)
            continue

        transcript, transcribe_error = _transcribe_single_file(client, audio_path)
        if transcribe_error:
            errors.append(transcribe_error)
            continue

        results.append({"file": filename, "transcript": transcript})

    # Return appropriate response
    if not results and errors:
        return err(f"All transcriptions failed: {'; '.join(errors)}")

    if errors:
        return ok(results=results, errors=errors)

    return ok(results=results)
