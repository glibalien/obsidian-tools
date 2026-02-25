#!/usr/bin/env python3
"""Index the Obsidian vault into ChromaDB for semantic search."""

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

from config import VAULT_PATH, CHROMA_PATH
from services.chroma import get_collection
from services.vault import get_vault_files, is_fence_line



# Frontmatter fields excluded from search indexing (display/config only)
FRONTMATTER_EXCLUDE = {"cssclass", "cssclasses", "aliases", "publish", "permalink"}


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


def _fixed_chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by character count (fallback chunker)."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from text, returning the body."""
    if not text.startswith("---"):
        return text
    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return text
    # Skip past closing --- and the newline after it
    body = text[end + 4:]
    return body


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from markdown text, returning dict or {}."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        logger.debug("Invalid frontmatter YAML: %s", e)
        return {}


def _strip_wikilink_brackets(text: str) -> str:
    """Strip [[]] from wikilinks. Aliased links keep the display name."""
    return re.sub(
        r"\[\[([^\]|]*?)(?:\|([^\]]*?))?\]\]",
        lambda m: m.group(2) or m.group(1),
        text,
    )


def _format_frontmatter_value(value) -> str:
    """Convert a frontmatter value to searchable text."""
    if isinstance(value, list):
        return ", ".join(_strip_wikilink_brackets(str(v)) for v in value)
    if isinstance(value, dict):
        parts = [f"{k}: {_format_frontmatter_value(v)}" for k, v in value.items()]
        return "; ".join(parts)
    return _strip_wikilink_brackets(str(value))


def format_frontmatter_for_indexing(frontmatter: dict) -> str:
    """Convert frontmatter dict to a searchable text block.

    Each field becomes a 'key: value' line. Wikilink brackets are stripped
    so that names are searchable as plain text. Fields in FRONTMATTER_EXCLUDE
    are omitted.
    """
    lines = []
    for key, value in frontmatter.items():
        if key.lower() in FRONTMATTER_EXCLUDE:
            continue
        if value is None:
            continue
        formatted = _format_frontmatter_value(value)
        if formatted.strip():
            lines.append(f"{key}: {formatted}")
    return "\n".join(lines)


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Split text on markdown headings, respecting code fences.

    Returns list of (heading, content) tuples. Content before the first
    heading gets heading="top-level".
    """
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []
    current_heading = "top-level"
    current_lines: list[str] = []
    in_fence = False

    for line in lines:
        # Track code fence state
        if is_fence_line(line):
            in_fence = not in_fence

        # Check for heading (only outside code fences)
        if not in_fence and re.match(r"^#{1,6} ", line):
            # Save previous section
            content = "\n".join(current_lines)
            if content.strip() or current_heading != "top-level":
                sections.append((current_heading, content))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    content = "\n".join(current_lines)
    if content.strip() or current_heading != "top-level":
        sections.append((current_heading, content))

    return sections


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries (. ? ! followed by space)."""
    # Split on sentence-ending punctuation followed by a space
    parts = re.split(r"(?<=[.?!]) ", text)
    return [p for p in parts if p]


def _chunk_sentences(
    text: str, heading: str, max_chunk_size: int
) -> list[dict]:
    """Accumulate sentences into chunks, falling back to fixed chunks for oversized ones."""
    sentences = _split_sentences(text)
    if not sentences:
        return []

    chunks: list[dict] = []
    current = ""

    for sentence in sentences:
        candidate = (current + " " + sentence).strip() if current else sentence
        if len(candidate) <= max_chunk_size:
            current = candidate
        else:
            # Flush current buffer
            if current:
                chunks.append({
                    "text": current,
                    "heading": heading,
                    "chunk_type": "sentence",
                })
                current = ""
            # Check if this single sentence fits
            if len(sentence) <= max_chunk_size:
                current = sentence
            else:
                # Sentence too big — fall back to fixed chunking
                for fragment in _fixed_chunk_text(sentence, chunk_size=max_chunk_size, overlap=50):
                    if fragment.strip():
                        chunks.append({
                            "text": fragment,
                            "heading": heading,
                            "chunk_type": "fragment",
                        })

    if current.strip():
        chunks.append({
            "text": current,
            "heading": heading,
            "chunk_type": "sentence",
        })

    return chunks


def _chunk_text_block(
    text: str, heading: str, max_chunk_size: int
) -> list[dict]:
    """Chunk a text block: try whole section, then paragraphs, then sentences."""
    if len(text) <= max_chunk_size:
        return [{
            "text": text,
            "heading": heading,
            "chunk_type": "section",
        }]

    # Split on paragraphs (double newlines)
    paragraphs = re.split(r"\n\n+", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if len(paragraphs) > 1:
        # Try to accumulate paragraphs into chunks
        chunks: list[dict] = []
        current = ""
        for para in paragraphs:
            candidate = (current + "\n\n" + para).strip() if current else para
            if len(candidate) <= max_chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append({
                        "text": current,
                        "heading": heading,
                        "chunk_type": "paragraph",
                    })
                    current = ""
                if len(para) <= max_chunk_size:
                    current = para
                else:
                    # Paragraph too big — split by sentences
                    chunks.extend(
                        _chunk_sentences(para, heading, max_chunk_size)
                    )
        if current.strip():
            chunks.append({
                "text": current,
                "heading": heading,
                "chunk_type": "paragraph",
            })
        return chunks

    # Single paragraph too big — split by sentences
    return _chunk_sentences(text, heading, max_chunk_size)


def chunk_markdown(
    text: str, max_chunk_size: int = 1500, frontmatter: dict | None = None,
) -> list[dict]:
    """Chunk markdown text using structure-aware splitting.

    Strips frontmatter, splits on headings, then chunks each section
    by paragraph and sentence boundaries as needed. Falls back to
    fixed character splitting for text with no natural boundaries.

    If frontmatter is provided, creates a dedicated frontmatter chunk
    prepended to the result list so metadata is searchable.

    Returns list of dicts with keys: text, heading, chunk_type.
    chunk_type is one of: frontmatter, section, paragraph, sentence, fragment.
    """
    if not text or not text.strip():
        return []

    all_chunks: list[dict] = []

    # Create frontmatter chunk if provided
    if frontmatter:
        fm_text = format_frontmatter_for_indexing(frontmatter)
        if fm_text.strip():
            all_chunks.append({
                "text": fm_text,
                "heading": "frontmatter",
                "chunk_type": "frontmatter",
            })

    # Chunk the body content
    body = _strip_frontmatter(text)
    if body.strip():
        sections = _split_by_headings(body)
        for heading, content in sections:
            if heading == "top-level":
                block = content.strip()
            else:
                block = (heading + "\n" + content).strip()
            if not block:
                continue
            all_chunks.extend(_chunk_text_block(block, heading, max_chunk_size))

    return all_chunks



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
    
    # Read and chunk the file (including frontmatter for search)
    content = md_file.read_text(encoding='utf-8', errors='ignore')
    frontmatter = _parse_frontmatter(content)
    chunks = chunk_markdown(content, frontmatter=frontmatter)

    if not chunks:
        return

    # Batch upsert all chunks at once
    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        ids.append(hashlib.md5(f"{md_file}_{i}".encode()).hexdigest())
        documents.append(f"[{md_file.stem}] {chunk['text']}")
        metadatas.append({
            "source": str(md_file),
            "chunk": i,
            "heading": chunk["heading"],
            "chunk_type": chunk["chunk_type"],
        })

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

    # Index new/modified files
    indexed = 0
    for md_file in all_files:
        try:
            modified = md_file.stat().st_mtime > last_run
        except FileNotFoundError:
            continue
        if modified:
            try:
                index_file(md_file)
            except FileNotFoundError:
                continue
            indexed += 1
            if indexed % 100 == 0:
                logger.info("Indexed %s files...", indexed)

    # Prune deleted files
    pruned = prune_deleted_files(valid_sources, indexed_sources=indexed_sources)

    # Save updated manifest; remove dirty sentinel only on success
    if save_manifest(valid_sources):
        try:
            os.remove(get_dirty_flag())
        except OSError as e:
            logger.warning("Failed to remove indexing sentinel %s: %s — future runs will use full scan",
                           get_dirty_flag(), e)

    mark_run(scan_start)
    collection = get_collection()
    logger.info("Done. Indexed %s new/modified files. Pruned %s deleted source(s). Total chunks: %s",
                indexed, pruned, collection.count())


if __name__ == "__main__":
    full_reindex = "--full" in sys.argv
    if full_reindex:
        print("Running full reindex...")
    print(f"Vault: {VAULT_PATH}")
    print(f"ChromaDB: {CHROMA_PATH}")
    index_vault(full=full_reindex)
