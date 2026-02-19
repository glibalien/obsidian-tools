# CLAUDE.md - Obsidian Tools

## Project Overview

Semantic search and interaction logging for an Obsidian vault. Two operational modes:

- **Development (Claude Code)**: Develop and maintain the vault tools — does not interact with vault content directly.
- **Vault Interaction (Agent)**: The LLM agent (`src/agent.py`) handles user queries via MCP server, searches the vault, logs interactions to daily notes.

## Architecture

![Architecture diagram](obsidian-tools-architecture.svg)

```
src/
├── mcp_server.py        # Entry point - registers tools from submodules
├── services/
│   ├── chroma.py        # Shared ChromaDB connection (lazy singletons, reset() for tests)
│   ├── compaction.py    # Tool message compaction (shared by API + CLI)
│   └── vault.py         # Path resolution, ok()/err() helpers, find_section, file scanning
├── tools/
│   ├── files.py         # read_file, create_file, move_file, append_to_file
│   ├── frontmatter.py   # list_files_by_frontmatter, update_frontmatter, batch ops
│   ├── links.py         # find_backlinks, find_outlinks, search_by_folder
│   ├── preferences.py   # save_preference, list_preferences, remove_preference
│   ├── search.py        # search_vault, web_search
│   ├── sections.py      # prepend_to_file, replace_section, append_to_section
│   ├── utility.py       # log_interaction, get_current_date
│   └── audio.py         # transcribe_audio
├── config.py            # Env config + setup_logging(name)
├── api_server.py        # FastAPI HTTP wrapper with session management
├── agent.py             # CLI chat client
├── hybrid_search.py     # Semantic + keyword search with RRF
├── search_vault.py      # Search interface
├── index_vault.py       # ChromaDB indexing (structure-aware chunking)
└── log_chat.py          # Daily note logging + add_wikilinks

plugin/                  # Obsidian chat sidebar plugin (optional)
install.sh / install.ps1 # Cross-platform installers
services/                # systemd/launchd/taskscheduler templates
```

### Key Components

- **services/vault.py**: `ok()`/`err()` response helpers, `resolve_file()`/`resolve_dir()` path validation, `find_section()` for heading lookup, `get_vault_files()`/`get_vault_note_names()` for scanning, `is_fence_line()` for code fence detection.
- **services/compaction.py**: `compact_tool_messages()` replaces tool results with lightweight stubs between turns. Tool-specific stub builders for search_vault, read_file, list tools, web_search; generic fallback for the rest. Dispatches by tool name resolved from assistant messages.
- **agent.py**: Connects LLM (Fireworks) to MCP server. Loads system prompt from `system_prompt.txt` (falls back to `.example`). Features: agent loop cap (20 iterations), 100K-char tool result truncation with `get_continuation`, compaction between turns, `on_event` callback for SSE streaming, preferences reload per turn, `ensure_interaction_logged` auto-calls `log_interaction` when agent forgets.
- **api_server.py**: FastAPI on 127.0.0.1. File-keyed sessions (LRU eviction, message trimming). CORS enabled. `/chat` and `/chat/stream` share `_prepare_turn`/`_restore_compacted_flags`.
- **hybrid_search.py**: Semantic (ChromaDB) + keyword search merged via RRF. Keyword: single `$or` query, term frequency ranking, `_case_variants()` for case-insensitive matching. Returns `heading` metadata.
- **index_vault.py**: Structure-aware chunking (headings → paragraphs → sentences). Frontmatter indexed as dedicated chunk with wikilink brackets stripped. Batch upserts per file. Incremental indexing uses scan-start time. `--full` for full reindex.
- **log_chat.py**: `add_wikilinks` uses strip-and-restore to protect code blocks, inline code, URLs, existing wikilinks.

## MCP Tools

All tools return JSON via `ok()`/`err()`. List tools support `limit`/`offset` pagination with `total`. Batch tools (`batch_update_frontmatter`, `batch_move_files`) require `confirm=True` when affecting >5 files (`BATCH_CONFIRM_THRESHOLD` in `services/vault.py`); without it they return a preview listing the files.

| MCP Tool | Purpose | Key Parameters |
|----------|---------|----------------|
| `search_vault` | Hybrid search (semantic + keyword) | `query`, `n_results` (5), `mode` ("hybrid"/"semantic"/"keyword"), `chunk_type` ("frontmatter"/"section"/"paragraph"/"sentence"/"fragment") |
| `read_file` | Read vault note with pagination | `path`, `offset` (0), `length` (3500) |
| `list_files_by_frontmatter` | Find files by frontmatter field(s) | `field`, `value`, `match_type` ("contains"/"equals"/"missing"/"exists"/"not_contains"/"not_equals"), `filters` (array of FilterCondition, compound AND), `include_fields` (array of strings), `folder` |
| `update_frontmatter` | Modify note metadata | `path`, `field`, `value`, `operation` ("set"/"remove"/"append") |
| `batch_update_frontmatter` | Bulk frontmatter update | `field`, `value`, `operation`, `paths` OR `target_field`/`target_value`/`target_filters` (query-based) OR `folder`, `confirm` |
| `move_file` | Relocate vault file | `source`, `destination` |
| `batch_move_files` | Move multiple files | `moves` (list of {source, destination}) |
| `create_file` | Create new note | `path`, `content`, `frontmatter` (JSON string) |
| `find_backlinks` | Find files linking to a note | `note_name` (no brackets/extension) |
| `find_outlinks` | Extract wikilinks from file | `path` |
| `search_by_folder` | List folder contents | `folder`, `recursive` (false) |
| `search_by_date_range` | Find files by date | `start_date`, `end_date`, `date_type` ("modified"/"created") |
| `log_interaction` | Log to daily note | `task_description`, `query`, `summary`, `files`, `full_response` |
| `save_preference` / `list_preferences` / `remove_preference` | Manage Preferences.md | `preference` / (none) / `line_number` |
| `get_current_date` | Today's date (YYYY-MM-DD) | (none) |
| `append_to_file` | Append to end of file | `path`, `content` |
| `prepend_to_file` | Insert after frontmatter | `path`, `content` |
| `replace_section` | Replace heading + content | `path`, `heading` (with `#`), `content` |
| `append_to_section` | Append to end of section | `path`, `heading` (with `#`), `content` |
| `web_search` | DuckDuckGo search | `query` |
| `transcribe_audio` | Whisper transcription of audio embeds | `path` (note with `![[audio.m4a]]` embeds) |

### System Prompt

Lives at `system_prompt.txt.example` (copied to `system_prompt.txt` at install). Agent also loads `Preferences.md` as appended "User Preferences" section. When adding new MCP tools, update the tool reference and decision tree sections.

## Testing

```bash
.venv/bin/python -m pytest tests/ -v          # all tests
.venv/bin/python -m pytest tests/test_X.py -v  # specific file
```

**Test files**: `tests/test_*.py` — see `tests/` directory for full list.

**Key fixtures** (`tests/conftest.py`):
- `temp_vault`: Temporary vault directory with sample files
- `vault_config`: Patches `VAULT_PATH` across all modules to use `temp_vault`

**Conventions**:
- All tools return JSON — tests use `json.loads()` and assert on structured fields
- New tool tests go in existing test files (e.g. `test_tools_files.py`), not new files
- Exception: cross-cutting features get their own test file
- **config.py tests**: Must `patch("dotenv.load_dotenv")` before `importlib.reload(config)` to prevent `.env` from overriding monkeypatched env vars

## Configuration

All paths configured via `.env`:

| Variable | Default | Notes |
|----------|---------|-------|
| `VAULT_PATH` | `~/Documents/obsidian-vault` | Path to Obsidian vault |
| `CHROMA_PATH` | `./.chroma_db` | ChromaDB database path |
| `FIREWORKS_API_KEY` | — | Required for LLM agent |
| `FIREWORKS_MODEL` | `accounts/fireworks/models/gpt-oss-120b` | Falls back to `LLM_MODEL` |
| `API_PORT` | `8000` | HTTP API port |
| `INDEX_INTERVAL` | `60` | Indexer interval (minutes) |
| `LOG_DIR` | `VAULT_PATH/logs/` | Log file directory |
| `LOG_MAX_BYTES` | `5242880` (5MB) | Max log file size before rotation |
| `LOG_BACKUP_COUNT` | `3` | Rotated log files to keep |
| `MAX_SESSIONS` | `20` | Max concurrent API sessions (LRU eviction) |
| `MAX_SESSION_MESSAGES` | `50` | Max messages per session (trimming) |
| `WHISPER_MODEL` | `whisper-v3` | Audio transcription model |

`config.py` also provides: `setup_logging(name)` (rotating file handler + stderr), `EXCLUDED_DIRS`, `PREFERENCES_FILE`, `ATTACHMENTS_DIR`.

`agent.py` constants: `MAX_TOOL_RESULT_CHARS` (100,000), `SYSTEM_PROMPT_FILE`, `SYSTEM_PROMPT_EXAMPLE`.

## HTTP API

API server binds to `127.0.0.1:API_PORT`. Two endpoints:

- **POST /chat**: Send message, receive response.
- **POST /chat/stream**: Same request, returns SSE events.

Sessions keyed by `active_file` (not UUID). See `api_server.py` for session management, compaction, and message trimming details.

## Obsidian Plugin

Optional chat sidebar in `plugin/`. Build: `cd plugin && npm install && npm run build`. Install by copying `manifest.json`, `main.js`, `styles.css` to `.obsidian/plugins/vault-chat/`.

Key details: Uses `MarkdownRenderer.render()` with `sourcePath` captured at request time. Uses browser `fetch` + `ReadableStream` for SSE (not Obsidian's `requestUrl`). `plugin/main.js` is gitignored.

## Installation

```bash
./install.sh          # macOS / Linux
.\install.ps1         # Windows
```

## Development Workflow

1. **Plan**: Enter planning mode → identify affected files → create GitHub issue with description, approach, success criteria, test cases
2. **Implement**: Feature branch (`git checkout -b feature/description`) → implement → validate against criteria → self-review
3. **Merge**: Only after all criteria met. Never commit directly to master for non-trivial changes.

## Coding Standards

- **No god functions**: Break into focused functions (~50 lines max)
- **DRY**: Extract repeated logic into helpers
- **Single responsibility**: Each function does one thing
- **Type hints** on function signatures
- **Docstrings** for non-obvious functions
- **Imports**: stdlib → third-party → local (blank line separated)
- **Error handling**: Fail gracefully with useful messages, don't swallow exceptions

## Notes

- Daily notes: `Daily Notes/YYYY-MM-DD.md`
