"""Link tools - backlinks, outlinks, folder search."""

import logging
import re
from collections import defaultdict
from pathlib import Path

from config import EXCLUDED_DIRS, LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT

logger = logging.getLogger(__name__)
from services.vault import err, get_relative_path, get_vault_files, ok, resolve_dir, resolve_file
from tools._validation import validate_pagination


def find_links(
    path: str,
    direction: str = "both",
    limit: int = LIST_DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Find links to or from a vault note.

    Args:
        path: Path to the note (relative to vault or absolute).
        direction: "backlinks" (files linking to this note),
                   "outlinks" (wikilinks from this note),
                   or "both" (both in one call).
        limit: Maximum results per direction (default 500).
        offset: Results to skip per direction (default 0).

    Returns:
        JSON response with link results. Backlinks are file path strings,
        outlinks are {name, path} objects. "both" returns separate sections.
    """
    if direction not in ("backlinks", "outlinks", "both"):
        return err(f"Invalid direction: {direction}. Must be 'backlinks', 'outlinks', or 'both'")

    file_path, error = resolve_file(path)
    if error:
        return err(error)

    validated_offset, validated_limit, pagination_error = validate_pagination(offset, limit)
    if pagination_error:
        return err(pagination_error)

    rel_path = get_relative_path(file_path)

    if direction == "backlinks":
        return _get_backlinks(file_path, rel_path, validated_offset, validated_limit)

    if direction == "outlinks":
        return _get_outlinks(file_path, path, validated_offset, validated_limit)

    # direction == "both"
    backlinks_data = _backlinks_data(file_path, rel_path, validated_offset, validated_limit)
    outlinks_data, outlinks_err = _outlinks_data(file_path, path, validated_offset, validated_limit)
    if outlinks_err:
        return err(outlinks_err)
    return ok(backlinks=backlinks_data, outlinks=outlinks_data)


def _get_backlinks(file_path: Path, rel_path: str, offset: int, limit: int) -> str:
    """Return paginated backlinks as a top-level ok() response."""
    note_name = file_path.stem
    all_results = _scan_backlinks(note_name, rel_path)
    if not all_results:
        return ok(f"No backlinks found to [[{note_name}]]", results=[], total=0)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _get_outlinks(file_path: Path, display_path: str, offset: int, limit: int) -> str:
    """Return paginated outlinks as a top-level ok() response."""
    all_results = _extract_outlinks(file_path)
    if all_results is None:
        return err(f"Reading file failed: {display_path}")
    if not all_results:
        return ok(f"No outlinks found in {display_path}", results=[], total=0)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return ok(results=page, total=total)


def _backlinks_data(file_path: Path, rel_path: str, offset: int, limit: int) -> dict:
    """Return backlinks as a dict for embedding in 'both' response."""
    note_name = file_path.stem
    all_results = _scan_backlinks(note_name, rel_path)
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return {"results": page, "total": total}


def _outlinks_data(
    file_path: Path, display_path: str, offset: int, limit: int,
) -> tuple[dict, str | None]:
    """Return outlinks as a dict for embedding in 'both' response.

    Returns:
        Tuple of (data_dict, error_message). error_message is None on success.
    """
    all_results = _extract_outlinks(file_path)
    if all_results is None:
        return {}, f"Reading file failed: {display_path}"
    total = len(all_results)
    page = all_results[offset:offset + limit]
    return {"results": page, "total": total}, None


def _extract_outlinks(file_path: Path) -> list[dict] | None:
    """Extract and resolve wikilinks from a file. Returns None on read error."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Failed to read %s for outlinks: %s", file_path, e)
        return None

    pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
    matches = re.findall(pattern, content)
    if not matches:
        return []

    stem_map, path_map = _build_note_path_map()
    unique_names = sorted(set(matches))
    return [
        {"name": name, "path": _resolve_link(name, stem_map, path_map)}
        for name in unique_names
    ]


def _scan_backlinks(note_name: str, rel_path: str) -> list[str]:
    """Scan all vault files for backlinks (O(n)).

    Matches both bare stem links ([[note]]) and folder-qualified links
    ([[folder/note]]) to handle disambiguation when stems collide.
    """
    # Build pattern matching both [[stem]] and [[folder/stem]] forms
    # rel_path is like "sub/foo.md" — strip .md for the qualified name
    qualified = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    qualified = qualified.replace("\\", "/")

    if qualified != note_name:
        # Match either bare stem or folder-qualified path
        alt = rf"(?:{re.escape(note_name)}|{re.escape(qualified)})"
    else:
        alt = re.escape(note_name)

    pattern = rf"\[\[{alt}(?:\|[^\]]+)?\]\]"
    backlinks = []

    for md_file in get_vault_files():
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.debug("Skipping %s during backlink scan: %s", md_file, e)
            continue
        if re.search(pattern, content, re.IGNORECASE):
            backlinks.append(get_relative_path(md_file))

    return sorted(backlinks)


def _build_note_path_map() -> tuple[dict[str, str], dict[str, str]]:
    """Build mappings for resolving wikilink names to vault paths.

    Returns:
        Tuple of (stem_map, path_map):
        - stem_map: lowercase stem → relative path (shortest wins for collisions)
        - path_map: lowercase relative path without .md → relative path (all files)
    """
    stem_map: dict[str, str] = {}
    path_map: dict[str, str] = {}
    for md_file in get_vault_files():
        rel_path = get_relative_path(md_file)
        # Normalize separators for cross-platform matching
        normalized = rel_path.replace("\\", "/")
        stem = md_file.stem.lower()
        if stem not in stem_map or len(rel_path) < len(stem_map[stem]):
            stem_map[stem] = rel_path
        # Store without .md for folder-qualified lookup
        key = normalized[:-3].lower() if normalized.endswith(".md") else normalized.lower()
        path_map[key] = rel_path
    return stem_map, path_map


def _resolve_link(
    name: str, stem_map: dict[str, str], path_map: dict[str, str]
) -> str | None:
    """Resolve a wikilink name to a relative vault path.

    Handles #heading suffixes and folder-prefixed links.
    """
    # Strip #heading suffix for resolution
    base_name = name.split("#")[0] if "#" in name else name

    # Try as bare stem (most common case)
    resolved = stem_map.get(base_name.lower())
    if resolved:
        return resolved

    # Try as folder-qualified path (e.g. [[Projects/note1]])
    normalized = base_name.replace("\\", "/").lower()
    return path_map.get(normalized)


def compare_folders(
    source: str,
    target: str,
    recursive: bool = False,
) -> str:
    """Compare two vault folders by markdown filename stem (case-insensitive).

    Args:
        source: Path to the source folder (relative to vault or absolute).
        target: Path to the target folder (relative to vault or absolute).
        recursive: If True, include files in subfolders. Default: False.

    Returns:
        JSON response with only_in_source, only_in_target, in_both lists and counts.
    """
    source_path, source_err = resolve_dir(source)
    if source_err:
        return err(source_err)

    target_path, target_err = resolve_dir(target)
    if target_err:
        return err(target_err)

    if source_path == target_path:
        return err("Source and target folders are the same")

    source_files = _scan_folder(source_path, recursive)
    target_files = _scan_folder(target_path, recursive)

    source_stems: dict[str, list[str]] = defaultdict(list)
    for stem, path in source_files:
        source_stems[stem].append(path)
    target_stems: dict[str, list[str]] = defaultdict(list)
    for stem, path in target_files:
        target_stems[stem].append(path)

    source_only_keys = sorted(source_stems.keys() - target_stems.keys())
    target_only_keys = sorted(target_stems.keys() - source_stems.keys())
    both_keys = sorted(source_stems.keys() & target_stems.keys())

    only_in_source = sorted(
        path for k in source_only_keys for path in source_stems[k]
    )
    only_in_target = sorted(
        path for k in target_only_keys for path in target_stems[k]
    )
    in_both = [
        {
            "name": sorted(source_stems[k])[0].rsplit("/", 1)[-1],
            "source_paths": sorted(source_stems[k]),
            "target_paths": sorted(target_stems[k]),
        }
        for k in both_keys
    ]

    counts = {
        "only_in_source": len(only_in_source),
        "only_in_target": len(only_in_target),
        "in_both": len(in_both),
    }

    return ok(
        f"Compared '{source}' with '{target}': "
        f"{counts['only_in_source']} only in source, "
        f"{counts['only_in_target']} only in target, "
        f"{counts['in_both']} in both",
        only_in_source=only_in_source,
        only_in_target=only_in_target,
        in_both=in_both,
        counts=counts,
    )


def _scan_folder(folder_path: Path, recursive: bool) -> list[tuple[str, str]]:
    """Scan a folder for .md files and return (lowercased_stem, relative_path) pairs."""
    pattern_func = folder_path.rglob if recursive else folder_path.glob
    results = []
    for md_file in pattern_func("*.md"):
        if any(excluded in md_file.parts for excluded in EXCLUDED_DIRS):
            continue
        rel_path = get_relative_path(md_file)
        stem = md_file.stem.lower()
        results.append((stem, rel_path))
    return results
