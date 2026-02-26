"""Editing tools - unified file content editing."""

import logging
import re

from services.vault import (
    err,
    find_section,
    get_relative_path,
    ok,
    resolve_file,
)

logger = logging.getLogger(__name__)


def edit_file(
    path: str,
    content: str,
    position: str,
    heading: str | None = None,
    mode: str | None = None,
) -> str:
    """Edit a vault file by inserting or replacing content.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to insert or replace with.
        position: Where to edit — "prepend", "append", or "section".
        heading: Required for position="section". Full heading with # symbols.
        mode: Required for position="section". One of "replace" or "append".

    Returns:
        JSON response: {"success": true, "path": "..."} on success,
        or {"success": false, "error": "..."} on failure.
    """
    if position == "prepend":
        return _prepend(path, content)
    elif position == "append":
        return _append(path, content)
    elif position == "section":
        if not heading:
            return err("heading is required when position is 'section'")
        if mode not in ("replace", "append"):
            return err("mode must be 'replace' or 'append' when position is 'section'")
        if mode == "replace":
            return _section_replace(path, content, heading)
        else:
            return _section_append(path, content, heading)
    else:
        return err(f"Unknown position: {position!r}. Must be 'prepend', 'append', or 'section'")


def _prepend(path: str, content: str) -> str:
    """Insert content after frontmatter (or at start if none)."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        existing_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    # Detect YAML frontmatter (must start at position 0)
    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", existing_content, re.DOTALL)

    if frontmatter_match:
        frontmatter_end = frontmatter_match.end()
        body = existing_content[frontmatter_end:]
        new_content = (
            existing_content[:frontmatter_end]
            + content
            + "\n\n"
            + body.lstrip("\n")
        )
    else:
        new_content = content + "\n\n" + existing_content.lstrip("\n")

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def _append(path: str, content: str) -> str:
    """Append content to end of file."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return err(f"Appending to file failed: {e}")

    return ok(path=get_relative_path(file_path))


def _section_replace(path: str, content: str, heading: str) -> str:
    """Replace heading + content with new content."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    new_lines = lines[:section_start] + [content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def _section_append(path: str, content: str, heading: str) -> str:
    """Append content to end of section."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    new_lines = lines[:section_end] + ["", content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))
