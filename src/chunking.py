"""Structure-aware markdown chunking for search indexing."""

import logging
import re

import yaml

from services.vault import is_fence_line

logger = logging.getLogger(__name__)

# Frontmatter fields excluded from search indexing (display/config only)
FRONTMATTER_EXCLUDE = {"cssclass", "cssclasses", "aliases", "publish", "permalink"}

OVERLAP_SENTENCES = 2


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


def _split_by_headings(text: str) -> list[tuple[str, list[str], str]]:
    """Split text on markdown headings, respecting code fences.

    Returns list of (heading, heading_chain, content) tuples.
    - heading: raw heading line (e.g. "## Foo") or "top-level"
    - heading_chain: list of clean heading names from root to current
      (e.g. ["Parent", "Child"]). Empty for top-level content.
    - content: text under this heading (before the next heading)
    """
    lines = text.split("\n")
    sections: list[tuple[str, list[str], str]] = []
    current_heading = "top-level"
    current_chain: list[str] = []
    current_lines: list[str] = []
    in_fence = False

    # Stack of (level, clean_name) for building heading chains
    stack: list[tuple[int, str]] = []

    for line in lines:
        # Track code fence state
        if is_fence_line(line):
            in_fence = not in_fence

        # Check for heading (only outside code fences)
        heading_match = None if in_fence else re.match(r"^(#{1,6}) (.+)", line)
        if heading_match:
            # Save previous section
            content = "\n".join(current_lines)
            if content.strip() or current_heading != "top-level":
                sections.append((current_heading, current_chain, content))

            # Parse heading level and clean name
            level = len(heading_match.group(1))
            clean_name = heading_match.group(2).strip()

            # Pop stack entries with level >= current
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, clean_name))

            current_heading = line.strip()
            current_chain = [name for _, name in stack]
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    content = "\n".join(current_lines)
    if content.strip() or current_heading != "top-level":
        sections.append((current_heading, current_chain, content))

    return sections


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries (. ? ! followed by space).

    Suppresses splitting after e.g. and i.e. — the only abbreviations
    that unambiguously never end sentences.
    """
    # Find candidate split positions: sentence-ending punctuation + space
    result = []
    last = 0
    for m in re.finditer(r"[.?!] ", text):
        pos = m.start()  # position of the punctuation mark
        char = text[pos]

        if char == ".":
            before = text[last:pos]

            # e.g. / i.e. — before the final period we see "e.g" or "i.e"
            stripped = before.rstrip()
            if len(stripped) >= 3 and stripped[-3:].lower() in ("e.g", "i.e"):
                continue

        # Valid split point
        split_at = m.end()  # after the space
        result.append(text[last:split_at - 1])  # exclude the trailing space
        last = split_at

    # Remaining text
    if last < len(text):
        result.append(text[last:])

    return [p for p in result if p]


def _chunk_sentences(
    text: str, heading: str, heading_chain: list[str], max_chunk_size: int
) -> list[dict]:
    """Accumulate sentences into chunks with overlap carry-forward.

    When flushing a buffer, the last OVERLAP_SENTENCES sentences are
    carried forward as the start of the next chunk for continuity.
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []

    chunks: list[dict] = []
    buffer: list[str] = []
    buf_len = 0

    for sentence in sentences:
        added_len = len(sentence) + (1 if buffer else 0)  # space separator
        if buf_len + added_len <= max_chunk_size:
            buffer.append(sentence)
            buf_len += added_len
        else:
            # Flush current buffer
            if buffer:
                chunks.append({
                    "text": " ".join(buffer),
                    "heading": heading,
                    "heading_chain": heading_chain,
                    "chunk_type": "sentence",
                })
                # Carry forward last N sentences
                carry = buffer[-OVERLAP_SENTENCES:]
                buffer = list(carry)
                buf_len = sum(len(s) for s in buffer) + max(0, len(buffer) - 1)

            # Check if this single sentence fits (with carry-forward)
            added_len = len(sentence) + (1 if buffer else 0)
            if buf_len + added_len <= max_chunk_size:
                buffer.append(sentence)
                buf_len += added_len
            elif len(sentence) <= max_chunk_size:
                # Sentence fits alone but not with carry — flush carry, start fresh
                if buffer:
                    chunks.append({
                        "text": " ".join(buffer),
                        "heading": heading,
                        "heading_chain": heading_chain,
                        "chunk_type": "sentence",
                    })
                buffer = [sentence]
                buf_len = len(sentence)
            else:
                # Flush any carry-forward before fragments
                if buffer:
                    chunks.append({
                        "text": " ".join(buffer),
                        "heading": heading,
                        "heading_chain": heading_chain,
                        "chunk_type": "sentence",
                    })
                    buffer = []
                    buf_len = 0
                # Sentence too big — fall back to fixed chunking
                for fragment in _fixed_chunk_text(sentence, chunk_size=max_chunk_size, overlap=50):
                    if fragment.strip():
                        chunks.append({
                            "text": fragment,
                            "heading": heading,
                            "heading_chain": heading_chain,
                            "chunk_type": "fragment",
                        })

    if buffer and " ".join(buffer).strip():
        chunks.append({
            "text": " ".join(buffer),
            "heading": heading,
            "heading_chain": heading_chain,
            "chunk_type": "sentence",
        })

    return chunks


def _trailing_sentences(text: str, n: int) -> str:
    """Extract the last n sentences from text."""
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    tail = sentences[-n:]
    return " ".join(tail)


def _chunk_text_block(
    text: str, heading: str, heading_chain: list[str], max_chunk_size: int
) -> list[dict]:
    """Chunk a text block: try whole section, then paragraphs, then sentences."""
    if len(text) <= max_chunk_size:
        return [{
            "text": text,
            "heading": heading,
            "heading_chain": heading_chain,
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
                        "heading_chain": heading_chain,
                        "chunk_type": "paragraph",
                    })
                    current = ""
                if len(para) <= max_chunk_size:
                    current = para
                else:
                    # Paragraph too big — split by sentences
                    chunks.extend(
                        _chunk_sentences(para, heading, heading_chain, max_chunk_size)
                    )
        if current.strip():
            chunks.append({
                "text": current,
                "heading": heading,
                "heading_chain": heading_chain,
                "chunk_type": "paragraph",
            })
        return chunks

    # Single paragraph too big — split by sentences
    return _chunk_sentences(text, heading, heading_chain, max_chunk_size)


def chunk_markdown(
    text: str, max_chunk_size: int = 1500, frontmatter: dict | None = None,
) -> list[dict]:
    """Chunk markdown text using structure-aware splitting.

    Strips frontmatter, splits on headings, then chunks each section
    by paragraph and sentence boundaries as needed. Falls back to
    fixed character splitting for text with no natural boundaries.

    If frontmatter is provided, creates a dedicated frontmatter chunk
    prepended to the result list so metadata is searchable.

    Returns list of dicts with keys: text, heading, heading_chain, chunk_type.
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
                "heading_chain": [],
                "chunk_type": "frontmatter",
            })

    # Chunk the body content
    body = _strip_frontmatter(text)
    if body.strip():
        sections = _split_by_headings(body)
        prev_trailing = ""
        for heading, heading_chain, content in sections:
            if heading == "top-level":
                block = content.strip()
            else:
                block = (heading + "\n" + content).strip()
            if not block:
                continue
            section_chunks = _chunk_text_block(
                block, heading, heading_chain, max_chunk_size
            )
            # Prepend cross-section overlap to first chunk
            if prev_trailing and section_chunks:
                section_chunks[0] = dict(section_chunks[0])
                section_chunks[0]["text"] = (
                    prev_trailing + "\n" + section_chunks[0]["text"]
                )
            all_chunks.extend(section_chunks)
            # Save trailing sentences for next section
            if section_chunks:
                last_text = section_chunks[-1]["text"]
                prev_trailing = _trailing_sentences(last_text, OVERLAP_SENTENCES)
            else:
                prev_trailing = ""

    return all_chunks
