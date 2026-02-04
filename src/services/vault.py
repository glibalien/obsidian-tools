"""Vault service - path resolution, file scanning, and utility functions."""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from config import EXCLUDED_DIRS, VAULT_PATH


# =============================================================================
# Response Envelope Helpers
# =============================================================================


def ok(data: str | dict | list | None = None, **kwargs) -> str:
    """Return a success JSON response.

    Args:
        data: Primary response data (string message, dict, or list).
        **kwargs: Additional fields to include in the response.

    Returns:
        JSON string with {"success": true, ...}.
    """
    response = {"success": True}
    if data is not None:
        if isinstance(data, str):
            response["message"] = data
        elif isinstance(data, (dict, list)):
            response["data"] = data
    response.update(kwargs)
    return json.dumps(response)


def err(message: str, **kwargs) -> str:
    """Return an error JSON response.

    Args:
        message: Error description.
        **kwargs: Additional fields to include in the response.

    Returns:
        JSON string with {"success": false, "error": ...}.
    """
    response = {"success": False, "error": message}
    response.update(kwargs)
    return json.dumps(response)


# =============================================================================
# Path Resolution
# =============================================================================


def resolve_vault_path(path: str) -> Path:
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


def resolve_file(path: str) -> tuple[Path | None, str | None]:
    """Resolve and validate a file path within the vault.

    Combines path resolution with existence and file-type checks.

    Args:
        path: Relative path (from vault root) or absolute path.

    Returns:
        Tuple of (resolved_path, None) on success, or (None, error_message) on failure.
    """
    try:
        file_path = resolve_vault_path(path)
    except ValueError as e:
        return None, str(e)

    if not file_path.exists():
        return None, f"File not found: {path}"

    if not file_path.is_file():
        return None, f"Not a file: {path}"

    return file_path, None


def resolve_dir(path: str) -> tuple[Path | None, str | None]:
    """Resolve and validate a directory path within the vault.

    Args:
        path: Relative path (from vault root) or absolute path.

    Returns:
        Tuple of (resolved_path, None) on success, or (None, error_message) on failure.
    """
    try:
        dir_path = resolve_vault_path(path)
    except ValueError as e:
        return None, str(e)

    if not dir_path.exists():
        return None, f"Folder not found: {path}"

    if not dir_path.is_dir():
        return None, f"Not a folder: {path}"

    return dir_path, None


def get_relative_path(absolute_path: Path) -> str:
    """Get a path relative to the vault root.

    Args:
        absolute_path: Absolute path to a file within the vault.

    Returns:
        String path relative to VAULT_PATH.
    """
    return str(absolute_path.relative_to(VAULT_PATH.resolve()))


# =============================================================================
# File Scanning
# =============================================================================


def get_vault_files() -> list[Path]:
    """Get all markdown files in vault, excluding tooling directories."""
    files = []
    for md_file in VAULT_PATH.rglob("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        files.append(md_file)
    return files


# =============================================================================
# Frontmatter Operations
# =============================================================================


def extract_frontmatter(file_path: Path) -> dict:
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


def parse_frontmatter_date(date_value) -> datetime | None:
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


def get_file_creation_time(file_path: Path) -> datetime | None:
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


def update_file_frontmatter(
    file_path: Path,
    field: str,
    value,
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


def do_update_frontmatter(
    path: str,
    field: str,
    parsed_value,
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
        file_path = resolve_vault_path(path)
    except ValueError as e:
        return False, str(e)

    if not file_path.exists():
        return False, f"File not found: {path}"

    if not file_path.is_file():
        return False, f"Not a file: {path}"

    try:
        update_file_frontmatter(
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


# =============================================================================
# Section Operations
# =============================================================================


# Precompiled patterns for section parsing
_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")
_HEADING_PATTERN = re.compile(r"^(#+)\s+(.*)$")


def find_section(
    lines: list[str], heading: str
) -> tuple[int | None, int | None, str | None]:
    """Find a markdown section by heading.

    Locates a heading by case-insensitive exact match and determines the section
    boundaries. A section extends from the heading line to the next heading of
    same or higher level, or end of file. Ignores headings inside code blocks.

    Args:
        lines: File content split into lines.
        heading: Full heading text including # symbols (e.g., "## Meeting Notes").

    Returns:
        Tuple of (section_start, section_end, None) on success, where section_start
        is the line index of the heading and section_end is the line index where
        the section ends (exclusive). Returns (None, None, error_message) on failure.
    """
    # Parse heading to extract level and text
    heading_match = re.match(r"^(#+)\s+(.*)$", heading.strip())
    if not heading_match:
        return None, None, f"Invalid heading format: {heading}"

    target_level = len(heading_match.group(1))
    target_text = heading_match.group(2).lower()

    # Find matching headings, tracking code blocks
    matches = []  # List of (line_index, line_content)
    in_code_block = False

    for i, line in enumerate(lines):
        # Check for code fence toggle
        if _FENCE_PATTERN.match(line):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Check if this line is a matching heading
        line_heading_match = _HEADING_PATTERN.match(line)
        if line_heading_match:
            line_level = len(line_heading_match.group(1))
            line_text = line_heading_match.group(2).lower()
            if line_level == target_level and line_text == target_text:
                matches.append((i, line))

    # Validate matches
    if not matches:
        return None, None, f"Heading not found: {heading}"

    if len(matches) > 1:
        line_nums = ", ".join(str(m[0] + 1) for m in matches)  # 1-indexed
        return None, None, f"Multiple headings match '{heading}': found at lines {line_nums}"

    # Find section boundary
    section_start = matches[0][0]
    section_end = len(lines)  # Default to end of file

    # Scan for next heading of same or higher level (tracking code blocks again)
    in_code_block = False
    for i in range(section_start + 1, len(lines)):
        line = lines[i]

        if _FENCE_PATTERN.match(line):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        line_heading_match = _HEADING_PATTERN.match(line)
        if line_heading_match:
            line_level = len(line_heading_match.group(1))
            if line_level <= target_level:
                section_end = i
                break

    return section_start, section_end, None


# =============================================================================
# File Move Operations
# =============================================================================


def do_move_file(
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
        source_path = resolve_vault_path(source)
    except ValueError as e:
        return False, str(e)

    if not source_path.exists():
        return False, f"Source file not found: {source}"

    if not source_path.is_file():
        return False, f"Source is not a file: {source}"

    try:
        dest_path = resolve_vault_path(destination)
    except ValueError as e:
        return False, str(e)

    # Handle same source and destination
    if source_path == dest_path:
        rel_path = get_relative_path(source_path)
        return True, f"Already at destination: {rel_path}"

    if dest_path.exists():
        return False, f"Destination already exists: {destination}"

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.move(str(source_path), str(dest_path))
    except Exception as e:
        return False, f"Move failed: {e}"

    src_rel = get_relative_path(source_path)
    dest_rel = get_relative_path(dest_path)
    return True, f"Moved {src_rel} to {dest_rel}"


# =============================================================================
# Batch Operations
# =============================================================================


def format_batch_result(
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
