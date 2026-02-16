"""Link tools - backlinks, outlinks, folder search."""

import json as _json
import os
import re
from pathlib import Path

from config import EXCLUDED_DIRS, VAULT_PATH
from services.vault import err, get_vault_files, ok, resolve_dir, resolve_file


def find_backlinks(note_name: str, limit: int = 100, offset: int = 0) -> str:
    """Find all vault files that contain wikilinks to a given note.

    Uses a pre-built link index for O(1) lookups when available,
    falling back to a full vault scan if the index doesn't exist.

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

    # Try link index first
    from config import CHROMA_PATH
    index_path = os.path.join(CHROMA_PATH, "link_index.json")

    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                link_index = _json.load(f)
            sources = link_index.get(note_name.lower(), [])
            vault_resolved = VAULT_PATH.resolve()
            all_results = sorted(
                str(Path(s).relative_to(vault_resolved))
                for s in sources
                if Path(s).exists()
            )
        except Exception:
            all_results = _scan_backlinks(note_name)
    else:
        all_results = _scan_backlinks(note_name)

    if not all_results:
        return ok(f"No backlinks found to [[{note_name}]]", results=[], total=0)

    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _scan_backlinks(note_name: str) -> list[str]:
    """Fallback: scan all vault files for backlinks (O(n))."""
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

    return sorted(backlinks)


def find_outlinks(path: str, limit: int = 100, offset: int = 0) -> str:
    """Extract all wikilinks from a vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        limit: Maximum number of results to return (default 100).
        offset: Number of results to skip (default 0).

    Returns:
        JSON response with list of linked note names (without brackets),
        or a message if no outlinks found.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Reading file failed: {e}")

    # Pattern captures note name before optional |alias
    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    if not matches:
        return ok(f"No outlinks found in {path}", results=[], total=0)

    # Deduplicate and sort
    all_results = sorted(set(matches))
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


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
    folder_path, error = resolve_dir(folder)
    if error:
        return err(error)

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
        return ok(f"No markdown files found {mode}in {folder}", results=[], total=0)

    all_results = sorted(files)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)
