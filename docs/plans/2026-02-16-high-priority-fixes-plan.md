# High-Priority Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the 4 high-priority issues from tool_analysis.txt: remove dead embedding code, optimize keyword search, standardize response formats, and share tool compaction between API and CLI agents.

**Architecture:** Four independent fixes that can be implemented in sequence. Task 1 (dead code removal) and Task 2 (keyword search) are isolated. Task 3 (response formats) is the largest — migrates 16 tools across 5 files. Task 4 (shared compaction) extracts code from api_server.py into a new services/compaction.py module.

**Tech Stack:** Python, ChromaDB, pytest

---

### Task 1: Remove Dead Embedding Code

**Files:**
- Modify: `src/index_vault.py` (remove lines 1-27 area: SentenceTransformer import, `_model`, `get_model()`)
- Modify: `src/config.py` (remove `EMBEDDING_MODEL`)
- Modify: `requirements.txt` (remove `sentence-transformers`)
- Modify: `tests/test_chunking.py` (update any imports if needed)

**Step 1: Remove dead code from index_vault.py**

Remove the `SentenceTransformer` import, `_model` global, and `get_model()` function:

```python
# DELETE these lines from index_vault.py:
from sentence_transformers import SentenceTransformer
from config import VAULT_PATH, CHROMA_PATH, EMBEDDING_MODEL  # remove EMBEDDING_MODEL from import

_model = None

def get_model():
    """Get or create the sentence transformer model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model
```

The config import line becomes:
```python
from config import VAULT_PATH, CHROMA_PATH
```

**Step 2: Remove EMBEDDING_MODEL from config.py**

Delete this line from `src/config.py:35`:
```python
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
```

**Step 3: Remove sentence-transformers from requirements.txt**

Delete line 2:
```
sentence-transformers>=5.0.0
```

**Step 4: Run tests to verify nothing breaks**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (nothing actually used `get_model()` or `EMBEDDING_MODEL` at runtime)

**Step 5: Commit**

```bash
git add src/index_vault.py src/config.py requirements.txt
git commit -m "fix: remove dead embedding model code (4e)

SentenceTransformer was imported but never called — both indexing and
querying use ChromaDB's default embedding model (all-MiniLM-L6-v2)."
```

---

### Task 2: Optimize keyword_search to Single Query

**Files:**
- Modify: `src/hybrid_search.py:44-91` (rewrite `keyword_search`)
- Modify: `tests/test_chunking.py:292-304` (update mock to match new query shape)

**Step 1: Write test for single-query keyword search**

Add to `tests/test_chunking.py`, in a new class after the existing `TestSearchHeadingMetadata`:

```python
class TestKeywordSearchOptimization:
    """Tests for optimized single-query keyword search."""

    @patch("hybrid_search.get_collection")
    def test_single_term_no_or_wrapper(self, mock_get_collection):
        """Single-term query should use $contains directly, not $or."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["some content here"],
            "metadatas": [{"source": "a.md", "heading": "## H", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("content", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        assert call_kwargs["where_document"] == {"$contains": "content"}

    @patch("hybrid_search.get_collection")
    def test_multi_term_uses_or_query(self, mock_get_collection):
        """Multi-term query should combine terms with $or."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["alpha bravo content", "bravo only content"],
            "metadatas": [
                {"source": "a.md", "heading": "", "chunk_type": "section"},
                {"source": "b.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("alpha bravo", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        where_doc = call_kwargs["where_document"]
        assert "$or" in where_doc
        assert {"$contains": "alpha"} in where_doc["$or"]
        assert {"$contains": "bravo"} in where_doc["$or"]

    @patch("hybrid_search.get_collection")
    def test_multi_term_ranked_by_hit_count(self, mock_get_collection):
        """Results should be ranked by number of matching terms."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1", "id2"],
            "documents": ["alpha bravo content", "bravo only content"],
            "metadatas": [
                {"source": "a.md", "heading": "", "chunk_type": "section"},
                {"source": "b.md", "heading": "", "chunk_type": "section"},
            ],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("alpha bravo", n_results=5)

        # a.md matches both terms, b.md matches one
        assert results[0]["source"] == "a.md"
        assert results[1]["source"] == "b.md"

    @patch("hybrid_search.get_collection")
    def test_query_uses_limit(self, mock_get_collection):
        """Query should include a limit parameter."""
        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        keyword_search("something", n_results=5)

        call_kwargs = mock_collection.get.call_args[1]
        assert "limit" in call_kwargs
        assert call_kwargs["limit"] == 200
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestKeywordSearchOptimization -v`
Expected: Failures (current code uses per-term queries, no `$or`, no `limit`)

**Step 3: Rewrite keyword_search**

Replace `keyword_search` in `src/hybrid_search.py:44-91`:

```python
KEYWORD_LIMIT = 200  # Max chunks to scan for keyword matching


def keyword_search(query: str, n_results: int = 5) -> list[dict[str, str]]:
    """Search the vault for chunks containing query keywords.

    Combines all query terms into a single ChromaDB $or query, then ranks
    results by number of matching terms.

    Args:
        query: Search query string.
        n_results: Maximum number of results to return.

    Returns:
        List of dicts with 'source', 'content', and 'heading' keys,
        sorted by hit count.
    """
    terms = _extract_query_terms(query)
    if not terms:
        return []

    collection = get_collection()

    # Build filter: single $contains for one term, $or for multiple
    if len(terms) == 1:
        where_document = {"$contains": terms[0]}
    else:
        where_document = {"$or": [{"$contains": t} for t in terms]}

    try:
        matches = collection.get(
            where_document=where_document,
            include=["documents", "metadatas"],
            limit=KEYWORD_LIMIT,
        )
    except Exception as e:
        logger.warning(f"Keyword search failed: {e}")
        return []

    if not matches["ids"]:
        return []

    # Count matching terms per chunk and build results
    scored = []
    for doc, metadata in zip(matches["documents"], matches["metadatas"]):
        doc_lower = doc.lower()
        hits = sum(1 for t in terms if t in doc_lower)
        scored.append({
            "source": metadata["source"],
            "content": doc[:500],
            "heading": metadata.get("heading", ""),
            "hits": hits,
        })

    scored.sort(key=lambda x: x["hits"], reverse=True)
    return [
        {"source": r["source"], "content": r["content"], "heading": r["heading"]}
        for r in scored[:n_results]
    ]
```

**Step 4: Update existing keyword test mock**

Update `tests/test_chunking.py::TestSearchHeadingMetadata::test_keyword_search_includes_heading` — the mock needs to return lists (not bare values) since the new code iterates `zip(matches["documents"], matches["metadatas"])`:

The existing mock already returns the correct shape. But the test calls `keyword_search("searchable content", n_results=1)` which extracts terms `["searchable", "content"]` and would now use `$or`. The mock just needs to work with the new single-call pattern — verify it still passes.

**Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/hybrid_search.py tests/test_chunking.py
git commit -m "perf: optimize keyword_search to single ChromaDB query (1a)

Replace N per-term collection.get() calls with a single \$or query
and limit=200. Terms are counted in Python for ranking."
```

---

### Task 3: Standardize Response Formats

Migrate all plain-string tools to use `ok()`/`err()`. Update tests in the same pass per file. Each sub-task handles one tool file.

#### Task 3a: Migrate tools/files.py

**Files:**
- Modify: `src/tools/files.py`
- Modify: `tests/test_tools_files.py`

**Step 1: Add ok/err imports to files.py**

Add to the imports in `src/tools/files.py`:
```python
from services.vault import ok, err
```

**Step 2: Migrate read_file**

`read_file` is special — it returns file content directly, not a status message. Wrap it:
- Errors: `return err("File not found: ...")` instead of `return f"Error: ..."`
- Success: `return ok(content=content_string)` where `content_string` is the assembled text (with pagination markers)

```python
def read_file(path: str, offset: int = 0, length: int = 4000) -> str:
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text()
    except Exception as e:
        return err(f"Reading file failed: {e}")

    total = len(content)

    if offset == 0 and total <= length:
        return ok(content=content)

    if offset >= total:
        return err(f"offset {offset} exceeds file length {total}")

    chunk = content[offset:offset + length]
    end_pos = offset + length

    parts = []
    if offset > 0:
        parts.append(f"[Continuing from char {offset} of {total}]\n\n")
    parts.append(chunk)
    if end_pos < total:
        parts.append(f"\n\n[... truncated at char {end_pos} of {total}. Use offset={end_pos} to read more.]")

    return ok(content="".join(parts))
```

**Step 3: Migrate create_file**

```python
# Errors become err(...), success becomes:
return ok(f"Created {get_relative_path(file_path)}", path=str(get_relative_path(file_path)))
```

**Step 4: Migrate move_file**

```python
def move_file(source: str, destination: str) -> str:
    success, message = do_move_file(source, destination)
    if success:
        return ok(message)
    return err(message)
```

**Step 5: Migrate batch_move_files**

`format_batch_result` returns a plain string. Wrap it:
```python
return ok(format_batch_result("move", results))
```
Keep the existing error returns as `err(...)`.

**Step 6: Migrate append_to_file**

```python
# Error: return err(error)
# Success: return ok(f"Appended to {get_relative_path(file_path)}", path=str(get_relative_path(file_path)))
```

**Step 7: Update tests in test_tools_files.py**

All assertions that check for string content need to parse JSON first. Pattern:

```python
# Before:
result = read_file("note1.md")
assert "# Note 1" in result

# After:
result = json.loads(read_file("note1.md"))
assert result["success"] is True
assert "# Note 1" in result["content"]
```

For error cases:
```python
# Before:
result = read_file("nonexistent.md")
assert "Error" in result

# After:
result = json.loads(read_file("nonexistent.md"))
assert result["success"] is False
assert "not found" in result["error"].lower()
```

Add `import json` at the top of the test file.

**Step 8: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -v`
Expected: All pass

**Step 9: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "refactor: migrate tools/files.py to ok()/err() responses (3a)"
```

#### Task 3b: Migrate tools/links.py

**Files:**
- Modify: `src/tools/links.py`
- Modify: `tests/test_tools_links.py`

**Step 1: Add ok/err imports**

```python
from services.vault import get_vault_files, ok, err, resolve_dir, resolve_file
```

**Step 2: Migrate find_backlinks**

```python
# Empty name: return err("note_name cannot be empty")
# No results: return ok("No backlinks found to [[{note_name}]]", results=[])
# Results: return ok(results=sorted(backlinks))
```

**Step 3: Migrate find_outlinks**

```python
# Error: return err(error)
# No results: return ok(f"No outlinks found in {path}", results=[])
# Results: return ok(results=sorted(set(matches)))
```

**Step 4: Migrate search_by_folder**

```python
# Error: return err(error)
# No results: return ok(f"No markdown files found {mode}in {folder}", results=[])
# Results: return ok(results=sorted(files))
```

**Step 5: Update tests**

Parse JSON in assertions. For result lists, check `result["results"]` is a list. For "no results" cases, check `result["success"] is True` and `result["results"] == []`.

```python
# Before:
result = find_backlinks("note1")
assert "note2.md" in result

# After:
result = json.loads(find_backlinks("note1"))
assert result["success"] is True
assert "note2.md" in result["results"]
```

**Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_links.py -v`
Expected: All pass

**Step 7: Commit**

```bash
git add src/tools/links.py tests/test_tools_links.py
git commit -m "refactor: migrate tools/links.py to ok()/err() responses (3a)"
```

#### Task 3c: Migrate tools/frontmatter.py

**Files:**
- Modify: `src/tools/frontmatter.py`
- Modify: `tests/test_vault_service.py` (frontmatter tests are here)

**Step 1: Add ok/err imports**

```python
from services.vault import (
    do_update_frontmatter,
    extract_frontmatter,
    format_batch_result,
    get_file_creation_time,
    get_vault_files,
    ok,
    err,
    parse_frontmatter_date,
)
```

**Step 2: Migrate list_files_by_frontmatter**

```python
# Validation error: return err(f"match_type must be ...")
# No results: return ok(f"No files found where ...", results=[])
# Results: return ok(results=sorted(matching))
```

**Step 3: Migrate update_frontmatter**

```python
# Validation errors: return err(...)
# Success/failure from do_update_frontmatter:
success, message = do_update_frontmatter(path, field, parsed_value, operation)
if success:
    return ok(message)
return err(message)
```

**Step 4: Migrate search_by_date_range**

```python
# Validation errors: return err(...)
# No results: return ok(f"No files found ...", results=[])
# Results: return ok(results=sorted(matching))
```

**Step 5: Update tests**

Check which tests in `test_vault_service.py` call these tools directly and update assertions.

**Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py -v`
Expected: All pass

**Step 7: Commit**

```bash
git add src/tools/frontmatter.py tests/test_vault_service.py
git commit -m "refactor: migrate tools/frontmatter.py to ok()/err() responses (3a)"
```

#### Task 3d: Migrate tools/utility.py

**Files:**
- Modify: `src/tools/utility.py`

**Step 1: Add ok/err imports**

```python
from services.vault import ok, err
```

**Step 2: Migrate log_interaction**

```python
def log_interaction(...) -> str:
    try:
        path = log_chat(task_description, query, summary, files, full_response)
    except Exception as e:
        return err(f"Logging failed: {e}")
    return ok(f"Logged to {path}")
```

**Step 3: Migrate get_current_date**

```python
def get_current_date() -> str:
    return ok(date=datetime.now().strftime("%Y-%m-%d"))
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass (these tools have no dedicated tests currently)

**Step 5: Commit**

```bash
git add src/tools/utility.py
git commit -m "refactor: migrate tools/utility.py to ok()/err() responses (3a)"
```

#### Task 3e: Migrate tools/preferences.py

**Files:**
- Modify: `src/tools/preferences.py`

**Step 1: Add ok/err imports**

```python
from services.vault import ok, err
```

**Step 2: Migrate save_preference**

```python
# Empty: return err("preference cannot be empty")
# Success: return ok(f"Saved preference: {preference}")
```

**Step 3: Migrate list_preferences**

```python
# Empty: return ok("No preferences saved.", results=[])
# Has prefs: return ok(results=[f"{i}. {pref}" for i, pref in enumerate(preferences, start=1)])
```

**Step 4: Migrate remove_preference**

```python
# No prefs: return err("No preferences to remove")
# Invalid line: return err(f"Invalid line number. Must be between 1 and {len(preferences)}")
# Success: return ok(f"Removed preference: {removed}")
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass (preferences have no dedicated tests currently)

**Step 6: Commit**

```bash
git add src/tools/preferences.py
git commit -m "refactor: migrate tools/preferences.py to ok()/err() responses (3a)"
```

#### Task 3f: Update build_tool_stub for new content field

**Files:**
- Modify: `src/api_server.py` (or `services/compaction.py` if Task 4 is done first)

**Step 1: Update build_tool_stub**

Add handling for the `content` field (used by `read_file`):

```python
if "content" in data:
    stub["has_content"] = True
    stub["content_length"] = len(data["content"])
```

And for the `date` field (used by `get_current_date`):
```python
if "date" in data:
    stub["date"] = data["date"]
```

**Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/test_session_management.py -v`
Expected: All pass

**Step 3: Commit**

```bash
git add src/api_server.py
git commit -m "refactor: update build_tool_stub for new response fields (3a)"
```

---

### Task 4: Extract Shared Compaction Module

**Files:**
- Create: `src/services/compaction.py`
- Modify: `src/api_server.py` (remove local functions, import from new module)
- Modify: `src/agent.py` (add compaction call)
- Modify: `tests/test_session_management.py` (update imports)

**Step 1: Write test for compaction in agent context**

Add to `tests/test_agent.py`:

```python
from services.compaction import compact_tool_messages

class TestAgentCompaction:
    """Tests for tool message compaction in agent context."""

    def test_compact_tool_messages_after_tool_round(self):
        """Tool messages should be compacted after execution."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({"success": True, "results": [{"source": "a.md", "content": "long..."}]})},
        ]
        compact_tool_messages(messages)

        tool_msg = messages[3]
        assert tool_msg["_compacted"] is True
        parsed = json.loads(tool_msg["content"])
        assert parsed["status"] == "success"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent.py::TestAgentCompaction -v`
Expected: ImportError (module doesn't exist yet)

**Step 3: Create services/compaction.py**

Move `build_tool_stub` and `compact_tool_messages` from `src/api_server.py` to `src/services/compaction.py`:

```python
"""Tool message compaction for managing conversation token usage."""

import json


def build_tool_stub(content: str) -> str:
    """Build a compact stub from a tool result string.

    Parses JSON tool results and extracts key metadata (status, file paths,
    result count, errors). Non-JSON content is summarized to 200 chars.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        summary = content[:200] if len(content) > 200 else content
        return json.dumps({"status": "unknown", "summary": summary})

    stub: dict = {}

    if "success" in data:
        stub["status"] = "success" if data["success"] else "error"
    else:
        stub["status"] = "unknown"

    if "error" in data:
        stub["error"] = data["error"]

    if "message" in data:
        stub["message"] = data["message"]

    if "path" in data:
        stub["path"] = data["path"]

    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        files = [
            r["source"]
            for r in data["results"]
            if isinstance(r, dict) and "source" in r
        ]
        if files:
            stub["files"] = files

    if "content" in data:
        stub["has_content"] = True
        stub["content_length"] = len(data["content"])

    if "date" in data:
        stub["date"] = data["date"]

    return json.dumps(stub)


def compact_tool_messages(messages: list[dict]) -> None:
    """Replace tool results with compact stubs in-place."""
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and not msg.get("_compacted"):
            messages[i] = {
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": build_tool_stub(msg["content"]),
                "_compacted": True,
            }
```

**Step 4: Update api_server.py imports**

Replace the local `build_tool_stub` and `compact_tool_messages` definitions with:

```python
from services.compaction import build_tool_stub, compact_tool_messages
```

Delete the two function definitions from `api_server.py`.

**Step 5: Add compaction to agent.py**

Add import at top of `src/agent.py`:
```python
from services.compaction import compact_tool_messages
```

In `agent_turn`, after the tool execution loop (after all tool results are appended to `messages`, before the `while True` loop continues), add:

```python
        # Compact tool results to manage token usage
        compact_tool_messages(messages)
```

This goes after the `for tool_call in assistant_message.tool_calls:` block, at the same indentation level as that for loop (inside the `while True` but after tool execution).

**Step 6: Update test imports in test_session_management.py**

Change line 13:
```python
# Before:
from api_server import app, build_tool_stub, compact_tool_messages

# After:
from api_server import app
from services.compaction import build_tool_stub, compact_tool_messages
```

**Step 7: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All pass

**Step 8: Commit**

```bash
git add src/services/compaction.py src/api_server.py src/agent.py tests/test_session_management.py tests/test_agent.py
git commit -m "refactor: extract shared compaction module, add to CLI agent (4c)

Move build_tool_stub and compact_tool_messages from api_server.py into
services/compaction.py. CLI agent now compacts tool messages after each
round to prevent unbounded token growth."
```
