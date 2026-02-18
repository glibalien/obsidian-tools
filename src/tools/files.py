"""File operation tools - read, create, move, append."""

import json

import yaml

from config import EXCLUDED_DIRS, VAULT_PATH
from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    do_move_file,
    err,
    get_relative_path,
    ok,
    resolve_file,
    resolve_vault_path,
)


def read_file(path: str, offset: int = 0, length: int = 3500) -> str:
    """Read content of a vault note with optional pagination."""
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
        return ok(
            message=f"Read {get_relative_path(file_path)}",
            content=content,
            result={"path": get_relative_path(file_path), "content": content},
            total=total,
            offset=offset,
            limit=length,
        )

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

    chunk_content = "".join(parts)
    return ok(
        message=f"Read {get_relative_path(file_path)} ({min(end_pos, total)}/{total} chars)",
        content=chunk_content,
        result={"path": get_relative_path(file_path), "content": chunk_content},
        total=total,
        offset=offset,
        limit=length,
    )


def create_file(
    path: str,
    content: str = "",
    frontmatter: dict | str | None = None,
) -> str:
    """Create a new markdown note in the vault.

    Args:
        path: Path for the new file (relative to vault or absolute).
              Parent directories will be created if they don't exist.
        content: The body content of the note (markdown).
        frontmatter: Optional frontmatter data. Prefer passing a native dict.
            JSON string input is also supported for backwards compatibility.
            Parsed data will be converted to YAML and wrapped in --- delimiters.

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
    return ok(
        message=f"Created {rel}",
        path=rel,
        item={"path": rel},
    )


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
    """Move a vault file to a different location within the vault."""
    success, message = do_move_file(source, destination)
    if success:
        return ok(message=message, result={"source": source, "destination": destination, "success": True})
    return err(message)


def batch_move_files(
    moves: list[dict],
    confirm: bool = False,
) -> str:
    """Move multiple vault files to new locations."""
    if not moves:
        return err("moves list is empty")

    # Require confirmation for large batches
    if len(moves) > BATCH_CONFIRM_THRESHOLD and not confirm:
        files = []
        for m in moves:
            if isinstance(m, dict) and m.get("source"):
                files.append({"source": m["source"], "destination": m.get("destination", "?")})
        return ok(
            message=(
                f"This will move {len(moves)} files. "
                "Show the file list to the user and call again with confirm=true to proceed."
            ),
            confirmation_required=True,
            files=files,
            total=len(moves),
        )

    successes = []
    failures = []
    for i, move in enumerate(moves):
        if not isinstance(move, dict):
            failures.append(
                {"index": i, "source": None, "destination": None, "success": False,
                 "message": f"Item {i}: expected dict, got {type(move).__name__}"}
            )
            continue

        source = move.get("source")
        destination = move.get("destination")

        if not source:
            failures.append(
                {"index": i, "source": None, "destination": destination, "success": False,
                 "message": f"Item {i}: missing 'source' key"}
            )
            continue
        if not destination:
            failures.append(
                {"index": i, "source": source, "destination": None, "success": False,
                 "message": f"Item {i}: missing 'destination' key"}
            )
            continue

        success, message = do_move_file(source, destination)
        item = {
            "index": i,
            "source": source,
            "destination": destination,
            "success": success,
            "message": message,
        }
        if success:
            successes.append(item)
        else:
            failures.append(item)

    total = len(successes) + len(failures)
    summary_message = f"Batch move: {len(successes)} succeeded, {len(failures)} failed"
    return ok(
        message=summary_message,
        result={
            "operation": "move",
            "succeeded": len(successes),
            "failed": len(failures),
            "successes": successes,
            "failures": failures,
        },
        successes=successes,
        failures=failures,
        total=total,
    )


def append_to_file(path: str, content: str) -> str:
    """Append content to the end of an existing vault file."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return err(f"Appending to file failed: {e}")

    rel = str(get_relative_path(file_path))
    return ok(
        message=f"Appended to {rel}",
        path=rel,
        item={"path": rel},
    )
