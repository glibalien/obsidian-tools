"""Frontmatter tools - list, update, search by date."""

import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from config import LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT
from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    consume_preview,
    do_update_frontmatter,
    err,
    extract_frontmatter,
    format_batch_result,
    get_file_creation_time,
    get_relative_path,
    get_vault_files,
    ok,
    parse_frontmatter_date,
    resolve_dir,
    store_preview,
)
from tools._validation import validate_pagination


class FilterCondition(BaseModel):
    """A single frontmatter filter condition."""

    field: str
    value: str = ""
    match_type: str = "contains"


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

VALID_MATCH_TYPES = ("contains", "equals", "missing", "exists", "not_contains", "not_equals")
NO_VALUE_MATCH_TYPES = ("missing", "exists")


def _strip_wikilinks(text: str) -> str:
    """Strip wikilink brackets: '[[Foo|alias]]' → 'Foo', '[[Bar]]' → 'Bar'."""
    return _WIKILINK_RE.sub(r"\1", text)


def _normalize_frontmatter_value(value):
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


def _find_matching_files(
    field: str | None,
    value: str,
    match_type: str,
    parsed_filters: list[dict],
    include_fields: list[str] | None = None,
    folder: Path | None = None,
    recursive: bool = False,
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

    for md_file in files:
        frontmatter = extract_frontmatter(md_file)

        # Primary field match (skip when field is None for folder-only mode)
        if field is not None:
            if not _matches_field(frontmatter, field, value, match_type):
                continue

        if not all(
            _matches_field(frontmatter, f["field"], f["value"], f.get("match_type", "contains"))
            for f in parsed_filters
        ):
            continue

        rel_path = get_relative_path(md_file)
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
    value: str = "",
    match_type: str = "contains",
    filters: list[FilterCondition] | None = None,
    include_fields: list[str] | None = None,
    folder: str = "",
    recursive: bool = False,
    limit: int = LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Find vault files by frontmatter metadata. Use this for structured queries like "find open tasks for project X", "list all notes tagged Y", or "find notes missing field Z".

    Args:
        field: Frontmatter field name to match (e.g., 'tags', 'project', 'category').
        value: Value to match against. Wikilink brackets are stripped automatically.
            Not required for 'missing' or 'exists' match types.
        match_type: How to match the field value:
            'contains' - substring/list member match (default).
            'equals' - exact match.
            'missing' - field is absent (value ignored).
            'exists' - field is present with any value (value ignored).
            'not_contains' - field is absent or doesn't contain value.
            'not_equals' - field is absent or doesn't equal value.
        filters: Additional AND conditions. Each needs 'field', optional 'value', and optional 'match_type'.
        include_fields: Field names whose values to return with each result, e.g. ["status", "scheduled"].
        folder: Restrict search to files within this folder (relative to vault root).
        recursive: Include subfolders when folder is set (default false). Set true to include subfolders.

    Returns:
        JSON with results (file paths or objects when include_fields is set) and total count.
    """
    if match_type not in VALID_MATCH_TYPES:
        return err(
            f"match_type must be one of {VALID_MATCH_TYPES}, got '{match_type}'"
        )

    if match_type not in NO_VALUE_MATCH_TYPES and not value:
        return err(f"value is required for match_type '{match_type}'")

    parsed_filters, filter_err = _validate_filters(filters)
    if filter_err:
        return err(filter_err)

    parsed_include = include_fields if include_fields else None

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    folder_path = None
    if folder:
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return err(folder_err)

    matching = _find_matching_files(
        field, value, match_type, parsed_filters, parsed_include, folder_path, recursive
    )

    if not matching:
        return ok(f"No files found where {field} {match_type} '{value}'", results=[], total=0)

    total = len(matching)
    page = matching[validated_offset:validated_offset + validated_limit]
    return ok(f"Found {total} matching files", results=page, total=total)


def update_frontmatter(
    path: str,
    field: str,
    value: str | list | None = None,
    operation: str = "set",
) -> str:
    """Update frontmatter on a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        field: Frontmatter field name to update.
        value: Value to set. Required for 'set'/'append'.
            For list-type fields (category, tags, aliases, cssclasses),
            ALWAYS pass an array — even for a single value: ["project"].
            For scalar fields (status, project, date), pass a plain string.
            Never pass comma-separated strings like "person, actor" — that
            becomes a single value, not a list.
        operation: 'set' to replace the field value, 'remove' to delete the field,
            'append' to add a single value to a list field (creates the list if
            missing, skips duplicates). Prefer 'append' over 'set' when adding
            to an existing list — it preserves other values.

    Returns:
        Confirmation message or error.
    """
    if operation not in ("set", "remove", "append", "rename"):
        return err(f"operation must be 'set', 'remove', 'append', or 'rename', got '{operation}'")

    if operation == "rename":
        if value is None or (isinstance(value, str) and not value.strip()):
            return err("value (new key name) is required for 'rename' operation")
        if not isinstance(value, str):
            return err("value must be a string (new key name) for 'rename' operation")
        # For rename, value is a key name — don't normalize as YAML value
        success, message = do_update_frontmatter(path, field, value, operation)
        if success:
            return ok(message)
        return err(message)

    if operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    parsed_value = _normalize_frontmatter_value(value)

    success, message = do_update_frontmatter(path, field, parsed_value, operation)
    if success:
        return ok(message)
    return err(message)


def _confirmation_preview(
    operation: str, field: str, value: str | None, paths: list, context: str,
) -> str:
    """Return a confirmation preview for a batch operation."""
    desc = f"{operation} '{field}'" + (f" = '{value}'" if value else "")
    return ok(
        "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
        confirmation_required=True,
        preview_message=f"This will {desc} on {len(paths)} files{context}.",
        files=paths,
    )


def _needs_confirmation(
    field: str, value: str | list | None, operation: str,
    paths: list[str], confirm: bool,
) -> bool:
    """Check confirmation gate. Returns True if preview is needed."""
    hashable_value = tuple(value) if isinstance(value, list) else value
    key = ("batch_update_frontmatter", field, hashable_value, operation, tuple(sorted(paths)))
    if confirm and consume_preview(key):
        return False
    store_preview(key)
    return True


def _resolve_batch_targets(
    paths: list[str] | None,
    target_field: str | None,
    target_value: str | None,
    target_match_type: str,
    target_filters: list[FilterCondition] | None,
    folder: str,
    recursive: bool,
    confirm: bool,
    operation: str,
    field: str,
    value: str | None,
) -> tuple[list[str] | None, str | None]:
    """Resolve target file paths for batch operations.

    Returns:
        (resolved_paths, early_return_json). If early_return_json is not None,
        the caller should return it directly (validation error or confirmation preview).
    """
    folder_path = None
    if folder:
        if paths is not None:
            return None, err("Cannot combine 'folder' with explicit 'paths'")
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return None, err(folder_err)

    if target_field is not None and paths is not None:
        return None, err("Provide either paths or target_field/target_value, not both")

    if target_field is not None:
        if target_match_type not in VALID_MATCH_TYPES:
            return None, err(
                f"target_match_type must be one of {VALID_MATCH_TYPES}, "
                f"got '{target_match_type}'"
            )
        if target_match_type not in NO_VALUE_MATCH_TYPES and target_value is None:
            return None, err(f"target_value is required for target_match_type '{target_match_type}'")

        parsed_target_filters, filter_err = _validate_filters(target_filters)
        if filter_err:
            return None, err(filter_err)

        paths = _find_matching_files(
            target_field, target_value or "", target_match_type,
            parsed_target_filters, folder=folder_path, recursive=recursive,
        )
        if not paths:
            return None, ok("No files matched the targeting criteria", results=[], total=0)

        if _needs_confirmation(field, value, operation, paths, confirm):
            folder_note = f" in folder '{folder}'" if folder else ""
            context = (
                f" matched by target_field='{target_field}', "
                f"target_value='{target_value}'{folder_note}"
            )
            return None, _confirmation_preview(operation, field, value, paths, context)

    elif folder_path is not None:
        paths = _find_matching_files(None, "", "contains", [], folder=folder_path, recursive=recursive)
        if not paths:
            return None, ok(f"No files found in folder '{folder}'", results=[], total=0)

        if _needs_confirmation(field, value, operation, paths, confirm):
            return None, _confirmation_preview(
                operation, field, value, paths, f" in folder '{folder}'"
            )

    elif paths is not None:
        if not paths:
            return None, err("paths list is empty")

        if len(paths) > BATCH_CONFIRM_THRESHOLD:
            if _needs_confirmation(field, value, operation, paths, confirm):
                return None, _confirmation_preview(operation, field, value, paths, "")

    else:
        return None, err("Provide paths, target_field/target_value, or folder")

    return paths, None


def batch_update_frontmatter(
    field: str,
    value: str | list | None = None,
    operation: str = "set",
    paths: list[str] | None = None,
    target_field: str | None = None,
    target_value: str | None = None,
    target_match_type: str = "contains",
    target_filters: list[FilterCondition] | None = None,
    folder: str = "",
    recursive: bool = False,
    confirm: bool = False,
) -> str:
    """Apply a frontmatter update to multiple vault files.

    Files can be specified in three ways:
    - paths: Explicit list of file paths.
    - target_field/target_value: Query-based targeting using frontmatter criteria.
    - folder: Target all files in a folder (can combine with target_field to narrow).

    Args:
        field: Frontmatter field name to update.
        value: Value to set. Required for 'set'/'append'.
            For list-type fields (category, tags, aliases, cssclasses),
            ALWAYS pass an array — even for a single value: ["project"].
            For scalar fields, pass a plain string.
            Prefer operation="append" to add a single value to an existing
            list without replacing it.
        operation: 'set' to replace the field value, 'remove' to delete the field,
            'append' to add a single value to a list field (creates the list if
            missing, skips duplicates). Prefer 'append' when adding to existing lists.
        paths: List of file paths (relative to vault or absolute). Cannot combine with folder.
        target_field: Find files where this frontmatter field matches target_value.
        target_value: Value to match for target_field. Not required for 'missing'/'exists' match types.
        target_match_type: How to match target_field - 'contains', 'equals', 'missing',
            'exists', 'not_contains', or 'not_equals' (default 'contains').
        target_filters: Additional targeting conditions (AND logic). Same format as list_files_by_frontmatter filters.
        folder: Restrict targeting to files within this folder (relative to vault root).
            Can be used alone (all files in folder) or with target_field (scoped query).
        recursive: Include subfolders when folder is set (default false). Set true to include subfolders.
        confirm: Must be true to execute when modifying more than 5 files (or any query/folder-based update).

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    if operation not in ("set", "remove", "append", "rename"):
        return err(f"operation must be 'set', 'remove', 'append', or 'rename', got '{operation}'")

    if operation == "rename":
        if value is None or (isinstance(value, str) and not value.strip()):
            return err("value (new key name) is required for 'rename' operation")
        if not isinstance(value, str):
            return err("value must be a string (new key name) for 'rename' operation")
    elif operation in ("set", "append") and value is None:
        return err(f"value is required for '{operation}' operation")

    resolved_paths, early_return = _resolve_batch_targets(
        paths, target_field, target_value, target_match_type,
        target_filters, folder, recursive, confirm, operation, field, value,
    )
    if early_return is not None:
        return early_return

    parsed_value = value if operation == "rename" else _normalize_frontmatter_value(value)

    results = []
    for path in resolved_paths:
        success, message = do_update_frontmatter(path, field, parsed_value, operation)
        results.append((success, message))

    return ok(format_batch_result("update", results))


def search_by_date_range(
    start_date: str,
    end_date: str,
    date_type: str = "modified",
    limit: int = LIST_DEFAULT_LIMIT,
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
            matching.append(get_relative_path(md_file))

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
