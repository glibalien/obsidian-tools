"""Link tools - backlinks, outlinks, folder search."""

import re

from config import EXCLUDED_DIRS, VAULT_PATH
from services.vault import err, get_vault_files, ok, resolve_dir, resolve_file


def find_backlinks(note_name: str, limit: int = 100, offset: int = 0) -> str:
    """Find all vault files that contain wikilinks to a given note."""
    if not note_name or not note_name.strip():
        return err("note_name cannot be empty")

    note_name = note_name.strip()
    if note_name.endswith(".md"):
        note_name = note_name[:-3]

    all_results = _scan_backlinks(note_name)

    if not all_results:
        return ok(
            message=f"No backlinks found to [[{note_name}]]",
            results=[],
            total=0,
            offset=offset,
            limit=limit,
        )

    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(
        message=f"Found {total} backlinks to [[{note_name}]]",
        results=page,
        total=total,
        offset=offset,
        limit=limit,
    )


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
    """Extract all wikilinks from a vault file."""
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Reading file failed: {e}")

    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)

    rel_path = str(file_path.relative_to(VAULT_PATH.resolve()))
    if not matches:
        return ok(
            message=f"No outlinks found in {path}",
            results=[],
            total=0,
            offset=offset,
            limit=limit,
            result={"path": rel_path, "links": []},
        )

    all_results = sorted(set(matches))
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(
        message=f"Found {total} outlinks in {rel_path}",
        results=page,
        total=total,
        offset=offset,
        limit=limit,
        result={"path": rel_path, "links": page},
    )


def search_by_folder(
    folder: str,
    recursive: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """List all markdown files in a vault folder."""
    folder_path, error = resolve_dir(folder)
    if error:
        return err(error)

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
        return ok(
            message=f"No markdown files found {mode}in {folder}",
            results=[],
            total=0,
            offset=offset,
            limit=limit,
        )

    all_results = sorted(files)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(
        message=f"Found {total} markdown files in {folder}",
        results=page,
        total=total,
        offset=offset,
        limit=limit,
    )
