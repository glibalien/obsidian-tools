"""File operation tools - read, create, move, append."""

import json

import yaml

from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    check_confirmation,
    compute_op_hash,
    do_move_file,
    err,
    format_batch_result,
    get_relative_path,
    ok,
    resolve_file,
    resolve_vault_path,
    store_confirmation,
)


def read_file(path: str, offset: int = 0, length: int = 3500) -> str:
    """Read content of a vault note with optional pagination.

    Args:
        path: Path to the note, either relative to vault root or absolute.
        offset: Character position to start reading from (default 0).
        length: Maximum characters to return (default 4000).

    Returns:
        The text content of the note, with pagination markers if truncated.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading file: {e}")

    total = len(content)

    # Short file with no offset — return as-is
    if offset == 0 and total <= length:
        return ok(content=content)

    # Offset past end of file
    if offset >= total:
        return err(f"offset {offset} exceeds file length {total}")

    # Slice the content
    chunk = content[offset:offset + length]
    end_pos = offset + length

    # Build result with markers
    parts = []
    if offset > 0:
        parts.append(f"[Continuing from char {offset} of {total}]\n\n")
    parts.append(chunk)
    if end_pos < total:
        parts.append(f"\n\n[... truncated at char {end_pos} of {total}. Use offset={end_pos} to read more.]")

    return ok(content="".join(parts))


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
        file_path = resolve_vault_path(path)
    except ValueError as e:
        return err(str(e))

    if file_path.exists():
        return err(f"File already exists: {path}")

    # Parse frontmatter if provided
    frontmatter_yaml = ""
    if frontmatter:
        fm_dict, parse_error = _parse_frontmatter(frontmatter)
        if parse_error:
            return err(parse_error)

        frontmatter_yaml = yaml.dump(fm_dict, default_flow_style=False, allow_unicode=True)

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
        return err(f"Error writing file: {e}")

    rel = str(get_relative_path(file_path))
    return ok(f"Created {rel}", path=rel)


def _parse_frontmatter(frontmatter: dict | str | None) -> tuple[dict, str | None]:
    """Normalize frontmatter input into a dictionary.

    Accepts None, a native dict, or a JSON object string.
    """
    if frontmatter is None:
        return {}, None

    if isinstance(frontmatter, dict):
        return frontmatter, None

    if not isinstance(frontmatter, str):
        return {}, (
            "Invalid frontmatter type: expected dict, JSON object string, or null. "
            f"Got {type(frontmatter).__name__}."
        )

    try:
        parsed = json.loads(frontmatter)
    except json.JSONDecodeError as e:
        return {}, f"Invalid frontmatter JSON: {e}"

    if not isinstance(parsed, dict):
        return {}, (
            "Invalid frontmatter JSON: expected a JSON object "
            f"(e.g., {{\"tags\": [\"meeting\"]}}), got {type(parsed).__name__}."
        )

    return parsed, None


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
    success, message = do_move_file(source, destination)
    if success:
        return ok(message)
    return err(message)


def batch_move_files(
    moves: list[dict],
    confirm: bool = False,
) -> str:
    """Move multiple vault files to new locations.

    Args:
        moves: List of move operations, each a dict with 'source' and 'destination' keys.
               Example: [{"source": "old/path.md", "destination": "new/path.md"}]
        confirm: Must be true to execute when moving more than 5 files.

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    if not moves:
        return err("moves list is empty")

    # Require confirmation for large batches
    if len(moves) > BATCH_CONFIRM_THRESHOLD:
        op_hash = compute_op_hash({"tool": "batch_move_files", "moves": moves})
        if not (confirm and check_confirmation(op_hash)):
            store_confirmation(op_hash)
            files = []
            for m in moves:
                if isinstance(m, dict) and m.get("source"):
                    files.append(f"{m['source']} → {m.get('destination', '?')}")
            return ok(
                f"This will move {len(moves)} files. "
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                files=files,
            )

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

        success, message = do_move_file(source, destination)
        results.append((success, message))

    return ok(format_batch_result("move", results))


def append_to_file(path: str, content: str) -> str:
    """Append content to the end of an existing vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to append to the file.

    Returns:
        Confirmation message or error.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return err(f"Appending to file failed: {e}")

    rel = str(get_relative_path(file_path))
    return ok(f"Appended to {rel}", path=rel)
