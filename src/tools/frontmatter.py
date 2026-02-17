"""Frontmatter tools - list, update, search by date."""

import json
from datetime import datetime

from config import VAULT_PATH
from services.vault import (
    do_update_frontmatter,
    err,
    extract_frontmatter,
    format_batch_result,
    get_file_creation_time,
    get_vault_files,
    ok,
    parse_frontmatter_date,
)


def list_files_by_frontmatter(
    field: str,
    value: str,
    match_type: str = "contains",
    limit: int = 100,
    offset: int = 0,
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
        return err(f"match_type must be 'contains' or 'equals', got '{match_type}'")

    matching = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in get_vault_files():
        frontmatter = extract_frontmatter(md_file)
        field_value = frontmatter.get(field)

        if field_value is None:
            continue

        matches = False
        if match_type == "contains":
            if isinstance(field_value, list):
                matches = any(value in str(item) for item in field_value)
            elif isinstance(field_value, str):
                matches = value in field_value
        elif match_type == "equals":
            matches = field_value == value

        if matches:
            rel_path = md_file.resolve().relative_to(vault_resolved)
            matching.append(str(rel_path))

    if not matching:
        return ok(f"No files found where {field} {match_type} '{value}'", results=[], total=0)

    all_results = sorted(matching)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


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
        return err(f"operation must be 'set', 'remove', or 'append', got '{operation}'")

    if operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    # Parse value - try JSON first, fall back to string
    parsed_value = value
    if value is not None:
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed_value = value  # Keep as string

    success, message = do_update_frontmatter(path, field, parsed_value, operation)
    if success:
        return ok(message)
    return err(message)


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
        return err(f"operation must be 'set', 'remove', or 'append', got '{operation}'")

    if operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    if not paths:
        return err("paths list is empty")

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
        success, message = do_update_frontmatter(path, field, parsed_value, operation)
        results.append((success, message))

    return ok(format_batch_result("update", results))


def search_by_date_range(
    start_date: str,
    end_date: str,
    date_type: str = "modified",
    limit: int = 100,
    offset: int = 0,
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
        return err(f"date_type must be 'created' or 'modified', got '{date_type}'")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return err(f"Invalid start_date format. Use YYYY-MM-DD, got '{start_date}'")

    try:
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return err(f"Invalid end_date format. Use YYYY-MM-DD, got '{end_date}'")

    if start > end:
        return err(f"start_date ({start_date}) is after end_date ({end_date})")

    matching = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in get_vault_files():
        file_date = None

        if date_type == "created":
            # Try frontmatter Date first, fall back to filesystem creation time
            frontmatter = extract_frontmatter(md_file)
            file_date = parse_frontmatter_date(frontmatter.get("Date"))
            if file_date is None:
                file_date = get_file_creation_time(md_file)
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
        return ok(
            f"No files found with {date_type} date between {start_date} and {end_date}",
            results=[],
            total=0,
        )

    all_results = sorted(matching)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
