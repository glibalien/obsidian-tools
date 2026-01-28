#!/usr/bin/env python3
"""Search interface for the Obsidian vault."""

import sys

from hybrid_search import hybrid_search, keyword_search, semantic_search

VALID_MODES = {"hybrid", "semantic", "keyword"}


def search_results(
    query: str, n_results: int = 5, mode: str = "hybrid"
) -> list[dict[str, str]]:
    """Search the vault and return structured results.

    Args:
        query: Search query string.
        n_results: Maximum number of results to return.
        mode: Search strategy -- "hybrid" (default), "semantic", or "keyword".

    Returns:
        List of dicts with 'source' and 'content' keys.

    Raises:
        ValueError: If mode is not one of the valid options.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid search mode '{mode}'. Must be one of: {VALID_MODES}")

    if mode == "hybrid":
        return hybrid_search(query, n_results)
    elif mode == "semantic":
        return semantic_search(query, n_results)
    else:
        return keyword_search(query, n_results)


def search(query: str, n_results: int = 5, mode: str = "hybrid") -> None:
    """Search the vault and print results."""
    for result in search_results(query, n_results, mode):
        print(f"\n--- {result['source']} ---")
        print(result["content"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search_vault.py 'query' [--mode hybrid|semantic|keyword]")
        sys.exit(1)

    mode = "hybrid"
    args = sys.argv[1:]
    if "--mode" in args:
        idx = args.index("--mode")
        mode = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    search(" ".join(args), mode=mode)
