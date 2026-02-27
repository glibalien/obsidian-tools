"""Search tools for vault and web."""

from datetime import datetime

from ddgs import DDGS

from pathlib import Path

from search_vault import search_results
import services.vault as vault_service
from services.vault import err, ok, resolve_dir
from tools._validation import validate_pagination
from tools.frontmatter import (
    FilterCondition,
    _find_matching_files,
    _validate_filters,
)

VALID_MODES = {"hybrid", "semantic", "keyword"}
VALID_SORTS = {"relevance", "modified", "created", "name"}


def _to_relative(source: str, vault_root: str) -> str:
    """Normalize a source path to vault-relative form.

    Handles both absolute paths (from ChromaDB metadata) and already-relative
    paths, returning a consistent vault-relative string for set intersection.
    """
    if source.startswith(vault_root):
        return str(Path(source).relative_to(vault_root))
    return source


def find_notes(
    query: str = "",
    mode: str = "hybrid",
    folder: str = "",
    recursive: bool = False,
    frontmatter: list[FilterCondition] | None = None,
    date_start: str = "",
    date_end: str = "",
    date_type: str = "modified",
    sort: str = "",
    include_fields: list[str] | None = None,
    n_results: int = 20,
    offset: int = 0,
) -> str:
    """Find vault notes using any combination of semantic search, frontmatter
    filters, folder scope, and date range.

    Args:
        query: Semantic/keyword search text. When provided, results include
            matched content chunks. When omitted, returns file paths.
        mode: Search mode when query is provided - "hybrid" (default),
            "semantic", or "keyword".
        folder: Restrict to files within this folder (relative to vault root).
        recursive: Include subfolders when folder is set (default false).
        frontmatter: Metadata filter conditions (AND logic). Each needs
            'field', optional 'value' and 'match_type'.
        date_start: Start of date range (inclusive), format: YYYY-MM-DD.
        date_end: End of date range (inclusive), format: YYYY-MM-DD.
        date_type: Which date to check - "modified" (default) or "created"
            (frontmatter Date field, falls back to filesystem creation time).
        sort: Sort order - "relevance" (default when query provided,
            requires query), "name" (default without query), "modified",
            or "created".
        include_fields: Frontmatter field names to include in results
            (only applies when query is not provided).
        n_results: Maximum number of results (default 20).
        offset: Number of results to skip for pagination.

    Returns:
        JSON with results and total count. With query: list of
        {source, content, heading} dicts. Without query: list of paths
        or {path, field1, field2, ...} dicts when include_fields is set.
    """
    has_query = bool(query and query.strip())
    has_folder = bool(folder)
    has_frontmatter = bool(frontmatter)
    has_date = bool(date_start or date_end)

    if not (has_query or has_folder or has_frontmatter or has_date):
        return err(
            "At least one filter is required: query, folder, frontmatter, "
            "or date_start/date_end"
        )

    # Resolve default sort: "relevance" when query provided, "name" otherwise
    if not sort:
        sort = "relevance" if has_query else "name"

    if sort not in VALID_SORTS:
        return err(f"sort must be one of {sorted(VALID_SORTS)}, got '{sort}'")

    if sort == "relevance" and not has_query:
        return err("sort='relevance' requires a query parameter")

    if has_query and mode not in VALID_MODES:
        return err(f"mode must be one of {sorted(VALID_MODES)}, got '{mode}'")

    if date_type not in ("created", "modified"):
        return err(f"date_type must be 'created' or 'modified', got '{date_type}'")

    # Parse dates
    parsed_start = None
    parsed_end = None
    if date_start:
        try:
            parsed_start = datetime.strptime(date_start, "%Y-%m-%d")
        except ValueError:
            return err(
                f"Invalid date_start format. Use YYYY-MM-DD, got '{date_start}'"
            )
    if date_end:
        try:
            parsed_end = datetime.strptime(date_end, "%Y-%m-%d")
        except ValueError:
            return err(
                f"Invalid date_end format. Use YYYY-MM-DD, got '{date_end}'"
            )
    if parsed_start and parsed_end and parsed_start > parsed_end:
        return err(f"date_start ({date_start}) is after date_end ({date_end})")

    # Validate frontmatter filters
    parsed_filters, filter_err = _validate_filters(frontmatter)
    if filter_err:
        return err(filter_err)

    # Validate pagination
    validated_offset, validated_limit, pagination_error = validate_pagination(
        offset, n_results
    )
    if pagination_error:
        return err(pagination_error)

    # Resolve folder
    folder_path = None
    if has_folder:
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return err(folder_err)

    if has_query:
        return _query_mode(
            query,
            mode,
            folder_path,
            recursive,
            parsed_filters,
            parsed_start,
            parsed_end,
            date_type,
            sort,
            include_fields,
            validated_offset,
            validated_limit,
        )
    else:
        return _scan_mode(
            folder_path,
            recursive,
            parsed_filters,
            parsed_start,
            parsed_end,
            date_type,
            sort,
            include_fields,
            validated_offset,
            validated_limit,
        )


def _scan_mode(
    folder_path,
    recursive,
    parsed_filters,
    date_start,
    date_end,
    date_type,
    sort,
    include_fields,
    offset,
    limit,
) -> str:
    """Pure vault-scan mode: no semantic query."""
    matching = _find_matching_files(
        None,
        "",
        "contains",
        parsed_filters,
        include_fields=include_fields,
        folder=folder_path,
        recursive=recursive,
        date_start=date_start,
        date_end=date_end,
        date_type=date_type,
    )

    if not matching:
        return ok("No matching notes found", results=[], total=0)

    # Sort
    if sort in ("modified", "created"):
        matching = _sort_by_date(matching, sort)
    # else: "name" — _find_matching_files already returns sorted by name

    total = len(matching)
    page = matching[offset : offset + limit]
    return ok(results=page, total=total)


def _sort_by_date(items: list, date_type: str, key_fn=None) -> list:
    """Sort results by file date (most recent first).

    Args:
        items: List of results (strings, dicts with "path", or dicts with "source").
        date_type: "modified" or "created".
        key_fn: Optional function to extract path string from an item.
            Defaults to checking "path" key for dicts, or using the string directly.
    """
    from services.vault import (
        extract_frontmatter,
        get_file_creation_time,
        parse_frontmatter_date,
        resolve_file,
    )

    def get_date(item):
        if key_fn:
            path_str = key_fn(item)
        else:
            path_str = item["path"] if isinstance(item, dict) else item
        resolved, _ = resolve_file(path_str)
        if not resolved:
            return datetime.min
        if date_type == "created":
            fm = extract_frontmatter(resolved)
            d = parse_frontmatter_date(fm.get("Date"))
            if d is None:
                d = get_file_creation_time(resolved)
            return d or datetime.min
        else:
            try:
                return datetime.fromtimestamp(resolved.stat().st_mtime)
            except OSError:
                return datetime.min

    return sorted(items, key=get_date, reverse=True)


def _query_mode(
    query,
    mode,
    folder_path,
    recursive,
    parsed_filters,
    date_start,
    date_end,
    date_type,
    sort,
    include_fields,
    offset,
    limit,
) -> str:
    """Semantic/keyword search with optional vault-scan post-filtering."""
    has_filters = folder_path or parsed_filters or date_start or date_end

    try:
        # When intersecting, over-fetch to account for results lost to filtering.
        # Ensure we always fetch enough to cover the requested page.
        search_limit = max(offset + limit, 500) if has_filters else offset + limit
        results = search_results(query, search_limit, mode)
    except Exception as e:
        return err(f"Search failed: {e}. Is the vault indexed? Run: python src/index_vault.py")

    if not results:
        return ok("No matching notes found", results=[], total=0)

    if has_filters:
        # Build filter set from vault scan (returns vault-relative paths)
        filter_paths = set(
            _find_matching_files(
                None, "", "contains", parsed_filters,
                folder=folder_path, recursive=recursive,
                date_start=date_start, date_end=date_end, date_type=date_type,
            )
        )
        # Normalize search sources to vault-relative paths for intersection.
        # Index stores absolute paths as source metadata; _find_matching_files
        # returns vault-relative paths.
        vault_root = str(vault_service.VAULT_PATH.resolve())
        results = [
            r for r in results
            if _to_relative(r["source"], vault_root) in filter_paths
        ]

    if not results:
        return ok("No matching notes found", results=[], total=0)

    # Apply sort (relevance = semantic ranking order, already the default)
    if sort in ("modified", "created"):
        results = _sort_by_date(
            results, sort, key_fn=lambda r: r["source"]
        )
    elif sort == "name":
        results.sort(key=lambda r: r["source"])

    total = len(results)
    page = results[offset:offset + limit]
    return ok(results=page, total=total)



def web_search(query: str) -> str:
    """Search the web using DuckDuckGo.

    Args:
        query: Search query string.

    Returns:
        JSON response with search results or error.
    """
    if not query or not query.strip():
        return err("query cannot be empty")

    try:
        results = DDGS().text(query, max_results=5)
    except Exception as e:
        return err(f"Search failed: {e}")

    if not results:
        return ok("No web results found", results=[])

    # Format results for readability
    formatted = [
        {"title": r.get("title", "No title"), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in results
    ]
    return ok(results=formatted)
