# Ad-hoc Topic Research Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `topic` parameter to `research_note` so users can research any topic from scratch without an existing vault note.

**Architecture:** Extend `research_note` with a mutually exclusive `topic` param. When `topic` is provided, skip file read, pass topic to `_extract_topics` for sub-topic extraction, run the existing gather/synthesize pipeline, generate a title via a new `_generate_title` LLM helper, then create a new note via `create_file`. All existing note-based behavior unchanged.

**Tech Stack:** Python, OpenAI SDK (Fireworks), MCP tools (create_file, edit_file)

---

### Task 1: Add `_generate_title` helper + tests

**Files:**
- Modify: `src/tools/research.py` (add helper near other helpers, ~line 115)
- Test: `tests/test_tools_research.py`

**Step 1: Write failing tests**

Add a new test class after `TestGetCompletionContent` in `tests/test_tools_research.py`:

```python
from tools.research import _generate_title

class TestGenerateTitle:
    """Tests for _generate_title helper."""

    def test_returns_llm_title(self):
        """Should return the title the LLM generates."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "New York Mets"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_title(mock_client, "the New York Mets", "Research about the Mets...")
        assert result == "New York Mets"

    def test_strips_whitespace_and_quotes(self):
        """Should strip surrounding whitespace and quotes from LLM response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '  "New York Mets"  \n'

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_title(mock_client, "the New York Mets", "Research...")
        assert result == "New York Mets"

    def test_fallback_on_empty_response(self):
        """Should fall back to title-cased topic when LLM returns empty."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_title(mock_client, "the new york mets", "Research...")
        assert result == "The New York Mets"

    def test_fallback_on_exception(self):
        """Should fall back to title-cased topic when LLM call raises."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = _generate_title(mock_client, "quantum computing", "Research...")
        assert result == "Quantum Computing"
```

Also add `_generate_title` to the import block at the top of the test file.

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestGenerateTitle -v`
Expected: FAIL with ImportError (function doesn't exist yet)

**Step 3: Implement `_generate_title`**

Add in `src/tools/research.py` after the `_MAX_URLS_PER_TOPIC` constants block (~line 116):

```python
_TITLE_PROMPT = """\
Given a research topic and the research content produced about it, generate a \
short, clean title suitable as a note filename. Return ONLY the title, no \
quotes, no file extension, no extra text. Examples:
- Topic "the New York Mets" → New York Mets
- Topic "quantum computing applications in drug discovery" → Quantum Computing in Drug Discovery"""


def _generate_title(
    client: OpenAI,
    topic: str,
    synthesis: str,
) -> str:
    """Generate a clean note title from a topic string via LLM.

    Falls back to title-cased topic string on any failure.

    Args:
        client: OpenAI-compatible API client.
        topic: The original topic string.
        synthesis: The synthesized research content for context.

    Returns:
        A clean title string suitable for use as a filename.
    """
    fallback = topic.strip().title()
    try:
        response = client.chat.completions.create(
            model=RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": _TITLE_PROMPT},
                {"role": "user", "content": f"Topic: {topic}\n\nResearch:\n{synthesis[:2000]}"},
            ],
        )
    except Exception:
        logger.warning("Title generation failed, using fallback", exc_info=True)
        return fallback

    raw = _get_completion_content(response)
    if not raw or not raw.strip():
        return fallback

    # Strip quotes and whitespace
    title = raw.strip().strip('"').strip("'").strip()
    return title if title else fallback
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestGenerateTitle -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add _generate_title helper for ad-hoc research (#150)"
```

---

### Task 2: Add `_sanitize_filename` helper + tests

**Files:**
- Modify: `src/tools/research.py`
- Test: `tests/test_tools_research.py`

**Step 1: Write failing tests**

Add test class and import `_sanitize_filename`:

```python
from tools.research import _sanitize_filename

class TestSanitizeFilename:
    """Tests for _sanitize_filename helper."""

    def test_clean_title_unchanged(self):
        """Clean titles should pass through with .md appended."""
        assert _sanitize_filename("New York Mets") == "New York Mets.md"

    def test_strips_unsafe_chars(self):
        """Should remove filesystem-unsafe characters."""
        assert _sanitize_filename('Test: A/B\\C*D?"E') == "Test ABCDE.md"

    def test_strips_leading_trailing_whitespace_and_dots(self):
        """Should strip leading/trailing whitespace and dots."""
        assert _sanitize_filename("  ..Hello World..  ") == "Hello World.md"

    def test_empty_after_sanitize_returns_fallback(self):
        """Should return 'Research.md' if sanitized result is empty."""
        assert _sanitize_filename("///") == "Research.md"

    def test_truncates_long_titles(self):
        """Should truncate titles longer than 200 chars."""
        long_title = "A" * 250
        result = _sanitize_filename(long_title)
        assert result == "A" * 200 + ".md"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestSanitizeFilename -v`
Expected: FAIL with ImportError

**Step 3: Implement `_sanitize_filename`**

Add in `src/tools/research.py` right after `_generate_title`:

```python
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*]')


def _sanitize_filename(title: str) -> str:
    """Sanitize a title string into a safe filename with .md extension.

    Removes filesystem-unsafe characters, strips leading/trailing whitespace
    and dots, truncates to 200 chars, falls back to 'Research.md' if empty.
    """
    name = _UNSAFE_FILENAME_RE.sub("", title)
    name = name.strip().strip(".")
    if not name:
        return "Research.md"
    if len(name) > 200:
        name = name[:200]
    return f"{name}.md"
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestSanitizeFilename -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add _sanitize_filename helper for ad-hoc research (#150)"
```

---

### Task 3: Add `topic` parameter to `research_note` + tests

**Files:**
- Modify: `src/tools/research.py:565-667` (the `research_note` function)
- Modify: `src/tools/research.py:26` (add `create_file` import)
- Test: `tests/test_tools_research.py`

**Step 1: Write failing tests**

Add a new test class `TestResearchNoteTopic` in `tests/test_tools_research.py`:

```python
class TestResearchNoteTopic:
    """Tests for research_note ad-hoc topic mode."""

    def _make_mock_response(self, content):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = content
        return mock_response

    def _make_web_search_ok(self, results):
        return json.dumps({"success": True, "results": results})

    def _make_find_notes_ok(self, results):
        return json.dumps({"success": True, "results": results, "total": len(results)})

    def test_happy_path_creates_note(self, vault_config):
        """Ad-hoc topic mode should create a new note with research findings."""
        topics = [
            {"topic": "Mets history", "context": "Franchise origins", "type": "theme"},
        ]

        # LLM calls: 1) extract topics, 2) synthesize, 3) generate title
        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Mets History\nFounded in 1962.")
        title_response = self._make_mock_response("New York Mets")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response, title_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="the New York Mets"))

        assert result["success"] is True
        assert result["topics_researched"] == 1
        assert "Mets" in result["preview"]

        # Verify file was created in vault root
        created_path = vault_config / "New York Mets.md"
        assert created_path.exists()
        content = created_path.read_text()
        assert "Mets History" in content

    def test_path_returned_is_relative(self, vault_config):
        """Result path should be relative to vault root."""
        topics = [{"topic": "Test", "context": "Testing", "type": "theme"}]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Test\nFindings.")
        title_response = self._make_mock_response("Test Research")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response, title_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="test topic"))

        assert result["success"] is True
        assert result["path"] == "Test Research.md"

    def test_both_path_and_topic_returns_error(self, vault_config):
        """Should error when both path and topic are provided."""
        result = json.loads(research_note(path="note1.md", topic="something"))
        assert result["success"] is False
        assert "mutually exclusive" in result["error"].lower()

    def test_neither_path_nor_topic_returns_error(self, vault_config):
        """Should error when neither path nor topic is provided."""
        result = json.loads(research_note())
        assert result["success"] is False
        assert "path" in result["error"].lower() or "topic" in result["error"].lower()

    def test_no_api_key(self):
        """Should return error when FIREWORKS_API_KEY is not set."""
        with patch("os.getenv", return_value=None):
            result = json.loads(research_note(topic="anything"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    def test_no_topics_extracted(self, vault_config):
        """Should return error when LLM finds no sub-topics."""
        topic_response = MagicMock()
        topic_response.choices = [MagicMock()]
        topic_response.choices[0].message.content = "[]"

        with patch("tools.research.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = topic_response

            result = json.loads(research_note(topic="xyzzy"))

        assert result["success"] is False
        assert "topic" in result["error"].lower()

    def test_synthesis_failure(self, vault_config):
        """Should return error when synthesis fails; no file created."""
        topics = [{"topic": "AI", "context": "Test", "type": "concept"}]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response(None)

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="AI safety"))

        assert result["success"] is False
        assert "synth" in result["error"].lower()

    def test_file_already_exists(self, vault_config):
        """Should return error if generated filename already exists."""
        # Create the file that would be generated
        (vault_config / "New York Mets.md").write_text("Existing note")

        topics = [{"topic": "History", "context": "Origins", "type": "theme"}]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### History\nFindings.")
        title_response = self._make_mock_response("New York Mets")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response, title_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="the New York Mets"))

        assert result["success"] is False
        assert "already exists" in result["error"].lower()

    def test_focus_param_works(self, vault_config):
        """Focus parameter should be passed through to _extract_topics."""
        topics = [{"topic": "Pitching", "context": "Mets pitchers", "type": "theme"}]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Pitching\nFindings.")
        title_response = self._make_mock_response("Mets Pitching")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response, title_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="New York Mets", focus="pitching staff"))

        assert result["success"] is True

        # Verify focus was passed to the topic extraction LLM call
        first_call = mock_client.chat.completions.create.call_args_list[0]
        user_msg = next(m for m in first_call.kwargs["messages"] if m["role"] == "user")
        assert "pitching staff" in user_msg["content"]

    def test_depth_param_works(self, vault_config):
        """Depth parameter should work for topic mode."""
        topics = [{"topic": "Test", "context": "Testing", "type": "theme"}]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Test\nFindings.")
        title_response = self._make_mock_response("Test Note")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault, \
             patch("tools.research._fetch_page", return_value="Page text"), \
             patch("tools.research._extract_page_content", return_value="Extracted"):
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response, title_response,
            ]
            mock_web.return_value = self._make_web_search_ok([
                {"title": "Result", "url": "https://example.com", "snippet": "Test"},
            ])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note(topic="test", depth="deep"))

        assert result["success"] is True

    def test_invalid_depth_returns_error(self):
        """Should return error for invalid depth even in topic mode."""
        result = json.loads(research_note(topic="test", depth="extreme"))
        assert result["success"] is False
        assert "depth" in result["error"].lower()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestResearchNoteTopic -v`
Expected: FAIL (signature doesn't accept `topic` yet)

**Step 3: Implement the `topic` parameter**

Modify `research_note` in `src/tools/research.py`. First add import at line 28:

```python
from tools.files import create_file, read_file
```

Then rewrite the function signature and body:

```python
def research_note(
    path: str | None = None,
    topic: str | None = None,
    depth: str = "shallow",
    focus: str | None = None,
) -> str:
    """Research topics found in a vault note, or research an ad-hoc topic.

    Two mutually exclusive modes:
    - Note-based (path): Extracts topics from the note, researches them,
      appends a ## Research section to the file.
    - Ad-hoc (topic): Uses the topic string for sub-topic extraction,
      researches them, creates a new note with findings.

    Args:
        path: Path to an existing note file (mutually exclusive with topic).
        topic: A topic string to research (mutually exclusive with path).
        depth: Research depth - "shallow" or "deep".
        focus: Optional focus area for topic extraction.

    Returns:
        JSON confirmation with path, topics_researched, and preview on
        success, or error on failure.
    """
    # Validate mutual exclusivity
    if path and topic:
        return err("'path' and 'topic' are mutually exclusive — provide one, not both")
    if not path and not topic:
        return err("Either 'path' or 'topic' must be provided")

    # Validate depth
    if depth not in _VALID_DEPTHS:
        return err(
            f"Invalid depth: {depth!r}. Must be one of: {', '.join(sorted(_VALID_DEPTHS))}"
        )

    # Check API key
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    if topic:
        return _research_adhoc(client, topic, depth, focus)

    return _research_from_note(client, path, depth, focus)
```

Extract the existing note-based flow into `_research_from_note`:

```python
def _research_from_note(
    client: OpenAI,
    path: str,
    depth: str,
    focus: str | None,
) -> str:
    """Research topics extracted from an existing vault note."""
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

    if len(content) > MAX_SUMMARIZE_CHARS:
        content = content[:MAX_SUMMARIZE_CHARS]

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
            formatted = f"\n## Research\n\n{synthesis}"
            write_result = json.loads(edit_file(path, formatted, "append"))

    if not write_result.get("success"):
        return err(write_result.get("error", "Failed to write research section"))

    rel_path = get_relative_path(file_path)
    preview = synthesis[:500]
    if len(synthesis) > 500:
        preview += "…"
    return ok(path=rel_path, topics_researched=len(topics), preview=preview)
```

Add the new ad-hoc function:

```python
def _research_adhoc(
    client: OpenAI,
    topic: str,
    depth: str,
    focus: str | None,
) -> str:
    """Research an ad-hoc topic and create a new note with findings."""
    # Stage 1: Extract sub-topics from the topic string
    logger.info("Extracting sub-topics for ad-hoc topic: %s", topic)
    topics = _extract_topics(client, topic, focus=focus)
    if not topics:
        return err("No topics could be extracted from the given topic")

    # Stage 2: Gather research
    logger.info("Researching %d sub-topics (depth=%s)", len(topics), depth)
    start = time.perf_counter()
    research_results = _gather_research(topics, depth=depth, client=client)
    elapsed_gather = time.perf_counter() - start
    logger.info("Research gathering completed in %.2fs", elapsed_gather)

    # Stage 3: Synthesize
    logger.info("Synthesizing research for topic: %s", topic)
    synthesis = _synthesize_research(client, topic, research_results)
    if not synthesis:
        return err("Research synthesis failed — LLM returned empty result")

    # Generate title and create file
    title = _generate_title(client, topic, synthesis)
    filename = _sanitize_filename(title)

    create_result = json.loads(create_file(filename, synthesis))
    if not create_result.get("success"):
        return err(create_result.get("error", "Failed to create research note"))

    preview = synthesis[:500]
    if len(synthesis) > 500:
        preview += "…"
    return ok(path=filename, topics_researched=len(topics), preview=preview)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py -v`
Expected: ALL PASS (existing + new tests)

**Step 5: Commit**

```bash
git add src/tools/research.py tests/test_tools_research.py
git commit -m "feat: add ad-hoc topic mode to research_note (#150)"
```

---

### Task 4: Update compaction stub test for topic mode

**Files:**
- Test: `tests/test_tools_research.py`

The existing compaction stub (`_build_research_note_stub`) already keeps `path` and `topics_researched`, which the ad-hoc mode also returns. Just add a test to confirm.

**Step 1: Write test**

Add to `TestResearchNoteCompaction`:

```python
    def test_stub_works_for_topic_mode(self):
        """Compaction stub should work for ad-hoc topic results too."""
        content = json.dumps({
            "success": True,
            "path": "New York Mets.md",
            "topics_researched": 5,
            "preview": "Long preview that should be dropped...",
        })

        stub = json.loads(build_tool_stub(content, "research_note"))
        assert stub["path"] == "New York Mets.md"
        assert stub["topics_researched"] == 5
        assert "preview" not in stub
```

**Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestResearchNoteCompaction -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_tools_research.py
git commit -m "test: add compaction stub test for topic mode (#150)"
```

---

### Task 5: Update system prompt and CLAUDE.md

**Files:**
- Modify: `system_prompt.txt.example` (research_note tool reference + decision tree)
- Modify: `CLAUDE.md` (tool table and research.py description)

**Step 1: Update system prompt**

In `system_prompt.txt.example`, find the research_note lines (~line 59 and ~175-180) and update:

Decision tree row:
```
| "Research this note" / "What does the web say about this?" / "Research quantum computing" | research_note | Agentic research — extracts topics, searches web + vault, appends ## Research or creates new note |
```

Research section:
```
- research_note: Research topics in a vault note OR research an ad-hoc topic.
  Two modes: (1) path mode — reads the note, extracts topics, appends ## Research
  section. (2) topic mode — researches the given topic, creates a new note with
  findings. Parameters: path OR topic (mutually exclusive), depth ("shallow"/"deep"),
  focus (optional). Returns a preview — relay it to the user.
```

**Step 2: Update CLAUDE.md**

Update the `research_note` row in the MCP Tools table:

```
| `research_note` | Research topics in a note or ad-hoc topic | `path` OR `topic` (mutually exclusive), `depth` ("shallow"/"deep"), `focus` |
```

Update the `tools/research.py` description in the file tree to mention ad-hoc mode:

```
├── research.py      # research_note (agentic LLM pipeline: extract → search → synthesize; note-based or ad-hoc topic)
```

**Step 3: Commit**

```bash
git add system_prompt.txt.example CLAUDE.md
git commit -m "docs: update system prompt and CLAUDE.md for ad-hoc research (#150)"
```

---

### Task 6: Run full test suite and verify

**Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Verify existing note-based tests still pass**

Run: `.venv/bin/python -m pytest tests/test_tools_research.py::TestResearchNote -v`
Expected: ALL PASS (7 existing tests unchanged)

---

### Task 7: Create PR

```bash
gh pr create --title "feat: ad-hoc topic research in research_note" --body "..."
```

Closes #150.
