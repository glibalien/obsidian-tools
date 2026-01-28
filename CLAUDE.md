# CLAUDE.md - Obsidian Tools

## Project Overview

This project provides semantic search and interaction logging for an Obsidian vault. It has two operational modes:

**Development (Claude Code)**: Use Claude Code to develop and maintain the vault tools themselves—adding features, fixing bugs, refactoring code. Claude Code does not interact with vault content directly.

**Vault Interaction (Qwen Agent)**: The Qwen agent (`src/qwen_agent.py`) handles user queries about vault content. It connects to the MCP server, searches the vault, and logs interactions to daily notes.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Qwen Agent    │────▶│   MCP Server    │────▶│  ChromaDB +     │
│ (qwen_agent.py) │     │ (mcp_server.py) │     │  Obsidian Vault │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │
        │                       ├── search_vault (hybrid search)
        │                       ├── read_file (full note content)
        │                       └── log_interaction (daily notes)
        │
        └── Fireworks API (Qwen 3 235B)
```

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
| `log_interaction` | Log interactions to daily note | `task_description`, `query`, `summary`, `files` (optional list), `full_response` (optional string) |

### search_vault

Searches the Obsidian vault using hybrid search (semantic + keyword by default). The `mode` parameter controls the search strategy:
- `"hybrid"` (default): Runs both semantic and keyword search, merges results using Reciprocal Rank Fusion.
- `"semantic"`: Vector similarity search only.
- `"keyword"`: Exact keyword matching only, ranked by number of query terms found.

### read_file

Reads the full content of a vault note. Accepts either a relative path (from vault root) or an absolute path. Security measures:
- Rejects paths that escape the vault (path traversal protection)
- Blocks access to excluded directories (`.obsidian`, `.git`, etc.)

### log_interaction

Logs an interaction to today's daily note. For conversational logs, pass `summary: "n/a"` and provide the `full_response` parameter instead.

## Configuration

All paths are configured via `.env`:
- `VAULT_PATH`: Path to Obsidian vault (default: `~/Documents/archvault2026`)
- `CHROMA_PATH`: Path to ChromaDB database (default: `./.chroma_db` relative to project)
- `FIREWORKS_API_KEY`: API key for Fireworks (used by Qwen agent)

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
