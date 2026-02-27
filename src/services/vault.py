"""Vault service - path resolution, file scanning, and utility functions."""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from pydantic import BaseModel

from config import BATCH_CONFIRM_THRESHOLD, EXCLUDED_DIRS, VAULT_PATH

logger = logging.getLogger(__name__)


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


def resolve_vault_path(path: str, base_path: Path | None = None) -> Path:
    """Resolve a path ensuring it stays within an allowed directory.

    Args:
        path: Relative path (from base root) or absolute path.
        base_path: Base directory to resolve against and constrain to.
            Defaults to VAULT_PATH.

    Returns:
        Resolved absolute Path within the base directory.

    Raises:
        ValueError: If path escapes base directory or is in excluded directory.
    """
    base = base_path if base_path is not None else VAULT_PATH

    if Path(path).is_absolute():
        resolved = Path(path).resolve()
    else:
        resolved = (base / path).resolve()

    # Security: ensure path is within base directory
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"Path must be within vault: {base}")

    # Block excluded directories
    if any(excluded in resolved.parts for excluded in EXCLUDED_DIRS):
        raise ValueError("Cannot access excluded directory")

    return resolved


def resolve_file(path: str, base_path: Path | None = None) -> tuple[Path | None, str | None]:
    """Resolve and validate a file path within the vault.

    Combines path resolution with existence and file-type checks.

    Args:
        path: Relative path (from base root) or absolute path.
        base_path: Base directory to resolve against. Defaults to VAULT_PATH.

    Returns:
        Tuple of (resolved_path, None) on success, or (None, error_message) on failure.
    """
    try:
        file_path = resolve_vault_path(path, base_path=base_path)
    except ValueError as e:
        return None, str(e)

    if not file_path.exists():
        return None, f"File not found: {path}"

    if not file_path.is_file():
        return None, f"Not a file: {path}"

    return file_path, None


def resolve_dir(path: str, base_path: Path | None = None) -> tuple[Path | None, str | None]:
    """Resolve and validate a directory path within the vault.

    Args:
        path: Relative path (from base root) or absolute path.
        base_path: Base directory to resolve against. Defaults to VAULT_PATH.

    Returns:
        Tuple of (resolved_path, None) on success, or (None, error_message) on failure.
    """
    try:
        dir_path = resolve_vault_path(path, base_path=base_path)
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


def get_vault_files(vault_path: Path | None = None) -> list[Path]:
    """Get all markdown files in vault, excluding tooling directories.

    Args:
        vault_path: Optional path to vault. Defaults to VAULT_PATH from config.

    Returns:
        List of Path objects for all markdown files in the vault.
    """
    vault = vault_path if vault_path is not None else VAULT_PATH
    files = []
    for md_file in vault.rglob("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        files.append(md_file)
    return files


def get_vault_note_names(vault_path: Path | None = None) -> set[str]:
    """Get set of note names (without .md extension) from vault.

    Args:
        vault_path: Optional path to vault. Defaults to VAULT_PATH from config.

    Returns:
        Set of note names without the .md extension.
    """
    return {f.stem for f in get_vault_files(vault_path)}


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
    except OSError as e:
        logger.debug("Could not read frontmatter from %s: %s", file_path, e)
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


# =============================================================================
# Frontmatter Matching / Targeting Utilities
# =============================================================================


class FilterCondition(BaseModel):
    """A single frontmatter filter condition."""

    field: str
    value: str = ""
    match_type: str = "contains"


VALID_MATCH_TYPES = ("contains", "equals", "missing", "exists", "not_contains", "not_equals")
NO_VALUE_MATCH_TYPES = ("missing", "exists")

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _get_field_ci(frontmatter: dict, field: str):
    """Get a frontmatter value by case-insensitive field name."""
    # Try exact match first (fast path)
    value = frontmatter.get(field)
    if value is not None:
        return value
    # Fall back to case-insensitive scan
    field_lower = field.lower()
    for key, val in frontmatter.items():
        if key.lower() == field_lower:
            return val
    return None


def _strip_wikilinks(text: str) -> str:
    """Strip wikilink brackets: '[[Foo|alias]]' → 'Foo', '[[Bar]]' → 'Bar'."""
    return _WIKILINK_RE.sub(r"\1", text)


def _matches_field(frontmatter: dict, field: str, value: str, match_type: str) -> bool:
    """Check if a frontmatter dict matches a single field condition.

    Both field names and values are compared case-insensitively.
    Wikilink brackets are stripped before comparison so "Foo" matches "[[Foo]]".
    Non-string/non-list values are converted to strings before comparison.

    Match types:
        contains/equals: positive matching (field must exist and match).
        missing: field must be absent (value ignored).
        exists: field must be present (value ignored).
        not_contains/not_equals: field absent OR doesn't match.
    """
    field_value = _get_field_ci(frontmatter, field)

    # Existence/absence checks — value is ignored
    if match_type == "missing":
        return field_value is None
    if match_type == "exists":
        return field_value is not None

    # Field absent: positive matches fail, negative matches succeed
    if field_value is None:
        return match_type in ("not_contains", "not_equals")

    value_lower = _strip_wikilinks(value).lower()

    if match_type == "contains":
        if isinstance(field_value, list):
            return any(value_lower in _strip_wikilinks(str(item)).lower() for item in field_value)
        return value_lower in _strip_wikilinks(str(field_value)).lower()
    elif match_type == "equals":
        if isinstance(field_value, list):
            return any(_strip_wikilinks(str(item)).lower() == value_lower for item in field_value)
        return _strip_wikilinks(str(field_value)).lower() == value_lower
    elif match_type == "not_contains":
        if isinstance(field_value, list):
            return not any(value_lower in _strip_wikilinks(str(item)).lower() for item in field_value)
        return value_lower not in _strip_wikilinks(str(field_value)).lower()
    elif match_type == "not_equals":
        if isinstance(field_value, list):
            return not any(_strip_wikilinks(str(item)).lower() == value_lower for item in field_value)
        return _strip_wikilinks(str(field_value)).lower() != value_lower
    return False


def _validate_filters(
    filters: list[FilterCondition] | None,
) -> tuple[list[dict], str | None]:
    """Validate filter conditions and convert to plain dicts.

    Returns:
        (filter_dicts, error_message). error_message is None on success.
    """
    if not filters:
        return [], None

    result = []
    for i, f in enumerate(filters):
        d = f.model_dump() if isinstance(f, FilterCondition) else dict(f)
        if "field" not in d:
            return [], f"filters[{i}] must have a 'field' key"
        mt = d.get("match_type", "contains")
        if mt not in VALID_MATCH_TYPES:
            return [], (
                f"filters[{i}] match_type must be one of {VALID_MATCH_TYPES}, "
                f"got '{mt}'"
            )
        if mt not in NO_VALUE_MATCH_TYPES and not d.get("value"):
            return [], f"filters[{i}] requires 'value' for match_type '{mt}'"
        result.append(d)
    return result, None


def _get_file_date(
    md_file: Path, date_type: str, frontmatter: dict | None = None,
) -> datetime | None:
    """Get the relevant date for a file based on date_type.

    Args:
        md_file: Path to the markdown file.
        date_type: "created" (frontmatter Date, fallback to filesystem) or "modified".
        frontmatter: Pre-parsed frontmatter dict, or None to parse on demand.

    Returns:
        datetime or None if date cannot be determined.
    """
    if date_type == "created":
        if frontmatter is None:
            frontmatter = extract_frontmatter(md_file)
        file_date = parse_frontmatter_date(frontmatter.get("Date"))
        if file_date is None:
            file_date = get_file_creation_time(md_file)
        return file_date
    else:  # modified
        try:
            mtime = md_file.stat().st_mtime
            return datetime.fromtimestamp(mtime)
        except OSError:
            return None


def _find_matching_files(
    field: str | None,
    value: str,
    match_type: str,
    parsed_filters: list[dict],
    include_fields: list[str] | None = None,
    folder: Path | None = None,
    recursive: bool = False,
    date_start: datetime | None = None,
    date_end: datetime | None = None,
    date_type: str = "modified",
) -> list[str | dict]:
    """Scan vault and return files matching all frontmatter conditions.

    Args:
        field: Primary field to match, or None to skip primary matching (folder-only mode).
        value: Primary value to match.
        match_type: Match strategy for primary field.
        parsed_filters: Additional filter conditions (already validated).
        include_fields: If provided, return dicts with path + these field values.
        folder: If provided, restrict scan to files within this directory.
        recursive: If False (default), only direct children. If True, include subfolders.
        date_start: If provided, exclude files before this date (inclusive).
        date_end: If provided, exclude files after this date (inclusive).
        date_type: "created" or "modified" (default "modified").

    Returns:
        Sorted list of path strings or dicts (when include_fields is set).
    """
    matching = []

    files = get_vault_files()
    if folder:
        folder_resolved = folder.resolve()
        if recursive:
            files = [f for f in files if f.resolve().is_relative_to(folder_resolved)]
        else:
            files = [f for f in files if f.resolve().parent == folder_resolved]

    # Fast path: no frontmatter access needed, skip YAML parsing entirely
    needs_frontmatter = field is not None or parsed_filters or include_fields
    needs_date = date_start is not None or date_end is not None

    for md_file in files:
        frontmatter = None

        if needs_frontmatter:
            frontmatter = extract_frontmatter(md_file)

            if field is not None:
                if not _matches_field(frontmatter, field, value, match_type):
                    continue

            if not all(
                _matches_field(
                    frontmatter, f["field"], f["value"], f.get("match_type", "contains"),
                )
                for f in parsed_filters
            ):
                continue

        if needs_date:
            file_date = _get_file_date(md_file, date_type, frontmatter)
            if file_date is None:
                continue
            file_date_only = file_date.replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            if date_start and file_date_only < date_start:
                continue
            if date_end and file_date_only > date_end:
                continue

        rel_path = get_relative_path(md_file)
        if include_fields:
            if frontmatter is None:
                frontmatter = extract_frontmatter(md_file)
            result = {"path": rel_path}
            for inc_field in include_fields:
                raw = _get_field_ci(frontmatter, inc_field)
                result[inc_field] = str(raw) if raw is not None else None
            matching.append(result)
        else:
            matching.append(rel_path)

    return sorted(matching, key=lambda x: x["path"] if isinstance(x, dict) else x)


def update_file_frontmatter(
    file_path: Path,
    field: str,
    value,
    remove: bool = False,
    append: bool = False,
    rename: bool = False,
) -> None:
    """Update frontmatter in a file, preserving body content.

    Args:
        file_path: Path to the markdown file.
        field: Frontmatter field to update.
        value: Value to set, or new key name if rename=True.
        remove: If True, remove the field instead of setting it.
        append: If True, append value to existing list field.
        rename: If True, rename field to value (new key name).

    Raises:
        ValueError: If file has no frontmatter and remove/rename=True.
        ValueError: If append=True but field is not a list.
        ValueError: If rename=True but field doesn't exist or target already exists.
    """
    content = file_path.read_text(encoding="utf-8")

    # Parse existing frontmatter and body
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if match:
        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = content[match.end():]
    else:
        if remove or rename:
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
        items = value if isinstance(value, list) else [value]
        for item in items:
            if item not in existing:
                existing.append(item)
        frontmatter[field] = existing
    elif rename:
        if field not in frontmatter:
            raise ValueError(f"Field '{field}' not found in frontmatter")
        new_key = value
        if new_key in frontmatter:
            raise ValueError(f"Field '{new_key}' already exists in frontmatter")
        frontmatter[new_key] = frontmatter.pop(field)
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
            rename=(operation == "rename"),
        )
    except ValueError as e:
        return False, f"{e} ({path})"
    except Exception as e:
        return False, f"Update failed for {path}: {e}"

    if operation == "remove":
        return True, f"Removed '{field}' from {path}"
    elif operation == "append":
        return True, f"Appended {parsed_value!r} to '{field}' in {path}"
    elif operation == "rename":
        return True, f"Renamed '{field}' to '{parsed_value}' in {path}"
    else:
        return True, f"Set '{field}' to {parsed_value!r} in {path}"


# =============================================================================
# Section Operations
# =============================================================================


# Precompiled patterns for section parsing
_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")
HEADING_PATTERN = re.compile(r"^(#+)\s+(.*)$")


def is_fence_line(line: str) -> bool:
    """Check if a line is a code fence opener/closer (``` or ~~~)."""
    return bool(_FENCE_PATTERN.match(line.strip()))


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
        if is_fence_line(line):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Check if this line is a matching heading
        line_heading_match = HEADING_PATTERN.match(line)
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

        if is_fence_line(line):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        line_heading_match = HEADING_PATTERN.match(line)
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
# Batch Confirmation Gate
# =============================================================================

# Tracks previewed operations so confirm=True only works after a preview.
# Keys are tuples of operation parameters; consumed on use (single-use).
_pending_previews: set[tuple] = set()


def store_preview(key: tuple) -> None:
    """Record that a confirmation preview was shown for this operation."""
    _pending_previews.add(key)


def consume_preview(key: tuple) -> bool:
    """Check and consume a pending preview. Returns True if one existed."""
    if key in _pending_previews:
        _pending_previews.discard(key)
        return True
    return False


def clear_pending_previews() -> None:
    """Clear all pending previews. For testing only."""
    _pending_previews.clear()


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
