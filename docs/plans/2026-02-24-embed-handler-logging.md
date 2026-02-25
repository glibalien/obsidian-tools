# Embed Handler Logging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add structured logging to binary embed handlers and the cache layer so that audio transcription, image description, office extraction, and cache hits/misses are visible in logs.

**Architecture:** Add `logger = logging.getLogger(__name__)` to `tools/readers.py` and `tools/files.py`. Each handler in `readers.py` logs entry (filename + bytes), duration, and success/failure. `_expand_binary` in `files.py` logs cache hit and cache miss at debug level.

**Tech Stack:** Python `logging` stdlib, `time.perf_counter()` for timing, pytest `caplog` fixture for test assertions.

---

### Task 1: Add logging to `_expand_binary` (cache hit/miss)

**Files:**
- Modify: `src/tools/files.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests**

Add these two test methods to `class TestEmbedExpansion` in `tests/test_tools_files.py` (after line 1378):

```python
def test_binary_embed_cache_miss_logged(self, vault_config, monkeypatch, caplog):
    """Cache miss is logged at DEBUG level with filename and handler type."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    audio = vault_config / "Attachments" / "rec.m4a"
    audio.write_bytes(b"fake audio")

    with patch("tools.files.handle_audio") as mock_audio:
        mock_audio.return_value = '{"success": true, "transcript": "Hello"}'
        _embed_cache.clear()
        with caplog.at_level(logging.DEBUG, logger="tools.files"):
            _expand_embeds("![[rec.m4a]]", vault_config / "parent.md")
        assert any("Cache miss" in r.message and "rec.m4a" in r.message
                   for r in caplog.records)

def test_binary_embed_cache_hit_logged(self, vault_config, monkeypatch, caplog):
    """Cache hit is logged at DEBUG level with filename."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    audio = vault_config / "Attachments" / "rec.m4a"
    audio.write_bytes(b"fake audio")

    with patch("tools.files.handle_audio") as mock_audio:
        mock_audio.return_value = '{"success": true, "transcript": "Hello"}'
        _embed_cache.clear()
        _expand_embeds("![[rec.m4a]]", vault_config / "parent.md")
        with caplog.at_level(logging.DEBUG, logger="tools.files"):
            _expand_embeds("![[rec.m4a]]", vault_config / "parent.md")
        assert any("Cache hit" in r.message and "rec.m4a" in r.message
                   for r in caplog.records)
```

Also add `import logging` to the test file imports (top of file).

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestEmbedExpansion::test_binary_embed_cache_miss_logged tests/test_tools_files.py::TestEmbedExpansion::test_binary_embed_cache_hit_logged -v
```

Expected: FAIL — no log records emitted yet.

**Step 3: Add logger and cache logging to `files.py`**

At the top of `src/tools/files.py`, add after the existing imports:

```python
import logging
```

After the existing imports block, add:

```python
logger = logging.getLogger(__name__)
```

Place this immediately before the `_embed_cache` dict definition (around line 79).

In `_expand_binary`, add the cache hit log before the early return, and the cache miss log before the handler dispatch:

```python
def _expand_binary(file_path: Path, reference: str) -> str:
    """Expand a binary embed (audio/image/office) with caching."""
    path_str = str(file_path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return f"> [Embed error: {reference} — Cannot stat file]"

    cache_key = (path_str, mtime)
    if cache_key in _embed_cache:
        logger.debug("Cache hit: %s", file_path.name)
        expanded = _embed_cache[cache_key]
    else:
        ext = file_path.suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            logger.debug("Cache miss: %s — calling audio handler", file_path.name)
            raw = handle_audio(file_path)
        elif ext in IMAGE_EXTENSIONS:
            logger.debug("Cache miss: %s — calling image handler", file_path.name)
            raw = handle_image(file_path)
        elif ext in OFFICE_EXTENSIONS:
            logger.debug("Cache miss: %s — calling office handler", file_path.name)
            raw = handle_office(file_path)
        else:
            return f"> [Embed error: {reference} — Unsupported binary type]"

        result = json.loads(raw)
        if not result.get("success"):
            return f"> [Embed error: {reference} — {result.get('error', 'Unknown error')}]"

        expanded = (
            result.get("transcript")
            or result.get("description")
            or result.get("content")
            or ""
        )
        # Evict oldest entries if cache is full
        if len(_embed_cache) >= _EMBED_CACHE_MAX:
            oldest = next(iter(_embed_cache))
            del _embed_cache[oldest]
        _embed_cache[cache_key] = expanded

    return _format_embed(reference, expanded)
```

**Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestEmbedExpansion::test_binary_embed_cache_miss_logged tests/test_tools_files.py::TestEmbedExpansion::test_binary_embed_cache_hit_logged -v
```

Expected: PASS

**Step 5: Run full test suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: log cache hit/miss in _expand_binary (closes part of #114)"
```

---

### Task 2: Add logging to `handle_audio`

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests**

Add to `tests/test_tools_files.py`. First, add `from tools.readers import handle_audio, handle_image, handle_office` to the imports block (alongside existing imports from `tools.files`).

Add a new test class after the existing `TestEmbedExpansion` class:

```python
class TestBinaryHandlerLogging:
    """Tests for logging in binary embed handlers."""

    def test_handle_audio_logs_entry_and_success(self, tmp_path, caplog):
        """handle_audio logs file name, size, and duration on success."""
        audio = tmp_path / "rec.m4a"
        audio.write_bytes(b"x" * 1024)

        mock_response = MagicMock()
        mock_response.text = "Hello world"

        with patch("tools.readers.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.audio.transcriptions.create.return_value = mock_response
            with patch.dict("os.environ", {"FIREWORKS_API_KEY": "test-key"}):
                with caplog.at_level(logging.INFO, logger="tools.readers"):
                    result = handle_audio(audio)

        assert json.loads(result)["success"] is True
        messages = [r.message for r in caplog.records]
        assert any("rec.m4a" in m and "1024" in m for m in messages), \
            f"Expected entry log with filename and size, got: {messages}"
        assert any("rec.m4a" in m and "s" in m for m in messages), \
            f"Expected success log with duration, got: {messages}"

    def test_handle_audio_logs_warning_on_failure(self, tmp_path, caplog):
        """handle_audio logs a WARNING when the API call raises."""
        audio = tmp_path / "bad.m4a"
        audio.write_bytes(b"data")

        with patch("tools.readers.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.audio.transcriptions.create.side_effect = RuntimeError("API down")
            with patch.dict("os.environ", {"FIREWORKS_API_KEY": "test-key"}):
                with caplog.at_level(logging.WARNING, logger="tools.readers"):
                    handle_audio(audio)

        assert any("bad.m4a" in r.message and r.levelname == "WARNING"
                   for r in caplog.records)
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_audio_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_audio_logs_warning_on_failure -v
```

Expected: FAIL — no log records yet.

**Step 3: Add logger and logging to `handle_audio` in `readers.py`**

At the top of `src/tools/readers.py`, add:

```python
import logging
import time
```

After the imports, add:

```python
logger = logging.getLogger(__name__)
```

Replace `handle_audio` with:

```python
def handle_audio(file_path: Path) -> str:
    """Transcribe an audio file using Fireworks Whisper API."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0

    logger.info("Transcribing audio: %s (%d bytes)", file_path.name, size)
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)
    start = time.perf_counter()
    try:
        with open(file_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                extra_body={"diarize": True},
            )
        elapsed = time.perf_counter() - start
        logger.info("Transcribed %s in %.2fs", file_path.name, elapsed)
        return ok(transcript=response.text)
    except Exception as e:
        logger.warning("Transcription failed for %s: %s", file_path.name, e)
        return err(f"Transcription failed: {e}")
```

**Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_audio_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_audio_logs_warning_on_failure -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/tools/readers.py tests/test_tools_files.py
git commit -m "feat: add logging to handle_audio (issue #114)"
```

---

### Task 3: Add logging to `handle_image`

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests**

Add to `TestBinaryHandlerLogging` in `tests/test_tools_files.py`:

```python
def test_handle_image_logs_entry_and_success(self, tmp_path, caplog):
    """handle_image logs file name, size, and duration on success."""
    img = tmp_path / "diagram.png"
    img.write_bytes(b"x" * 2048)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "A diagram"

    with patch("tools.readers.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_response
        with patch.dict("os.environ", {"FIREWORKS_API_KEY": "test-key"}):
            with caplog.at_level(logging.INFO, logger="tools.readers"):
                result = handle_image(img)

    assert json.loads(result)["success"] is True
    messages = [r.message for r in caplog.records]
    assert any("diagram.png" in m and "2048" in m for m in messages)
    assert any("diagram.png" in m and "s" in m for m in messages)

def test_handle_image_logs_warning_on_failure(self, tmp_path, caplog):
    """handle_image logs a WARNING when the API call raises."""
    img = tmp_path / "broken.png"
    img.write_bytes(b"data")

    with patch("tools.readers.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("timeout")
        with patch.dict("os.environ", {"FIREWORKS_API_KEY": "test-key"}):
            with caplog.at_level(logging.WARNING, logger="tools.readers"):
                handle_image(img)

    assert any("broken.png" in r.message and r.levelname == "WARNING"
               for r in caplog.records)
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_image_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_image_logs_warning_on_failure -v
```

Expected: FAIL

**Step 3: Update `handle_image` in `readers.py`**

Replace `handle_image` with:

```python
def handle_image(file_path: Path) -> str:
    """Describe an image using Fireworks vision model."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0

    logger.info("Describing image: %s (%d bytes)", file_path.name, size)
    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)
    start = time.perf_counter()
    try:
        image_data = file_path.read_bytes()
        b64 = base64.b64encode(image_data).decode("utf-8")

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
        elapsed = time.perf_counter() - start
        description = response.choices[0].message.content
        logger.info("Described %s in %.2fs", file_path.name, elapsed)
        return ok(description=description)
    except Exception as e:
        logger.warning("Image description failed for %s: %s", file_path.name, e)
        return err(f"Image description failed: {e}")
```

**Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_image_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_image_logs_warning_on_failure -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add src/tools/readers.py tests/test_tools_files.py
git commit -m "feat: add logging to handle_image (issue #114)"
```

---

### Task 4: Add logging to `handle_office`

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write failing tests**

Add to `TestBinaryHandlerLogging`:

```python
def test_handle_office_logs_entry_and_success(self, tmp_path, caplog):
    """handle_office logs file name, size, and duration on success."""
    from docx import Document as DocxDocument
    docx = tmp_path / "report.docx"
    doc = DocxDocument()
    doc.add_paragraph("Hello")
    doc.save(str(docx))
    size = docx.stat().st_size

    with caplog.at_level(logging.INFO, logger="tools.readers"):
        result = handle_office(docx)

    assert json.loads(result)["success"] is True
    messages = [r.message for r in caplog.records]
    assert any("report.docx" in m and str(size) in m for m in messages)
    assert any("report.docx" in m and "s" in m for m in messages)

def test_handle_office_logs_warning_on_failure(self, tmp_path, caplog):
    """handle_office logs a WARNING when extraction fails."""
    bad = tmp_path / "corrupt.docx"
    bad.write_bytes(b"not a real docx")

    with caplog.at_level(logging.WARNING, logger="tools.readers"):
        handle_office(bad)

    assert any("corrupt.docx" in r.message and r.levelname == "WARNING"
               for r in caplog.records)
```

**Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_office_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_office_logs_warning_on_failure -v
```

Expected: FAIL

**Step 3: Update `handle_office` in `readers.py`**

Replace `handle_office` with:

```python
def handle_office(file_path: Path) -> str:
    """Extract text content from Office documents (.docx, .xlsx, .pptx)."""
    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0

    logger.info("Extracting office document: %s (%d bytes)", file_path.name, size)
    ext = file_path.suffix.lower()
    start = time.perf_counter()
    try:
        if ext == ".docx":
            result = _read_docx(file_path)
        elif ext == ".xlsx":
            result = _read_xlsx(file_path)
        elif ext == ".pptx":
            result = _read_pptx(file_path)
        else:
            return err(f"Unsupported office format: {ext}")
        elapsed = time.perf_counter() - start
        logger.info("Extracted %s in %.2fs", file_path.name, elapsed)
        return result
    except Exception as e:
        logger.warning("Office extraction failed for %s: %s", file_path.name, e)
        return err(f"Failed to read {file_path.name}: {e}")
```

Note: the existing inner `try/except` in each `_read_*` helper is no longer needed for top-level error handling since `handle_office` now catches all exceptions. The helpers themselves remain unchanged.

**Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_office_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_office_logs_warning_on_failure -v
```

Expected: PASS

**Step 5: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add src/tools/readers.py tests/test_tools_files.py
git commit -m "feat: add logging to handle_office (issue #114)"
```

---

### Task 5: Final verification and PR

**Step 1: Run complete test suite**

```bash
.venv/bin/python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests pass.

**Step 2: Create feature branch and PR**

```bash
git checkout -b feature/embed-handler-logging
git push -u origin feature/embed-handler-logging
gh pr create --title "feat: add logging for binary embed handlers (closes #114)" --body "$(cat <<'EOF'
## Summary

- Adds `logger = logging.getLogger(__name__)` to `tools/readers.py` and `tools/files.py`
- `handle_audio`, `handle_image`, `handle_office` each log: entry (filename + bytes), duration, success/failure
- `_expand_binary` logs cache hit and cache miss at DEBUG level
- 8 new tests in `TestBinaryHandlerLogging` using `caplog`

## Test plan
- [ ] `test_binary_embed_cache_miss_logged` — DEBUG message on cache miss
- [ ] `test_binary_embed_cache_hit_logged` — DEBUG message on cache hit
- [ ] `test_handle_audio_logs_entry_and_success` — INFO entry + success with duration
- [ ] `test_handle_audio_logs_warning_on_failure` — WARNING on API error
- [ ] `test_handle_image_logs_entry_and_success`
- [ ] `test_handle_image_logs_warning_on_failure`
- [ ] `test_handle_office_logs_entry_and_success`
- [ ] `test_handle_office_logs_warning_on_failure`
- [ ] Full suite passes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
