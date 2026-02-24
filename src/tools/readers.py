"""File type handlers for read_file dispatch.

Each handler takes a resolved Path and returns ok()/err() JSON.
"""

import base64
import os
from pathlib import Path

from openai import OpenAI

from config import FIREWORKS_BASE_URL, VISION_MODEL, WHISPER_MODEL
from services.vault import err, ok


# Extension sets for dispatch
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


def handle_audio(file_path: Path) -> str:
    """Transcribe an audio file using Fireworks Whisper API."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    try:
        with open(file_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                extra_body={"diarize": True},
            )
        return ok(transcript=response.text)
    except Exception as e:
        return err(f"Transcription failed: {e}")
