# Design: Logging for Binary Embed Handlers (Issue #114)

## Summary

Add structured logging to `tools/readers.py` and `tools/files.py` so that binary embed expansion (audio transcription, image description, office extraction, and cache hits/misses) is visible in logs.

## Problem

With embed expansion (#112), `read_file` transparently calls `handle_audio`, `handle_image`, and `handle_office` from `tools/readers.py`. These handlers make external API calls (Fireworks Whisper, vision model) or do CPU-bound extraction, but log nothing. When debugging slow `read_file` calls or unexpected results, there is no visibility into which handlers fired, how long they took, or whether cached results were used.

## Design

### Modules changed

**`tools/readers.py`** — add `import logging`, `import time`, and `logger = logging.getLogger(__name__)`.

Each public handler logs:
- Entry: filename + file size in bytes (from `file_path.stat().st_size`; log 0 on OSError)
- Success: filename + elapsed time in seconds
- Failure: filename + exception (at `warning` level)

**`tools/files.py`** — add `import logging` and `logger = logging.getLogger(__name__)`.

`_expand_binary` logs:
- Cache hit: `debug` level with filename
- Cache miss: `debug` level with filename and which handler type will be called

### Log messages

#### `handle_audio`
```
INFO  Transcribing audio: recording.m4a (2048000 bytes)
INFO  Transcribed recording.m4a in 4.23s
WARNING  Transcription failed for recording.m4a: <error>
```

#### `handle_image`
```
INFO  Describing image: diagram.png (512000 bytes)
INFO  Described diagram.png in 2.11s
WARNING  Image description failed for diagram.png: <error>
```

#### `handle_office`
```
INFO  Extracting office document: report.docx (98304 bytes)
INFO  Extracted report.docx in 0.34s
WARNING  Office extraction failed for report.docx: <error>
```

#### `_expand_binary`
```
DEBUG  Cache hit: recording.m4a
DEBUG  Cache miss: recording.m4a — calling audio handler
```

### Timing

Each handler uses `time.perf_counter()` around the try block. File size fetched with `file_path.stat().st_size` at handler entry; on `OSError`, log size as 0.

### Testing

Tests added to `tests/test_tools_files.py` using pytest `caplog` fixture:
- Cache hit logs `DEBUG` message containing filename
- Cache miss logs `DEBUG` message containing filename and handler type
- Successful handler call logs `INFO` entry and `INFO` success with duration
- Failed handler call logs `WARNING` with filename and error

## Decision Log

- Timing in handlers (not in `_expand_binary`) — handlers own the API calls; timing belongs with the work
- Option C (verbose with file size) chosen — file size helps correlate audio length/image size to API duration
- No new test files — embed expansion tests already live in `test_tools_files.py`
