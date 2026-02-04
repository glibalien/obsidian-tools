#!/usr/bin/env python3
"""Index the Obsidian vault into ChromaDB for semantic search."""

import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

from sentence_transformers import SentenceTransformer

from config import VAULT_PATH, CHROMA_PATH, EMBEDDING_MODEL
from services.chroma import get_client, get_collection
from services.vault import get_vault_files


# Lazy-loaded embedding model
_model = None


def get_model():
    """Get or create the sentence transformer model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def get_last_run_file() -> str:
    """Return path to the last-run marker file."""
    return os.path.join(CHROMA_PATH, ".last_indexed")


def get_last_run() -> float:
    """Get timestamp of last indexing run, or 0 if never run."""
    last_run_file = get_last_run_file()
    if os.path.exists(last_run_file):
        return os.path.getmtime(last_run_file)
    return 0


def mark_run() -> None:
    """Mark the current time as last run."""
    os.makedirs(CHROMA_PATH, exist_ok=True)
    with open(get_last_run_file(), 'w') as f:
        f.write(datetime.now().isoformat())


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def index_file(md_file: Path) -> None:
    """Index a single markdown file, replacing any existing chunks."""
    collection = get_collection()
    
    # Delete existing chunks for this file
    existing = collection.get(
        where={"source": str(md_file)},
        include=[]
    )
    if existing['ids']:
        collection.delete(ids=existing['ids'])
    
    # Read and chunk the file
    content = md_file.read_text(encoding='utf-8', errors='ignore')
    chunks = chunk_text(content)
    
    # Index each chunk
    for i, chunk in enumerate(chunks):
        doc_id = hashlib.md5(f"{md_file}_{i}".encode()).hexdigest()
        collection.upsert(
            ids=[doc_id],
            documents=[chunk],
            metadatas=[{"source": str(md_file), "chunk": i}]
        )


def prune_deleted_files(valid_sources: set[str]) -> int:
    """Remove entries for files that no longer exist. Returns count pruned."""
    collection = get_collection()
    all_entries = collection.get(include=["metadatas"])
    
    if not all_entries['ids']:
        return 0
    
    ids_to_delete = []
    for doc_id, metadata in zip(all_entries['ids'], all_entries['metadatas']):
        source = metadata.get('source', '')
        if source not in valid_sources:
            ids_to_delete.append(doc_id)
    
    if ids_to_delete:
        batch_size = 5000
        for i in range(0, len(ids_to_delete), batch_size):
            batch = ids_to_delete[i:i + batch_size]
            collection.delete(ids=batch)
    
    return len(ids_to_delete)


def index_vault(full: bool = False) -> None:
    """Index the vault, updating only changed files unless full=True."""
    last_run = 0 if full else get_last_run()
    
    # Get all valid markdown files
    all_files = get_vault_files(VAULT_PATH)
    valid_sources = set(str(f) for f in all_files)
    
    # Index new/modified files
    indexed = 0
    for md_file in all_files:
        if md_file.stat().st_mtime > last_run:
            index_file(md_file)
            indexed += 1
            if indexed % 100 == 0:
                print(f"Indexed {indexed} files...")
    
    # Prune deleted files
    pruned = prune_deleted_files(valid_sources)
    
    mark_run()
    collection = get_collection()
    print(f"Done. Indexed {indexed} new/modified files. Pruned {pruned} stale entries. Total chunks: {collection.count()}")


if __name__ == "__main__":
    full_reindex = "--full" in sys.argv
    if full_reindex:
        print("Running full reindex...")
    print(f"Vault: {VAULT_PATH}")
    print(f"ChromaDB: {CHROMA_PATH}")
    index_vault(full=full_reindex)
