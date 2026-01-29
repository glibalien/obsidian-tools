# CLAUDE.md - Obsidian Tools

## Project Overview

This project provides semantic search and interaction logging for an Obsidian vault. It has two operational modes:

**Development (Claude Code)**: Use Claude Code to develop and maintain the vault tools themselves—adding features, fixing bugs, refactoring code. Claude Code does not interact with vault content directly.

**Vault Interaction (Qwen Agent)**: The Qwen agent (`src/qwen_agent.py`) handles user queries about vault content. It connects to the MCP server, searches the vault, and logs interactions to daily notes.

## Architecture

```
                        ┌─────────────────┐
                        │   HTTP Client   │
                        │  (curl, apps)   │
                        └────────┬────────┘
                                 │ POST /chat
                                 ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Qwen Agent    │────▶│   MCP Server    │────▶│  ChromaDB +     │
│ (qwen_agent.py) │     │ (mcp_server.py) │     │  Obsidian Vault │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        ▲                       │
        │                       ├── search_vault (hybrid search)
┌───────┴───────┐               ├── read_file (full note content)
│  API Server   │               ├── list_files_by_frontmatter (metadata queries)
│(api_server.py)│               ├── update_frontmatter (modify metadata)
└───────────────┘               ├── batch_update_frontmatter (bulk metadata)
                                ├── move_file (relocate files)
                                ├── batch_move_files (bulk relocate)
                                ├── create_file (new notes)
                                ├── find_backlinks (wikilink discovery)
                                ├── search_by_date_range (date filtering)
                                ├── find_outlinks (extract wikilinks)
                                ├── search_by_folder (list folder contents)
                                ├── log_interaction (daily notes)
                                ├── save_preference (store user prefs)
                                ├── list_preferences (view prefs)
                                └── remove_preference (delete pref)
```

- **api_server.py**: FastAPI HTTP wrapper exposing the agent via REST API
- **qwen_agent.py**: CLI chat client that connects Qwen (via Fireworks) to the MCP server
- **mcp_server.py**: FastMCP server exposing vault tools
- **hybrid_search.py**: Combines semantic (ChromaDB) and keyword search with RRF ranking
- **index_vault.py**: Indexes vault content into ChromaDB (runs via systemd, not manually)
- **log_chat.py**: Appends interaction logs to daily notes

## MCP Tools

These tools are exposed by the MCP server. Documentation here is for development reference.

| MCP Tool | Purpose | Parameters |
|----------|---------|------------|
| `search_vault` | Hybrid search (semantic + keyword) | `query` (string), `n_results` (int, default 5), `mode` (string: "hybrid"\|"semantic"\|"keyword", default "hybrid") |
| `read_file` | Read full content of a vault note | `path` (string: relative to vault or absolute) |
| `list_files_by_frontmatter` | Find files by frontmatter criteria | `field` (string), `value` (string), `match_type` (string: "contains"\|"equals", default "contains") |
| `update_frontmatter` | Modify frontmatter on a vault file | `path` (string), `field` (string), `value` (string, optional), `operation` (string: "set"\|"remove"\|"append", default "set") |
| `batch_update_frontmatter` | Apply frontmatter update to multiple files | `paths` (list[str]), `field` (string), `value` (string, optional), `operation` (string: "set"\|"remove"\|"append", default "set") |
| `move_file` | Relocate a file within the vault | `source` (string), `destination` (string) |
| `batch_move_files` | Move multiple files to new locations | `moves` (list[dict] with "source" and "destination" keys) |
| `create_file` | Create a new markdown note | `path` (string), `content` (string, default ""), `frontmatter` (JSON string, optional) |
| `find_backlinks` | Find files linking to a note | `note_name` (string: note name without brackets or .md) |
| `search_by_date_range` | Find files by date range | `start_date` (YYYY-MM-DD), `end_date` (YYYY-MM-DD), `date_type` ("created"\|"modified", default "modified") |
| `find_outlinks` | Extract wikilinks from a file | `path` (string: relative to vault or absolute) |
| `search_by_folder` | List files in a folder | `folder` (string), `recursive` (bool, default false) |
| `log_interaction` | Log interactions to daily note | `task_description`, `query`, `summary`, `files` (optional list), `full_response` (optional string) |
| `save_preference` | Save a user preference | `preference` (string) |
| `list_preferences` | List all saved preferences | (none) |
| `remove_preference` | Remove a preference by line number | `line_number` (int, 1-indexed) |

### search_vault

Searches the Obsidian vault using hybrid search (semantic + keyword by default). The `mode` parameter controls the search strategy:
- `"hybrid"` (default): Runs both semantic and keyword search, merges results using Reciprocal Rank Fusion.
- `"semantic"`: Vector similarity search only.
- `"keyword"`: Exact keyword matching only, ranked by number of query terms found.

### read_file

Reads the full content of a vault note. Accepts either a relative path (from vault root) or an absolute path. Security measures:
- Rejects paths that escape the vault (path traversal protection)
- Blocks access to excluded directories (`.obsidian`, `.git`, etc.)

### list_files_by_frontmatter

Finds vault files matching frontmatter criteria. Useful for queries like "find all meeting notes" or "find files tagged as person".
- `field`: The frontmatter field to check (e.g., `tags`, `company`, `project`)
- `value`: The value to match
- `match_type`: `"contains"` checks if value is in a list or substring of a string; `"equals"` requires exact match

### update_frontmatter

Updates frontmatter on a vault file, preserving body content.
- `operation`: `"set"` to add/modify a field, `"remove"` to delete, `"append"` to add to a list
- `value`: For complex values (lists), use JSON: `'["tag1", "tag2"]'`
- Append creates the list if field doesn't exist, and skips duplicates

### batch_update_frontmatter

Applies the same frontmatter update to multiple files. Useful for bulk operations like archiving projects or adding tags to a group of files.
- Same field/value/operation semantics as `update_frontmatter`
- Continues processing after individual failures
- Returns summary showing successes and failures

### move_file

Moves a vault file to a different location within the vault.
- Creates target directory if it doesn't exist
- Prevents moves outside the vault (both paths validated)
- Prevents overwriting existing files

### batch_move_files

Moves multiple files to new locations in a single operation.
- `moves`: List of objects like `{"source": "old/path.md", "destination": "new/path.md"}`
- Creates destination directories if needed
- Continues processing after individual failures
- Returns summary showing successes and failures

### create_file

Creates a new markdown note in the vault.
- `frontmatter`: Pass as JSON string (e.g., `'{"tags": ["meeting"]}'`), auto-converted to YAML
- Creates parent directories if needed
- Prevents overwriting existing files

### find_backlinks

Finds all vault files containing wikilinks to a given note name.
- `note_name`: The note name to search for (without `[[]]` brackets or `.md` extension)
- Matches both `[[note_name]]` and `[[note_name|alias]]` patterns
- Case-insensitive matching (matches Obsidian behavior)
- Returns sorted list of relative file paths

### search_by_date_range

Finds vault files within a specified date range.
- `start_date`, `end_date`: Date range (inclusive), format YYYY-MM-DD
- `date_type`: `"created"` uses frontmatter `Date` field (falls back to filesystem creation time), `"modified"` uses filesystem mtime
- Handles wikilink date format in frontmatter (`[[2023-08-11]]`)
- Returns sorted list of relative file paths

### find_outlinks

Extracts all wikilinks from a given vault file.
- `path`: Path to the note to analyze
- Returns deduplicated, sorted list of linked note names
- Handles aliased links: `[[note|alias]]` returns just "note"

### search_by_folder

Lists all markdown files in a vault folder.
- `folder`: Path to the folder to list
- `recursive`: If `true`, include files in subfolders (default: `false`)
- Returns sorted list of relative file paths

### log_interaction

Logs an interaction to today's daily note. For conversational logs, pass `summary: "n/a"` and provide the `full_response` parameter instead.

### save_preference

Saves a user preference to `Preferences.md` at the vault root. Preferences are stored as bullet points.
- Creates the file if it doesn't exist
- Use for user corrections, preferences, or instructions the agent should remember

### list_preferences

Returns all saved preferences with line numbers (1-indexed). Use this to show users what preferences are saved.

### remove_preference

Removes a preference by its line number.
- `line_number`: 1-indexed line number from `list_preferences` output
- Returns error if line number is out of range

**Note**: The Qwen agent automatically loads `Preferences.md` into its system prompt at startup. Preferences are appended as a "User Preferences" section that the agent follows.

## Configuration

All paths are configured via `.env`:
- `VAULT_PATH`: Path to Obsidian vault (default: `~/Documents/archvault2026`)
- `CHROMA_PATH`: Path to ChromaDB database (default: `./.chroma_db` relative to project)
- `FIREWORKS_API_KEY`: API key for Fireworks (used by Qwen agent)

## HTTP API

The API server (`src/api_server.py`) provides HTTP access to the Qwen agent. It binds to `127.0.0.1:8000` only (localhost) for security.

### Running the Server

```bash
python src/api_server.py
```

### POST /chat

Send a message and receive the agent's response.

**Request:**
```json
{
  "message": "Find notes about projects",
  "session_id": "optional-uuid"
}
```

**Response:**
```json
{
  "response": "I found 3 notes about projects...",
  "session_id": "abc123-uuid"
}
```

**Behavior:**
- Omit `session_id` to start a new conversation (returns a new UUID)
- Include `session_id` to continue an existing conversation
- Invalid `session_id` returns HTTP 404

**Session Management:**
- Sessions are stored in-memory (lost on server restart)
- Each session maintains full conversation history
- The MCP connection is shared across all sessions

---

## Development Workflow

When adding features or making non-trivial changes, follow this process:

### 1. Planning Phase

Before writing code, enter planning mode:
- Describe the feature requirements and constraints
- Identify affected files and potential side effects
- Create a GitHub issue with:
  - Clear description of the feature
  - Implementation approach
  - Success criteria (specific, testable)
  - Testing/validation steps

Example issue template:
```markdown
## Description
[What and why]

## Implementation Approach
[How - files to modify, new functions, etc.]

## Success Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Testing
- [ ] Test case 1
- [ ] Test case 2
```

### 2. Implementation Phase

```bash
# Create feature branch
git checkout -b feature/description

# Do the work...

# Validate against success criteria
# Run tests if applicable

# Self-review before marking complete
```

**Before considering implementation complete, verify:**
- [ ] Meets all success criteria from the issue
- [ ] Separation of concerns (no god functions)
- [ ] DRY - no duplicated logic
- [ ] Clean, idiomatic Python
- [ ] Functions are focused and under ~50 lines
- [ ] Error handling is appropriate
- [ ] Logging is useful but not excessive

If criteria are not met, continue iterating in the feature branch.

### 3. Merge Phase

Only after all criteria are met:

```bash
git checkout main
git merge feature/description
git push
# Close the GitHub issue
```

**Never commit directly to main for non-trivial changes.**

---

## Coding Standards

### Structure
- **No god functions**: Break large functions into smaller, focused ones
- **DRY**: Extract repeated logic into helper functions
- **Single responsibility**: Each function does one thing well
- **Max function length**: ~50 lines (guideline, not hard rule)

### Style
- **Clear naming**: Functions and variables should be self-documenting
- **Type hints**: Use them for function signatures
- **Docstrings**: Required for any function that isn't immediately obvious
- **Imports**: Standard library → third-party → local (separated by blank lines)

### Error Handling
- Fail gracefully with useful error messages
- Don't swallow exceptions silently
- Log errors appropriately

### Example

```python
# Good
def get_vault_notes(vault_path: Path, excluded_dirs: set[str]) -> set[str]:
    """Return set of note names (without .md extension) from vault."""
    notes = set()
    for md_file in vault_path.rglob("*.md"):
        if any(excluded in md_file.parts for excluded in excluded_dirs):
            continue
        notes.add(md_file.stem)
    return notes

# Bad
def process(p):  # unclear name, no types, no docstring
    n = set()
    for f in p.rglob("*.md"):
        if '.venv' in str(f) or '.chroma_db' in str(f) or '.trash' in str(f):  # duplicated logic
            continue
        n.add(f.stem)
    return n
```

---

## Notes

- The `.venv/` and `.chroma_db/` directories are tooling, not content
- Daily notes are in `Daily Notes/YYYY-MM-DD.md` within the vault
- The vault's "tags" frontmatter field describes content types: task, project, meeting, recipe, etc.
