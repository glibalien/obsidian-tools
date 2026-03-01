"""Shared configuration for obsidian-tools."""

import logging
import os
from logging.handlers import RotatingFileHandler
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

# Fireworks API
FIREWORKS_BASE_URL = os.getenv(
    "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
)
FIREWORKS_MODEL = os.getenv(
    "FIREWORKS_MODEL",
    os.getenv("LLM_MODEL", "accounts/fireworks/models/gpt-oss-120b"),
)
LLM_MODEL = FIREWORKS_MODEL  # backward compat alias
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-v3")
VISION_MODEL = os.getenv(
    "VISION_MODEL", "accounts/fireworks/models/qwen3-vl-30b-a3b-instruct"
)
SUMMARIZE_MODEL = os.getenv("SUMMARIZE_MODEL", FIREWORKS_MODEL)

# Server configuration
API_PORT = int(os.getenv("API_PORT", "8000"))
MAX_SESSIONS = max(1, int(os.getenv("MAX_SESSIONS", "20")))
MAX_SESSION_MESSAGES = max(2, int(os.getenv("MAX_SESSION_MESSAGES", "50")))

# Indexer configuration
INDEX_INTERVAL = int(os.getenv("INDEX_INTERVAL", "60"))
INDEX_WORKERS = max(1, int(os.getenv("INDEX_WORKERS", "4")))

# Pagination defaults for path-only list tools
LIST_DEFAULT_LIMIT = 500
LIST_MAX_LIMIT = 2000

# Batch operations
BATCH_CONFIRM_THRESHOLD = 5  # Require confirmation above this many files

# Hybrid search
RRF_K = 60  # Reciprocal rank fusion constant
KEYWORD_LIMIT = 200  # Max chunks to scan for keyword matching

# Tool message compaction
COMPACTION_SNIPPET_LENGTH = 80  # find_notes semantic result snippet length
COMPACTION_CONTENT_PREVIEW_LENGTH = 100  # generic tool content preview length
MAX_SUMMARIZE_CHARS = 200_000  # Safety cap for content sent to summarize LLM
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", FIREWORKS_MODEL)
MAX_RESEARCH_TOPICS = 10  # Max topics to extract from a note
MAX_PAGE_CHARS = 50_000  # Safety cap for fetched web page content
PAGE_FETCH_TIMEOUT = 10  # Seconds per web page fetch

# Logging configuration
LOG_DIR = Path(os.getenv("LOG_DIR", str(VAULT_PATH / "logs"))).expanduser()
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))


def setup_logging(name: str) -> None:
    """Configure logging with both stderr and rotating file output.

    Args:
        name: Log file name without extension (e.g. "api", "agent").
    """
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # stderr handler (for journalctl)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(stderr_handler)

    # Rotating file handler (best-effort — fall back to stderr-only)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_DIR / f"{name}.log.md",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
    except OSError as e:
        root.warning(f"Could not set up file logging: {e} — using stderr only")
