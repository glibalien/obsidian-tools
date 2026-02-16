"""Search tools for vault and web."""

from ddgs import DDGS

from search_vault import search_results
from services.vault import err, ok


def search_vault(
    query: str,
    n_results: int = 5,
    mode: str = "hybrid",
    chunk_type: str = "",
) -> str:
    """Search the Obsidian vault using hybrid search (semantic + keyword).

    Args:
        query: Natural language search query.
        n_results: Number of results to return (default 5).
        mode: Search mode - "hybrid" (default), "semantic", or "keyword".
        chunk_type: Filter by chunk type - "frontmatter", "section", "paragraph",
            "sentence", or "fragment". Empty string means no filter (default).

    Returns:
        JSON response with search results or error.
    """
    try:
        results = search_results(query, n_results, mode, chunk_type=chunk_type or None)
    except Exception as e:
        return err(f"Search failed: {e}. Is the vault indexed? Run: python src/index_vault.py")

    if not results:
        return ok("No matching documents found", results=[])

    return ok(results=results)


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
