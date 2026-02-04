"""Link tools - backlinks, outlinks, folder search."""

import re

from config import EXCLUDED_DIRS, VAULT_PATH
from services.vault import get_vault_files, resolve_dir, resolve_file


def find_backlinks(note_name: str) -> str:
    """Find all vault files that contain wikilinks to a given note.

    Searches for both [[note_name]] and [[note_name|alias]] patterns.

    Args:
        note_name: The note name to search for (without brackets or .md extension).
                   Example: "CNP MVP" to find links like [[CNP MVP]] or [[CNP MVP|alias]].

    Returns:
        Newline-separated list of file paths (relative to vault) that link to the note,
        or a message if no backlinks found.
    """
    if not note_name or not note_name.strip():
        return "Error: note_name cannot be empty"

    note_name = note_name.strip()

    # Remove .md extension if provided
    if note_name.endswith(".md"):
        note_name = note_name[:-3]

    # Pattern: [[note_name]] or [[note_name|alias]]
    pattern = rf"\[\[{re.escape(note_name)}(?:\|[^\]]+)?\]\]"

    backlinks = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in get_vault_files():
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if re.search(pattern, content, re.IGNORECASE):
            rel_path = md_file.relative_to(vault_resolved)
            backlinks.append(str(rel_path))

    if not backlinks:
        return f"No backlinks found to [[{note_name}]]"

    return "\n".join(sorted(backlinks))


def find_outlinks(path: str) -> str:
    """Extract all wikilinks from a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).

    Returns:
        Newline-separated list of linked note names (without brackets),
        or a message if no outlinks found.
    """
    file_path, error = resolve_file(path)
    if error:
        return f"Error: {error}"

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading file: {e}"

    # Pattern captures note name before optional |alias
    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    if not matches:
        return f"No outlinks found in {path}"

    # Deduplicate and sort
    unique_links = sorted(set(matches))
    return "\n".join(unique_links)


def search_by_folder(
    folder: str,
    recursive: bool = False,
) -> str:
    """List all markdown files in a vault folder.

    Args:
        folder: Path to the folder (relative to vault or absolute).
        recursive: If True, include files in subfolders. Default: False.

    Returns:
        Newline-separated list of file paths (relative to vault),
        or a message if no files found.
    """
    folder_path, error = resolve_dir(folder)
    if error:
        return f"Error: {error}"

    # Use rglob for recursive, glob for non-recursive
    pattern_func = folder_path.rglob if recursive else folder_path.glob

    files = []
    vault_resolved = VAULT_PATH.resolve()

    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        rel_path = md_file.relative_to(vault_resolved)
        files.append(str(rel_path))

    if not files:
        mode = "recursively " if recursive else ""
        return f"No markdown files found {mode}in {folder}"

    return "\n".join(sorted(files))
