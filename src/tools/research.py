"""Research tool - LLM-powered topic extraction and research."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import html2text
import httpx
from openai import OpenAI

from config import MAX_PAGE_CHARS, MAX_RESEARCH_TOPICS, PAGE_FETCH_TIMEOUT, RESEARCH_MODEL
from tools.search import find_notes, web_search

logger = logging.getLogger(__name__)

_MAX_URLS_PER_TOPIC = 2

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


def _fetch_page(url: str) -> str | None:
    """Fetch a web page and convert HTML to markdown.

    Args:
        url: The URL to fetch.

    Returns:
        Markdown text of the page content, or None on any failure.
    """
    try:
        response = httpx.get(
            url,
            timeout=PAGE_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "obsidian-tools/1.0"},
        )
        response.raise_for_status()
    except Exception:
        logger.warning("Failed to fetch page: %s", url, exc_info=True)
        return None

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0

    text = converter.handle(response.text)
    return text[:MAX_PAGE_CHARS]


def _extract_page_content(
    client: OpenAI,
    text: str,
    topic: str,
) -> str | None:
    """Extract information relevant to a topic from fetched page text.

    Args:
        client: OpenAI-compatible API client.
        text: The page text (markdown) to extract from.
        topic: The topic to focus extraction on.

    Returns:
        Extracted relevant content as a string, or None on failure.
    """
    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract information relevant to the given topic from "
                        "the provided text. Return only the relevant content, "
                        "concisely summarized. If nothing is relevant, return "
                        "an empty string."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Topic: {topic}\n\nText:\n{text}",
                },
            ],
        )
    except Exception:
        logger.warning("Page content extraction failed for topic: %s", topic, exc_info=True)
        return None

    content = response.choices[0].message.content
    if not content:
        return None
    return content


def _research_topic(
    topic: dict,
    depth: str,
    client: OpenAI | None = None,
) -> dict:
    """Research a single topic via web search and vault search.

    Args:
        topic: Dict with "topic", "context", and "type" keys.
        depth: "shallow" (web + vault search only) or "deep" (also fetches pages).
        client: OpenAI client, required for deep mode page extraction.

    Returns:
        Dict with topic info and research results.
    """
    label = topic.get("topic", "")
    context = topic.get("context", "")
    topic_type = topic.get("type", "")

    # Web search
    web_results = []
    try:
        raw = web_search(label)
        parsed = json.loads(raw)
        if parsed.get("success"):
            web_results = parsed.get("results", [])
    except Exception:
        logger.warning("Web search failed for topic: %s", label, exc_info=True)

    # Vault search
    vault_results = []
    try:
        raw = find_notes(query=label, mode="hybrid", n_results=5)
        parsed = json.loads(raw)
        if parsed.get("success"):
            vault_results = parsed.get("results", [])
    except Exception:
        logger.warning("Vault search failed for topic: %s", label, exc_info=True)

    result = {
        "topic": label,
        "context": context,
        "type": topic_type,
        "web_results": web_results,
        "vault_results": vault_results,
    }

    # Deep mode: fetch and extract content from top web result pages
    if depth == "deep" and client is not None:
        page_extracts = []
        urls = [r["url"] for r in web_results if r.get("url")][:_MAX_URLS_PER_TOPIC]
        for url in urls:
            try:
                text = _fetch_page(url)
                if text:
                    extracted = _extract_page_content(client, text, label)
                    if extracted:
                        page_extracts.append(extracted)
            except Exception:
                logger.warning("Page processing failed for URL: %s", url, exc_info=True)
        result["page_extracts"] = page_extracts

    return result


def _gather_research(
    topics: list[dict],
    depth: str = "shallow",
    client: OpenAI | None = None,
) -> list[dict]:
    """Research all topics concurrently.

    Args:
        topics: List of topic dicts from _extract_topics.
        depth: "shallow" or "deep".
        client: OpenAI client, required for deep mode.

    Returns:
        List of research result dicts, preserving original topic order.
    """
    results = [None] * len(topics)

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_index = {
            executor.submit(_research_topic, topic, depth, client): i
            for i, topic in enumerate(topics)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception:
                logger.warning(
                    "Research failed for topic: %s",
                    topics[idx].get("topic", "unknown"),
                    exc_info=True,
                )
                # Return a minimal result for failed topics
                results[idx] = {
                    "topic": topics[idx].get("topic", ""),
                    "context": topics[idx].get("context", ""),
                    "type": topics[idx].get("type", ""),
                    "web_results": [],
                    "vault_results": [],
                }

    return results


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
