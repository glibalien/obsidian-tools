"""Summarization tool - LLM-powered file summarization."""

import json
import logging
import os
import time

from openai import OpenAI

from pathlib import Path

from config import FIREWORKS_BASE_URL, MAX_SUMMARIZE_CHARS, SUMMARIZE_MODEL
from services.vault import err, get_relative_path, ok, resolve_file
from tools.editing import edit_file
from tools.files import read_file
from tools.readers import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, OFFICE_EXTENSIONS

_BINARY_EXTENSIONS = AUDIO_EXTENSIONS | IMAGE_EXTENSIONS | OFFICE_EXTENSIONS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a summarization assistant. Given the contents of a note, produce a \
detailed, structured summary in markdown.

Guidelines:
- Use subsections (### headings) to organize the summary when the content \
covers multiple topics or themes.
- Use tables when data is naturally tabular.
- Be thorough — capture key points, decisions, context, and nuance. Do not \
reduce the content to a few generic sentences.
- End with a "### Action Items" section containing checkboxes (- [ ]) for \
any tasks, follow-ups, commitments, or next steps identified in the content.
- If there are no action items, omit the Action Items section entirely.
- Use markdown formatting appropriate for Obsidian (wikilinks, callouts, etc. \
where helpful).
- Do NOT include a top-level heading (the caller adds "## Summary").
- Write in a clear, conversational tone — not robotic or overly formal."""


def summarize_file(
    path: str,
    focus: str | None = None,
) -> str:
    """Summarize a vault file and append the summary to it.

    Reads the file content (with embed expansion for markdown, audio
    transcription, image description), sends it to an LLM for
    summarization, and appends the result as a ## Summary section.

    Args:
        path: Path to the file (relative to vault or absolute).
        focus: Optional guidance for what to emphasize in the summary.

    Returns:
        JSON confirmation with path and summary_length on success,
        or error on failure.
    """
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    # Reject binary files — appending markdown would corrupt them
    file_path, resolve_err = resolve_file(path)
    if resolve_err:
        return err(resolve_err)
    if file_path.suffix.lower() in _BINARY_EXTENSIONS:
        return err(
            f"Cannot summarize binary file ({file_path.suffix}). "
            "Only text/markdown files are supported."
        )

    # Read file content via read_file (handles embeds, audio, images, office)
    raw = read_file(path, offset=0, length=MAX_SUMMARIZE_CHARS)
    data = json.loads(raw)
    if not data.get("success"):
        return err(data.get("error", "Failed to read file"))

    # Extract text content from read_file result
    content = (
        data.get("content")
        or data.get("transcript")
        or data.get("description")
        or ""
    )
    if not content.strip():
        return err("File has no content to summarize")

    # Enforce safety cap — slice then append notice
    if len(content) > MAX_SUMMARIZE_CHARS:
        content = content[:MAX_SUMMARIZE_CHARS]
        content += (
            f"\n\n[Content truncated at {MAX_SUMMARIZE_CHARS:,} characters. "
            "Summarize what is available.]"
        )

    # Build LLM messages
    user_content = ""
    if focus:
        user_content += f"Focus especially on: {focus}\n\n"
    user_content += content

    # Call LLM
    logger.info("Summarizing %s (%d chars)", path, len(content))
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=SUMMARIZE_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as e:
        logger.warning("Summarization failed for %s: %s", path, e)
        return err(f"Summarization failed: {e}")

    summary = response.choices[0].message.content
    if not summary:
        return err("LLM returned empty summary")
    elapsed = time.perf_counter() - start
    logger.info("Summarized %s in %.2fs (%d chars)", path, elapsed, len(summary))

    # Append summary to file
    formatted = f"\n## Summary\n\n{summary}"
    append_result = json.loads(edit_file(path, formatted, "append"))
    if not append_result.get("success"):
        return err(append_result.get("error", "Failed to append summary"))

    rel_path = get_relative_path(file_path)
    return ok(path=rel_path, summary_length=len(summary))
