# Unified read_file with Extension-Based Dispatch

**Issue**: #110 (supersedes #108, #109)
**Date**: 2026-02-24

## Problem

Separate MCP tools for different file types (text, audio, images, Office docs) force the LLM to pick the right tool, adding tool selection overhead and room for error. A single `read_file` that auto-detects file type matches Obsidian's mental model where everything is a vault file.

## Design Decisions

- **Single file dispatch**: `read_file('recording.m4a')` processes that one file. No embed scanning — agent drives embed processing explicitly.
- **Vision model**: Fireworks `qwen3-vl-30b-a3b-instruct` via `VISION_MODEL` env var.
- **Office output**: Full text extraction (no pagination). Excel sheets as markdown tables.
- **Handler module**: `src/tools/readers.py` with focused handler functions.

## Architecture

### Router (files.py)

`read_file` gains an extension map. After `resolve_file(path)`, check suffix:
- In map → dispatch to handler
- Not in map → existing text reading with pagination (unchanged)

```python
HANDLER_MAP = {
    '.m4a': handle_audio, '.mp3': handle_audio, '.wav': handle_audio,
    '.ogg': handle_audio, '.webm': handle_audio,
    '.png': handle_image, '.jpg': handle_image, '.jpeg': handle_image,
    '.gif': handle_image, '.webp': handle_image, '.svg': handle_image,
    '.docx': handle_office, '.xlsx': handle_office, '.pptx': handle_office,
}
```

### Handlers (readers.py)

Three public functions, each takes `file_path: Path`, returns `ok()`/`err()` JSON:

1. **`handle_audio(file_path)`** — Extracted from `tools/audio.py`. Fireworks Whisper transcription of a single file. Checks `FIREWORKS_API_KEY`.

2. **`handle_image(file_path)`** — New. Reads image as base64, sends to Fireworks vision model (`qwen3-vl-30b-a3b-instruct`). Returns description text. New config: `VISION_MODEL` env var.

3. **`handle_office(file_path)`** — New. Dispatches by extension:
   - `.docx`: `python-docx` — extract paragraphs, preserve heading styles
   - `.xlsx`: `openpyxl` — each sheet as `## SheetName` heading + markdown table
   - `.pptx`: `python-pptx` — each slide as `## Slide N` heading + extracted text frames

### Retirement

- `transcribe_audio` MCP tool removed from `mcp_server.py`
- `tools/audio.py` deleted (logic moved to `readers.py`)
- System prompt updated
- Compaction stub for `transcribe_audio` removed/updated

### New Dependencies

- `python-docx` (Word)
- `openpyxl` (Excel)
- `python-pptx` (PowerPoint)

### New Config

- `VISION_MODEL` env var (default: `accounts/fireworks/models/qwen3-vl-30b-a3b-instruct`)

## Testing

- Existing `read_file` text tests unchanged
- Router tests: verify correct dispatch by extension
- Audio handler: mock Whisper API
- Image handler: mock vision API
- Office handler: minimal test fixtures (create in-memory or temp files)
- Error cases: missing API key, corrupt files, unsupported extensions
