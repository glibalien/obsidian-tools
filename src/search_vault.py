#!/usr/bin/env python3
"""Semantic search against the Obsidian vault using ChromaDB."""

import sys
import chromadb

from config import CHROMA_PATH


def search(query: str, n_results: int = 5) -> None:
    """Search the vault and print results."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection("obsidian_vault")
    
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )
    
    for doc, metadata in zip(results['documents'][0], results['metadatas'][0]):
        print(f"\n--- {metadata['source']} ---")
        print(doc[:500])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search_vault.py 'query'")
        sys.exit(1)
    search(" ".join(sys.argv[1:]))
