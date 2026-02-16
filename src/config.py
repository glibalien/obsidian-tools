"""Shared configuration for obsidian-tools."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Vault path - where your Obsidian notes live
VAULT_PATH = Path(os.getenv("VAULT_PATH", "~/Documents/obsidian-vault")).expanduser()

# ChromaDB path - where the vector database lives
_chroma_env = os.getenv("CHROMA_PATH", "./.chroma_db")
if _chroma_env.startswith("./") or _chroma_env.startswith("../"):
    # Relative path - relative to project root, not cwd
    CHROMA_PATH = str(Path(__file__).parent.parent / _chroma_env)
else:
    CHROMA_PATH = str(Path(_chroma_env).expanduser())

# Directories to exclude when scanning vault
EXCLUDED_DIRS = {'.venv', '.chroma_db', '.trash', '.obsidian', '.git'}

# Preferences file location (user preferences stored as bullet points)
PREFERENCES_FILE = VAULT_PATH / "Preferences.md"

# Attachments directory (where Obsidian stores audio, images, etc.)
ATTACHMENTS_DIR = VAULT_PATH / "Attachments"

# Model configuration
FIREWORKS_MODEL = os.getenv(
    "FIREWORKS_MODEL",
    os.getenv("LLM_MODEL", "accounts/fireworks/models/deepseek-v3p1"),
)
LLM_MODEL = FIREWORKS_MODEL  # backward compat alias
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-v3")

# Server configuration
API_PORT = int(os.getenv("API_PORT", "8000"))

# Indexer configuration
INDEX_INTERVAL = int(os.getenv("INDEX_INTERVAL", "60"))
