"""Frontmatter tools - list, update, search by date."""

import json
import re
from datetime import datetime

from config import VAULT_PATH
from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    do_update_frontmatter,
    err,
    extract_frontmatter,
    format_batch_result,
    get_file_creation_time,
    get_vault_files,
    ok,
    parse_frontmatter_date,
)
from tools._validation import validate_pagination


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


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_JSON_SCALAR_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


FrontmatterValue = str | int | float | bool | list | dict | None


def _strip_wikilinks(text: str) -> str:
    """Strip wikilink brackets: '[[Foo|alias]]' → 'Foo', '[[Bar]]' → 'Bar'."""
    return _WIKILINK_RE.sub(r"\1", text)


def _normalize_frontmatter_value(value: FrontmatterValue) -> FrontmatterValue:
    """Normalize tool input while preserving legacy JSON-string support.

    Native JSON-compatible values are passed through unchanged.
    String values are parsed as JSON only when they look like JSON containers
    or scalar literals (quoted strings, numbers, true/false/null).
    """
    if not isinstance(value, str):
        return value

    candidate = value.strip()
    if not candidate:
        return value

    looks_like_json = (
        (candidate[0] == "{" and candidate[-1] == "}")
        or (candidate[0] == "[" and candidate[-1] == "]")
        or (candidate[0] == '"' and candidate[-1] == '"')
        or candidate in ("true", "false", "null")
        or bool(_JSON_SCALAR_RE.match(candidate))
    )

    if not looks_like_json:
        return value

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return value


def _matches_field(frontmatter: dict, field: str, value: str, match_type: str) -> bool:
    """Check if a frontmatter dict matches a single field condition.

    Both field names and values are compared case-insensitively.
    Wikilink brackets are stripped before comparison so "Foo" matches "[[Foo]]".
    Non-string/non-list values are converted to strings before comparison.
    """
    field_value = _get_field_ci(frontmatter, field)
    if field_value is None:
        return False
    value_lower = _strip_wikilinks(value).lower()
    if match_type == "contains":
        if isinstance(field_value, list):
            return any(value_lower in _strip_wikilinks(str(item)).lower() for item in field_value)
        return value_lower in _strip_wikilinks(str(field_value)).lower()
    elif match_type == "equals":
        if isinstance(field_value, list):
            return any(_strip_wikilinks(str(item)).lower() == value_lower for item in field_value)
        return _strip_wikilinks(str(field_value)).lower() == value_lower
    return False


def _parse_filters(filters: str | list[dict] | None) -> tuple[list[dict], str | None]:
    """Parse and validate filter conditions.

    Accepts either:
    - A JSON-encoded string array (legacy behavior), or
    - A native list of dicts (preferred for structured tool calls).

    Returns:
        (parsed_filters, error_message). error_message is None on success.
    """
    if filters is None:
        return [], None

    if isinstance(filters, list):
        parsed = filters
    else:
        try:
            parsed = json.loads(filters)
        except (json.JSONDecodeError, TypeError):
            return [], f"filters must be a valid JSON array, got: {filters!r}"
    if not isinstance(parsed, list):
        return [], "filters must be a JSON array"
    for i, f in enumerate(parsed):
        if not isinstance(f, dict):
            return [], f"filters[{i}] must be an object, got {type(f).__name__}"
        if "field" not in f or "value" not in f:
            return [], f"filters[{i}] must have 'field' and 'value' keys"
        fmt = f.get("match_type", "contains")
        if fmt not in ("contains", "equals"):
            return [], f"filters[{i}] match_type must be 'contains' or 'equals', got '{fmt}'"
    return parsed, None


def _find_matching_files(
    field: str,
    value: str,
    match_type: str,
    parsed_filters: list[dict],
    include_fields: list[str] | None = None,
) -> list[str | dict]:
    """Scan vault and return files matching all frontmatter conditions.

    Args:
        field: Primary field to match.
        value: Primary value to match.
        match_type: Match strategy for primary field.
        parsed_filters: Additional filter conditions (already validated).
        include_fields: If provided, return dicts with path + these field values.

    Returns:
        Sorted list of path strings or dicts (when include_fields is set).
    """
    matching = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in get_vault_files():
        frontmatter = extract_frontmatter(md_file)
        if not _matches_field(frontmatter, field, value, match_type):
            continue
        if not all(
            _matches_field(frontmatter, f["field"], f["value"], f.get("match_type", "contains"))
            for f in parsed_filters
        ):
            continue
        rel_path = str(md_file.resolve().relative_to(vault_resolved))
        if include_fields:
            result = {"path": rel_path}
            for inc_field in include_fields:
                raw = _get_field_ci(frontmatter, inc_field)
                result[inc_field] = str(raw) if raw is not None else None
            matching.append(result)
        else:
            matching.append(rel_path)

    return sorted(matching, key=lambda x: x["path"] if isinstance(x, dict) else x)


def list_files_by_frontmatter(
    field: str,
    value: str,
    match_type: str = "contains",
    filters: str | list[dict] | None = None,
    include_fields: str | list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Find vault files matching frontmatter criteria.

    Args:
        field: Frontmatter field name (e.g., 'tags', 'company', 'project').
        value: Value to match against.
        match_type: How to match - 'contains' (value in list), 'equals' (exact match).
        filters: Optional additional conditions (AND logic), either as a JSON array string
                 or native list of objects with 'field', 'value', and optional 'match_type'.
                 Example: '[{"field": "status", "value": "open"}]' or
                 [{"field": "status", "value": "open"}]
        include_fields: Optional field names to include in results, either as a JSON array
                        string or native list.
                        When set, results are objects with 'path' plus the requested fields.
                        Example: '["status", "scheduled"]'

    Returns:
        List of matching file paths (or objects when include_fields is set).
    """
    if match_type not in ("contains", "equals"):
        return err(f"match_type must be 'contains' or 'equals', got '{match_type}'")

    parsed_filters, filter_err = _parse_filters(filters)
    if filter_err:
        return err(filter_err)

    # Parse include_fields
    parsed_include = None
    if include_fields is not None:
        if isinstance(include_fields, list):
            parsed_include = include_fields
        else:
            try:
                parsed_include = json.loads(include_fields)
            except (json.JSONDecodeError, TypeError):
                return err(f"include_fields must be a valid JSON array, got: {include_fields!r}")
        if not isinstance(parsed_include, list):
            return err("include_fields must be a JSON array")
        if not parsed_include:
            parsed_include = None  # Empty list = same as omitted

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    matching = _find_matching_files(field, value, match_type, parsed_filters, parsed_include)

    if not matching:
        return ok(f"No files found where {field} {match_type} '{value}'", results=[], total=0)

    total = len(matching)
    page = matching[validated_offset:validated_offset + validated_limit]
    return ok(f"Found {total} matching files", results=page, total=total)


def update_frontmatter(
    path: str,
    field: str,
    value: FrontmatterValue = None,
    operation: str = "set",
) -> str:
    """Update frontmatter on a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        field: Frontmatter field name to update.
        value: Value to set. Prefer native structured values (list/dict/bool/number/null)
               when available. JSON strings are still accepted for compatibility.
               Required for 'set'/'append'.
        operation: 'set' to add/modify, 'remove' to delete, 'append' to add to list.

    Returns:
        Confirmation message or error.
    """
    if operation not in ("set", "remove", "append"):
        return err(f"operation must be 'set', 'remove', or 'append', got '{operation}'")

    if operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    parsed_value = _normalize_frontmatter_value(value)

    success, message = do_update_frontmatter(path, field, parsed_value, operation)
    if success:
        return ok(message)
    return err(message)


def batch_update_frontmatter(
    field: str,
    value: FrontmatterValue = None,
    operation: str = "set",
    paths: list[str] | None = None,
    target_field: str | None = None,
    target_value: str | None = None,
    target_match_type: str = "contains",
    target_filters: str | list[dict] | None = None,
    confirm: bool = False,
) -> str:
    """Apply a frontmatter update to multiple vault files.

    Files can be specified in two ways (mutually exclusive):
    - paths: Explicit list of file paths.
    - target_field/target_value: Query-based targeting using frontmatter criteria.

    Args:
        field: Frontmatter field name to update.
        value: Value to set. Prefer native structured values (list/dict/bool/number/null)
               when available. JSON strings are still accepted for compatibility.
               Required for 'set'/'append'.
        operation: 'set' to add/modify, 'remove' to delete, 'append' to add to list.
        paths: List of file paths (relative to vault or absolute).
        target_field: Find files where this frontmatter field matches target_value.
        target_value: Value to match for target_field.
        target_match_type: How to match target_field - 'contains' or 'equals' (default 'contains').
        target_filters: Optional additional targeting conditions (AND logic), as JSON array
                        string or native list of objects.
        confirm: Must be true to execute when modifying more than 5 files (or any query-based update).

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    if operation not in ("set", "remove", "append"):
        return err(f"operation must be 'set', 'remove', or 'append', got '{operation}'")

    if operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    # Resolve target files
    if target_field is not None and paths is not None:
        return err("Provide either paths or target_field/target_value, not both")

    if target_field is not None:
        if target_value is None:
            return err("target_value is required when using target_field")
        if target_match_type not in ("contains", "equals"):
            return err(f"target_match_type must be 'contains' or 'equals', got '{target_match_type}'")

        parsed_target_filters, filter_err = _parse_filters(target_filters)
        if filter_err:
            return err(filter_err)

        paths = _find_matching_files(
            target_field, target_value, target_match_type, parsed_target_filters
        )
        if not paths:
            return ok("No files matched the targeting criteria", results=[], total=0)

        # Query-based targeting always requires confirmation
        if not confirm:
            desc = f"{operation} '{field}'" + (f" = '{value}'" if value else "")
            return ok(
                f"This will {desc} on {len(paths)} files matched by "
                f"target_field='{target_field}', target_value='{target_value}'. "
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                files=paths,
            )
    elif paths is not None:
        if not paths:
            return err("paths list is empty")

        # Require confirmation for large explicit batches
        if len(paths) > BATCH_CONFIRM_THRESHOLD and not confirm:
            desc = f"{operation} '{field}'" + (f" = '{value}'" if value else "")
            return ok(
                f"This will {desc} on {len(paths)} files. "
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                files=paths,
            )
    else:
        return err("Provide either paths or target_field/target_value")

    # Normalize value once (same for all files)
    parsed_value = _normalize_frontmatter_value(value)

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
    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

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
    page = all_results[validated_offset:validated_offset + validated_limit]
    return ok(results=page, total=total)
