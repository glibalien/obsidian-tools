# Obsidian Tools

Semantic search and vault management tools for Obsidian, exposed via MCP (Model Context Protocol).

## Features

- **Obsidian plugin** (optional): Chat sidebar for interacting with your vault directly in Obsidian
- **Hybrid search**: Combines semantic (vector) and keyword search with Reciprocal Rank Fusion
- **Vault management**: Read, create, and move files; update frontmatter
- **Link discovery**: Find backlinks and outlinks between notes
- **Query by metadata**: Search by frontmatter fields, date ranges, or folder
- **Interaction logging**: Log AI conversations to daily notes
- **HTTP API**: REST endpoint for programmatic access

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Obsidian Plugin │────▶│   API Server    │────▶│   MCP Server    │
│   (plugin/)     │     │ (api_server.py) │     │ (mcp_server.py) │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
                                                ┌─────────────────┐
                                                │  ChromaDB +     │
                                                │  Obsidian Vault │
                                                └─────────────────┘
```

The Obsidian plugin provides a chat sidebar that connects to the API server. The API server wraps the Qwen agent, which uses MCP tools to search and manage your vault.

## Installation

### Requirements

- **Python 3.11, 3.12, or 3.13** (not 3.14 — `onnxruntime` doesn't have wheels for 3.14 yet)

### macOS Users (Homebrew)

If you're on macOS and Homebrew has upgraded you to Python 3.14, use pyenv to install a compatible version:

```bash
# Install pyenv if you don't have it
brew install pyenv

# Add pyenv to your shell (add these to ~/.zshrc or ~/.bashrc)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc

# Restart your shell or run:
source ~/.zshrc

# Install Python 3.12
pyenv install 3.12.8
```

### Clone and Set Up

```bash
# Clone the repository
git clone https://github.com/glibalien/obsidian-tools.git
cd obsidian-tools

# If using pyenv, it will automatically use 3.12.8 (from .python-version)
# Verify your Python version:
python --version  # Should show Python 3.12.8

# Create virtual environment (use 'python', not 'python3', with pyenv)
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

**Note:** After activating the virtual environment, `python` and `python3` are equivalent. The `python -m venv` command matters because pyenv shims `python` to point to the correct version.

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Edit `.env`:

```
VAULT_PATH=~/Documents/your-vault-name
CHROMA_PATH=./.chroma_db
FIREWORKS_API_KEY=your-api-key-here
```

- `VAULT_PATH`: Path to your Obsidian vault
- `CHROMA_PATH`: Where to store the ChromaDB database (relative or absolute)
- `FIREWORKS_API_KEY`: API key from [Fireworks AI](https://fireworks.ai/) (required for the chat agent and HTTP API)

### MCP Client Configuration

To use the MCP server with Claude Code or another MCP client, copy `.mcp.json.example` to `.mcp.json` and update the paths:

```bash
cp .mcp.json.example .mcp.json
```

Edit `.mcp.json` to point to your local installation:

```json
{
  "mcpServers": {
    "obsidian-tools": {
      "command": "/path/to/obsidian-tools/.venv/bin/python",
      "args": ["/path/to/obsidian-tools/src/mcp_server.py"]
    }
  }
}
```

Replace `/path/to/obsidian-tools` with the actual path where you cloned the repository.

## Usage

### Index your vault

Before searching, index your vault to create embeddings:

```bash
python src/index_vault.py
```

Re-run this when vault content changes significantly. 

Alternatively, create a systemd service and timer to index on a schedule. The service prunes chunks for deleted files automatically, and after the initial index will only index any files that have changed since the previous index run.

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

### Install the Obsidian plugin (optional)

The `plugin/` directory contains an optional Obsidian plugin with a chat sidebar. You can use the MCP server and HTTP API without installing the plugin.

```bash
# Build the plugin
cd plugin
npm install
npm run build

# Install to your vault (adjust path as needed)
mkdir -p ~/Documents/your-vault/.obsidian/plugins/vault-chat
cp manifest.json main.js styles.css ~/Documents/your-vault/.obsidian/plugins/vault-chat/
```

Then in Obsidian:
1. Go to Settings → Community Plugins
2. Enable "Vault Chat"
3. Click the message icon in the ribbon to open the chat sidebar

**Note:** The API server must be running for the plugin to work.

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
| `append_to_file` | Append content to end of a file |
| `prepend_to_file` | Insert content after frontmatter (or at start if none) |
| `replace_section` | Replace a markdown heading and its content |
| `insert_after_heading` | Insert content after a heading, preserving existing content |
| `save_preference` | Save a user preference to Preferences.md |
| `list_preferences` | List all saved preferences |
| `remove_preference` | Remove a preference by line number |
| `web_search` | Search the web using DuckDuckGo |
| `get_current_date` | Get current date in YYYY-MM-DD format |

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

### Example: replace_section

```json
{
  "path": "Projects/my-project.md",
  "heading": "## Status",
  "content": "## Status\n\nUpdated to complete."
}
```

Replaces the entire "## Status" section (heading + content through to the next same-level or higher heading).

### Example: insert_after_heading

```json
{
  "path": "Projects/my-project.md",
  "heading": "## Notes",
  "content": "- New note added today"
}
```

Inserts content immediately after the heading, preserving existing section content.

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
└── qwen_agent.py     # CLI chat agent

plugin/
├── src/
│   ├── main.ts       # Plugin entry point
│   └── ChatView.ts   # Chat sidebar view
├── styles.css        # Chat UI styling
└── manifest.json     # Plugin metadata
```

## Dependencies

- [ChromaDB](https://www.trychroma.com/) - Vector database for embeddings
- [sentence-transformers](https://www.sbert.net/) - Embedding model
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- [FastAPI](https://fastapi.tiangolo.com/) - HTTP API framework
- [PyYAML](https://pyyaml.org/) - YAML parsing for frontmatter

## License

MIT
