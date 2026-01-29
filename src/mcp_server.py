#!/usr/bin/env python3
"""MCP server exposing Obsidian vault tools."""

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from ddgs import DDGS

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


def _parse_frontmatter_date(date_value: any) -> datetime | None:
    """Parse a frontmatter Date field into a datetime object."""
    if date_value is None:
        return None

    date_str = str(date_value).strip()

    # Strip wikilink brackets if present: [[2023-08-11]] -> 2023-08-11
    if date_str.startswith("[[") and date_str.endswith("]]"):
        date_str = date_str[2:-2]

    # Try parsing as ISO date
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def _get_file_creation_time(file_path: Path) -> datetime | None:
    """Get file creation time, falling back to ctime if birthtime unavailable."""
    try:
        stat_result = file_path.stat()
        # Try birthtime first (available on macOS, some filesystems)
        if hasattr(stat_result, "st_birthtime"):
            return datetime.fromtimestamp(stat_result.st_birthtime)
        # Fall back to ctime (inode change time on Linux)
        return datetime.fromtimestamp(stat_result.st_ctime)
    except OSError:
        return None


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


def _do_update_frontmatter(
    path: str,
    field: str,
    parsed_value: any,
    operation: str,
) -> tuple[bool, str]:
    """Execute a single frontmatter update.

    Args:
        path: File path (relative or absolute).
        field: Frontmatter field to update.
        parsed_value: Already-parsed value to set.
        operation: "set", "remove", or "append".

    Returns:
        Tuple of (success, message).
    """
    try:
        file_path = _resolve_vault_path(path)
    except ValueError as e:
        return False, str(e)

    if not file_path.exists():
        return False, f"File not found: {path}"

    if not file_path.is_file():
        return False, f"Not a file: {path}"

    try:
        _update_file_frontmatter(
            file_path,
            field,
            parsed_value,
            remove=(operation == "remove"),
            append=(operation == "append"),
        )
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Update failed: {e}"

    if operation == "remove":
        return True, f"Removed '{field}' from {path}"
    elif operation == "append":
        return True, f"Appended to '{field}' in {path}"
    else:
        return True, f"Set '{field}' in {path}"


def _do_move_file(
    source: str,
    destination: str,
) -> tuple[bool, str]:
    """Execute a single file move.

    Args:
        source: Source file path.
        destination: Destination file path.

    Returns:
        Tuple of (success, message).
    """
    try:
        source_path = _resolve_vault_path(source)
    except ValueError as e:
        return False, str(e)

    if not source_path.exists():
        return False, f"Source file not found: {source}"

    if not source_path.is_file():
        return False, f"Source is not a file: {source}"

    try:
        dest_path = _resolve_vault_path(destination)
    except ValueError as e:
        return False, str(e)

    # Handle same source and destination
    if source_path == dest_path:
        vault_resolved = VAULT_PATH.resolve()
        rel_path = source_path.relative_to(vault_resolved)
        return True, f"Already at destination: {rel_path}"

    if dest_path.exists():
        return False, f"Destination already exists: {destination}"

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.move(str(source_path), str(dest_path))
    except Exception as e:
        return False, f"Move failed: {e}"

    vault_resolved = VAULT_PATH.resolve()
    src_rel = source_path.relative_to(vault_resolved)
    dest_rel = dest_path.relative_to(vault_resolved)
    return True, f"Moved {src_rel} to {dest_rel}"


def _format_batch_result(
    operation_name: str,
    results: list[tuple[bool, str]],
) -> str:
    """Format batch operation results into a summary string."""
    succeeded = [msg for success, msg in results if success]
    failed = [msg for success, msg in results if not success]

    parts = [f"Batch {operation_name}: {len(succeeded)} succeeded, {len(failed)} failed"]

    if succeeded:
        parts.append("\nSucceeded:")
        for msg in succeeded:
            parts.append(f"- {msg}")

    if failed:
        parts.append("\nFailed:")
        for msg in failed:
            parts.append(f"- {msg}")

    return "\n".join(parts)


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

    # Parse value - try JSON first, fall back to string
    parsed_value = value
    if value is not None:
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed_value = value  # Keep as string

    success, message = _do_update_frontmatter(path, field, parsed_value, operation)
    return message if success else f"Error: {message}"


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
    success, message = _do_move_file(source, destination)
    return message if success else f"Error: {message}"


@mcp.tool()
def batch_update_frontmatter(
    paths: list[str],
    field: str,
    value: str | None = None,
    operation: str = "set",
) -> str:
    """Apply a frontmatter update to multiple vault files.

    Args:
        paths: List of file paths (relative to vault or absolute).
        field: Frontmatter field name to update.
        value: Value to set. For lists, use JSON: '["tag1", "tag2"]'. Required for 'set'/'append'.
        operation: 'set' to add/modify, 'remove' to delete, 'append' to add to list.

    Returns:
        Summary of successes and failures.
    """
    if operation not in ("set", "remove", "append"):
        return f"Error: operation must be 'set', 'remove', or 'append', got '{operation}'"

    if operation in ("set", "append") and value is None:
        return f"Error: value is required for '{operation}' operation"

    if not paths:
        return "Error: paths list is empty"

    # Parse value once (same for all files)
    parsed_value = value
    if value is not None:
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed_value = value

    # Process each file
    results = []
    for path in paths:
        success, message = _do_update_frontmatter(path, field, parsed_value, operation)
        results.append((success, message))

    return _format_batch_result("update", results)


@mcp.tool()
def batch_move_files(
    moves: list[dict],
) -> str:
    """Move multiple vault files to new locations.

    Args:
        moves: List of move operations, each a dict with 'source' and 'destination' keys.
               Example: [{"source": "old/path.md", "destination": "new/path.md"}]

    Returns:
        Summary of successes and failures.
    """
    if not moves:
        return "Error: moves list is empty"

    results = []
    for i, move in enumerate(moves):
        if not isinstance(move, dict):
            results.append((False, f"Item {i}: expected dict, got {type(move).__name__}"))
            continue

        source = move.get("source")
        destination = move.get("destination")

        if not source:
            results.append((False, f"Item {i}: missing 'source' key"))
            continue
        if not destination:
            results.append((False, f"Item {i}: missing 'destination' key"))
            continue

        success, message = _do_move_file(source, destination)
        results.append((success, message))

    return _format_batch_result("move", results)


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


@mcp.tool()
def find_backlinks(note_name: str) -> str:
    """Find all vault files that contain wikilinks to a given note.

    Searches for both [[note_name]] and [[note_name|alias]] patterns.

    Args:
        note_name: The note name to search for (without brackets or .md extension).
                   Example: "CNP MVP" to find links like [[CNP MVP]] or [[CNP MVP|alias]].

    Returns:
        Newline-separated list of file paths (relative to vault) that link to the note,
        or a message if no backlinks found.
    """
    if not note_name or not note_name.strip():
        return "Error: note_name cannot be empty"

    note_name = note_name.strip()

    # Remove .md extension if provided
    if note_name.endswith(".md"):
        note_name = note_name[:-3]

    # Pattern: [[note_name]] or [[note_name|alias]]
    pattern = rf"\[\[{re.escape(note_name)}(?:\|[^\]]+)?\]\]"

    backlinks = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in _get_vault_files():
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if re.search(pattern, content, re.IGNORECASE):
            rel_path = md_file.relative_to(vault_resolved)
            backlinks.append(str(rel_path))

    if not backlinks:
        return f"No backlinks found to [[{note_name}]]"

    return "\n".join(sorted(backlinks))


@mcp.tool()
def search_by_date_range(
    start_date: str,
    end_date: str,
    date_type: str = "modified",
) -> str:
    """Find vault files within a date range.

    Args:
        start_date: Start of date range (inclusive), format: YYYY-MM-DD.
        end_date: End of date range (inclusive), format: YYYY-MM-DD.
        date_type: Which date to check - "created" (frontmatter Date field,
                   falls back to filesystem creation time) or "modified"
                   (filesystem modification time). Default: "modified".

    Returns:
        Newline-separated list of matching file paths (relative to vault),
        or a message if no files found.
    """
    if date_type not in ("created", "modified"):
        return f"Error: date_type must be 'created' or 'modified', got '{date_type}'"

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return f"Error: Invalid start_date format. Use YYYY-MM-DD, got '{start_date}'"

    try:
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return f"Error: Invalid end_date format. Use YYYY-MM-DD, got '{end_date}'"

    if start > end:
        return f"Error: start_date ({start_date}) is after end_date ({end_date})"

    matching = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in _get_vault_files():
        file_date = None

        if date_type == "created":
            # Try frontmatter Date first, fall back to filesystem creation time
            frontmatter = _extract_frontmatter(md_file)
            file_date = _parse_frontmatter_date(frontmatter.get("Date"))
            if file_date is None:
                file_date = _get_file_creation_time(md_file)
        else:  # modified
            try:
                mtime = md_file.stat().st_mtime
                file_date = datetime.fromtimestamp(mtime)
            except OSError:
                continue

        if file_date is None:
            continue

        # Compare date only (ignore time component)
        file_date_only = file_date.replace(hour=0, minute=0, second=0, microsecond=0)
        if start <= file_date_only <= end:
            rel_path = md_file.relative_to(vault_resolved)
            matching.append(str(rel_path))

    if not matching:
        return f"No files found with {date_type} date between {start_date} and {end_date}"

    return "\n".join(sorted(matching))


@mcp.tool()
def find_outlinks(path: str) -> str:
    """Extract all wikilinks from a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).

    Returns:
        Newline-separated list of linked note names (without brackets),
        or a message if no outlinks found.
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
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading file: {e}"

    # Pattern captures note name before optional |alias
    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    if not matches:
        return f"No outlinks found in {path}"

    # Deduplicate and sort
    unique_links = sorted(set(matches))
    return "\n".join(unique_links)


@mcp.tool()
def search_by_folder(
    folder: str,
    recursive: bool = False,
) -> str:
    """List all markdown files in a vault folder.

    Args:
        folder: Path to the folder (relative to vault or absolute).
        recursive: If True, include files in subfolders. Default: False.

    Returns:
        Newline-separated list of file paths (relative to vault),
        or a message if no files found.
    """
    try:
        folder_path = _resolve_vault_path(folder)
    except ValueError as e:
        return f"Error: {e}"

    if not folder_path.exists():
        return f"Error: Folder not found: {folder}"

    if not folder_path.is_dir():
        return f"Error: Not a folder: {folder}"

    # Use rglob for recursive, glob for non-recursive
    pattern_func = folder_path.rglob if recursive else folder_path.glob

    files = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        rel_path = md_file.relative_to(vault_resolved)
        files.append(str(rel_path))

    if not files:
        mode = "recursively " if recursive else ""
        return f"No markdown files found {mode}in {folder}"

    return "\n".join(sorted(files))


@mcp.tool()
def append_to_file(path: str, content: str) -> str:
    """Append content to the end of an existing vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to append to the file.

    Returns:
        Confirmation message or error.
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
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return f"Error appending to file: {e}"

    vault_resolved = VAULT_PATH.resolve()
    rel_path = file_path.relative_to(vault_resolved)
    return f"Appended to {rel_path}"


@mcp.tool()
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo.

    Args:
        query: Search query string.

    Returns:
        Formatted search results with title, URL, and snippet.
    """
    if not query or not query.strip():
        return "Error: query cannot be empty"

    try:
        results = DDGS().text(query, max_results=5)
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found."

    parts = []
    for r in results:
        title = r.get("title", "No title")
        url = r.get("href", "")
        snippet = r.get("body", "")
        parts.append(f"**{title}**\n{url}\n{snippet}")

    return "\n\n".join(parts)


if __name__ == "__main__":
    mcp.run()
