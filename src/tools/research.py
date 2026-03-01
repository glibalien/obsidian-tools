"""Research tool - LLM-powered topic extraction and research."""

import json
import logging

from openai import OpenAI

from config import MAX_RESEARCH_TOPICS, RESEARCH_MODEL

logger = logging.getLogger(__name__)

_TOPIC_EXTRACTION_PROMPT = """\
You are a topic extraction assistant. Given the contents of a note, extract \
the key topics that could be researched further.

Return a JSON array of objects, each with these fields:
- "topic": A concise topic name (2-6 words)
- "context": Brief context from the note explaining why this topic is relevant (1 sentence)
- "type": One of "concept", "person", "event", "place", "theme", "task", "question"

Return ONLY the JSON array, no other text. Example:
[
  {"topic": "Quarterly OKR review", "context": "Team discussed Q2 objectives and key results", "type": "theme"},
  {"topic": "Maria Chen", "context": "New engineering lead joining next month", "type": "person"}
]"""


def _extract_topics(
    client: OpenAI,
    content: str,
    focus: str | None = None,
) -> list[dict]:
    """Extract key topics from content using an LLM.

    Args:
        client: OpenAI-compatible API client.
        content: The text content to extract topics from.
        focus: Optional guidance for what to focus on during extraction.

    Returns:
        List of topic dicts with "topic", "context", and "type" keys.
        Returns empty list on any failure.
    """
    user_content = ""
    if focus:
        user_content += f"Focus especially on: {focus}\n\n"
    user_content += content

    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": _TOPIC_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception:
        logger.warning("Topic extraction failed", exc_info=True)
        return []

    raw = response.choices[0].message.content
    if not raw:
        logger.warning("LLM returned empty response for topic extraction")
        return []

    try:
        topics = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM returned invalid JSON for topic extraction: %s", raw[:200])
        return []

    if not isinstance(topics, list):
        logger.warning("LLM returned non-list JSON for topic extraction")
        return []

    valid = [t for t in topics if isinstance(t, dict) and "topic" in t]
    return valid[:MAX_RESEARCH_TOPICS]


def research_note(
    path: str,
    depth: str = "shallow",
    focus: str | None = None,
) -> str:
    """Research topics found in a vault note.

    Args:
        path: Path to the note file.
        depth: Research depth - "shallow" or "deep".
        focus: Optional focus area for topic extraction.

    Raises:
        NotImplementedError: This is a placeholder for Stage 2.
    """
    raise NotImplementedError("research_note will be implemented in Stage 2")
