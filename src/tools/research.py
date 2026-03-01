"""Research tool - LLM-powered topic extraction and research."""

import ipaddress
import json
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from openai import OpenAI

from config import (
    FIREWORKS_BASE_URL,
    MAX_PAGE_CHARS,
    MAX_RESEARCH_TOPICS,
    MAX_SUMMARIZE_CHARS,
    PAGE_FETCH_TIMEOUT,
    RESEARCH_MODEL,
)
from services.vault import err, get_relative_path, ok, resolve_file
from tools.editing import edit_file
from tools.files import read_file
from tools.search import find_notes, web_search

logger = logging.getLogger(__name__)


def _is_public_ip(host: str) -> bool:
    """Check whether all resolved IPs for a hostname are globally routable.

    Uses is_global which rejects loopback, private, link-local, reserved,
    multicast, carrier-grade NAT (100.64/10), documentation, and other
    non-globally-routable ranges.  Returns False if DNS resolution fails.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        return False

    if not infos:
        return False

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global or addr.is_multicast:
            return False

    return True


def _is_public_url(url: str) -> bool:
    """Validate that a URL targets a public host."""
    parsed = urlparse(str(url))
    host = parsed.hostname
    if not host:
        return False
    return _is_public_ip(host)


def _get_completion_content(response) -> str | None:
    """Safely extract message content from an LLM chat completion response.

    Returns None if choices is empty or content is absent.
    """
    if not response.choices:
        return None
    return response.choices[0].message.content


_MAX_URLS_PER_TOPIC = 2
_TEXT_SAFE_EXTENSIONS = {".md", ".txt", ".markdown"}
_VALID_DEPTHS = {"shallow", "deep"}

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

    raw = _get_completion_content(response)
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


_MAX_REDIRECTS = 10


def _fetch_page(url: str) -> str | None:
    """Fetch a web page and convert HTML to markdown.

    Validates that the initial URL and every redirect target resolve to
    public IP addresses before issuing any request, preventing SSRF
    against internal services.

    Args:
        url: The URL to fetch.

    Returns:
        Markdown text of the page content, or None on any failure.
    """
    if not _is_public_url(url):
        logger.warning("Blocked fetch to non-public URL: %s", url)
        return None

    current_url = url
    try:
        for _ in range(_MAX_REDIRECTS):
            response = httpx.get(
                current_url,
                timeout=PAGE_FETCH_TIMEOUT,
                follow_redirects=False,
                headers={"User-Agent": "obsidian-tools/1.0"},
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    logger.warning("Redirect with no Location header from %s", current_url)
                    return None
                next_url = urljoin(current_url, location)
                if not _is_public_url(next_url):
                    logger.warning(
                        "Blocked redirect to non-public URL: %s -> %s",
                        current_url, next_url,
                    )
                    return None
                current_url = next_url
                continue
            response.raise_for_status()
            break
        else:
            logger.warning("Too many redirects from %s", url)
            return None
    except Exception:
        logger.warning("Failed to fetch page: %s", url, exc_info=True)
        return None

    try:
        import html2text
    except ImportError:
        logger.warning("html2text not installed — cannot convert page to text")
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

    content = _get_completion_content(response)
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
                        page_extracts.append({"url": url, "content": extracted})
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


_SYNTHESIS_PROMPT = """\
You are a research synthesis assistant. Given the contents of a note and \
research findings gathered from web searches and vault notes, produce a \
structured research supplement in markdown.

Guidelines:
- Organize findings by topic using ### headings.
- Cite web sources as markdown links: [Title](URL)
- Reference vault notes as Obsidian wikilinks: [[Note Name]]
- Flag contradictions between the note and external sources.
- Highlight related vault content the user may not know about.
- Be concise — this is a research supplement, not a thesis.
- Do NOT include a top-level heading (the caller adds "## Research").
- Use markdown formatting appropriate for Obsidian."""


def _synthesize_research(
    client: OpenAI,
    note_content: str,
    research_results: list[dict],
) -> str | None:
    """Synthesize research results into a markdown summary.

    Args:
        client: OpenAI-compatible API client.
        note_content: The original note content for context.
        research_results: List of per-topic research result dicts.

    Returns:
        Markdown string with synthesized research, or None on failure.
    """
    # Build research context string
    parts = []
    for result in research_results:
        topic = result.get("topic", "")
        parts.append(f"### Topic: {topic}")

        # Web results
        web_results = result.get("web_results", [])
        if web_results:
            parts.append("Web results:")
            for wr in web_results:
                title = wr.get("title", "")
                url = wr.get("url", "")
                snippet = wr.get("snippet", "")
                parts.append(f"- [{title}]({url}): {snippet}")

        # Vault results
        vault_results = result.get("vault_results", [])
        if vault_results:
            parts.append("Vault notes:")
            for vr in vault_results:
                vr_path = vr.get("path") or vr.get("source", "")
                note_name = Path(vr_path).stem if vr_path else ""
                content = vr.get("content", "")
                parts.append(f"- [[{note_name}]]: {content}")

        # Page extracts (deep mode)
        page_extracts = result.get("page_extracts", [])
        if page_extracts:
            parts.append("Page extracts:")
            for pe in page_extracts:
                url = pe.get("url", "")
                content = pe.get("content", "")
                parts.append(f"- {url}: {content}")

        parts.append("")

    research_context = "\n".join(parts)

    user_content = (
        f"Note content:\n{note_content}\n\n"
        f"Research findings:\n{research_context}"
    )

    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": _SYNTHESIS_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception:
        logger.warning("Research synthesis failed", exc_info=True)
        return None

    content = _get_completion_content(response)
    if not content:
        logger.warning("LLM returned empty response for research synthesis")
        return None
    return content


def research_note(
    path: str,
    depth: str = "shallow",
    focus: str | None = None,
) -> str:
    """Research topics found in a vault note.

    Reads the note, extracts topics via LLM, gathers research from web
    and vault searches, synthesizes findings, and appends a ## Research
    section to the file.

    Args:
        path: Path to the note file (relative to vault or absolute).
        depth: Research depth - "shallow" or "deep".
        focus: Optional focus area for topic extraction.

    Returns:
        JSON confirmation with path, topics_researched, and preview on
        success, or error on failure.
    """
    # Validate depth
    if depth not in _VALID_DEPTHS:
        return err(
            f"Invalid depth: {depth!r}. Must be one of: {', '.join(sorted(_VALID_DEPTHS))}"
        )

    # Check API key
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    # Resolve file and check extension
    file_path, resolve_err = resolve_file(path)
    if resolve_err:
        return err(resolve_err)
    if file_path.suffix.lower() not in _TEXT_SAFE_EXTENSIONS:
        return err(
            f"Cannot research {file_path.suffix or 'extensionless'} file. "
            "Only markdown/text files are supported."
        )

    # Read note content via read_file (handles embeds)
    raw = read_file(path, offset=0, length=MAX_SUMMARIZE_CHARS)
    data = json.loads(raw)
    if not data.get("success"):
        return err(data.get("error", "Failed to read file"))

    content = (
        data.get("content")
        or data.get("transcript")
        or data.get("description")
        or ""
    )
    if not content.strip():
        return err("File has no content to research")

    # Cap content for LLM
    if len(content) > MAX_SUMMARIZE_CHARS:
        content = content[:MAX_SUMMARIZE_CHARS]

    # Create OpenAI client
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    # Stage 1: Extract topics
    logger.info("Extracting topics from %s", path)
    topics = _extract_topics(client, content, focus=focus)
    if not topics:
        return err("No topics could be extracted from the note")

    # Stage 2: Gather research
    logger.info("Researching %d topics from %s (depth=%s)", len(topics), path, depth)
    start = time.perf_counter()
    research_results = _gather_research(topics, depth=depth, client=client)
    elapsed_gather = time.perf_counter() - start
    logger.info("Research gathering completed in %.2fs", elapsed_gather)

    # Stage 3: Synthesize
    logger.info("Synthesizing research for %s", path)
    synthesis = _synthesize_research(client, content, research_results)
    if not synthesis:
        return err("Research synthesis failed — LLM returned empty result")

    # Write to file — try section replace first, fall back to append
    formatted = f"## Research\n\n{synthesis}"
    write_result = json.loads(
        edit_file(path, formatted, "section", heading="## Research", mode="replace")
    )
    if not write_result.get("success"):
        error_msg = write_result.get("error", "")
        if "Heading not found" in error_msg:
            # Section doesn't exist yet — append it
            formatted = f"\n## Research\n\n{synthesis}"
            write_result = json.loads(edit_file(path, formatted, "append"))
        # Other failures (e.g. multiple matching headings) propagate as errors

    if not write_result.get("success"):
        return err(write_result.get("error", "Failed to write research section"))

    rel_path = get_relative_path(file_path)
    preview = synthesis[:500]
    if len(synthesis) > 500:
        preview += "…"
    return ok(path=rel_path, topics_researched=len(topics), preview=preview)
