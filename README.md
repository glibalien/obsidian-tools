# Obsidian Tools

Semantic search and vault management tools for Obsidian, exposed via MCP (Model Context Protocol).

## Features

- **Hybrid search**: Combines semantic (vector) and keyword search with Reciprocal Rank Fusion
- **Vault management**: Read, create, and move files; update frontmatter
- **Link discovery**: Find backlinks and outlinks between notes
- **Query by metadata**: Search by frontmatter fields, date ranges, or folder
- **Interaction logging**: Log AI conversations to daily notes

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   MCP Client    │────▶│   MCP Server    │────▶│  ChromaDB +     │
│ (Claude, etc.)  │     │ (mcp_server.py) │     │  Obsidian Vault │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

The MCP server exposes tools that any MCP-compatible client can use to interact with your Obsidian vault.

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/obsidian-tools.git
cd obsidian-tools

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Edit `.env`:

```
VAULT_PATH=~/Documents/your-vault-name
CHROMA_PATH=./.chroma_db
```

- `VAULT_PATH`: Path to your Obsidian vault
- `CHROMA_PATH`: Where to store the ChromaDB database (relative or absolute)

## Usage

### Index your vault

Before searching, index your vault to create embeddings:

```bash
python src/index_vault.py
```

Re-run this when vault content changes significantly.

### Run the MCP server

```bash
python src/mcp_server.py
```

Or configure it in your MCP client's settings.

### Run the HTTP API server

For programmatic access via HTTP:

```bash
python src/api_server.py
```

The server binds to `127.0.0.1:8000` (localhost only). Send chat messages via POST:

```bash
# Start a new conversation
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Find notes about projects"}'

# Continue a conversation (use session_id from previous response)
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me more", "session_id": "<uuid>"}'
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_vault` | Hybrid search (semantic + keyword) with configurable mode |
| `read_file` | Read full content of a vault note |
| `create_file` | Create a new markdown note with optional frontmatter |
| `move_file` | Move a file within the vault |
| `batch_move_files` | Move multiple files at once |
| `list_files_by_frontmatter` | Find files by frontmatter field values |
| `update_frontmatter` | Modify frontmatter (set, remove, or append) |
| `batch_update_frontmatter` | Update frontmatter on multiple files |
| `find_backlinks` | Find files that link to a given note |
| `find_outlinks` | Extract all wikilinks from a file |
| `search_by_date_range` | Find files by created or modified date |
| `search_by_folder` | List files in a folder |
| `log_interaction` | Log an interaction to today's daily note |
| `web_search` | Search the web using DuckDuckGo |
| `append_to_file` | Append content to an existing file |

### Example: search_vault

```json
{
  "query": "meeting notes about project X",
  "n_results": 5,
  "mode": "hybrid"
}
```

Modes: `"hybrid"` (default), `"semantic"`, `"keyword"`

### Example: update_frontmatter

```json
{
  "path": "Projects/my-project.md",
  "field": "tags",
  "value": "[\"project\", \"active\"]",
  "operation": "set"
}
```

Operations: `"set"`, `"remove"`, `"append"`

## Project Structure

```
src/
├── mcp_server.py     # FastMCP server exposing vault tools
├── api_server.py     # FastAPI HTTP wrapper for the agent
├── hybrid_search.py  # Semantic + keyword search with RRF
├── search_vault.py   # Search interface
├── index_vault.py    # Vault indexer for ChromaDB
├── log_chat.py       # Daily note logging
├── config.py         # Shared configuration
└── qwen_agent.py     # CLI chat agent (optional)
```

## Dependencies

- [ChromaDB](https://www.trychroma.com/) - Vector database for embeddings
- [sentence-transformers](https://www.sbert.net/) - Embedding model
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- [FastAPI](https://fastapi.tiangolo.com/) - HTTP API framework
- [PyYAML](https://pyyaml.org/) - YAML parsing for frontmatter

## License

MIT
