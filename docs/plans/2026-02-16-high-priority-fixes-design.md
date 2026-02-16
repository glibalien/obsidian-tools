# High-Priority Fixes Design

Addresses the four high-priority issues from `docs/analyses/tool_analysis.txt`.

## 1. Embedding Model Mismatch (4e) — Remove Dead Code

**Problem:** `index_vault.py` imports `SentenceTransformer` and defines `get_model()`, but never calls it. Both indexing and querying rely on ChromaDB's default embedding model (`all-MiniLM-L6-v2`). If someone changes `EMBEDDING_MODEL` in `.env`, it has no effect — a latent bug.

**Fix:** Remove the dead code. ChromaDB's default is what we actually use and that's fine.

- Delete `get_model()`, `_model` global, and `SentenceTransformer` import from `index_vault.py`
- Remove `sentence-transformers` from `requirements.txt` (verify no other module imports it)
- Remove `EMBEDDING_MODEL` from `config.py` and `.env.example`
- Document in CLAUDE.md that embeddings use ChromaDB's default (`all-MiniLM-L6-v2`)

## 2. Keyword Search N Queries (1a) — Single `$or` + Limit

**Problem:** `keyword_search()` in `hybrid_search.py` issues one `collection.get(where_document={"$contains": term})` per query term. Each is a full-collection linear scan. A 5-word query means 5 scans.

**Fix:** Combine all terms into a single `$or` query with a result limit, then count matching terms in Python.

- Build `where_document={"$or": [{"$contains": t} for t in terms]}` for multi-term queries
- Single-term queries use `{"$contains": term}` directly (no `$or` wrapper)
- Add `limit=200` to cap the result set
- Count matching terms per doc in Python: `sum(1 for t in terms if t in doc.lower())`
- Rank by hit count descending, return top `n_results`
- Content truncation (`doc[:500]`) stays as-is

## 3. Inconsistent Response Formats (3a) — Migrate to ok()/err()

**Problem:** Some tools return structured JSON via `ok()`/`err()`, others return plain strings. This breaks tool compaction in `api_server.py` (plain-string tools get `{"status": "unknown"}` stubs) and forces the LLM to handle two response patterns.

**Fix:** Migrate all plain-string tools to use `ok()`/`err()` from `services/vault.py`. Update tests in the same pass per file.

### Tools to migrate

**files.py** (5 tools):
- `read_file` → `ok(content=...)` for success, `err(...)` for errors. Pagination markers stay in the content string.
- `create_file` → `ok(path=..., message=...)`
- `move_file` → `ok(message=...)` / `err(...)`
- `batch_move_files` → already returns `format_batch_result()`, wrap in `ok()`/`err()`
- `append_to_file` → `ok(path=..., message=...)`

**links.py** (3 tools):
- `find_backlinks` → `ok(results=[...])` with list of paths, `ok(message="No backlinks...")` for empty
- `find_outlinks` → `ok(results=[...])` with list of link names
- `search_by_folder` → `ok(results=[...])` with list of paths

**frontmatter.py** (3 tools):
- `list_files_by_frontmatter` → `ok(results=[...])` with list of paths
- `update_frontmatter` → `ok(path=..., message=...)`
- `search_by_date_range` → `ok(results=[...])` with list of paths

**utility.py** (2 tools):
- `log_interaction` → `ok(message=...)`
- `get_current_date` → `ok(date=...)`

**preferences.py** (3 tools):
- `save_preference` → `ok(message=...)`
- `list_preferences` → `ok(results=[...])` with numbered list, or `ok(message="No preferences...")`
- `remove_preference` → `ok(message=...)`

### Response format conventions

- Success with data: `ok(results=[...])` — list of items
- Success with path: `ok(path="relative/path.md")` — file operation confirmation
- Success with message: `ok("Human-readable message")` — simple confirmations
- Error: `err("description")` — all error cases

## 4. CLI Agent Compaction (4c) — Shared Compaction Module

**Problem:** `api_server.py` compacts tool messages after each turn to prevent token explosion. `agent.py` does not — over a long CLI conversation, prompt tokens grow unboundedly.

**Fix:** Extract compaction into a shared module, use it in both servers.

- Create `services/compaction.py` with `build_tool_stub()` and `compact_tool_messages()`
- `api_server.py` imports from the new module instead of defining locally
- `agent.py` calls `compact_tool_messages(messages)` after each tool execution round (after appending all tool results, before the next LLM call)
- Move compaction tests from `test_session_management.py` to test the shared module (or keep them where they are and add imports)
