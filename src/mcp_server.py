#!/usr/bin/env python3
"""MCP server exposing Obsidian vault tools."""

import sys
from pathlib import Path

# Ensure src/ is on the import path when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from config import EXCLUDED_DIRS, VAULT_PATH
from log_chat import log_chat
from search_vault import search_results

mcp = FastMCP("obsidian-tools")


def _resolve_vault_path(path: str) -> Path:
    """Resolve a path ensuring it stays within the vault.

    Args:
        path: Relative path (from vault root) or absolute path.

    Returns:
        Resolved absolute Path within the vault.

    Raises:
        ValueError: If path escapes vault or is in excluded directory.
    """
    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
        resolved = (VAULT_PATH / path).resolve()

    # Security: ensure path is within vault
    try:
        resolved.relative_to(VAULT_PATH.resolve())
    except ValueError:
        raise ValueError(f"Path must be within vault: {VAULT_PATH}")

    # Block excluded directories
    if any(excluded in resolved.parts for excluded in EXCLUDED_DIRS):
        raise ValueError("Cannot access excluded directory")

    return resolved


@mcp.tool()
def search_vault(query: str, n_results: int = 5, mode: str = "hybrid") -> str:
    """Search the Obsidian vault using hybrid search (semantic + keyword).

    Args:
        query: Natural language search query.
        n_results: Number of results to return (default 5).
        mode: Search mode - "hybrid" (default), "semantic", or "keyword".

    Returns:
        Formatted search results with source file and content excerpt.
    """
    try:
        results = search_results(query, n_results, mode)
    except Exception as e:
        return f"Search failed: {e}\nIs the vault indexed? Run: python src/index_vault.py"

    if not results:
        return "No results found."

    parts = []
    for r in results:
        parts.append(f"--- {r['source']} ---\n{r['content']}")
    return "\n\n".join(parts)


@mcp.tool()
def log_interaction(
    task_description: str,
    query: str,
    summary: str,
    files: list[str] | None = None,
    full_response: str | None = None,
) -> str:
    """Log a Claude interaction to today's Obsidian daily note.

    Args:
        task_description: Brief description of the task performed.
        query: The original query or prompt.
        summary: Summary of the response (use 'n/a' if full_response provided).
        files: List of referenced file paths (optional).
        full_response: Full response text for conversational logs (optional).

    Returns:
        Confirmation message with the daily note path.
    """
    try:
        path = log_chat(task_description, query, summary, files, full_response)
    except Exception as e:
        return f"Logging failed: {e}"

    return f"Logged to {path}"


@mcp.tool()
def read_file(path: str) -> str:
    """Read the full content of a vault note.

    Args:
        path: Path to the note, either relative to vault root or absolute.

    Returns:
        The full text content of the note.
    """
    try:
        file_path = _resolve_vault_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not file_path.exists():
        return f"Error: File not found: {path}"

    if not file_path.is_file():
        return f"Error: Not a file: {path}"

    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


if __name__ == "__main__":
    mcp.run()
