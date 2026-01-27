#!/usr/bin/env python3
"""Semantic search against the Obsidian vault using ChromaDB."""

import sys
import chromadb

from config import CHROMA_PATH


def search_results(query: str, n_results: int = 5) -> list[dict[str, str]]:
    """Search the vault and return structured results.

    Returns a list of dicts with 'source' and 'content' keys.
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection("obsidian_vault")

    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    return [
        {"source": metadata["source"], "content": doc[:500]}
        for doc, metadata in zip(results["documents"][0], results["metadatas"][0])
    ]


def search(query: str, n_results: int = 5) -> None:
    """Search the vault and print results."""
    for result in search_results(query, n_results):
        print(f"\n--- {result['source']} ---")
        print(result["content"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search_vault.py 'query'")
        sys.exit(1)
    search(" ".join(sys.argv[1:]))
