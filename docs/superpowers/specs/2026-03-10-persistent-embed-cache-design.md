# Persistent Embed Cache

## Goal

Persist binary embed processing results (audio transcriptions, image descriptions, office/PDF text extractions) to disk so they survive process restarts and work across systems sharing the same vault.

## Problem

Binary file processing (Whisper transcription, vision model, office/PDF extraction) is expensive — a single audio transcription takes 15-30 seconds and costs API credits. Currently:

- `_expand_binary` has an in-memory cache (`_embed_cache`) that is lost on process restart.
- `read_file` dispatching to binary handlers has no caching at all.
- `transcribe_to_file` calls `handle_audio` directly with no caching.

This means the same audio file can be transcribed multiple times per session (read_file + transcribe_to_file) and always re-transcribed across sessions.

## Design

### Cache location

`VAULT_PATH/.embed_cache/` — stored in the vault so it travels with the vault across systems. Obsidian can hide dotfolders via its settings.

### What is cached

The cache stores the **bare content string** — the extracted text, transcript, or description. This is the same string that `_expand_binary` already caches in-memory: the value extracted from the handler's JSON response via `result.get("transcript") or result.get("description") or result.get("content")`.

Consumers that need a different format re-wrap the cached string:
- `_expand_binary`: uses the string directly (wraps in blockquote formatting).
- `read_file`: re-wraps in the appropriate `ok()` envelope using an extension-to-key mapping (`AUDIO_EXTENSIONS` -> `transcript`, `IMAGE_EXTENSIONS` -> `description`, others -> `content`).
- `transcribe_to_file`: uses the string directly (writes to file).

### Cache file format

One JSON file per cached result:

```json
{"mtime": 1710100000.0, "content": "**Speaker 0** (0:00 - 0:45)\nHello..."}
```

- `mtime`: The source file's `st_mtime` at the time the result was generated.
- `content`: The extracted text/transcript/description string.

### Cache key

Filename is the SHA-256 hex digest of the vault-relative source path (e.g. `Attachments/meeting.m4a` -> `a1b2c3...json`). Avoids filesystem issues with special characters, deep paths, and case sensitivity.

The vault-relative path is computed via `file_path.relative_to(VAULT_PATH)` and converted to POSIX (`as_posix()`) before hashing, ensuring cross-platform consistency (Windows backslashes vs. Unix forward slashes produce the same cache key).

### Read path

1. Check in-memory cache (`_embed_cache`) — fast path, unchanged.
2. Check disk cache — if `.embed_cache/<hash>.json` exists and stored `mtime` matches current file `mtime`, load content into memory cache and return.
3. Cache miss — call the appropriate handler, write result to disk cache, populate in-memory cache.

### Write path

After a successful handler call, write the JSON cache file atomically (write to temp, rename). Populate the in-memory cache as before.

### Invalidation

Purely mtime-based. Changed source file -> different mtime -> cache miss -> reprocess -> overwrite cache file. No explicit purge mechanism needed.

### Integration points

Three consumers need cache integration:

1. **`_expand_binary`** (embed expansion) — already has in-memory cache. Add disk cache as a backing layer: check disk on in-memory miss, write to disk on handler call.

2. **`read_file`** (direct binary file reads) — currently no caching. Check cache before dispatching to handler; write to cache after.

3. **`transcribe_to_file`** — currently calls `handle_audio` directly. Check cache first; if hit, write cached transcript to output file without calling the API. Write to cache on miss.

### Shared cache helper

Extract cache read/write into a shared helper used by all three consumers:

```python
def _cache_read(file_path: Path) -> str | None:
    """Read from persistent embed cache. Returns content string or None on miss."""
```

- Stats `file_path` to get current mtime.
- Computes cache key from vault-relative POSIX path.
- If cache file exists and stored mtime matches, populates in-memory `_embed_cache` and returns the content string.
- Returns `None` on any miss (no file, mtime mismatch, corrupt JSON, IO error).

```python
def _cache_write(file_path: Path, mtime: float, content: str) -> None:
    """Write to persistent embed cache."""
```

- Computes cache key from vault-relative POSIX path.
- Creates `.embed_cache/` directory if needed.
- Writes JSON atomically: `tempfile.NamedTemporaryFile(dir=cache_dir, delete=False)` then `os.replace()` (same filesystem guarantees atomic rename).
- Populates in-memory `_embed_cache`.
- Logs warning and returns silently on any IO error.

Callers pass `mtime` explicitly (they already have it from their own stat call) to avoid double-statting.

### Scope

All binary types benefit: audio, image, office, PDF. The cache stores the processed output string, which is the same regardless of which consumer triggered it.

### Error handling

- Disk cache read failures (corrupt JSON, IO errors): log warning, treat as cache miss, proceed normally.
- Disk cache write failures: log warning, continue — the result is still returned to the caller and stored in-memory.
- Missing `.embed_cache/` directory: create on first write.
- **Only cache successful handler results.** Error responses (e.g. `err("Transcription failed")`) must not be written to the cache.

### Concurrency

Concurrent cache misses for the same file are tolerated — both calls process the file and write the same content via atomic rename (last writer wins, no data corruption). Avoiding duplicate API calls would require per-path locking, which is not worth the complexity for this use case.

### Excluded directories

Add `.embed_cache` to `EXCLUDED_DIRS` in `config.py` to prevent any vault scanning from entering the cache directory.

## What this does NOT include

- No cache size limits or eviction (binary processing results are small text files).
- No config env var for the cache path (always `VAULT_PATH/.embed_cache/`).
- No CLI command to clear the cache (users can `rm -rf .embed_cache/` if needed).
