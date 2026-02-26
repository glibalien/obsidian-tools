# Obsidian Tools

**An agentic Obsidian vault manager.** Ask questions in natural language, search across notes semantically, explore wikilinks, transcribe meeting recordings, manipulate and organize files and metadata, all conversationally. Works as an Obsidian sidebar plugin, a CLI chat agent, or an HTTP API.

<!-- TODO: Add a screenshot of the Obsidian chat sidebar here -->

## What It Does

Your vault gets indexed into a vector database. An LLM agent then uses [MCP tools](https://modelcontextprotocol.io/) to search, read, and modify your notes — combining semantic understanding with keyword matching to find what you need.

**Search & Discovery** — Hybrid semantic + keyword search with [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf), link graph traversal (backlinks, outlinks), frontmatter queries, date range filtering, folder browsing

**Vault Management** — Read, create, move files; edit specific markdown sections by heading; update frontmatter fields; batch operations for bulk changes

**Integrations** — Audio transcription via Whisper, web search via DuckDuckGo, interaction logging to daily notes, persistent user preferences

## How It Works

![Architecture diagram](obsidian-tools-architecture.svg)

1. **Indexer** scans your vault and creates embeddings in ChromaDB, splitting notes by headings, paragraphs, and sentences for precise retrieval
2. **MCP Server** exposes 23 tools for searching, reading, and modifying vault content
3. **LLM Agent** (powered by [Fireworks AI](https://fireworks.ai/)) orchestrates the tools to answer your questions
4. **Interfaces** — chat in Obsidian via the sidebar plugin, from the terminal via the CLI agent, or programmatically via the HTTP API

## Requirements

- **Python 3.11, 3.12, or 3.13** (not 3.14 — `onnxruntime` doesn't have wheels yet)
- **[Fireworks AI](https://fireworks.ai/) API key** — required for the chat agent and audio transcription

## Quick Start

```bash
git clone https://github.com/glibalien/obsidian-tools.git
cd obsidian-tools

# macOS / Linux
./install.sh

# Windows (PowerShell)
.\install.ps1
```

The installer will:
1. Find or help you install a compatible Python (resolves the real binary, not pyenv shims)
2. Create a virtual environment and install dependencies
3. Walk you through `.env` configuration (vault path, API key, etc.)
4. Optionally install background services (API server + vault indexer)
5. Optionally run the initial vault index

That's it — once installed, open Obsidian and start chatting, or run the CLI agent with `python src/agent.py`.

<details>
<summary>Manual installation</summary>

#### macOS Users (Homebrew)

If Homebrew has upgraded you to Python 3.14, use pyenv to install a compatible version:

```bash
brew install pyenv

# Add pyenv to your shell (add these to ~/.zshrc or ~/.bashrc)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc
source ~/.zshrc

pyenv install 3.12.8
```

#### Clone and Set Up

```bash
git clone https://github.com/glibalien/obsidian-tools.git
cd obsidian-tools

# If using pyenv, create the venv with the real binary (not the shim):
$(pyenv which python3.12) -m venv .venv
# Otherwise:
python -m venv .venv

source .venv/bin/activate
pip install -r requirements.txt
```

#### Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```
VAULT_PATH=~/Documents/your-vault-name
CHROMA_PATH=./.chroma_db
FIREWORKS_API_KEY=your-api-key-here
FIREWORKS_MODEL=accounts/fireworks/models/gpt-oss-120b
API_PORT=8000
INDEX_INTERVAL=60
```

| Variable | Description |
|----------|-------------|
| `VAULT_PATH` | Path to your Obsidian vault |
| `CHROMA_PATH` | Where to store the ChromaDB database (relative or absolute) |
| `FIREWORKS_API_KEY` | API key from [Fireworks AI](https://fireworks.ai/) |
| `FIREWORKS_MODEL` | Fireworks model ID (default: gpt-oss-120b) |
| `API_PORT` | Port for the HTTP API server (default: `8000`) |
| `INDEX_INTERVAL` | How often the vault indexer runs, in minutes (default: `60`) |

See `.env.example` for additional optional variables (logging, session limits, Whisper model, etc.).

</details>

## Usage

### 1. Index your vault

Before searching, build the vector index:

```bash
python src/index_vault.py
```

The indexer is incremental — subsequent runs only process files modified since the last run and prune deleted files. Use `--full` for a complete reindex.

To keep the index up to date automatically, see [Running as a Service](#running-as-a-service).

### 2. Choose your interface

**Obsidian Plugin** (recommended for daily use)

The `plugin/` directory contains a chat sidebar that connects to the API server.

```bash
cd plugin && npm install && npm run build

# Copy to your vault (adjust path)
mkdir -p ~/Documents/your-vault/.obsidian/plugins/vault-agent
cp manifest.json main.js styles.css ~/Documents/your-vault/.obsidian/plugins/vault-agent/
```

Enable "Vault Agent" in Obsidian Settings > Community Plugins. The API server must be running (see below).

**CLI Agent** (for terminal users)

```bash
python src/agent.py
```

**HTTP API** (for programmatic access)

```bash
python src/api_server.py
```

The server binds to `127.0.0.1:8000` (localhost only):

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Summarize this note", "active_file": "Projects/Marketing.md"}'
```

Sessions are keyed by `active_file` — same file continues the conversation, different file starts a new one.

**MCP Client** (Claude Code, etc.)

Copy `.mcp.json.example` to `.mcp.json` and update the paths:

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

## Customizing for Your Vault

The agent's system prompt describes your vault's folder layout and frontmatter conventions. **The default is tuned to the author's vault and almost certainly doesn't match yours.** An uncustomized prompt wastes tokens on failed lookups and gives worse results.

The installer copies `system_prompt.txt.example` to `system_prompt.txt` (gitignored). Edit it:

1. Update the **Vault Structure** section with your actual folder layout and frontmatter conventions
2. Adjust the **Interaction Logging** section if you don't use daily notes
3. Keep the **Choosing the Right Tool** and **Available Tools** sections as-is — they apply universally

## Available Tools

| Tool | Description |
|------|-------------|
| `search_vault` | Hybrid search (semantic + keyword) with mode and chunk_type filters |
| `read_file` | Read vault note content with pagination |
| `create_file` | Create a new note with optional frontmatter |
| `move_file` | Move a file within the vault |
| `batch_move_files` | Move multiple files at once |
| `append_to_file` | Append content to end of a file |
| `prepend_to_file` | Insert content after frontmatter |
| `replace_section` | Replace a markdown section by heading |
| `append_to_section` | Append content to a section |
| `list_files_by_frontmatter` | Find files by frontmatter field values |
| `update_frontmatter` | Set, remove, or append frontmatter fields |
| `batch_update_frontmatter` | Update frontmatter on multiple files |
| `find_backlinks` | Find files linking to a note |
| `find_outlinks` | Extract wikilinks from a file |
| `search_by_date_range` | Find files by created or modified date |
| `search_by_folder` | List files in a folder |
| `log_interaction` | Log interactions to daily notes |
| `save_preference` / `list_preferences` / `remove_preference` | Manage persistent user preferences |
| `web_search` | Search the web via DuckDuckGo |
| `transcribe_audio` | Transcribe audio embeds via Whisper API |

## Running as a Service

The installer can set up background services automatically. If you prefer manual setup:

<details>
<summary>Linux (systemd)</summary>

```bash
mkdir -p ~/.config/systemd/user

for f in services/systemd/*.service services/systemd/*.timer; do
    sed -e "s|__PROJECT_DIR__|$PWD|g" \
        -e "s|__VENV_PYTHON__|$PWD/.venv/bin/python|g" \
        -e "s|__INDEX_INTERVAL__|60|g" \
        "$f" > ~/.config/systemd/user/$(basename "$f")
done

systemctl --user daemon-reload
systemctl --user enable --now obsidian-tools-api
systemctl --user enable --now obsidian-tools-indexer-scheduler.timer
```

```bash
# Check status
systemctl --user status obsidian-tools-api

# View logs
journalctl --user -u obsidian-tools-api -f

# Restart
systemctl --user restart obsidian-tools-api
```

**Note:** To run services without being logged in: `sudo loginctl enable-linger $USER`

</details>

<details>
<summary>macOS (launchd)</summary>

```bash
for f in services/launchd/*.plist; do
    sed -e "s|__VENV_PYTHON__|$PWD/.venv/bin/python|g" \
        -e "s|__PROJECT_DIR__|$PWD|g" \
        -e "s|__USERNAME__|$(whoami)|g" \
        -e "s|__INDEX_INTERVAL_SEC__|3600|g" \
        "$f" > ~/Library/LaunchAgents/$(basename "$f")
done

launchctl load ~/Library/LaunchAgents/com.obsidian-tools.api.plist
launchctl load ~/Library/LaunchAgents/com.obsidian-tools.indexer.plist
```

```bash
# Check status
launchctl list | grep obsidian-tools

# View logs
tail -f ~/Library/Logs/obsidian-tools-api.log
```

</details>

<details>
<summary>Windows (Task Scheduler)</summary>

```powershell
$xml = (Get-Content services\taskscheduler\obsidian-tools-api.xml -Raw) `
    -replace '__VENV_PYTHON__', "$PWD\.venv\Scripts\python.exe" `
    -replace '__PROJECT_DIR__', "$PWD"
Register-ScheduledTask -TaskName "ObsidianToolsAPI" -Xml $xml

$xml = (Get-Content services\taskscheduler\obsidian-tools-indexer.xml -Raw) `
    -replace '__VENV_PYTHON__', "$PWD\.venv\Scripts\python.exe" `
    -replace '__PROJECT_DIR__', "$PWD" `
    -replace '__INDEX_INTERVAL__', '60'
Register-ScheduledTask -TaskName "ObsidianToolsIndexer" -Xml $xml
```

```powershell
# Check status
Get-ScheduledTask | Where-Object TaskName -like 'ObsidianTools*'
```

</details>

### Uninstall

```bash
./uninstall.sh        # macOS / Linux
.\uninstall.ps1       # Windows
```

Your `.env` and `.chroma_db/` are preserved.

<details>
<summary>Project structure</summary>

```
src/
├── mcp_server.py        # FastMCP server — registers tools from submodules
├── api_server.py        # FastAPI HTTP wrapper with session management
├── agent.py             # CLI chat agent with tool result continuation
├── config.py            # Shared configuration
├── hybrid_search.py     # Semantic + keyword search with RRF
├── search_vault.py      # Search interface
├── index_vault.py       # Structure-aware vault indexer
├── log_chat.py          # Daily note logging
├── services/
│   ├── chroma.py        # ChromaDB connection management
│   ├── compaction.py    # Tool message compaction for token management
│   └── vault.py         # Path resolution, response helpers, utilities
└── tools/
    ├── files.py         # File operations
    ├── frontmatter.py   # Frontmatter queries and updates
    ├── links.py         # Backlinks, outlinks, folder listing
    ├── preferences.py   # User preferences
    ├── search.py        # Vault search, web search
    ├── sections.py      # Section editing
    ├── utility.py       # Logging, date
    └── audio.py         # Audio transcription

plugin/                  # Obsidian chat sidebar (optional)
services/                # Service templates (systemd, launchd, Task Scheduler)
```

</details>

## License

MIT
