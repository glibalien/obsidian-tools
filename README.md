# Obsidian Tools

Semantic search and vault management tools for Obsidian, exposed via MCP (Model Context Protocol).

## Features

- **Obsidian plugin** (optional): Chat sidebar for interacting with your vault directly in Obsidian
- **Hybrid search**: Combines semantic (vector) and keyword search with Reciprocal Rank Fusion
- **Vault management**: Read, create, move, and append/prepend to files; update frontmatter
- **Section editing**: Replace or append to specific markdown sections by heading
- **Link discovery**: Find backlinks and outlinks between notes
- **Query by metadata**: Search by frontmatter fields, date ranges, or folder
- **Interaction logging**: Log AI conversations to daily notes
- **User preferences**: Persistent preferences stored in vault and loaded by the agent
- **Audio transcription**: Transcribe audio embeds using Whisper API
- **Web search**: DuckDuckGo integration for web queries
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

The Obsidian plugin provides a chat sidebar that connects to the API server. The API server wraps the LLM agent, which uses MCP tools to search and manage your vault.

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
python --version  # Should show Python 3.12.x

# Create virtual environment
python -m venv .venv
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
FIREWORKS_API_KEY=your-api-key-here
```

| Variable | Description |
|----------|-------------|
| `VAULT_PATH` | Path to your Obsidian vault |
| `CHROMA_PATH` | Where to store the ChromaDB database (relative or absolute) |
| `FIREWORKS_API_KEY` | API key from [Fireworks AI](https://fireworks.ai/) (required for the chat agent and audio transcription) |

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

The indexer is incremental — after the initial run, it only indexes files modified since the last run and prunes entries for deleted files. Use `--full` for a complete reindex:

```bash
python src/index_vault.py --full
```

To keep the index up to date automatically, see [Running as a Service](#running-as-a-service).

### Run the MCP server

```bash
python src/mcp_server.py
```

Or configure it in your MCP client's settings (see [MCP Client Configuration](#mcp-client-configuration)).

### Run the HTTP API server

For programmatic access or to use the Obsidian plugin:

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

To keep the API server running persistently, see [Running as a Service](#running-as-a-service).

### Install the Obsidian plugin (optional)

The `plugin/` directory contains an optional Obsidian plugin with a chat sidebar. You can use the MCP server and HTTP API without installing the plugin.

```bash
# Build the plugin
cd plugin
npm install
npm run build

# Install to your vault (adjust path as needed)
mkdir -p ~/Documents/your-vault/.obsidian/plugins/vault-agent
cp manifest.json main.js styles.css ~/Documents/your-vault/.obsidian/plugins/vault-agent/
```

Then in Obsidian:
1. Go to Settings → Community Plugins
2. Enable "Vault Agent"
3. Click the message icon in the ribbon to open the chat sidebar

The API server must be running for the plugin to work.

## Running as a Service

Example service files are provided in the `services/` directory to run the API server and vault indexer as background services.

### Linux (systemd)

The systemd unit files use `%h` (home directory), so paths are relative to your home. If you cloned the repo somewhere other than `~/obsidian-tools`, edit the `WorkingDirectory` and `ExecStart` paths in the `.service` files.

```bash
# Copy the unit files to your user systemd directory
mkdir -p ~/.config/systemd/user
cp services/systemd/obsidian-tools-api.service ~/.config/systemd/user/
cp services/systemd/obsidian-tools-indexer.service ~/.config/systemd/user/
cp services/systemd/obsidian-tools-indexer-scheduler.timer ~/.config/systemd/user/

# Reload systemd to pick up the new files
systemctl --user daemon-reload

# Enable and start the API server (runs on boot)
systemctl --user enable --now obsidian-tools-api

# Enable and start the indexer timer (runs hourly)
systemctl --user enable --now obsidian-tools-indexer-scheduler.timer

# Run the indexer once immediately
systemctl --user start obsidian-tools-indexer
```

Useful commands:

```bash
# Check status
systemctl --user status obsidian-tools-api
systemctl --user status obsidian-tools-indexer-scheduler.timer

# View logs
journalctl --user -u obsidian-tools-api -f
journalctl --user -u obsidian-tools-indexer

# Restart the API server
systemctl --user restart obsidian-tools-api
```

**Note:** User services require an active login session by default. To allow them to run without being logged in:

```bash
sudo loginctl enable-linger $USER
```

### macOS (launchd)

The plist files contain placeholder paths. Before installing, replace `YOUR_USERNAME` with your macOS username:

```bash
# Replace YOUR_USERNAME in the plist files
sed -i '' "s/YOUR_USERNAME/$(whoami)/g" services/launchd/com.obsidian-tools.api.plist
sed -i '' "s/YOUR_USERNAME/$(whoami)/g" services/launchd/com.obsidian-tools.indexer.plist
```

If you cloned the repo somewhere other than `~/obsidian-tools`, also update the paths in the plist files accordingly.

```bash
# Copy to LaunchAgents
cp services/launchd/com.obsidian-tools.api.plist ~/Library/LaunchAgents/
cp services/launchd/com.obsidian-tools.indexer.plist ~/Library/LaunchAgents/

# Load and start the API server (runs on login)
launchctl load ~/Library/LaunchAgents/com.obsidian-tools.api.plist

# Load and start the indexer (runs hourly)
launchctl load ~/Library/LaunchAgents/com.obsidian-tools.indexer.plist
```

Useful commands:

```bash
# Check status
launchctl list | grep obsidian-tools

# View logs
tail -f ~/Library/Logs/obsidian-tools-api.log
tail -f ~/Library/Logs/obsidian-tools-indexer.log

# Unload a service
launchctl unload ~/Library/LaunchAgents/com.obsidian-tools.api.plist

# Reload after editing a plist
launchctl unload ~/Library/LaunchAgents/com.obsidian-tools.api.plist
launchctl load ~/Library/LaunchAgents/com.obsidian-tools.api.plist
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_vault` | Hybrid search (semantic + keyword) with configurable mode |
| `read_file` | Read full content of a vault note |
| `create_file` | Create a new markdown note with optional frontmatter |
| `move_file` | Move a file within the vault |
| `batch_move_files` | Move multiple files at once |
| `append_to_file` | Append content to end of a file |
| `prepend_to_file` | Insert content after frontmatter (or at start if none) |
| `replace_section` | Replace a markdown heading and its content |
| `append_to_section` | Append content to end of a section |
| `list_files_by_frontmatter` | Find files by frontmatter field values |
| `update_frontmatter` | Modify frontmatter (set, remove, or append) |
| `batch_update_frontmatter` | Update frontmatter on multiple files |
| `find_backlinks` | Find files that link to a given note |
| `find_outlinks` | Extract all wikilinks from a file |
| `search_by_date_range` | Find files by created or modified date |
| `search_by_folder` | List files in a folder |
| `log_interaction` | Log an interaction to today's daily note |
| `save_preference` | Save a user preference to Preferences.md |
| `list_preferences` | List all saved preferences |
| `remove_preference` | Remove a preference by line number |
| `web_search` | Search the web using DuckDuckGo |
| `get_current_date` | Get current date in YYYY-MM-DD format |
| `transcribe_audio` | Transcribe audio embeds in a note via Whisper API |

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

### Example: append_to_section

```json
{
  "path": "Projects/my-project.md",
  "heading": "## Notes",
  "content": "- New note added today"
}
```

Appends content at the end of the "## Notes" section, preserving all existing content.

### Example: transcribe_audio

```json
{
  "path": "Meetings/2024-01-15.md"
}
```

Parses audio embeds like `![[recording.m4a]]` from the note and transcribes them using Fireworks Whisper API. Supported formats: m4a, webm, mp3, wav, ogg. Audio files are resolved from the `Attachments` folder.

## Project Structure

```
src/
├── mcp_server.py        # FastMCP server - registers tools from submodules
├── api_server.py        # FastAPI HTTP wrapper for the agent
├── qwen_agent.py        # CLI chat agent
├── config.py            # Shared configuration
├── hybrid_search.py     # Semantic + keyword search with RRF
├── search_vault.py      # Search interface
├── index_vault.py       # Vault indexer for ChromaDB
├── log_chat.py          # Daily note logging
├── services/
│   ├── chroma.py        # Shared ChromaDB connection management
│   └── vault.py         # Path resolution, response helpers, utilities
└── tools/
    ├── files.py         # read_file, create_file, move_file, append_to_file
    ├── frontmatter.py   # list_files_by_frontmatter, update_frontmatter, etc.
    ├── links.py         # find_backlinks, find_outlinks, search_by_folder
    ├── preferences.py   # save_preference, list_preferences, remove_preference
    ├── search.py        # search_vault, web_search
    ├── sections.py      # prepend_to_file, replace_section, append_to_section
    ├── utility.py       # log_interaction, get_current_date
    └── audio.py         # transcribe_audio

services/
├── systemd/             # Linux systemd unit files
│   ├── obsidian-tools-api.service
│   ├── obsidian-tools-indexer.service
│   └── obsidian-tools-indexer-scheduler.timer
└── launchd/             # macOS launchd plist files
    ├── com.obsidian-tools.api.plist
    └── com.obsidian-tools.indexer.plist

plugin/
├── src/
│   ├── main.ts          # Plugin entry point
│   └── ChatView.ts      # Chat sidebar view
├── styles.css           # Chat UI styling
└── manifest.json        # Plugin metadata
```

## Dependencies

- [ChromaDB](https://www.trychroma.com/) - Vector database for embeddings
- [sentence-transformers](https://www.sbert.net/) - Embedding model
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- [FastAPI](https://fastapi.tiangolo.com/) - HTTP API framework
- [PyYAML](https://pyyaml.org/) - YAML parsing for frontmatter
- [OpenAI SDK](https://github.com/openai/openai-python) - Whisper API client (via Fireworks)
- [python-dotenv](https://github.com/theskumar/python-dotenv) - Environment variable loading
- [ddgs](https://github.com/deedy5/duckduckgo_search) - DuckDuckGo search

## License

MIT
