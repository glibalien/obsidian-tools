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
    """Prepend content to a vault file, inserting after any frontmatter."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        existing_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return err(f"Error reading file: {e}")

    frontmatter_match = re.match(r"^---\n(.*?)\n---\n", existing_content, re.DOTALL)

    if frontmatter_match:
        frontmatter_end = frontmatter_match.end()
        body = existing_content[frontmatter_end:]
        new_content = existing_content[:frontmatter_end] + content + "\n\n" + body.lstrip("\n")
    else:
        new_content = content + "\n\n" + existing_content.lstrip("\n")

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    rel = get_relative_path(file_path)
    return ok(message=f"Prepended content to {rel}", path=rel, item={"path": rel})


def replace_section(path: str, heading: str, content: str) -> str:
    """Replace a markdown heading and its content with new content."""
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

    rel = get_relative_path(file_path)
    return ok(
        message=f"Replaced section {heading} in {rel}",
        path=rel,
        item={"path": rel, "heading": heading},
    )


def append_to_section(path: str, heading: str, content: str) -> str:
    """Append content at the end of a section."""
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

    rel = get_relative_path(file_path)
    return ok(
        message=f"Appended to section {heading} in {rel}",
        path=rel,
        item={"path": rel, "heading": heading},
    )
