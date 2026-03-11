# Persistent Embed Cache Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist binary embed processing results to disk so they survive process restarts and work across systems sharing the same vault.

**Architecture:** Add `_cache_read`/`_cache_write` helpers in `files.py` that back the existing in-memory `_embed_cache` with JSON files in `VAULT_PATH/.embed_cache/`. Cache key is SHA-256 of the vault-relative POSIX path; invalidation is mtime-based. Three consumers are wired up: `_expand_binary`, `read_file`, and `transcribe_to_file`.

**Tech Stack:** Python stdlib (`hashlib`, `json`, `tempfile`, `os`), existing project infrastructure

**Spec:** `docs/superpowers/specs/2026-03-10-persistent-embed-cache-design.md`

---

## Chunk 1: Cache helpers and _expand_binary integration

### Task 1: Add `_cache_read` and `_cache_write` helpers

**Files:**
- Modify: `src/tools/files.py:94-99` (cache constants and helpers)
- Modify: `src/config.py:24` (EXCLUDED_DIRS)
- Test: `tests/test_tools_files.py`

#### Step 1: Write tests for cache helpers

- [ ] **Step 1a: Add imports to test file**

Add `_cache_read`, `_cache_write` to the import from `tools.files` at line 14 of `tests/test_tools_files.py`:

```python
from tools.files import (
    _cache_read,
    _cache_write,
    _embed_cache,
    ...
)
```

- [ ] **Step 1b: Write tests for `_cache_write` and `_cache_read`**

Add a new test class in `tests/test_tools_files.py`:

```python
class TestPersistentEmbedCache:
    """Tests for disk-backed embed cache helpers."""

    def test_cache_write_and_read(self, vault_config, tmp_path):
        """Written cache entry is readable."""
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        _cache_write(audio, mtime, "Hello world transcript")
        result = _cache_read(audio)
        assert result == "Hello world transcript"

    def test_cache_read_miss_no_file(self, vault_config):
        """Returns None when no cache file exists."""
        missing = vault_config / "Attachments" / "missing.m4a"
        # File doesn't exist, so _cache_read should return None gracefully
        assert _cache_read(missing) is None

    def test_cache_read_stale_mtime(self, vault_config):
        """Returns None when cached mtime doesn't match current file."""
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        old_mtime = audio.stat().st_mtime

        _cache_write(audio, old_mtime, "Old transcript")

        # Modify the file to change its mtime
        import time
        time.sleep(0.05)
        audio.write_bytes(b"modified")

        assert _cache_read(audio) is None

    def test_cache_read_corrupt_json(self, vault_config):
        """Returns None on corrupt cache file."""
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")

        # Write corrupt data directly to cache location
        cache_dir = vault_config / ".embed_cache"
        cache_dir.mkdir(exist_ok=True)
        import hashlib
        rel = audio.relative_to(vault_config).as_posix()
        key = hashlib.sha256(rel.encode()).hexdigest()
        (cache_dir / f"{key}.json").write_text("not valid json{{{")

        assert _cache_read(audio) is None

    def test_cache_write_creates_directory(self, vault_config):
        """Cache directory is created on first write."""
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        cache_dir = vault_config / ".embed_cache"
        assert not cache_dir.exists()

        _cache_write(audio, mtime, "transcript")
        assert cache_dir.is_dir()

    def test_cache_populates_in_memory(self, vault_config):
        """Cache read populates in-memory _embed_cache."""
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        _cache_write(audio, mtime, "transcript")
        _embed_cache.clear()

        result = _cache_read(audio)
        assert result == "transcript"
        assert (str(audio), mtime) in _embed_cache

    def test_cache_cross_platform_key(self, vault_config):
        """Cache key uses POSIX path for cross-platform consistency."""
        audio = vault_config / "Attachments" / "sub" / "rec.m4a"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        _cache_write(audio, mtime, "transcript")

        # Verify the cache file uses POSIX-normalized path hash
        import hashlib
        rel = audio.relative_to(vault_config).as_posix()
        expected_key = hashlib.sha256(rel.encode()).hexdigest()
        cache_file = vault_config / ".embed_cache" / f"{expected_key}.json"
        assert cache_file.exists()

    def test_error_response_not_cached(self, vault_config, monkeypatch):
        """Handler errors are not written to disk cache."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "bad.m4a"
        audio.write_bytes(b"fake")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            mock_audio.return_value = '{"success": false, "error": "Transcription failed"}'
            _embed_cache.clear()
            _expand_embeds("![[bad.m4a]]", vault_config / "parent.md")

        assert _cache_read(audio) is None
```

- [ ] **Step 1c: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache -v`
Expected: FAIL — `_cache_read` and `_cache_write` do not exist

#### Step 2: Implement cache helpers

- [ ] **Step 2a: Add `.embed_cache` to `EXCLUDED_DIRS` in `config.py` and `vault_config` fixture**

In `src/config.py` line 24, add `.embed_cache` to the set:

```python
EXCLUDED_DIRS = {'.venv', '.chroma_db', '.trash', '.obsidian', '.git', '.embed_cache'}
```

Also update the `vault_config` fixture in `tests/conftest.py` to include `.embed_cache` in the patched `EXCLUDED_DIRS` sets. There are three `monkeypatch.setattr` calls for `EXCLUDED_DIRS` (config, services.vault, tools.links) — add `.embed_cache` to each:

```python
monkeypatch.setattr(config, "EXCLUDED_DIRS", {".git", ".obsidian", ".embed_cache"})
# ... and same for services.vault and tools.links
```

- [ ] **Step 2b: Add `_EMBED_CACHE_DIR` constant and imports to `files.py`**

Add `import os` (after `logging`) and `import tempfile` (after `re`) to the stdlib imports at the top of `src/tools/files.py`. `hashlib` and `json` are already imported. Alphabetical order: `hashlib`, `json`, `logging`, `os`, `re`, `tempfile`.

Add after the `_EMBED_CACHE_MAX = 128` line (line 99):

```python
_EMBED_CACHE_DIR = ".embed_cache"
```

- [ ] **Step 2c: Add `_cache_key` helper**

Add after the new constant:

```python
def _cache_key(file_path: Path) -> str:
    """Compute cache filename from vault-relative POSIX path."""
    rel = file_path.relative_to(config.VAULT_PATH).as_posix()
    return hashlib.sha256(rel.encode()).hexdigest()
```

- [ ] **Step 2d: Add `_cache_read` helper**

```python
def _cache_read(file_path: Path) -> str | None:
    """Read from persistent embed cache.

    Checks disk cache for a valid (mtime-matching) entry. Populates
    in-memory cache on hit.

    Args:
        file_path: Absolute path to the source binary file.

    Returns:
        Cached content string, or None on miss.
    """
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return None

    cache_file = Path(config.VAULT_PATH) / _EMBED_CACHE_DIR / f"{_cache_key(file_path)}.json"
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("mtime") == mtime:
            content = data["content"]
            # Evict oldest in-memory entry if full
            if len(_embed_cache) >= _EMBED_CACHE_MAX:
                oldest = next(iter(_embed_cache))
                del _embed_cache[oldest]
            _embed_cache[(str(file_path), mtime)] = content
            return content
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Disk cache miss for %s: unreadable or stale", file_path.name)

    return None
```

- [ ] **Step 2e: Add `_cache_write` helper**

```python
def _cache_write(file_path: Path, mtime: float, content: str) -> None:
    """Write to persistent embed cache.

    Writes atomically (temp file + rename) and populates in-memory cache.

    Args:
        file_path: Absolute path to the source binary file.
        mtime: Source file's st_mtime at processing time.
        content: Extracted text/transcript/description string.
    """
    cache_dir = Path(config.VAULT_PATH) / _EMBED_CACHE_DIR
    try:
        cache_dir.mkdir(exist_ok=True)
        cache_file = cache_dir / f"{_cache_key(file_path)}.json"
        data = json.dumps({"mtime": mtime, "content": content})
        fd = tempfile.NamedTemporaryFile(
            mode="w", dir=cache_dir, suffix=".tmp", delete=False, encoding="utf-8",
        )
        try:
            fd.write(data)
            fd.close()
            os.replace(fd.name, cache_file)
        except BaseException:
            fd.close()
            try:
                os.unlink(fd.name)
            except OSError:
                pass
            raise
    except OSError:
        logger.warning("Failed to write embed cache for %s", file_path.name)

    # Populate in-memory cache (with eviction)
    if len(_embed_cache) >= _EMBED_CACHE_MAX:
        oldest = next(iter(_embed_cache))
        del _embed_cache[oldest]
    _embed_cache[(str(file_path), mtime)] = content
```

- [ ] **Step 2f: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache -v`
Expected: All PASS

- [ ] **Step 2g: Commit**

```bash
git add src/tools/files.py src/config.py tests/test_tools_files.py
git commit -m "feat: add persistent embed cache helpers (_cache_read/_cache_write)"
```

### Task 2: Wire `_expand_binary` to use persistent cache

**Files:**
- Modify: `src/tools/files.py:213-258` (`_expand_binary`)
- Test: `tests/test_tools_files.py`

#### Step 1: Write tests

- [ ] **Step 1a: Write test for disk cache backing _expand_binary**

Add to `TestPersistentEmbedCache`:

```python
    def test_expand_binary_writes_disk_cache(self, vault_config, monkeypatch):
        """_expand_binary writes result to disk cache on miss."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            mock_audio.return_value = '{"success": true, "transcript": "Hello"}'
            _embed_cache.clear()
            _expand_embeds("![[rec.m4a]]", vault_config / "parent.md")

        # Verify disk cache was written
        result = _cache_read(audio)
        assert result == "Hello"

    def test_expand_binary_reads_disk_cache(self, vault_config, monkeypatch):
        """_expand_binary uses disk cache when in-memory cache is empty."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        # Pre-populate disk cache
        _cache_write(audio, mtime, "Cached transcript")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            _embed_cache.clear()
            result = _expand_embeds("![[rec.m4a]]", vault_config / "parent.md")
            mock_audio.assert_not_called()

        assert "Cached transcript" in result
```

- [ ] **Step 1b: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache::test_expand_binary_writes_disk_cache tests/test_tools_files.py::TestPersistentEmbedCache::test_expand_binary_reads_disk_cache -v`
Expected: FAIL

#### Step 2: Modify `_expand_binary`

- [ ] **Step 2a: Update `_expand_binary` in `files.py`**

The current `_expand_binary` (lines 213-258) checks in-memory cache, then calls handler. Modify to:

1. On in-memory miss, check disk cache via `_cache_read` before calling handler.
2. After handler success, call `_cache_write` in addition to populating in-memory cache.

Replace the function body:

```python
def _expand_binary(file_path: Path, reference: str) -> str:
    """Expand a binary embed (audio/image/office/pdf) with caching."""
    path_str = str(file_path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return f"> [Embed error: {reference} — Cannot stat file]"

    cache_key = (path_str, mtime)
    if cache_key in _embed_cache:
        logger.debug("Cache hit: %s", file_path.name)
        return _format_embed(reference, _embed_cache[cache_key])

    # Check persistent disk cache
    disk_hit = _cache_read(file_path)
    if disk_hit is not None:
        logger.debug("Disk cache hit: %s", file_path.name)
        return _format_embed(reference, disk_hit)

    # Full miss — call handler
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
    elif ext in PDF_EXTENSIONS:
        logger.debug("Cache miss: %s — calling pdf handler", file_path.name)
        raw = handle_pdf(file_path)
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

    _cache_write(file_path, mtime, expanded)
    return _format_embed(reference, expanded)
```

Key changes:
- Added `_cache_read` check between in-memory miss and handler call
- `_cache_read` and `_cache_write` handle in-memory population and eviction internally — no redundant eviction block needed

- [ ] **Step 2b: Run all embed cache tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache tests/test_tools_files.py::TestExpandEmbeds -v`
Expected: All PASS

- [ ] **Step 2c: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: wire _expand_binary to persistent disk cache"
```

## Chunk 2: read_file and transcribe_to_file integration

### Task 3: Wire `read_file` to use persistent cache

**Files:**
- Modify: `src/tools/files.py:377-389` (binary dispatch in read_file)
- Test: `tests/test_tools_files.py`

#### Step 1: Write tests

- [ ] **Step 1a: Write test for read_file using disk cache**

Add to `TestPersistentEmbedCache`:

```python
    def test_read_file_uses_disk_cache(self, vault_config, monkeypatch):
        """read_file returns cached result for audio without calling API."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        _cache_write(audio, mtime, "Cached transcript")
        _embed_cache.clear()

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            result = json.loads(read_file("Attachments/rec.m4a"))
            mock_audio.assert_not_called()

        assert result["success"] is True
        assert result["transcript"] == "Cached transcript"

    def test_read_file_writes_disk_cache(self, vault_config, monkeypatch):
        """read_file populates disk cache after calling handler."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "rec.m4a"
        audio.write_bytes(b"fake")

        from unittest.mock import patch as _patch
        with _patch("tools.readers.OpenAI") as mock_openai_class:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client
            mock_response = MagicMock()
            mock_response.segments = None
            mock_response.text = "Fresh transcript"
            mock_client.audio.transcriptions.create.return_value = mock_response

            _embed_cache.clear()
            result = json.loads(read_file("Attachments/rec.m4a"))

        assert result["success"] is True
        # Verify disk cache was populated
        cached = _cache_read(audio)
        assert cached == "Fresh transcript"

    def test_read_file_image_uses_disk_cache(self, vault_config, monkeypatch):
        """read_file returns cached result for images with description key."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        img = vault_config / "Attachments" / "photo.png"
        img.write_bytes(b"fake png")
        mtime = img.stat().st_mtime

        _cache_write(img, mtime, "A photo of a cat")
        _embed_cache.clear()

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_image") as mock_image:
            result = json.loads(read_file("Attachments/photo.png"))
            mock_image.assert_not_called()

        assert result["success"] is True
        assert result["description"] == "A photo of a cat"

    def test_read_file_error_not_cached(self, vault_config, monkeypatch):
        """read_file does not cache handler error responses."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "bad.m4a"
        audio.write_bytes(b"fake")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            mock_audio.return_value = '{"success": false, "error": "Transcription failed"}'
            _embed_cache.clear()
            result = json.loads(read_file("Attachments/bad.m4a"))

        assert result["success"] is False
        assert _cache_read(audio) is None
```

- [ ] **Step 1b: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache::test_read_file_uses_disk_cache tests/test_tools_files.py::TestPersistentEmbedCache::test_read_file_writes_disk_cache tests/test_tools_files.py::TestPersistentEmbedCache::test_read_file_image_uses_disk_cache tests/test_tools_files.py::TestPersistentEmbedCache::test_read_file_error_not_cached -v`
Expected: FAIL

#### Step 2: Modify `read_file`

- [ ] **Step 2a: Add extension-to-key mapping constant**

Add after `_EMBED_CACHE_DIR` in `files.py`:

```python
# Maps binary extension sets to the JSON response key used by ok()
_BINARY_RESPONSE_KEYS: dict[str, str] = {}
for _ext in AUDIO_EXTENSIONS:
    _BINARY_RESPONSE_KEYS[_ext] = "transcript"
for _ext in IMAGE_EXTENSIONS:
    _BINARY_RESPONSE_KEYS[_ext] = "description"
for _ext in OFFICE_EXTENSIONS | PDF_EXTENSIONS:
    _BINARY_RESPONSE_KEYS[_ext] = "content"
```

- [ ] **Step 2b: Add `_read_binary` helper**

Add a helper that encapsulates the cache-check + handler-call + cache-write + ok-wrapping pattern for `read_file`:

```python
def _read_binary(file_path: Path, ext: str) -> str:
    """Read a binary file with persistent cache support.

    Checks cache first, calls appropriate handler on miss,
    writes result to cache, and returns ok() JSON with the correct key.

    Args:
        file_path: Resolved absolute path to the binary file.
        ext: Lowercase file extension (e.g. ".m4a").

    Returns:
        JSON string via ok() with the appropriate response key.
    """
    # Check persistent cache (in-memory then disk)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = None

    if mtime is not None:
        cache_key = (str(file_path), mtime)
        if cache_key in _embed_cache:
            content = _embed_cache[cache_key]
            return ok(**{_BINARY_RESPONSE_KEYS[ext]: content})

        disk_hit = _cache_read(file_path)
        if disk_hit is not None:
            return ok(**{_BINARY_RESPONSE_KEYS[ext]: disk_hit})

    # Cache miss — call handler
    if ext in AUDIO_EXTENSIONS:
        raw = handle_audio(file_path)
    elif ext in IMAGE_EXTENSIONS:
        raw = handle_image(file_path)
    elif ext in OFFICE_EXTENSIONS:
        raw = handle_office(file_path)
    elif ext in PDF_EXTENSIONS:
        raw = handle_pdf(file_path)
    else:
        return err(f"Unsupported binary type: {ext}")

    # Extract content and cache on success
    result = json.loads(raw)
    if result.get("success") and mtime is not None:
        content = (
            result.get("transcript")
            or result.get("description")
            or result.get("content")
            or ""
        )
        _cache_write(file_path, mtime, content)

    return raw
```

- [ ] **Step 2c: Update `read_file` binary dispatch**

Replace the binary dispatch block in `read_file` (lines 377-389):

```python
    # Extension-based dispatch for non-text files
    ext = file_path.suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return handle_audio(file_path)

    if ext in IMAGE_EXTENSIONS:
        return handle_image(file_path)

    if ext in OFFICE_EXTENSIONS:
        return handle_office(file_path)

    if ext in PDF_EXTENSIONS:
        return handle_pdf(file_path)
```

With:

```python
    # Extension-based dispatch for non-text files (with persistent cache)
    ext = file_path.suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        return _read_binary(file_path, ext)
```

- [ ] **Step 2d: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache tests/test_tools_files.py::TestReadFileAudio tests/test_tools_files.py::TestReadFileAttachmentsFallback -v`
Expected: All PASS

- [ ] **Step 2e: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: wire read_file binary dispatch to persistent cache"
```

### Task 4: Wire `transcribe_to_file` to use persistent cache

**Files:**
- Modify: `src/tools/files.py:424-472` (transcribe_to_file)
- Test: `tests/test_tools_files.py`

#### Step 1: Write tests

- [ ] **Step 1a: Write test for transcribe_to_file using disk cache**

Add to `TestPersistentEmbedCache`:

```python
    def test_transcribe_to_file_uses_disk_cache(self, vault_config, monkeypatch):
        """transcribe_to_file uses cached transcript without calling API."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "meeting.m4a"
        audio.write_bytes(b"fake")
        mtime = audio.stat().st_mtime

        _cache_write(audio, mtime, "Cached diarized transcript")
        _embed_cache.clear()

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_audio") as mock_audio:
            result = json.loads(transcribe_to_file("Attachments/meeting.m4a", "transcript.md"))
            mock_audio.assert_not_called()

        assert result["success"] is True
        output = vault_config / "transcript.md"
        assert output.exists()
        assert output.read_text() == "Cached diarized transcript"

    def test_transcribe_to_file_writes_disk_cache(self, vault_config, monkeypatch):
        """transcribe_to_file populates disk cache after API call."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "meeting.m4a"
        audio.write_bytes(b"fake")

        from unittest.mock import patch as _patch
        with _patch("tools.readers.OpenAI") as mock_openai_class:
            mock_client = MagicMock()
            mock_openai_class.return_value = mock_client
            mock_response = MagicMock()
            mock_response.segments = [
                {"speaker_id": "0", "text": "Hello.", "start": 0.0, "end": 3.0},
            ]
            mock_response.text = "Hello."
            mock_client.audio.transcriptions.create.return_value = mock_response

            _embed_cache.clear()
            result = json.loads(transcribe_to_file("Attachments/meeting.m4a", "transcript.md"))

        assert result["success"] is True
        cached = _cache_read(audio)
        assert cached is not None
        assert "**Speaker 0**" in cached
```

- [ ] **Step 1b: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPersistentEmbedCache::test_transcribe_to_file_uses_disk_cache tests/test_tools_files.py::TestPersistentEmbedCache::test_transcribe_to_file_writes_disk_cache -v`
Expected: FAIL

#### Step 2: Modify `transcribe_to_file`

- [ ] **Step 2a: Update transcribe_to_file to use cache**

Replace the transcription section (lines 459-465) in `transcribe_to_file`:

```python
    # Transcribe
    raw = handle_audio(file_path)
    result = json.loads(raw)
    if not result.get("success"):
        return err(result.get("error", "Transcription failed"))

    transcript = result["transcript"]
```

With:

```python
    # Check cache first, then transcribe on miss
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        mtime = None

    transcript = None
    if mtime is not None:
        cache_key = (str(file_path), mtime)
        if cache_key in _embed_cache:
            transcript = _embed_cache[cache_key]
        else:
            transcript = _cache_read(file_path)

    if transcript is None:
        raw = handle_audio(file_path)
        result = json.loads(raw)
        if not result.get("success"):
            return err(result.get("error", "Transcription failed"))
        transcript = result["transcript"]
        if mtime is not None:
            _cache_write(file_path, mtime, transcript)
```

- [ ] **Step 2b: Run all transcribe_to_file tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestTranscribeToFile tests/test_tools_files.py::TestPersistentEmbedCache -v`
Expected: All PASS

- [ ] **Step 2c: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: wire transcribe_to_file to persistent cache"
```

### Task 5: Final verification

- [ ] **Step 5a: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass, no regressions

- [ ] **Step 5b: Commit any remaining changes**
