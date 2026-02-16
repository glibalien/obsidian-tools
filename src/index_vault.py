#!/usr/bin/env python3
"""Index the Obsidian vault into ChromaDB for semantic search."""

import hashlib
import os
import re
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
        stripped = line.strip()
        # Track code fence state
        if stripped.startswith("```") or stripped.startswith("~~~"):
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


def chunk_markdown(text: str, max_chunk_size: int = 1500) -> list[dict]:
    """Chunk markdown text using structure-aware splitting.

    Strips frontmatter, splits on headings, then chunks each section
    by paragraph and sentence boundaries as needed. Falls back to
    fixed character splitting for text with no natural boundaries.

    Returns list of dicts with keys: text, heading, chunk_type.
    chunk_type is one of: section, paragraph, sentence, fragment.
    """
    if not text or not text.strip():
        return []

    body = _strip_frontmatter(text)
    if not body.strip():
        return []

    sections = _split_by_headings(body)
    all_chunks: list[dict] = []

    for heading, content in sections:
        # Build the chunk text: include heading for search context
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
    
    # Read and chunk the file
    content = md_file.read_text(encoding='utf-8', errors='ignore')
    chunks = _fixed_chunk_text(content)
    
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
