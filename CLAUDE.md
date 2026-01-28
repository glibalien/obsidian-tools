# CLAUDE.md - Obsidian Tools

## Project Overview

This is a tooling project for semantic search and interaction logging against an Obsidian vault.
The vault location is configured via environment variable (see `.env`).

## Tools

These are exposed as MCP tools and should be called directly (not via shell commands).

| MCP Tool | Purpose | Parameters |
|----------|---------|------------|
| `search_vault` | Hybrid search (semantic + keyword) | `query` (string), `n_results` (int, default 5), `mode` (string: "hybrid"\|"semantic"\|"keyword", default "hybrid") |
| `log_interaction` | Log interactions to daily note | `task_description`, `query`, `summary`, `files` (optional list), `full_response` (optional string) |

### search_vault

Searches the Obsidian vault using hybrid search (semantic + keyword by default). The `mode` parameter controls the search strategy:
- `"hybrid"` (default): Runs both semantic and keyword search, merges results using Reciprocal Rank Fusion.
- `"semantic"`: Vector similarity search only (original behavior).
- `"keyword"`: Exact keyword matching only, ranked by number of query terms found.

### log_interaction

Logs a Claude interaction to today's daily note. For conversational logs, pass `summary: "n/a"` and provide the `full_response` parameter instead.

## Conventions

- Daily notes are in `Daily Notes/YYYY-MM-DD.md` within the vault
- The vault's "tags" frontmatter field describes content types: task, project, meeting, recipe, etc.
- When summarizing search results, cite which files the information came from
- Use the `search_vault` MCP tool for searching - don't grep or find by filename

## Configuration

All paths are configured via `.env`:
- `VAULT_PATH`: Path to Obsidian vault (default: `~/Documents/archvault2026`)
- `CHROMA_PATH`: Path to ChromaDB database (default: `./.chroma_db` relative to project)

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

## Interaction Logging

**Every interaction must be logged** to the daily note using the `log_interaction` MCP tool.

- At the end of every conversation turn that completes a user request, call `log_interaction` with a concise `task_description`, the user's `query`, and a `summary` of the outcome.
- **Lengthy responses**: If your response includes substantial text output (e.g. search results, explanations, analysis, multi-paragraph answers), pass `summary: "n/a"` and provide your full conversational output in the `full_response` parameter instead.
- **Short responses**: For brief or action-only responses (e.g. "Done", a one-liner confirmation), use the `summary` field with a concise description of what was done.
- Include relevant `files` when the interaction references specific vault notes or project files.

## Notes

- The `.venv/` and `.chroma_db/` directories are tooling, not content
- When asked about a topic in the vault, use the `search_vault` MCP tool first
- When citing information, reference the source files
