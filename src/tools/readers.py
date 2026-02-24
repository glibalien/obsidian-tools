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


def handle_image(file_path: Path) -> str:
    """Describe an image using Fireworks vision model."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    try:
        image_data = file_path.read_bytes()
        b64 = base64.b64encode(image_data).decode("utf-8")

        # Infer MIME type from extension
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        }
        mime = mime_map.get(file_path.suffix.lower(), "image/png")

        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in detail, including any visible text."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        )
        description = response.choices[0].message.content
        return ok(description=description)
    except Exception as e:
        return err(f"Image description failed: {e}")
