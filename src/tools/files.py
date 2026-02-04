"""File operation tools - read, create, move, append."""

import json

import yaml

from config import EXCLUDED_DIRS, VAULT_PATH
from services.vault import (
    do_move_file,
    format_batch_result,
    get_relative_path,
    resolve_file,
    resolve_vault_path,
)


def read_file(path: str) -> str:
    """Read the full content of a vault note.

    Args:
        path: Path to the note, either relative to vault root or absolute.

    Returns:
        The full text content of the note.
    """
    file_path, error = resolve_file(path)
    if error:
        return f"Error: {error}"

    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading file: {e}"


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

    return f"Created {get_relative_path(file_path)}"


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
    return message if success else f"Error: {message}"


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

        success, message = do_move_file(source, destination)
        results.append((success, message))

    return format_batch_result("move", results)


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
        return f"Error: {error}"

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return f"Error appending to file: {e}"

    return f"Appended to {get_relative_path(file_path)}"
