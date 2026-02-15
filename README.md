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

![Architecture diagram](obsidian-tools-architecture.svg)

The Obsidian plugin provides a chat sidebar that connects to the API server. The API server wraps the LLM agent, which uses MCP tools to search and manage your vault.

## Installation

### Requirements

- **Python 3.11, 3.12, or 3.13** (not 3.14 — `onnxruntime` doesn't have wheels for 3.14 yet)

### Quick Install (recommended)

The install script handles Python detection, virtual environment setup, dependency installation, `.env` configuration, and background service installation:

```bash
# Clone the repository
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
3. Walk you through `.env` configuration
4. Optionally install background services (API server + vault indexer)
5. Optionally run the initial vault index

To uninstall services: `./uninstall.sh` (macOS/Linux) or `.\uninstall.ps1` (Windows).

### Manual Install

<details>
<summary>Click to expand manual installation steps</summary>

#### macOS Users (Homebrew)

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

#### Clone and Set Up

```bash
# Clone the repository
git clone https://github.com/glibalien/obsidian-tools.git
cd obsidian-tools

# If using pyenv, it will automatically use 3.12.8 (from .python-version)
# Verify your Python version:
python --version  # Should show Python 3.12.x

# IMPORTANT: If using pyenv, create the venv with the real binary, not the shim:
$(pyenv which python3.12) -m venv .venv

# Otherwise:
python -m venv .venv

source .venv/bin/activate
pip install -r requirements.txt
```

#### Configure `.env`

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```
VAULT_PATH=~/Documents/your-vault-name
CHROMA_PATH=./.chroma_db
FIREWORKS_API_KEY=your-api-key-here
FIREWORKS_MODEL=accounts/fireworks/models/deepseek-v3p1
API_PORT=8000
INDEX_INTERVAL=60
```

| Variable | Description |
|----------|-------------|
| `VAULT_PATH` | Path to your Obsidian vault |
| `CHROMA_PATH` | Where to store the ChromaDB database (relative or absolute) |
| `FIREWORKS_API_KEY` | API key from [Fireworks AI](https://fireworks.ai/) (required for the chat agent and audio transcription) |
| `FIREWORKS_MODEL` | Fireworks model ID (default: DeepSeek V3.1) |
| `API_PORT` | Port for the HTTP API server (default: `8000`) |
| `INDEX_INTERVAL` | How often the vault indexer runs, in minutes (default: `60`) |

</details>

## MCP Client Configuration

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
# Start a conversation (with active file context)
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Summarize this note", "active_file": "Projects/Marketing.md"}'

# Continue the conversation (same active_file = same session)
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the action items?", "active_file": "Projects/Marketing.md"}'
```

Sessions are keyed by `active_file` — requests with the same file continue the conversation, while a different file starts a fresh session. Switching back to a previous file resumes that file's session. Tool results in the conversation history are automatically compacted to lightweight stubs to prevent token explosion.

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

The install script (`./install.sh` or `.\install.ps1`) can set up background services automatically. Service templates are in the `services/` directory.

If you prefer to install services manually, expand the section for your platform below.

<details>
<summary>Linux (systemd) — manual setup</summary>

The template files in `services/systemd/` use `__PROJECT_DIR__` and `__VENV_PYTHON__` placeholders. Replace them with absolute paths before installing:

```bash
mkdir -p ~/.config/systemd/user

# Replace placeholders and copy (adjust paths to your setup)
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

</details>

<details>
<summary>macOS (launchd) — manual setup</summary>

The template files in `services/launchd/` use `__USERNAME__`, `__PROJECT_DIR__`, `__VENV_PYTHON__`, and `__INDEX_INTERVAL_SEC__` placeholders. Replace them before installing:

```bash
# Replace placeholders and copy (adjust paths to your setup)
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

</details>

<details>
<summary>Windows (Task Scheduler) — manual setup</summary>

The template files in `services/taskscheduler/` use `__VENV_PYTHON__`, `__PROJECT_DIR__`, and `__INDEX_INTERVAL__` placeholders. Edit the XML files to replace these with absolute paths, then register them:

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

Useful commands:

```powershell
# Check status
Get-ScheduledTask | Where-Object TaskName -like 'ObsidianTools*'

# Start/stop
Start-ScheduledTask -TaskName ObsidianToolsAPI
Stop-ScheduledTask -TaskName ObsidianToolsAPI

# Remove
Unregister-ScheduledTask -TaskName ObsidianToolsAPI -Confirm:$false
```

</details>

### Uninstall

To remove background services and optionally the virtual environment:

```bash
# macOS / Linux
./uninstall.sh

# Windows (PowerShell)
.\uninstall.ps1
```

Your `.env` and `.chroma_db/` are preserved by the uninstaller.

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

## Customizing the Agent System Prompt

**Important:** The system prompt is loaded from `system_prompt.txt` at startup and is tuned to the author's vault structure. The default describes specific folders (`Daily Notes/`, `Meetings/`, `Projects/`, etc.) and frontmatter conventions (`category: meeting`, `category: project`, etc.) that almost certainly don't match your vault.

**Why this matters:**
- **Token waste:** A mismatched system prompt causes the agent to make unnecessary tool calls — searching for folders and frontmatter fields that don't exist in your vault, burning tokens on failed lookups.
- **Poor results:** The agent will try strategies that don't apply (e.g., filtering by a `category` field you don't use), leading to worse answers and more back-and-forth.

**What to change:**
1. Copy `system_prompt.txt.example` to `system_prompt.txt` (the installer does this automatically)
2. Update the **Vault Structure** section to describe your actual folder layout and frontmatter conventions
3. Adjust the **Interaction Logging** section if you don't use daily notes or want a different logging format
4. Keep the **Guidelines** and **Tool Orchestration** sections as-is — they apply universally

`system_prompt.txt` is gitignored so your customizations won't be overwritten by updates. If the file is missing, the agent falls back to `system_prompt.txt.example` with a warning.

## Project Structure

```
src/
├── mcp_server.py        # FastMCP server - registers tools from submodules
├── api_server.py        # FastAPI HTTP wrapper for the agent
├── agent.py             # CLI chat agent
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
├── launchd/             # macOS launchd plist files
│   ├── com.obsidian-tools.api.plist
│   └── com.obsidian-tools.indexer.plist
└── taskscheduler/       # Windows Task Scheduler XML files
    ├── obsidian-tools-api.xml
    └── obsidian-tools-indexer.xml

plugin/                  # Obsidian chat plugin (optional)
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
