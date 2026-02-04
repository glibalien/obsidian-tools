# Codebase Review: obsidian-tools MCP Server

**Date:** 2026-02-04
**Scope:** Architecture, consistency, maintainability, and testability assessment

---

## 1. Consistency Across Tool Handlers

**Severity: Worth Improving**

### Findings

The 21 tools in `mcp_server.py` follow two different return format patterns:

| Pattern | Tools | Format |
|---------|-------|--------|
| Plain strings | 18 tools | `"Error: {message}"` or plain text |
| JSON | 3 tools | `{"success": bool, "error"?: str, "path"?: str}` |

**JSON tools** (lines 853-1135):
- `prepend_to_file`
- `replace_section`
- `append_to_section`

**Plain string tools** (all others):
- `search_vault`, `read_file`, `list_files_by_frontmatter`, `update_frontmatter`, etc.

### Inconsistencies Identified

1. **Error prefix varies** (`mcp_server.py`):
   - Lines 366-378: `read_file` returns `"Error: {e}"`
   - Lines 461-462: `update_frontmatter` returns `"Error: {message}"` (via helper)
   - Lines 867-879: `prepend_to_file` returns `json.dumps({"success": False, "error": ...})`

2. **Empty result signaling differs**:
   - Line 318: `search_vault` returns `"No results found."` — valid or failure?
   - Line 1157: `web_search` returns `"No results found."` — same ambiguity
   - Line 424: `list_files_by_frontmatter` returns `"No files found where..."` — clear context

3. **Input validation placement**:
   - `search_vault` (line 301): No explicit mode validation — relies on downstream
   - `list_files_by_frontmatter` (lines 397-398): Validates `match_type` upfront
   - `search_by_date_range` (lines 686-700): Comprehensive upfront validation

### Recommendations

1. **Standardize on JSON responses** for all tools that perform actions (create/update/delete). This prevents agent confabulation on partial failures.

2. **Distinguish "not found" from "error"**:
   ```python
   # Instead of:
   return "No results found."

   # Use:
   return json.dumps({"success": True, "results": [], "message": "No matching documents"})
   ```

3. **Extract validation to a consistent pattern** — validate all inputs before any side effects.

---

## 2. Error Handling

**Severity: Needs Attention**

### Findings

Several tools can return ambiguous results that don't clearly signal error states to the agent.

### Problem Cases

| Tool | Line | Issue |
|------|------|-------|
| `read_file` | 376 | Returns file content as string — if content starts with "Error:", agent can't distinguish |
| `search_vault` | 318 | `"No results found."` could be valid empty result or ChromaDB failure |
| `web_search` | 1157 | Same ambiguity as `search_vault` |
| `find_outlinks` | 768 | `"No outlinks found in {path}"` — is the file empty or did parsing fail? |
| `keyword_search` | 76-77 | Silently `continue` on exception — errors are swallowed |

### Strengths

- JSON-returning tools (`prepend_to_file`, `replace_section`, `append_to_section`) have explicit `success` flags
- Batch operations (`batch_update_frontmatter`, `batch_move_files`) track individual failures
- `_do_update_frontmatter` and `_do_move_file` return `(success, message)` tuples — good pattern

### Recommendations

1. **Wrap all tool responses in a result envelope**:
   ```python
   def ok(data: str | dict) -> str:
       return json.dumps({"success": True, "data": data})

   def err(message: str) -> str:
       return json.dumps({"success": False, "error": message})
   ```

2. **Fix silent failures** in `hybrid_search.py:76-77`:
   ```python
   # Currently:
   except Exception:
       continue

   # Should at least log:
   except Exception as e:
       logger.warning(f"Keyword search term '{term}' failed: {e}")
       continue
   ```

3. **Distinguish "empty" from "error"** — an empty search result is success with zero matches.

---

## 3. Code Duplication

**Severity: Worth Improving**

### Repeated Patterns

#### 3.1 Path Resolution Boilerplate
Appears in 12 tools (~10 lines each):

```python
# mcp_server.py lines 364-378, 747-761, 831-850, 865-879, etc.
try:
    file_path = _resolve_vault_path(path)
except ValueError as e:
    return f"Error: {e}"

if not file_path.exists():
    return f"Error: File not found: {path}"

if not file_path.is_file():
    return f"Error: Not a file: {path}"
```

**Recommendation:** Extract to a helper that returns a result tuple or raises:
```python
def resolve_file(path: str) -> tuple[Path | None, str | None]:
    """Returns (resolved_path, None) on success, (None, error_message) on failure."""
```

#### 3.2 Section Finding Logic
Nearly identical code in two places:

- `replace_section`: lines 953-1007
- `append_to_section`: lines 1067-1122

Both implement:
- Code fence tracking (`in_code_block` toggle)
- Heading pattern matching
- Section boundary detection

**Recommendation:** Extract to `_find_section(lines, heading) -> tuple[int, int, str | None]` returning `(start, end, error)`.

#### 3.3 Vault File Scanning
Three similar implementations:

| Location | Function | Returns |
|----------|----------|---------|
| `mcp_server.py:56-63` | `_get_vault_files()` | `list[Path]` |
| `index_vault.py:104-111` | `get_vault_files()` | `list[Path]` |
| `log_chat.py:12-19` | `get_vault_note_names()` | `set[str]` |

**Recommendation:** Consolidate into a shared utility in `config.py` or new `vault_utils.py`.

#### 3.4 Relative Path Calculation
Pattern `vault_resolved = VAULT_PATH.resolve()` followed by `file_path.relative_to(vault_resolved)` appears 15+ times.

**Recommendation:** Add `get_relative_path(absolute: Path) -> str` helper.

### Estimated Savings

| Extraction | Current Lines | After | Net Reduction |
|------------|---------------|-------|---------------|
| Path resolution helper | ~120 | ~30 | ~90 lines |
| Section finding | ~110 | ~60 | ~50 lines |
| Vault scanning | ~30 | ~12 | ~18 lines |
| **Total** | ~260 | ~102 | **~158 lines** |

---

## 4. Architecture and Separation of Concerns

**Severity: Worth Improving**

### Current Structure

```
src/
├── mcp_server.py      # 1270 lines - MCP tools + business logic
├── hybrid_search.py   # 159 lines - search algorithms
├── search_vault.py    # 58 lines - search interface
├── index_vault.py     # 169 lines - ChromaDB indexing
├── log_chat.py        # 154 lines - daily note logging
├── api_server.py      # 136 lines - HTTP wrapper
├── qwen_agent.py      # 243 lines - CLI agent
└── config.py          # 22 lines - configuration
```

### Issues

1. **`mcp_server.py` is a monolith** (1270 lines)
   - Contains tool definitions, business logic, and domain helpers
   - Frontmatter parsing, date handling, section manipulation all inline
   - Should be ~400 lines if business logic extracted

2. **No domain/service layer**
   - Tools directly manipulate files and ChromaDB
   - No abstraction for "vault operations"
   - Makes testing and reuse difficult

3. **Duplicate singleton patterns**
   - `hybrid_search.py:14-17`: Creates ChromaDB client per call
   - `index_vault.py:22-36`: Lazy global singletons
   - No shared connection management

### Recommended Structure

```
src/
├── mcp_server.py        # ~200 lines - MCP tool definitions only
├── tools/
│   ├── search.py        # Search tool implementations
│   ├── files.py         # File CRUD operations
│   ├── frontmatter.py   # Frontmatter tools
│   └── sections.py      # Section manipulation
├── services/
│   ├── vault.py         # Vault abstraction (file listing, path resolution)
│   ├── search.py        # Hybrid search service
│   └── chroma.py        # ChromaDB connection management
├── domain/
│   └── types.py         # Result types, error types
├── config.py
├── api_server.py
└── qwen_agent.py
```

---

## 5. Config Management

**Severity: Worth Improving**

### Current Approach

- Environment variables via `.env` and `python-dotenv`
- Centralized in `config.py` for vault/chroma paths
- `EXCLUDED_DIRS` hardcoded

### Issues

1. **Hardcoded Python path** (`api_server.py:44`, `qwen_agent.py:176`):
   ```python
   command=str(PROJECT_ROOT / ".venv" / "bin" / "python")
   ```
   This breaks on Windows (should be `Scripts/python.exe`) and systems without `.venv`.

2. **Duplicate PREFERENCES_FILE definition**:
   - `mcp_server.py:1180`: `PREFERENCES_FILE = VAULT_PATH / "Preferences.md"`
   - `qwen_agent.py:21`: `PREFERENCES_FILE = VAULT_PATH / "Preferences.md"`

3. **No environment-specific config**:
   - No way to switch between dev/test/prod configurations
   - No validation that required env vars exist

4. **Model config scattered**:
   - `qwen_agent.py:26`: `MODEL = "accounts/fireworks/models/deepseek-v3p1"`
   - `index_vault.py:43`: `'all-MiniLM-L6-v2'` (embedding model)
   - Should be in config or environment

### Recommendations

1. **Fix cross-platform Python path**:
   ```python
   import sys
   PYTHON_PATH = Path(sys.executable)
   ```

2. **Consolidate all config in `config.py`**:
   ```python
   PREFERENCES_FILE = VAULT_PATH / "Preferences.md"
   EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
   LLM_MODEL = os.getenv("LLM_MODEL", "accounts/fireworks/models/deepseek-v3p1")
   ```

3. **Add config validation on startup**:
   ```python
   def validate_config():
       if not VAULT_PATH.exists():
           raise ConfigError(f"VAULT_PATH does not exist: {VAULT_PATH}")
       if not FIREWORKS_API_KEY:
           raise ConfigError("FIREWORKS_API_KEY not set")
   ```

---

## 6. Testability

**Severity: Needs Attention**

### Current State

- **No tests exist** in the project
- Tools are tightly coupled to global state
- File system and ChromaDB accessed directly

### Barriers to Testing

1. **Global VAULT_PATH** (`config.py:10`):
   - Every module imports this directly
   - No way to inject a test vault path

2. **Decorated tool functions** (`mcp_server.py`):
   - `@mcp.tool()` binds to FastMCP instance
   - Can't call tool logic without server

3. **Lazy global singletons** (`index_vault.py:16-19`):
   ```python
   _client = None
   _collection = None
   _model = None
   ```
   - No reset mechanism for tests
   - Persists across test cases

4. **Direct file I/O** everywhere:
   - `file_path.read_text()` / `write_text()`
   - No abstraction layer

### Recommendations

1. **Extract pure business logic from tools**:
   ```python
   # Instead of:
   @mcp.tool()
   def read_file(path: str) -> str:
       # ... 15 lines of logic

   # Do:
   def _read_vault_file(vault_path: Path, file_path: str) -> Result:
       # Pure function, testable
       ...

   @mcp.tool()
   def read_file(path: str) -> str:
       return format_result(_read_vault_file(VAULT_PATH, path))
   ```

2. **Add dependency injection for tests**:
   ```python
   class VaultService:
       def __init__(self, vault_path: Path):
           self.vault_path = vault_path

   # Production: VaultService(VAULT_PATH)
   # Tests: VaultService(tmp_path / "test_vault")
   ```

3. **Create test fixtures** for:
   - Temporary vault directory with sample files
   - Mock ChromaDB collection
   - Sample frontmatter scenarios

### Minimum Viable Test Structure

```
tests/
├── conftest.py           # pytest fixtures (temp vault, mock chroma)
├── test_path_resolution.py
├── test_frontmatter.py
├── test_section_ops.py
├── test_search.py
└── test_integration.py   # Full tool tests
```

---

## 7. Adding New Tools

**Severity: Fine (with minor improvements)**

### Current Process

To add a new tool:

1. Add function to `mcp_server.py` with `@mcp.tool()` decorator
2. Write docstring (becomes tool description for agents)
3. Add type hints (FastMCP extracts parameter schema)
4. Handle errors inline
5. Return string result

### Boilerplate Required

For a file-based tool, ~16 lines of repeated code:

```python
@mcp.tool()
def new_file_tool(path: str, ...) -> str:
    """Docstring here."""
    # Lines 1-4: Path resolution with error handling
    try:
        file_path = _resolve_vault_path(path)
    except ValueError as e:
        return f"Error: {e}"

    # Lines 5-10: File existence checks
    if not file_path.exists():
        return f"Error: File not found: {path}"
    if not file_path.is_file():
        return f"Error: Not a file: {path}"

    # Lines 11-16: Read content with error handling
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    # ... actual tool logic ...

    # Lines n-n+3: Format relative path for response
    vault_resolved = VAULT_PATH.resolve()
    rel_path = file_path.relative_to(vault_resolved)
    return f"Done: {rel_path}"
```

### Recommendations

1. **Create a `@file_tool` decorator** that handles common boilerplate:
   ```python
   @file_tool  # Handles path resolution, existence checks, error formatting
   def new_tool(file_path: Path, content: str) -> Result:
       # Just the business logic
       ...
   ```

2. **Document the tool addition process** in CLAUDE.md or a CONTRIBUTING.md

3. **Add a tool template** in comments or docs:
   ```python
   # Template for new tools:
   # @mcp.tool()
   # def tool_name(required_param: str, optional_param: str = "default") -> str:
   #     """Tool description for agent.
   #
   #     Args:
   #         required_param: Description.
   #         optional_param: Description (default: "default").
   #
   #     Returns:
   #         JSON result with success status.
   #     """
   ```

---

## Prioritized Refactoring Tasks

Ordered by impact-to-effort ratio (highest first):

| Priority | Task | Impact | Effort | Files |
|----------|------|--------|--------|-------|
| **1** | Extract path resolution helper | High | Low | `mcp_server.py` |
| **2** | Extract section-finding logic | High | Low | `mcp_server.py` |
| **3** | Standardize response format (JSON envelope) | High | Medium | `mcp_server.py` |
| **4** | Fix cross-platform Python path | Medium | Low | `api_server.py`, `qwen_agent.py` |
| **5** | Consolidate vault file scanning | Medium | Low | `mcp_server.py`, `index_vault.py`, `log_chat.py` |
| **6** | Consolidate config (PREFERENCES_FILE, models) | Medium | Low | `config.py`, `mcp_server.py`, `qwen_agent.py` |
| **7** | Add silent failure logging in search | Medium | Low | `hybrid_search.py` |
| **8** | Create shared ChromaDB connection manager | Medium | Medium | `hybrid_search.py`, `index_vault.py` |
| **9** | Split `mcp_server.py` into modules | High | High | New structure |
| **10** | Add test infrastructure | High | High | New `tests/` directory |

### Quick Wins (< 1 hour each)

1. **Path resolution helper** — extract 12 instances of try/except boilerplate
2. **Fix Python path** — use `sys.executable` instead of hardcoded `.venv` path
3. **Consolidate PREFERENCES_FILE** — move to `config.py`

### Medium Effort (1-4 hours)

4. **Section logic extraction** — deduplicate `replace_section` and `append_to_section`
5. **Response envelope** — add `ok()`/`err()` helpers, update 3-5 most critical tools

### Larger Refactors (4+ hours)

6. **Split mcp_server.py** — requires careful dependency management
7. **Add test suite** — needs fixtures, mocks, and initial test cases

---

## Summary

The codebase is functional and well-organized at a high level, with good security practices (path traversal protection) and a clean separation between the MCP server, API server, and CLI agent.

**Main areas for improvement:**

1. **Error signaling** — inconsistent formats make it hard for agents to reliably detect failures
2. **Code duplication** — ~150+ lines could be eliminated with simple extractions
3. **Testability** — no tests exist; adding them requires some refactoring first
4. **Configuration** — cross-platform issues and scattered constants

The recommended starting point is the "Quick Wins" section — these changes are low-risk, high-impact, and will make subsequent improvements easier.
