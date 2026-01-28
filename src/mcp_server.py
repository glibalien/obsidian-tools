#!/usr/bin/env python3
"""MCP server exposing Obsidian vault tools."""

import json
import re
import shutil
import sys
from pathlib import Path

import yaml

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


def _get_vault_files() -> list[Path]:
    """Get all markdown files in vault, excluding tooling directories."""
    files = []
    for md_file in VAULT_PATH.rglob("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        files.append(md_file)
    return files


def _extract_frontmatter(file_path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file.

    Returns:
        Dictionary of frontmatter fields, or empty dict if none/invalid.
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return {}

    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _update_file_frontmatter(
    file_path: Path,
    field: str,
    value: any,
    remove: bool = False,
    append: bool = False,
) -> None:
    """Update frontmatter in a file, preserving body content.

    Args:
        file_path: Path to the markdown file.
        field: Frontmatter field to update.
        value: Value to set (ignored if remove=True).
        remove: If True, remove the field instead of setting it.
        append: If True, append value to existing list field.

    Raises:
        ValueError: If file has no frontmatter and remove=True.
        ValueError: If append=True but field is not a list.
    """
    content = file_path.read_text(encoding="utf-8")

    # Parse existing frontmatter and body
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end():]
    else:
        if remove:
            raise ValueError("File has no frontmatter")
        frontmatter = {}
        body = content

    # Update frontmatter
    if remove:
        if field not in frontmatter:
            raise ValueError(f"Field '{field}' not found in frontmatter")
        del frontmatter[field]
    elif append:
        existing = frontmatter.get(field, [])
        if not isinstance(existing, list):
            raise ValueError(f"Cannot append to non-list field '{field}'")
        if value not in existing:
            existing.append(value)
        frontmatter[field] = existing
    else:
        frontmatter[field] = value

    # Rebuild file
    if frontmatter:
        new_yaml = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
        new_content = f"---\n{new_yaml}---\n{body}"
    else:
        # All fields removed, no frontmatter needed
        new_content = body

    file_path.write_text(new_content, encoding="utf-8")


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


@mcp.tool()
def list_files_by_frontmatter(
    field: str,
    value: str,
    match_type: str = "contains",
) -> str:
    """Find vault files matching frontmatter criteria.

    Args:
        field: Frontmatter field name (e.g., 'tags', 'company', 'project').
        value: Value to match against.
        match_type: How to match - 'contains' (value in list), 'equals' (exact match).

    Returns:
        Newline-separated list of matching file paths (relative to vault).
    """
    if match_type not in ("contains", "equals"):
        return f"Error: match_type must be 'contains' or 'equals', got '{match_type}'"

    matching = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in _get_vault_files():
        frontmatter = _extract_frontmatter(md_file)
        field_value = frontmatter.get(field)

        if field_value is None:
            continue

        matches = False
        if match_type == "contains":
            if isinstance(field_value, list):
                matches = value in field_value
            elif isinstance(field_value, str):
                matches = value in field_value
        elif match_type == "equals":
            matches = field_value == value

        if matches:
            rel_path = md_file.resolve().relative_to(vault_resolved)
            matching.append(str(rel_path))

    if not matching:
        return f"No files found where {field} {match_type} '{value}'"

    return "\n".join(sorted(matching))


@mcp.tool()
def update_frontmatter(
    path: str,
    field: str,
    value: str | None = None,
    operation: str = "set",
) -> str:
    """Update frontmatter on a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        field: Frontmatter field name to update.
        value: Value to set. For lists, use JSON: '["tag1", "tag2"]'. Required for 'set'/'append'.
        operation: 'set' to add/modify, 'remove' to delete, 'append' to add to list.

    Returns:
        Confirmation message or error.
    """
    if operation not in ("set", "remove", "append"):
        return f"Error: operation must be 'set', 'remove', or 'append', got '{operation}'"

    if operation in ("set", "append") and value is None:
        return f"Error: value is required for '{operation}' operation"

    try:
        file_path = _resolve_vault_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if not file_path.exists():
        return f"Error: File not found: {path}"

    if not file_path.is_file():
        return f"Error: Not a file: {path}"

    # Parse value - try JSON first, fall back to string
    parsed_value = value
    if value is not None:
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed_value = value  # Keep as string

    try:
        _update_file_frontmatter(
            file_path,
            field,
            parsed_value,
            remove=(operation == "remove"),
            append=(operation == "append"),
        )
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error updating frontmatter: {e}"

    if operation == "remove":
        return f"Removed '{field}' from {path}"
    elif operation == "append":
        return f"Appended {value!r} to '{field}' in {path}"
    else:
        return f"Set '{field}' to {value!r} in {path}"


@mcp.tool()
def move_file(
    source: str,
    destination: str,
) -> str:
    """Move a vault file to a different location within the vault.

    Args:
        source: Current path of the file (relative to vault or absolute).
        destination: New path for the file (relative to vault or absolute).
                    Parent directories will be created if they don't exist.

    Returns:
        Confirmation message or error.
    """
    # Validate source path
    try:
        source_path = _resolve_vault_path(source)
    except ValueError as e:
        return f"Error: {e}"

    if not source_path.exists():
        return f"Error: Source file not found: {source}"

    if not source_path.is_file():
        return f"Error: Source is not a file: {source}"

    # Validate destination path
    try:
        dest_path = _resolve_vault_path(destination)
    except ValueError as e:
        return f"Error: {e}"

    if dest_path.exists():
        return f"Error: Destination already exists: {destination}"

    # Create parent directories if needed
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Move the file
    try:
        shutil.move(str(source_path), str(dest_path))
    except Exception as e:
        return f"Error moving file: {e}"

    # Return relative paths for cleaner output
    vault_resolved = VAULT_PATH.resolve()
    src_rel = source_path.relative_to(vault_resolved)
    dest_rel = dest_path.relative_to(vault_resolved)
    return f"Moved {src_rel} to {dest_rel}"


@mcp.tool()
def create_file(
    path: str,
    content: str = "",
    frontmatter: str | None = None,
) -> str:
    """Create a new markdown note in the vault.

    Args:
        path: Path for the new file (relative to vault or absolute).
              Parent directories will be created if they don't exist.
        content: The body content of the note (markdown).
        frontmatter: Optional YAML frontmatter as JSON string, e.g., '{"tags": ["meeting"]}'.
                    Will be converted to YAML and wrapped in --- delimiters.

    Returns:
        Confirmation message or error.
    """
    # Validate path
    try:
        file_path = _resolve_vault_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if file_path.exists():
        return f"Error: File already exists: {path}"

    # Parse frontmatter if provided
    frontmatter_yaml = ""
    if frontmatter:
        try:
            fm_dict = json.loads(frontmatter)
            frontmatter_yaml = yaml.dump(fm_dict, default_flow_style=False, allow_unicode=True)
        except json.JSONDecodeError as e:
            return f"Error: Invalid frontmatter JSON: {e}"

    # Build file content
    if frontmatter_yaml:
        file_content = f"---\n{frontmatter_yaml}---\n\n{content}"
    else:
        file_content = content

    # Create parent directories if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the file
    try:
        file_path.write_text(file_content, encoding="utf-8")
    except Exception as e:
        return f"Error writing file: {e}"

    vault_resolved = VAULT_PATH.resolve()
    rel_path = file_path.relative_to(vault_resolved)
    return f"Created {rel_path}"


if __name__ == "__main__":
    mcp.run()
