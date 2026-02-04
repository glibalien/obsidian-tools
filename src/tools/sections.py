"""Section tools - prepend, replace, append to sections."""

import re

from services.vault import (
    err,
    find_section,
    get_relative_path,
    ok,
    resolve_file,
)


def prepend_to_file(path: str, content: str) -> str:
    """Prepend content to a vault file, inserting after any frontmatter.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to prepend to the file.

    Returns:
        JSON response: {"success": true, "path": "..."} on success,
        or {"success": false, "error": "..."} on failure.
    """
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
        # Insert after frontmatter with blank line separator
        frontmatter_end = frontmatter_match.end()
        body = existing_content[frontmatter_end:]
        new_content = (
            existing_content[:frontmatter_end]
            + content
            + "\n\n"
            + body.lstrip("\n")
        )
    else:
        # No frontmatter, prepend to beginning
        new_content = content + "\n\n" + existing_content.lstrip("\n")

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def replace_section(path: str, heading: str, content: str) -> str:
    """Replace a markdown heading and its content with new content.

    Finds a heading by case-insensitive exact match and replaces the entire
    section (heading + content) through to the next heading of same or higher
    level, or end of file.

    Args:
        path: Path to the note (relative to vault or absolute).
        heading: Full heading text including # symbols (e.g., "## Meeting Notes").
        content: Replacement content (can include a heading or not).

    Returns:
        JSON response: {"success": true, "path": "..."} on success,
        or {"success": false, "error": "..."} on failure.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    # Read file content
    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")

    # Find section boundaries
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    # Replace section
    new_lines = lines[:section_start] + [content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))


def append_to_section(path: str, heading: str, content: str) -> str:
    """Append content at the end of a section.

    Finds a heading by case-insensitive exact match and appends content
    at the end of that section (just before the next same-or-higher-level
    heading, or end of file). Preserves the heading and all existing content.

    Args:
        path: Path to the note (relative to vault or absolute).
        heading: Full heading text including # symbols (e.g., "## Context").
        content: Content to append to the section (may be multiline).

    Returns:
        JSON response: {"success": true, "path": "..."} on success,
        or {"success": false, "error": "..."} on failure.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    # Read file content
    try:
        file_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    lines = file_content.split("\n")

    # Find section boundaries
    section_start, section_end, error = find_section(lines, heading)
    if error:
        return err(error)

    # Append content at end of section
    new_lines = lines[:section_end] + ["", content] + lines[section_end:]
    new_content = "\n".join(new_lines)

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    return ok(path=get_relative_path(file_path))
