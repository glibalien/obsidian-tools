#!/usr/bin/env python3
"""Index the Obsidian vault into ChromaDB for semantic search."""

import hashlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from chunking import _parse_frontmatter, chunk_markdown
from config import VAULT_PATH, CHROMA_PATH, INDEX_WORKERS, setup_logging
from services.chroma import get_collection, purge_database
from services.vault import get_vault_files


def get_last_run_file() -> str:
    """Return path to the last-run marker file."""
    return os.path.join(CHROMA_PATH, ".last_indexed")


def get_last_run() -> float:
    """Get timestamp of last indexing run, or 0 if never run."""
    last_run_file = get_last_run_file()
    if os.path.exists(last_run_file):
        return os.path.getmtime(last_run_file)
    return 0


def mark_run(timestamp: float | None = None) -> None:
    """Mark the given timestamp (or current time) as last run.

    Args:
        timestamp: Unix timestamp to record. Defaults to current time.
    """
    os.makedirs(CHROMA_PATH, exist_ok=True)
    marker = get_last_run_file()
    with open(marker, 'w') as f:
        f.write(datetime.now().isoformat())
    if timestamp is not None:
        os.utime(marker, (timestamp, timestamp))


def get_manifest_file() -> str:
    """Return path to the indexed sources manifest file."""
    return os.path.join(CHROMA_PATH, "indexed_sources.json")


def get_dirty_flag() -> str:
    """Return path to the in-progress sentinel file."""
    return os.path.join(CHROMA_PATH, ".indexing_in_progress")


def load_manifest() -> set[str] | None:
    """Load set of previously indexed source paths.

    Returns None if no manifest exists, it cannot be read, or a dirty
    sentinel indicates the previous run did not complete cleanly —
    all of which trigger a full-scan fallback in prune_deleted_files.
    """
    if os.path.exists(get_dirty_flag()):
        logger.warning("Previous indexing run was incomplete; falling back to full scan")
        return None
    path = get_manifest_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
            logger.warning("indexed_sources manifest has unexpected schema, falling back to full scan")
            return None
        return set(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load indexed_sources manifest: %s — falling back to full scan", e)
        return None


def save_manifest(sources: set[str]) -> bool:
    """Save the current set of indexed source paths to disk.

    Returns True on success, False if the write failed.
    """
    os.makedirs(CHROMA_PATH, exist_ok=True)
    try:
        with open(get_manifest_file(), "w") as f:
            json.dump(sorted(sources), f)
        return True
    except OSError as e:
        logger.warning("Failed to save indexed_sources manifest: %s", e)
        return False


def _prepare_file_chunks(
    md_file: Path,
) -> tuple[str, list[str], list[str], list[dict]] | None:
    """Read and chunk a file, returning indexing data without touching ChromaDB.

    Safe to call from worker threads — pure Python only.
    Returns (source, ids, documents, metadatas) or None if the file is empty.
    """
    content = md_file.read_text(encoding='utf-8', errors='ignore')
    frontmatter = _parse_frontmatter(content)
    chunks = chunk_markdown(content, frontmatter=frontmatter)

    if not chunks:
        return None

    source = str(md_file)
    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        ids.append(hashlib.md5(f"{md_file}_{i}".encode()).hexdigest())
        documents.append(f"[{md_file.stem}] {chunk['text']}")
        metadatas.append({
            "source": source,
            "chunk": i,
            "heading": chunk["heading"],
            "chunk_type": chunk["chunk_type"],
        })

    return source, ids, documents, metadatas


def index_file(md_file: Path) -> None:
    """Index a single markdown file, replacing any existing chunks."""
    result = _prepare_file_chunks(md_file)
    source = str(md_file)
    collection = get_collection()
    existing = collection.get(where={"source": source}, include=[])
    if existing['ids']:
        collection.delete(ids=existing['ids'])
    if result is not None:
        _, ids, documents, metadatas = result
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)


def prune_deleted_files(valid_sources: set[str], indexed_sources: set[str] | None = None) -> int:
    """Remove entries for files that no longer exist. Returns count of deleted sources.

    Uses a manifest-based fast path when indexed_sources is provided,
    falling back to a full metadata scan when it is None (first run or --full).
    """
    collection = get_collection()

    if indexed_sources is not None:
        # Fast path: only examine sources known to be indexed
        deleted_sources = indexed_sources - valid_sources
        for source in deleted_sources:
            collection.delete(where={"source": source})
        return len(deleted_sources)

    # Slow path: full metadata scan (no manifest available)
    all_entries = collection.get(include=["metadatas"])
    if not all_entries["ids"]:
        return 0

    ids_to_delete = []
    deleted_sources = set()
    for doc_id, metadata in zip(all_entries["ids"], all_entries["metadatas"]):
        source = metadata.get("source", "")
        if source not in valid_sources:
            ids_to_delete.append(doc_id)
            deleted_sources.add(source)

    if ids_to_delete:
        batch_size = 5000
        for i in range(0, len(ids_to_delete), batch_size):
            collection.delete(ids=ids_to_delete[i:i + batch_size])

    return len(deleted_sources)


def index_vault(full: bool = False) -> None:
    """Index the vault, updating only changed files unless full=True."""
    scan_start = time.time()
    last_run = 0 if full else get_last_run()

    # Get all valid markdown files
    all_files = get_vault_files(VAULT_PATH)
    valid_sources = set(str(f) for f in all_files)

    # Load manifest for fast pruning (skip on --full to force full scan).
    # Must happen before writing the dirty sentinel so the sentinel from a
    # prior incomplete run (if any) is still visible here.
    indexed_sources = None if full else load_manifest()

    # Write dirty sentinel — removed only after successful manifest save
    try:
        os.makedirs(CHROMA_PATH, exist_ok=True)
        with open(get_dirty_flag(), "w"):
            pass
    except OSError as e:
        logger.warning("Failed to write indexing sentinel: %s — disabling manifest trust", e)
        indexed_sources = None
        try:
            os.remove(get_manifest_file())
        except OSError:
            pass  # already absent or unremovable; next run will fall back to slow path

    # Collect files to index
    to_index = []
    for md_file in all_files:
        try:
            modified = md_file.stat().st_mtime > last_run
        except FileNotFoundError:
            continue
        if modified:
            to_index.append(md_file)

    # Compute chunks in parallel (pure Python in worker threads).
    # All ChromaDB writes happen on the main thread — ChromaDB is not
    # thread-safe (telemetry races, singleton init races, SQLite deadlocks).
    collection = get_collection()
    indexed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=INDEX_WORKERS) as executor:
        futures = {executor.submit(_prepare_file_chunks, f): f for f in to_index}
        for future in as_completed(futures):
            md_file = futures[future]
            try:
                result = future.result()
                source = str(md_file)
                existing = collection.get(where={"source": source}, include=[])
                if existing['ids']:
                    collection.delete(ids=existing['ids'])
                if result is not None:
                    _, ids, documents, metadatas = result
                    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
                indexed += 1
                if indexed % 100 == 0:
                    logger.info("Indexed %s files...", indexed)
            except FileNotFoundError:
                logger.debug("File disappeared during indexing: %s", md_file)
                valid_sources.discard(str(md_file))
            except Exception:
                failed += 1
                logger.error("Failed to index %s", md_file, exc_info=True)

    # Prune deleted files
    pruned = prune_deleted_files(valid_sources, indexed_sources=indexed_sources)

    # Save updated manifest; remove dirty sentinel only on success
    if save_manifest(valid_sources):
        try:
            os.remove(get_dirty_flag())
        except OSError as e:
            logger.warning("Failed to remove indexing sentinel %s: %s — future runs will use full scan",
                           get_dirty_flag(), e)

    if failed:
        logger.warning("Skipping last-run update due to %s failure(s) — next run will retry", failed)
    else:
        mark_run(scan_start)
    logger.info("Done. Indexed %s new/modified files (%s failed). Pruned %s deleted source(s). Total chunks: %s",
                indexed, failed, pruned, collection.count())


if __name__ == "__main__":
    setup_logging("index_vault")
    full_reindex = "--full" in sys.argv
    reset_db = "--reset" in sys.argv
    if reset_db:
        print("Deleting ChromaDB database and rebuilding from scratch...")
        purge_database()
        full_reindex = True
    elif full_reindex:
        print("Running full reindex...")
    print(f"Vault: {VAULT_PATH}")
    print(f"ChromaDB: {CHROMA_PATH}")
    index_vault(full=full_reindex)
