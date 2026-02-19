"""Link tools - backlinks, outlinks, folder search."""

import re

from config import EXCLUDED_DIRS
from services.vault import err, get_relative_path, get_vault_files, ok, resolve_dir, resolve_file
from tools._validation import validate_pagination


def find_backlinks(note_name: str, limit: int = 100, offset: int = 0) -> str:
    """Find all vault files that contain wikilinks to a given note.

    Args:
        note_name: The note name to search for (without brackets or .md extension).
        limit: Maximum number of results to return (default 100).
        offset: Number of results to skip (default 0).

    Returns:
        JSON response with list of file paths that link to the note.
    """
    if not note_name or not note_name.strip():
        return err("note_name cannot be empty")

    note_name = note_name.strip()
    if note_name.endswith(".md"):
        note_name = note_name[:-3]

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    all_results = _scan_backlinks(note_name)

    if not all_results:
        return ok(f"No backlinks found to [[{note_name}]]", results=[], total=0)

    total = len(all_results)
    page = all_results[validated_offset:validated_offset + validated_limit]
    return ok(results=page, total=total)


def _scan_backlinks(note_name: str) -> list[str]:
    """Fallback: scan all vault files for backlinks (O(n))."""
    pattern = rf"\[\[{re.escape(note_name)}(?:\|[^\]]+)?\]\]"
    backlinks = []

    for md_file in get_vault_files():
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(pattern, content, re.IGNORECASE):
            backlinks.append(get_relative_path(md_file))

    return sorted(backlinks)


def find_outlinks(path: str, limit: int = 100, offset: int = 0) -> str:
    """Extract all wikilinks from a vault file with resolved paths.

    Args:
        path: Path to the note (relative to vault or absolute).
        limit: Maximum number of results to return (default 100).
        offset: Number of results to skip (default 0).

    Returns:
        JSON response with list of {name, path} objects. path is null
        for unresolved links (non-existent notes).
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Reading file failed: {e}")

    # Pattern captures note name before optional |alias
    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    if not matches:
        return ok(f"No outlinks found in {path}", results=[], total=0)

    # Build stem → relative path lookup for resolution
    name_to_path = _build_note_path_map()

    # Deduplicate, resolve paths, and sort
    unique_names = sorted(set(matches))
    all_results = [
        {"name": name, "path": _resolve_link(name, name_to_path)}
        for name in unique_names
    ]
    total = len(all_results)
    page = all_results[validated_offset:validated_offset + validated_limit]
    return ok(results=page, total=total)


def _build_note_path_map() -> dict[str, str]:
    """Build a mapping from note stem (lowercase) to relative vault path.

    When multiple notes share a stem, the shortest path wins
    (matches Obsidian's default resolution behavior).
    """
    name_to_path: dict[str, str] = {}
    for md_file in get_vault_files():
        rel_path = get_relative_path(md_file)
        stem = md_file.stem.lower()
        if stem not in name_to_path or len(rel_path) < len(name_to_path[stem]):
            name_to_path[stem] = rel_path
    return name_to_path


def _resolve_link(name: str, name_to_path: dict[str, str]) -> str | None:
    """Resolve a wikilink name to a relative vault path.

    Handles #heading suffixes and folder-prefixed links.
    """
    # Strip #heading suffix for resolution
    base_name = name.split("#")[0] if "#" in name else name

    # Try as bare stem (most common case)
    resolved = name_to_path.get(base_name.lower())
    if resolved:
        return resolved

    # Try as relative path (e.g. [[Projects/note1]])
    with_ext = base_name if base_name.endswith(".md") else base_name + ".md"
    for path in name_to_path.values():
        if path.lower() == with_ext.lower():
            return path

    return None


def search_by_folder(
    folder: str,
    recursive: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """List all markdown files in a vault folder.

    Args:
        folder: Path to the folder (relative to vault or absolute).
        recursive: If True, include files in subfolders. Default: False.
        limit: Maximum number of results to return (default 100).
        offset: Number of results to skip (default 0).

    Returns:
        JSON response with list of file paths (relative to vault),
        or a message if no files found.
    """
    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    folder_path, error = resolve_dir(folder)
    if error:
        return err(error)

    # Use rglob for recursive, glob for non-recursive
    pattern_func = folder_path.rglob if recursive else folder_path.glob

    files = []

    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        files.append(get_relative_path(md_file))

    if not files:
        mode = "recursively " if recursive else ""
        return ok(f"No markdown files found {mode}in {folder}", results=[], total=0)

    all_results = sorted(files)
    total = len(all_results)
    page = all_results[validated_offset:validated_offset + validated_limit]
    return ok(results=page, total=total)
