# research_note Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `research_note` MCP tool that reads a vault note, autonomously extracts topics, researches them via web + vault, and appends a `## Research` section with synthesized findings.

**Architecture:** Self-contained inner LLM pipeline in `src/tools/research.py`. Three stages: topic extraction → concurrent research gathering (web + vault, optionally fetching pages) → synthesis. Follows the `summarize_file` pattern (own Fireworks client, text-safe allowlist, edit_file integration). Uses `ThreadPoolExecutor` for concurrent per-topic research.

**Tech Stack:** Python, OpenAI SDK (Fireworks), httpx, html2text (new dep), DuckDuckGo (ddgs), ChromaDB (via existing search infrastructure)

**Design doc:** `docs/plans/2026-02-28-research-note-design.md`

---

### Task 1: Add config constants and dependency

**Files:**
- Modify: `src/config.py:70` (after `MAX_SUMMARIZE_CHARS`)
- Modify: `requirements.txt` (add `html2text`)

**Step 1: Add constants to config.py**

Add after line 70 (`MAX_SUMMARIZE_CHARS = 200_000`):

```python
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", FIREWORKS_MODEL)
MAX_RESEARCH_TOPICS = 10  # Max topics to extract from a note
MAX_PAGE_CHARS = 50_000  # Safety cap for fetched web page content
PAGE_FETCH_TIMEOUT = 10  # Seconds per web page fetch
```

**Step 2: Add html2text to requirements.txt**

Add `html2text` to the dependencies list.

**Step 3: Install the dependency**

Run: `pip install html2text`

**Step 4: Commit**

```bash
git add src/config.py requirements.txt
git commit -m "feat: add research_note config constants and html2text dependency"
```

---

### Task 2: Write core research module with topic extraction tests

**Files:**
- Create: `src/tools/research.py`
- Create: `tests/test_tools_research.py`

**Step 1: Write the failing tests for topic extraction**

Create `tests/test_tools_research.py`:

```python
"""Tests for tools/research.py - agentic note research."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.research import research_note, _extract_topics


class TestExtractTopics:
    """Tests for topic extraction (Stage 1)."""

    def test_extracts_topics_from_content(self):
        """Should send content to LLM and parse structured topic list."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"topic": "climate change", "context": "rising temperatures", "type": "concept"},
            {"topic": "carbon taxes", "context": "proposed policy", "type": "claim"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        topics = _extract_topics(mock_client, "Note about climate change and carbon taxes.")
        assert len(topics) == 2
        assert topics[0]["topic"] == "climate change"
        assert topics[1]["type"] == "claim"

    def test_focus_narrows_extraction(self):
        """Should include focus in the extraction prompt."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"topic": "carbon taxes", "context": "proposed policy", "type": "claim"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _extract_topics(mock_client, "Note content.", focus="carbon taxes")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "carbon taxes" in user_msg["content"]

    def test_caps_at_max_topics(self):
        """Should truncate to MAX_RESEARCH_TOPICS."""
        topics_list = [
            {"topic": f"topic {i}", "context": "ctx", "type": "concept"}
            for i in range(15)
        ]
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics_list)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("tools.research.MAX_RESEARCH_TOPICS", 10):
            topics = _extract_topics(mock_client, "content")

        assert len(topics) == 10

    def test_llm_returns_none(self):
        """Should return empty list when LLM returns None."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        topics = _extract_topics(mock_client, "content")
        assert topics == []

    def test_llm_returns_invalid_json(self):
        """Should return empty list when LLM returns non-JSON."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Here are some topics: ..."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        topics = _extract_topics(mock_client, "content")
        assert topics == []
```

**Step 2: Write minimal implementation to make tests pass**

Create `src/tools/research.py`:

```python
"""Research tool - agentic note research via LLM pipeline."""

import json
import logging

from openai import OpenAI

from config import MAX_RESEARCH_TOPICS, RESEARCH_MODEL

logger = logging.getLogger(__name__)

_TOPIC_EXTRACTION_PROMPT = """\
You are a research assistant. Given the contents of a note, identify the key \
topics, claims, and questions that would benefit from further research.

Return a JSON array of objects, each with:
- "topic": a short label (2-5 words)
- "context": what the note says about this topic (1-2 sentences)
- "type": one of "claim", "concept", or "question"

Return ONLY valid JSON — no markdown fences, no commentary."""


def _extract_topics(
    client: OpenAI,
    content: str,
    focus: str | None = None,
) -> list[dict]:
    """Extract research topics from note content via LLM.

    Args:
        client: OpenAI client configured for Fireworks.
        content: Note text content.
        focus: Optional focus to narrow extraction.

    Returns:
        List of topic dicts, capped at MAX_RESEARCH_TOPICS.
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
    except Exception as e:
        logger.warning("Topic extraction failed: %s", e)
        return []

    raw = response.choices[0].message.content
    if not raw:
        return []

    try:
        topics = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON for topic extraction")
        return []

    if not isinstance(topics, list):
        return []

    return topics[:MAX_RESEARCH_TOPICS]


def research_note(path: str, depth: str = "shallow", focus: str | None = None) -> str:
    """Placeholder — implemented in Task 4."""
    raise NotImplementedError
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestExtractTopics -v`
Expected: All 5 tests PASS

**Step 4: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add topic extraction for research_note (Stage 1)"
```

---

### Task 3: Research gathering (Stage 2) with tests

**Files:**
- Modify: `src/tools/research.py`
- Modify: `tests/test_tools_research.py`

**Step 1: Write the failing tests for research gathering**

Add to `tests/test_tools_research.py`:

```python
from tools.research import _gather_research


class TestGatherResearch:
    """Tests for research gathering (Stage 2)."""

    def test_shallow_searches_web_and_vault(self):
        """Should call web_search and find_notes for each topic."""
        topics = [
            {"topic": "climate change", "context": "rising temps", "type": "concept"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": True, "results": [
                {"title": "Climate FAQ", "url": "https://example.com", "snippet": "Info about climate."}
            ]})
            mock_vault.return_value = json.dumps({"success": True, "results": [
                {"path": "notes/climate.md", "snippet": "Vault content about climate."}
            ]})

            results = _gather_research(topics, depth="shallow")

        assert len(results) == 1
        assert results[0]["topic"] == "climate change"
        assert len(results[0]["web_results"]) == 1
        assert len(results[0]["vault_results"]) == 1

    def test_deep_fetches_pages(self):
        """Should fetch web pages and extract content in deep mode."""
        topics = [
            {"topic": "carbon taxes", "context": "policy", "type": "claim"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault, \
             patch("tools.research.httpx") as mock_httpx, \
             patch("tools.research._extract_page_content") as mock_extract:
            mock_web.return_value = json.dumps({"success": True, "results": [
                {"title": "Tax Policy", "url": "https://example.com/tax", "snippet": "Tax info."},
            ]})
            mock_vault.return_value = json.dumps({"success": True, "results": []})

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = "<html><body>Page content</body></html>"
            mock_httpx.get.return_value = mock_response

            mock_extract.return_value = "Extracted page content about taxes."

            results = _gather_research(topics, depth="deep")

        assert len(results[0].get("page_extracts", [])) > 0

    def test_web_search_failure_skipped(self):
        """Should skip failed web searches gracefully."""
        topics = [
            {"topic": "topic1", "context": "ctx", "type": "concept"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": False, "error": "Search failed"})
            mock_vault.return_value = json.dumps({"success": True, "results": []})

            results = _gather_research(topics, depth="shallow")

        assert len(results) == 1
        assert results[0]["web_results"] == []

    def test_page_fetch_failure_skipped(self):
        """Should skip failed page fetches in deep mode."""
        topics = [
            {"topic": "topic1", "context": "ctx", "type": "concept"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault, \
             patch("tools.research.httpx") as mock_httpx:
            mock_web.return_value = json.dumps({"success": True, "results": [
                {"title": "Page", "url": "https://example.com", "snippet": "s"},
            ]})
            mock_vault.return_value = json.dumps({"success": True, "results": []})
            mock_httpx.get.side_effect = Exception("Connection timeout")

            results = _gather_research(topics, depth="deep")

        assert results[0].get("page_extracts", []) == []
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestGatherResearch -v`
Expected: FAIL (ImportError — `_gather_research` not defined)

**Step 3: Implement _gather_research**

Add to `src/tools/research.py`:

```python
import html2text
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import MAX_PAGE_CHARS, PAGE_FETCH_TIMEOUT
from tools.search import find_notes, web_search

_MAX_URLS_PER_TOPIC = 2


def _fetch_page(url: str) -> str | None:
    """Fetch a web page and convert HTML to markdown text.

    Returns None on any failure (timeout, HTTP error, etc.).
    """
    try:
        response = httpx.get(
            url,
            timeout=PAGE_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "ObsidianTools/1.0 (research)"},
        )
        response.raise_for_status()
    except Exception as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0  # No line wrapping
    text = converter.handle(response.text)

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS]
    return text


def _extract_page_content(client: OpenAI, text: str, topic: str) -> str | None:
    """Send fetched page text to LLM to extract relevant content.

    Returns extracted content string, or None on failure.
    """
    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": "Extract information relevant to the given topic from the web page text. Be concise. Return only the relevant extracted information."},
                {"role": "user", "content": f"Topic: {topic}\n\nPage content:\n{text}"},
            ],
        )
    except Exception as e:
        logger.warning("Page extraction failed for topic '%s': %s", topic, e)
        return None

    return response.choices[0].message.content


def _research_topic(
    topic: dict,
    depth: str,
    client: OpenAI | None = None,
) -> dict:
    """Research a single topic via web search and vault search.

    Args:
        topic: Dict with "topic", "context", "type" keys.
        depth: "shallow" or "deep".
        client: OpenAI client (required for deep mode page extraction).

    Returns:
        Dict with topic info plus web_results, vault_results, and
        optionally page_extracts.
    """
    label = topic["topic"]
    result = {
        "topic": label,
        "context": topic.get("context", ""),
        "type": topic.get("type", "concept"),
        "web_results": [],
        "vault_results": [],
    }

    # Web search
    try:
        web_raw = json.loads(web_search(label))
        if web_raw.get("success") and web_raw.get("results"):
            result["web_results"] = web_raw["results"]
    except Exception as e:
        logger.debug("Web search failed for topic '%s': %s", label, e)

    # Vault search
    try:
        vault_raw = json.loads(find_notes(query=label, mode="hybrid", n_results=5))
        if vault_raw.get("success") and vault_raw.get("results"):
            result["vault_results"] = vault_raw["results"]
    except Exception as e:
        logger.debug("Vault search failed for topic '%s': %s", label, e)

    # Deep mode: fetch and extract web pages
    if depth == "deep" and client and result["web_results"]:
        extracts = []
        urls = [r["url"] for r in result["web_results"][:_MAX_URLS_PER_TOPIC] if r.get("url")]
        for url in urls:
            page_text = _fetch_page(url)
            if page_text:
                extracted = _extract_page_content(client, page_text, label)
                if extracted:
                    extracts.append({"url": url, "content": extracted})
        result["page_extracts"] = extracts

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
        client: OpenAI client for deep mode page extraction.

    Returns:
        List of research result dicts, one per topic.
    """
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_research_topic, topic, depth, client): topic
            for topic in topics
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                topic = futures[future]
                logger.warning("Research failed for topic '%s': %s", topic.get("topic"), e)

    # Preserve original topic order
    topic_order = {t["topic"]: i for i, t in enumerate(topics)}
    results.sort(key=lambda r: topic_order.get(r["topic"], len(topics)))
    return results
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestGatherResearch -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add research gathering for research_note (Stage 2)"
```

---

### Task 4: Synthesis and main tool function (Stage 3) with tests

**Files:**
- Modify: `src/tools/research.py`
- Modify: `tests/test_tools_research.py`

**Step 1: Write the failing tests for synthesis and main function**

Add to `tests/test_tools_research.py`:

```python
from tools.research import _synthesize_research


class TestSynthesizeResearch:
    """Tests for research synthesis (Stage 3)."""

    def test_sends_all_material_to_llm(self):
        """Should include note content and all research in synthesis prompt."""
        research_results = [
            {
                "topic": "climate change",
                "context": "rising temps",
                "type": "concept",
                "web_results": [{"title": "FAQ", "url": "https://example.com", "snippet": "info"}],
                "vault_results": [{"path": "notes/climate.md", "snippet": "vault info"}],
            },
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "## Research findings here."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _synthesize_research(mock_client, "Original note content.", research_results)

        assert result == "## Research findings here."
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "climate change" in user_msg["content"]
        assert "Original note content" in user_msg["content"]

    def test_llm_returns_none(self):
        """Should return None when LLM returns empty."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _synthesize_research(mock_client, "content", [])
        assert result is None


class TestResearchNote:
    """Tests for the main research_note tool."""

    def test_happy_path(self, vault_config):
        """Should read note, research topics, append ## Research section."""
        mock_response_topics = MagicMock()
        mock_response_topics.choices = [MagicMock()]
        mock_response_topics.choices[0].message.content = json.dumps([
            {"topic": "testing", "context": "unit tests", "type": "concept"},
        ])

        mock_response_synthesis = MagicMock()
        mock_response_synthesis.choices = [MagicMock()]
        mock_response_synthesis.choices[0].message.content = (
            "### Testing\n\nResearch findings about testing."
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            mock_response_topics,
            mock_response_synthesis,
        ]

        with patch("tools.research.OpenAI", return_value=mock_client), \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": True, "results": []})
            mock_vault.return_value = json.dumps({"success": True, "results": []})

            result = json.loads(research_note("note1.md"))

        assert result["success"] is True
        assert result["topics_researched"] == 1
        assert "testing" in result["preview"].lower()

        content = (vault_config / "note1.md").read_text()
        assert "## Research" in content
        assert "Research findings about testing" in content

    def test_replaces_existing_research_section(self, vault_config):
        """Should replace existing ## Research when re-run."""
        note = vault_config / "note1.md"
        original = note.read_text()
        note.write_text(original + "\n## Research\n\nOld research content.\n")

        mock_response_topics = MagicMock()
        mock_response_topics.choices = [MagicMock()]
        mock_response_topics.choices[0].message.content = json.dumps([
            {"topic": "testing", "context": "ctx", "type": "concept"},
        ])

        mock_response_synthesis = MagicMock()
        mock_response_synthesis.choices = [MagicMock()]
        mock_response_synthesis.choices[0].message.content = "New research content."

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            mock_response_topics,
            mock_response_synthesis,
        ]

        with patch("tools.research.OpenAI", return_value=mock_client), \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": True, "results": []})
            mock_vault.return_value = json.dumps({"success": True, "results": []})

            result = json.loads(research_note("note1.md"))

        assert result["success"] is True
        content = note.read_text()
        assert "Old research content" not in content
        assert "New research content" in content

    def test_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(research_note("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_binary_file_rejected(self, vault_config):
        """Should reject non-text files."""
        attachments = vault_config / "Attachments"
        (attachments / "recording.m4a").write_bytes(b"fake audio")

        result = json.loads(research_note("Attachments/recording.m4a"))
        assert result["success"] is False
        assert "markdown/text" in result["error"].lower()

    def test_no_api_key(self, vault_config):
        """Should return error when FIREWORKS_API_KEY is not set."""
        with patch("os.getenv", return_value=None):
            result = json.loads(research_note("note1.md"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    def test_no_topics_extracted(self, vault_config):
        """Should return error when no topics found."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[]"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("tools.research.OpenAI", return_value=mock_client):
            result = json.loads(research_note("note1.md"))

        assert result["success"] is False
        assert "topic" in result["error"].lower()

    def test_synthesis_failure(self, vault_config):
        """Should return error when synthesis fails, file unchanged."""
        original = (vault_config / "note1.md").read_text()

        mock_response_topics = MagicMock()
        mock_response_topics.choices = [MagicMock()]
        mock_response_topics.choices[0].message.content = json.dumps([
            {"topic": "testing", "context": "ctx", "type": "concept"},
        ])

        mock_response_synthesis = MagicMock()
        mock_response_synthesis.choices = [MagicMock()]
        mock_response_synthesis.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            mock_response_topics,
            mock_response_synthesis,
        ]

        with patch("tools.research.OpenAI", return_value=mock_client), \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": True, "results": []})
            mock_vault.return_value = json.dumps({"success": True, "results": []})

            result = json.loads(research_note("note1.md"))

        assert result["success"] is False
        assert (vault_config / "note1.md").read_text() == original

    def test_invalid_depth(self, vault_config):
        """Should return error for invalid depth value."""
        result = json.loads(research_note("note1.md", depth="extreme"))
        assert result["success"] is False
        assert "depth" in result["error"].lower()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestSynthesizeResearch tests/test_tools_research.py::TestResearchNote -v`
Expected: FAIL (ImportError — `_synthesize_research` not defined, `research_note` raises NotImplementedError)

**Step 3: Implement _synthesize_research and research_note**

Add `_synthesize_research` to `src/tools/research.py`:

```python
import os
import time

from config import FIREWORKS_BASE_URL, MAX_SUMMARIZE_CHARS
from services.vault import err, get_relative_path, ok, resolve_file
from tools.editing import edit_file
from tools.files import read_file

_TEXT_SAFE_EXTENSIONS = {".md", ".txt", ".markdown"}
_VALID_DEPTHS = {"shallow", "deep"}

_SYNTHESIS_PROMPT = """\
You are a research assistant. Given a note's original content and research \
gathered on its key topics, produce a structured research supplement in markdown.

Guidelines:
- Organize findings by topic using ### headings.
- Cite web sources as markdown links: [Title](URL).
- Reference vault notes as Obsidian wikilinks: [[Note Name]].
- Flag any contradictions between the note and external sources.
- Highlight related vault content the user may not be aware of.
- Be concise — this is a research supplement, not a thesis.
- Do NOT include a top-level heading (the caller adds "## Research").
- Use markdown formatting appropriate for Obsidian."""


def _synthesize_research(
    client: OpenAI,
    note_content: str,
    research_results: list[dict],
) -> str | None:
    """Synthesize research findings into a markdown section.

    Args:
        client: OpenAI client configured for Fireworks.
        note_content: Original note text.
        research_results: Per-topic research from _gather_research.

    Returns:
        Markdown string with research findings, or None on failure.
    """
    # Build research context
    research_text = ""
    for r in research_results:
        research_text += f"\n### Topic: {r['topic']}\n"
        research_text += f"Note context: {r.get('context', 'N/A')}\n"

        if r.get("web_results"):
            research_text += "\nWeb results:\n"
            for w in r["web_results"]:
                research_text += f"- [{w.get('title', 'Untitled')}]({w.get('url', '')}): {w.get('snippet', '')}\n"

        if r.get("vault_results"):
            research_text += "\nRelated vault notes:\n"
            for v in r["vault_results"]:
                path = v.get("path") or v.get("source", "")
                snippet = v.get("snippet") or v.get("content", "")[:200]
                name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] if path else "Unknown"
                research_text += f"- [[{name}]]: {snippet}\n"

        if r.get("page_extracts"):
            research_text += "\nDetailed page extracts:\n"
            for p in r["page_extracts"]:
                research_text += f"\nFrom {p['url']}:\n{p['content']}\n"

    user_content = (
        f"Original note:\n{note_content}\n\n"
        f"Research gathered:\n{research_text}"
    )

    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": _SYNTHESIS_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as e:
        logger.warning("Research synthesis failed: %s", e)
        return None

    return response.choices[0].message.content
```

Replace the `research_note` placeholder:

```python
def research_note(
    path: str,
    depth: str = "shallow",
    focus: str | None = None,
) -> str:
    """Research topics in a vault note and append findings.

    Reads the note, extracts key topics via LLM, researches each topic
    using web search and vault discovery, synthesizes findings, and
    appends a ## Research section.

    Args:
        path: Path to the note (relative to vault or absolute).
        depth: "shallow" (snippets only) or "deep" (fetch web pages).
        focus: Optional focus to narrow topic extraction.

    Returns:
        JSON with path, topics_researched, and preview on success,
        or error on failure.
    """
    if depth not in _VALID_DEPTHS:
        return err(f"Invalid depth: {depth!r}. Must be 'shallow' or 'deep'.")

    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    # Validate file
    file_path, resolve_err = resolve_file(path)
    if resolve_err:
        return err(resolve_err)
    if file_path.suffix.lower() not in _TEXT_SAFE_EXTENSIONS:
        return err(
            f"Cannot research {file_path.suffix or 'extensionless'} file. "
            "Only markdown/text files are supported."
        )

    # Read note content
    raw = read_file(path, offset=0, length=MAX_SUMMARIZE_CHARS)
    data = json.loads(raw)
    if not data.get("success"):
        return err(data.get("error", "Failed to read file"))

    content = data.get("content") or ""
    if not content.strip():
        return err("File has no content to research")

    if len(content) > MAX_SUMMARIZE_CHARS:
        content = content[:MAX_SUMMARIZE_CHARS]

    # Stage 1: Extract topics
    logger.info("Researching %s (depth=%s)", path, depth)
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)
    start = time.perf_counter()

    topics = _extract_topics(client, content, focus=focus)
    if not topics:
        return err("Could not extract any research topics from note")

    # Stage 2: Gather research
    research_results = _gather_research(topics, depth=depth, client=client)

    # Stage 3: Synthesize
    synthesis = _synthesize_research(client, content, research_results)
    if not synthesis:
        return err("Research synthesis failed — LLM returned empty result")

    elapsed = time.perf_counter() - start
    logger.info(
        "Researched %s: %d topics in %.2fs (%d chars)",
        path, len(topics), elapsed, len(synthesis),
    )

    # Write to file — replace existing ## Research or append
    existing_content = file_path.read_text(encoding="utf-8")
    if "\n## Research" in existing_content or existing_content.startswith("## Research"):
        write_result = json.loads(
            edit_file(path, f"## Research\n\n{synthesis}", "section", heading="## Research", mode="replace")
        )
    else:
        write_result = json.loads(
            edit_file(path, f"\n## Research\n\n{synthesis}", "append")
        )

    if not write_result.get("success"):
        return err(write_result.get("error", "Failed to write research section"))

    rel_path = get_relative_path(file_path)
    preview = synthesis[:500]
    if len(synthesis) > 500:
        preview += "..."
    return ok(
        path=rel_path,
        topics_researched=len(topics),
        preview=preview,
    )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add synthesis and main research_note function (Stage 3)"
```

---

### Task 5: MCP registration and compaction stub

**Files:**
- Modify: `src/mcp_server.py:42,79`
- Modify: `src/services/compaction.py:167,176`
- Modify: `tests/test_tools_research.py` (add compaction test)

**Step 1: Write the failing compaction test**

Add to `tests/test_tools_research.py`:

```python
from services.compaction import build_tool_stub


class TestResearchNoteCompaction:
    """Tests for research_note compaction stub."""

    def test_stub_keeps_path_and_topics(self):
        """Should keep path and topics_researched, drop preview."""
        content = json.dumps({
            "success": True,
            "path": "notes/test.md",
            "topics_researched": 3,
            "preview": "Long preview text that should be dropped...",
        })

        stub = json.loads(build_tool_stub(content, "research_note"))
        assert stub["path"] == "notes/test.md"
        assert stub["topics_researched"] == 3
        assert "preview" not in stub
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestResearchNoteCompaction -v`
Expected: FAIL (falls through to generic stub — `preview` may still be present or structure differs)

**Step 3: Add compaction stub builder**

Add to `src/services/compaction.py` after `_build_summarize_file_stub` (line 166):

```python
def _build_research_note_stub(data: dict) -> str:
    """Compact research_note: keep path and topics_researched."""
    stub = _base_stub(data)
    if "path" in data:
        stub["path"] = data["path"]
    if "topics_researched" in data:
        stub["topics_researched"] = data["topics_researched"]
    return json.dumps(stub)
```

Add to `_TOOL_STUB_BUILDERS` dict:

```python
"research_note": _build_research_note_stub,
```

**Step 4: Register in MCP server**

Add import to `src/mcp_server.py` (after line 42):
```python
from tools.research import research_note
```

Add registration (after line 79):
```python
# Research tools
mcp.tool()(research_note)
```

**Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add src/mcp_server.py src/services/compaction.py tests/test_tools_research.py
git commit -m "feat: register research_note in MCP server and add compaction stub"
```

---

### Task 6: Tool timeout and system prompt update

**Files:**
- Modify: `src/agent.py:33,220-232` (per-tool timeout)
- Modify: `system_prompt.txt.example` (tool reference + decision tree)

**Step 1: Add per-tool timeout override**

In `src/agent.py`, add a timeout override dict after the `TOOL_TIMEOUT` constant (line 33):

```python
TOOL_TIMEOUT = 60  # seconds
_TOOL_TIMEOUT_OVERRIDES = {
    "research_note": 300,  # 5 minutes — multi-step LLM pipeline
}
```

Modify `execute_tool_call` (around line 225) to use the override:

```python
async def execute_tool_call(
    session: ClientSession, tool_name: str, arguments: dict
) -> str:
    """Execute a tool call via MCP and return the result."""
    timeout = _TOOL_TIMEOUT_OVERRIDES.get(tool_name, TOOL_TIMEOUT)
    try:
        with anyio.fail_after(timeout):
            result = await session.call_tool(tool_name, arguments)
        if result.isError:
            return f"Tool error: {extract_text_content(result.content)}"
        return extract_text_content(result.content)
    except TimeoutError:
        logger.warning("Tool '%s' timed out after %ds", tool_name, timeout)
        return f"Tool error: '{tool_name}' timed out after {timeout}s"
    except Exception as e:
        return f"Failed to execute tool {tool_name}: {e}"
```

**Step 2: Update system_prompt.txt.example**

Add to the "Choosing the Right Tool" decision table:

```
| "Research this note" / "What does the web say about this?" | research_note | Agentic research — extracts topics, searches web + vault, appends ## Research |
```

Add to the "Available Tools" section after summarize_file:

```
### Research
- research_note: Research topics in a vault note. Reads the note, extracts key
  topics/claims/questions, searches the web and vault for each, and appends a
  ## Research section with synthesized findings and citations. Parameters: path,
  depth ("shallow" for search snippets, "deep" to fetch and read web pages),
  focus (optional — narrow to specific topic). Returns a preview of the research
  — relay it to the user. Do NOT call read_file afterward; the research is
  already written to the file. This tool takes longer than others (especially
  in deep mode) — let the user know it's working.
```

**Step 3: Commit**

```bash
git add src/agent.py system_prompt.txt.example
git commit -m "feat: add research_note timeout override and system prompt docs"
```

---

### Task 7: Update CLAUDE.md and run full test suite

**Files:**
- Modify: `CLAUDE.md` (tool count, architecture table, tool reference)

**Step 1: Update CLAUDE.md**

Update the MCP tool count from 17 to 18 wherever it appears. Add `research_note` to the MCP Tools table:

```
| `research_note` | Research topics in a note via web + vault | `path`, `depth` ("shallow"/"deep"), `focus` |
```

Add to the architecture section under `src/tools/`:
```
│   ├── research.py      # research_note (agentic LLM pipeline: extract → search → synthesize)
```

**Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests PASS (including all new research tests)

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add research_note to CLAUDE.md tool reference"
```
