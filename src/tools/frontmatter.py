"""Frontmatter tools - list, update, search by date."""

import json
import re

from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    FilterCondition,
    NO_VALUE_MATCH_TYPES,
    VALID_MATCH_TYPES,
    _find_matching_files,
    _validate_filters,
    consume_preview,
    do_update_frontmatter,
    err,
    format_batch_result,
    ok,
    resolve_dir,
    store_preview,
)

_JSON_SCALAR_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


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
        target_filters: Additional targeting conditions (AND logic). Same format as find_notes frontmatter conditions.
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


