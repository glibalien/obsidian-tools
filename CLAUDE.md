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
│   ├── chroma.py        # Shared ChromaDB connection (thread-safe singletons, purge_database, telemetry fix)
│   ├── compaction.py    # Tool message compaction (shared by API + CLI)
│   └── vault.py         # Path resolution, ok()/err() helpers, find_section, file scanning
├── tools/
│   ├── files.py         # read_file, create_file, move_file, merge_files, batch_merge_files
│   ├── frontmatter.py   # update_frontmatter, batch ops, FilterCondition, internal helpers
│   ├── links.py         # find_links, compare_folders
│   ├── preferences.py   # manage_preferences (list/add/remove)
│   ├── search.py        # find_notes, web_search
│   ├── editing.py       # edit_file
│   ├── utility.py       # log_interaction
│   └── readers.py       # File type handlers (audio, image, office) for read_file dispatch
├── config.py            # Env config + setup_logging(name)
├── api_server.py        # FastAPI HTTP wrapper with session management
├── agent.py             # CLI chat client
├── chunking.py          # Structure-aware markdown chunking (headings, paragraphs, sentences)
├── hybrid_search.py     # Semantic + keyword search with RRF
├── search_vault.py      # Search interface
├── index_vault.py       # ChromaDB indexing orchestration (incremental/full, manifest, pruning)
└── log_chat.py          # Daily note logging + add_wikilinks

plugin/                  # Obsidian chat sidebar plugin (optional)
install.sh / install.ps1 # Cross-platform installers
services/                # systemd/launchd/taskscheduler templates
```

### Key Components

- **services/vault.py**: `ok()`/`err()` response helpers, `resolve_file()`/`resolve_dir()` path validation, `find_section()` for heading lookup, `get_vault_files()`/`get_vault_note_names()` for scanning, `is_fence_line()` for code fence detection.
- **services/compaction.py**: `compact_tool_messages()` replaces tool results with lightweight stubs between turns. Tool-specific stub builders for find_notes (detects semantic vs vault-scan result shape), read_file, find_links, web_search; generic fallback for the rest. Dispatches by tool name resolved from assistant messages.
- **agent.py**: Connects LLM (Fireworks) to MCP server. Loads system prompt from `system_prompt.txt` (falls back to `.example`). Features: agent loop cap (20 iterations), 100K-char tool result truncation with `get_continuation`, compaction between turns, `on_event` callback for SSE streaming, preferences reload per turn, `ensure_interaction_logged` auto-calls `log_interaction` when agent forgets, `force_text_only` code-level enforcement (strips tool calls if model ignores `tool_choice="none"`, capped at 3 retries with preview message fallback). Confirmation preview SSE events are emitted after the response event to guarantee correct rendering order in the plugin.
- **api_server.py**: FastAPI on 127.0.0.1. File-keyed sessions (LRU eviction, message trimming). CORS enabled. `/chat` and `/chat/stream` share `_prepare_turn`/`_restore_compacted_flags`.
- **hybrid_search.py**: Semantic (ChromaDB) + keyword search merged via RRF. Keyword: single `$or` query, term frequency ranking, `_case_variants()` for case-insensitive matching. Returns `heading` metadata.
- **chunking.py**: Structure-aware chunking (headings → paragraphs → sentences). Frontmatter indexed as dedicated chunk with wikilink brackets stripped. `chunk_markdown` is the main entry point; `_parse_frontmatter`/`_strip_frontmatter` also live here.
- **index_vault.py**: Indexing orchestration. Batch upserts per file. Incremental indexing uses scan-start time. `--full` for full reindex; `--reset` deletes the database and rebuilds from scratch (needed when HNSW index is corrupt or cross-platform-incompatible). Parallel file reading/chunking via `ThreadPoolExecutor` (`INDEX_WORKERS`) with `_prepare_file_chunks` (pure Python, thread-safe); all ChromaDB operations run on the main thread (ChromaDB is not thread-safe). Failures skip `mark_run` so next run retries; `FileNotFoundError` removes source from `valid_sources` so pruning cleans up.
- **services/chroma.py**: Lazy singletons with `threading.RLock()` for thread-safe init. `purge_database()` for full DB wipe. Monkey-patches ChromaDB's Posthog `capture()` to no-op (thread-unsafe race in `batched_events` dict). `reset()` for tests.
- **log_chat.py**: `add_wikilinks` uses strip-and-restore to protect code blocks, inline code, URLs, existing wikilinks.

## MCP Tools

All tools return JSON via `ok()`/`err()`. List tools support `limit`/`offset` pagination with `total` (default 500, max 2000 — constants `LIST_DEFAULT_LIMIT`/`LIST_MAX_LIMIT` in config.py). Batch tools (`batch_update_frontmatter`, `batch_move_files`, `batch_merge_files`) require a two-step confirmation flow when affecting >5 files (`BATCH_CONFIRM_THRESHOLD`): the first call always returns a preview (server-side gate via `store_preview`/`consume_preview` in `vault.py`), and `agent_turn` breaks after `confirmation_required` results so the user sees the preview before the agent can confirm (`force_text_only` / `tool_choice="none"`). Note: `tool_choice="none"` is a hint that Fireworks may ignore — `agent_turn` enforces it in code by stripping tool calls when `force_text_only` is active (retries if the stripped response has no text content).

| MCP Tool | Purpose | Key Parameters |
|----------|---------|----------------|
| `find_notes` | Unified discovery (search + filter + date) | `query`, `mode` ("hybrid"/"semantic"/"keyword"), `folder`, `recursive` (false), `frontmatter` (array of FilterCondition, AND), `date_start`/`date_end` (YYYY-MM-DD), `date_type` ("modified"/"created"), `sort` ("relevance"/"name"/"modified"/"created"), `include_fields`, `n_results` (20), `offset` |
| `read_file` | Read any vault file (text, audio, image, Office) | `path`, `offset` (0), `length` (30000). Auto-dispatches by extension: audio→Whisper, image→vision model, .docx/.xlsx/.pptx→text extraction. Markdown files auto-expand `![[...]]` embeds inline (1 level deep, binary results cached by mtime). |
| `update_frontmatter` | Modify note metadata | `path`, `field`, `value` (str\|list), `operation` ("set"/"remove"/"append"/"rename") |
| `batch_update_frontmatter` | Bulk frontmatter update | `field`, `value`, `operation` ("set"/"remove"/"append"/"rename"), `paths` OR `target_field`/`target_value`/`target_filters` (query-based) OR `folder`, `confirm` |
| `move_file` | Relocate vault file | `source`, `destination` |
| `batch_move_files` | Move multiple files | `moves` (list of {source, destination}) |
| `merge_files` | Merge source into destination | `source`, `destination`, `strategy` ("smart"/"concat"), `delete_source` (bool) |
| `batch_merge_files` | Batch merge duplicates across folders | `source_folder`, `destination_folder`, `recursive`, `strategy`, `delete_source`, `confirm` |
| `create_file` | Create new note | `path`, `content`, `frontmatter` (JSON string) |
| `find_links` | Find links to/from a vault note | `path`, `direction` ("backlinks"/"outlinks"/"both"), `limit`, `offset` |
| `compare_folders` | Compare two folders by filename stem | `source`, `target`, `recursive` (false) |
| `log_interaction` | Log to daily note | `task_description`, `query`, `summary`, `files`, `full_response` |
| `manage_preferences` | List/add/remove preferences | `operation` ("list"/"add"/"remove"), `preference`, `line_number` |
| `edit_file` | Edit file content (prepend/append/section) | `path`, `content`, `position` ("prepend"/"append"/"section"), `heading` (for section), `mode` ("replace"/"append" for section) |
| `web_search` | DuckDuckGo search | `query` |

### Tool Parameter Types and LLM Efficiency

MCP tool parameter types must match what the LLM naturally produces. If a parameter is typed `str` but the LLM sends a native JSON array, MCP validation rejects it, causing corrective retries and wasted LLM calls. For frontmatter `value` params, `str | list` allows the model to send arrays directly for list-type fields (category, tags, aliases) without stringifying.

The system prompt includes efficiency guidance (batch independent calls, trust successful results) to reduce unnecessary LLM round-trips. The `_simplify_schema` function in agent.py inlines `$ref` and simplifies `anyOf[T, null]` patterns for weaker models; multi-type unions like `str | list | None` are left as full `anyOf`.

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
| `INDEX_WORKERS` | `4` | Thread pool size for file indexing |
| `LOG_DIR` | `VAULT_PATH/logs/` | Log file directory |
| `LOG_MAX_BYTES` | `5242880` (5MB) | Max log file size before rotation |
| `LOG_BACKUP_COUNT` | `3` | Rotated log files to keep |
| `MAX_SESSIONS` | `20` | Max concurrent API sessions (LRU eviction) |
| `MAX_SESSION_MESSAGES` | `50` | Max messages per session (trimming) |
| `WHISPER_MODEL` | `whisper-v3` | Audio transcription model |
| `VISION_MODEL` | `accounts/fireworks/models/qwen3-vl-30b-a3b-instruct` | Image description model |

`config.py` also provides: `setup_logging(name)` (rotating file handler + stderr), `EXCLUDED_DIRS`, `PREFERENCES_FILE`, `ATTACHMENTS_DIR`. Entry points that call `setup_logging`: `api_server.py` ("api"), `agent.py` ("agent"), `mcp_server.py` ("mcp"), `index_vault.py` ("index_vault"). Note: the MCP server runs as a **subprocess** of the API server, so it needs its own `setup_logging` call — logs from tool handlers (e.g. `readers.py`) only appear in `mcp.log.md`, not `api.log.md`.

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

- **Logging**: `import logging` + `logger = logging.getLogger(__name__)` immediately after imports (before constants). Use `%s` lazy formatting, not f-strings. Levels: DEBUG for high-frequency internal state, INFO for external calls/lifecycle, WARNING for failures. New `__main__` entry points must call `setup_logging(name)` to get file output.
- **No god functions**: Break into focused functions (~50 lines max)
- **DRY**: Extract repeated logic into helpers
- **Single responsibility**: Each function does one thing
- **Type hints** on function signatures
- **Docstrings** for non-obvious functions
- **Imports**: stdlib → third-party → local (blank line separated)
- **Error handling**: Fail gracefully with useful messages, don't swallow exceptions

## Notes

- Daily notes: `Daily Notes/YYYY-MM-DD.md`
