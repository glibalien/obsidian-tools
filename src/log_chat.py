#!/usr/bin/env python3
"""Log Claude Code interactions to the daily note in Obsidian vault."""

import re
import sys
from datetime import datetime
from pathlib import Path

from config import VAULT_PATH, EXCLUDED_DIRS


def get_vault_note_names() -> set[str]:
    """Scan the vault and return a set of all note names (without .md extension)."""
    note_names = set()
    for md_file in VAULT_PATH.rglob("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        note_names.add(md_file.stem)
    return note_names


def add_wikilinks(text: str, note_names: set[str]) -> str:
    """Replace references to known notes with wikilinks."""
    if not note_names:
        return text

    # Sort by length descending to match longer names first
    sorted_names = sorted(note_names, key=len, reverse=True)

    for name in sorted_names:
        # Skip very short names (likely false positives)
        if len(name) < 3:
            continue

        # Match whole words, not already in wikilinks or backticks
        pattern = r'(?<!\[\[)(?<!`)\b' + re.escape(name) + r'\b(?!\]\])(?!`)'
        replacement = f'[[{name}]]'
        text = re.sub(pattern, replacement, text)

    return text


def get_daily_note_path() -> Path:
    """Get path to today's daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    return VAULT_PATH / "Daily Notes" / f"{today}.md"


def ensure_daily_note_exists(path: Path) -> str:
    """Create daily note if it doesn't exist, return its content."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        path.write_text(f"# {today}\n\n## Claude Chats\n\n")
    
    content = path.read_text()
    
    # Add Claude Chats header if missing
    if "## Claude Chats" not in content:
        content += "\n## Claude Chats\n\n"
    
    return content


def format_entry(
    task_description: str,
    query: str,
    summary: str,
    files: list[str] | None,
    full_response: str | None
) -> str:
    """Format a log entry."""
    time_now = datetime.now().strftime("%H:%M")
    files_str = "\n".join(f"- `{f}`" for f in files) if files else "- None"

    if full_response:
        note_names = get_vault_note_names()
        full_response = add_wikilinks(full_response, note_names)
        return f"""### {time_now} - {task_description}

**Query:** {query}

**Response:**

{full_response}

**Files referenced:**
{files_str}

---

"""
    else:
        return f"""### {time_now} - {task_description}

**Query:** {query}

**Summary:** {summary}

**Files referenced:**
{files_str}

---

"""


def insert_entry(content: str, entry: str) -> str:
    """Insert entry after the Claude Chats header."""
    marker = "## Claude Chats\n"
    pos = content.find(marker)
    
    if pos != -1:
        insert_pos = pos + len(marker)
        # Skip existing newlines after header
        while insert_pos < len(content) and content[insert_pos] == '\n':
            insert_pos += 1
        content = content[:insert_pos] + "\n" + entry + content[insert_pos:]
    else:
        content += entry
    
    return content


def log_chat(
    task_description: str,
    query: str,
    summary: str,
    files: list[str] | None = None,
    full_response: str | None = None
) -> None:
    """Log an interaction to today's daily note."""
    daily_note_path = get_daily_note_path()
    content = ensure_daily_note_exists(daily_note_path)
    
    entry = format_entry(task_description, query, summary, files, full_response)
    content = insert_entry(content, entry)
    
    daily_note_path.write_text(content)
    print(f"Logged to {daily_note_path}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python log_chat.py 'task description' 'query' 'summary' 'file1,file2' ['full_response']")
        print("  - Use 'none' for files if no files referenced")
        print("  - full_response is optional; when provided, logs full conversational output")
        sys.exit(1)
    
    files = sys.argv[4].split(",") if len(sys.argv) > 4 and sys.argv[4] != "none" else None
    full_response = sys.argv[5] if len(sys.argv) > 5 else None
    log_chat(sys.argv[1], sys.argv[2], sys.argv[3], files, full_response)
