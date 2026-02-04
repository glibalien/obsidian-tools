"""Shared ChromaDB connection management."""

import os

import chromadb

from config import CHROMA_PATH

_client = None
_collection = None


def get_client() -> chromadb.PersistentClient:
    """Get or create ChromaDB client (lazy singleton)."""
    global _client
    if _client is None:
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
    return _client


def get_collection() -> chromadb.Collection:
    """Get or create the vault collection (lazy singleton)."""
    global _collection
    if _collection is None:
        _collection = get_client().get_or_create_collection("obsidian_vault")
    return _collection


def reset():
    """Reset singletons (for testing)."""
    global _client, _collection
    _client = None
    _collection = None
