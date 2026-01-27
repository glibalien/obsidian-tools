# CLAUDE.md - Obsidian Tools

## Project Overview

This is a tooling project for semantic search and interaction logging against an Obsidian vault.
The vault location is configured via environment variable (see `.env`).

## Tools

| Script | Purpose | Usage |
|--------|---------|-------|
| `src/search_vault.py` | Semantic search via ChromaDB | `python src/search_vault.py "query"` |
| `src/log_chat.py` | Log interactions to daily note | `python src/log_chat.py "task" "query" "summary" "files" ["full_response"]` |
| `src/index_vault.py` | Update/prune vector index | `python src/index_vault.py` or `--full` for full reindex |

## Usage

```bash
# Semantic search
python src/search_vault.py "query here"

# Log a task completion
python src/log_chat.py "task description" "query" "summary" "file1.md,file2.md"

# Log conversational response (includes full text)
python src/log_chat.py "task description" "query" "n/a" "file1.md,file2.md" "full response text"

# Update index (run hourly or manually)
python src/index_vault.py

# Full reindex
python src/index_vault.py --full
```

## Conventions

- Daily notes are in `Daily Notes/YYYY-MM-DD.md` within the vault
- The vault's "tags" frontmatter field describes content types: task, project, meeting, recipe
- When summarizing search results, cite which files the information came from
- Use `search_vault.py` for searching - don't grep or find by filename

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

## Notes

- The `.venv/` and `.chroma_db/` directories are tooling, not content
- When asked about a topic in the vault, use `search_vault.py` first
- When citing information, reference the source files
