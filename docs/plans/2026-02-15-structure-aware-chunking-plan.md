# Structure-Aware Chunking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace naive character-split chunking with structure-aware markdown chunking that respects frontmatter, headings, paragraphs, and sentences.

**Architecture:** Single-pass regex splitter in `index_vault.py`. Frontmatter is stripped (not indexed). Body is split by headings, with fallbacks to paragraphs, sentences, and finally character splits. Each chunk carries `heading` and `chunk_type` metadata. `hybrid_search.py` passes `heading` through to search results.

**Tech Stack:** Python stdlib only (re module). No new dependencies.

---

### Task 1: Create test file and write tests for `chunk_markdown()`

**Files:**
- Create: `tests/test_chunking.py`

**Step 1: Write tests for the chunking function**

```python
"""Tests for structure-aware markdown chunking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from index_vault import chunk_markdown, _fixed_chunk_text


class TestFixedChunkText:
    """Tests for the renamed legacy chunker."""

    def test_basic_split(self):
        text = "a" * 1000
        chunks = _fixed_chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) == 3
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500

    def test_short_text_single_chunk(self):
        chunks = _fixed_chunk_text("short", chunk_size=500, overlap=50)
        assert chunks == ["short"]

    def test_empty_text(self):
        chunks = _fixed_chunk_text("", chunk_size=500, overlap=50)
        assert chunks == [""]


class TestChunkMarkdownFrontmatter:
    """Tests for frontmatter handling."""

    def test_frontmatter_stripped(self):
        text = "---\ntags:\n  - test\n---\n\n# Title\n\nBody content."
        chunks = chunk_markdown(text)
        for chunk in chunks:
            assert "tags:" not in chunk["text"]
            assert "---" not in chunk["text"]

    def test_no_frontmatter(self):
        text = "# Title\n\nBody content."
        chunks = chunk_markdown(text)
        assert len(chunks) >= 1
        assert chunks[0]["heading"] == "# Title"

    def test_frontmatter_only(self):
        text = "---\ntags: test\n---\n"
        chunks = chunk_markdown(text)
        assert chunks == []


class TestChunkMarkdownHeadings:
    """Tests for heading-based splitting."""

    def test_splits_on_headings(self):
        text = "# Title\n\nIntro.\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "# Title" in headings
        assert "## Section A" in headings
        assert "## Section B" in headings

    def test_chunk_type_is_section(self):
        text = "## Heading\n\nSmall section content."
        chunks = chunk_markdown(text)
        assert all(c["chunk_type"] == "section" for c in chunks)

    def test_top_level_content_before_first_heading(self):
        text = "Some intro text before any heading.\n\n# First Heading\n\nContent."
        chunks = chunk_markdown(text)
        top_level = [c for c in chunks if c["heading"] == "top-level"]
        assert len(top_level) == 1
        assert "intro text" in top_level[0]["text"]

    def test_heading_in_code_fence_ignored(self):
        text = "## Real Heading\n\nContent.\n\n```\n## Not a heading\n```\n\nMore content."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "## Real Heading" in headings
        assert "## Not a heading" not in headings

    def test_tilde_code_fence(self):
        text = "## Heading\n\nBefore.\n\n~~~\n## Fake\n~~~\n\nAfter."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "## Fake" not in headings

    def test_nested_headings(self):
        text = "# H1\n\nIntro.\n\n## H2\n\nSub.\n\n### H3\n\nDeep."
        chunks = chunk_markdown(text)
        headings = [c["heading"] for c in chunks]
        assert "# H1" in headings
        assert "## H2" in headings
        assert "### H3" in headings


class TestChunkMarkdownParagraphFallback:
    """Tests for paragraph splitting of oversized sections."""

    def test_large_section_split_on_paragraphs(self):
        paragraphs = ["Paragraph " + str(i) + ". " + "x" * 400 for i in range(5)]
        text = "## Big Section\n\n" + "\n\n".join(paragraphs)
        chunks = chunk_markdown(text, max_chunk_size=500)
        assert len(chunks) > 1
        assert any(c["chunk_type"] == "paragraph" for c in chunks)
        assert all(c["heading"] == "## Big Section" for c in chunks)


class TestChunkMarkdownSentenceFallback:
    """Tests for sentence splitting of oversized paragraphs."""

    def test_large_paragraph_split_on_sentences(self):
        sentences = ["This is sentence number " + str(i) + "." for i in range(100)]
        text = "## Section\n\n" + " ".join(sentences)
        chunks = chunk_markdown(text, max_chunk_size=500)
        assert len(chunks) > 1
        assert any(c["chunk_type"] == "sentence" for c in chunks)


class TestChunkMarkdownFragmentFallback:
    """Tests for character-split fallback."""

    def test_no_boundaries_falls_back_to_fixed_chunk(self):
        # One giant word with no sentence or paragraph boundaries
        text = "## Section\n\n" + "x" * 3000
        chunks = chunk_markdown(text, max_chunk_size=500)
        assert len(chunks) > 1
        assert any(c["chunk_type"] == "fragment" for c in chunks)


class TestChunkMarkdownMetadata:
    """Tests for metadata correctness."""

    def test_chunk_has_required_keys(self):
        text = "# Title\n\nContent."
        chunks = chunk_markdown(text)
        for chunk in chunks:
            assert "text" in chunk
            assert "heading" in chunk
            assert "chunk_type" in chunk

    def test_empty_input(self):
        chunks = chunk_markdown("")
        assert chunks == []

    def test_whitespace_only(self):
        chunks = chunk_markdown("   \n\n  ")
        assert chunks == []
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: ImportError — `chunk_markdown` and `_fixed_chunk_text` don't exist yet.

**Step 3: Commit the test file**

```bash
git add tests/test_chunking.py
git commit -m "test: add tests for structure-aware chunking"
```

---

### Task 2: Rename `chunk_text` and implement `chunk_markdown()`

**Files:**
- Modify: `src/index_vault.py:49-57` (rename `chunk_text` → `_fixed_chunk_text`)
- Modify: `src/index_vault.py` (add `chunk_markdown` function)

**Step 1: Rename `chunk_text` to `_fixed_chunk_text`**

In `src/index_vault.py`, rename the function at line 49 from `chunk_text` to `_fixed_chunk_text`. No callers outside this file use it (verified: `index_file` at line 74 is the only caller).

**Step 2: Implement `chunk_markdown`**

Add these imports at the top of `src/index_vault.py`:

```python
import re
```

Add the following functions after `_fixed_chunk_text`:

```python
def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown text.

    Returns the body content after the closing --- delimiter.
    If no frontmatter is found, returns the original text.
    """
    if not text.startswith("---"):
        return text
    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return text
    # Skip past the closing --- and any trailing newline
    body_start = end + 4
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return text[body_start:]


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Split markdown text into sections by headings.

    Respects code fences (``` and ~~~) — headings inside fences are ignored.

    Returns:
        List of (heading, content) tuples. Content before the first heading
        gets heading="top-level".
    """
    sections: list[tuple[str, str]] = []
    current_heading = "top-level"
    current_lines: list[str] = []
    in_fence = False

    for line in text.split("\n"):
        # Track code fences
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence

        # Check for heading (only outside code fences)
        if not in_fence and re.match(r"^#{1,6} ", line):
            # Save previous section
            section_text = "\n".join(current_lines).strip()
            if section_text or current_heading != "top-level":
                sections.append((current_heading, section_text))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    section_text = "\n".join(current_lines).strip()
    if section_text or current_heading != "top-level":
        sections.append((current_heading, section_text))

    return sections


def _split_sentences(text: str) -> list[str]:
    """Split text on sentence boundaries."""
    # Split on period/question/exclamation followed by space
    parts = re.split(r'(?<=[.?!])\s+', text)
    return [p for p in parts if p.strip()]


def _chunk_text_block(
    text: str, heading: str, max_chunk_size: int
) -> list[dict[str, str]]:
    """Chunk a text block using paragraph → sentence → character fallback.

    Args:
        text: The text content to chunk.
        heading: The heading this text belongs to.
        max_chunk_size: Maximum characters per chunk.

    Returns:
        List of chunk dicts with text, heading, and chunk_type.
    """
    if not text.strip():
        return []

    # If it fits, return as-is
    if len(text) <= max_chunk_size:
        return [{"text": text, "heading": heading, "chunk_type": "section"}]

    # Try paragraph split
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) > 1:
        chunks = []
        for para in paragraphs:
            if len(para) <= max_chunk_size:
                chunks.append({"text": para, "heading": heading, "chunk_type": "paragraph"})
            else:
                # Try sentence split
                chunks.extend(_chunk_sentences(para, heading, max_chunk_size))
        return chunks

    # Single paragraph too large — try sentences
    return _chunk_sentences(text, heading, max_chunk_size)


def _chunk_sentences(
    text: str, heading: str, max_chunk_size: int
) -> list[dict[str, str]]:
    """Split text by sentences, falling back to character split."""
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        chunks = []
        current = ""
        for sentence in sentences:
            candidate = (current + " " + sentence).strip() if current else sentence
            if len(candidate) <= max_chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append({"text": current, "heading": heading, "chunk_type": "sentence"})
                # If single sentence exceeds max, it needs character split
                if len(sentence) > max_chunk_size:
                    for fragment in _fixed_chunk_text(sentence, chunk_size=max_chunk_size, overlap=50):
                        chunks.append({"text": fragment, "heading": heading, "chunk_type": "fragment"})
                else:
                    current = sentence
        if current:
            chunks.append({"text": current, "heading": heading, "chunk_type": "sentence"})
        return chunks

    # No sentence boundaries — character split
    return [
        {"text": fragment, "heading": heading, "chunk_type": "fragment"}
        for fragment in _fixed_chunk_text(text, chunk_size=max_chunk_size, overlap=50)
    ]


def chunk_markdown(
    text: str, max_chunk_size: int = 1500
) -> list[dict[str, str]]:
    """Chunk markdown text using structure-aware splitting.

    Hierarchy: frontmatter (skipped) → headings → paragraphs → sentences → characters.

    Args:
        text: Raw markdown text including optional frontmatter.
        max_chunk_size: Maximum characters per chunk (default 1500).

    Returns:
        List of dicts with 'text', 'heading', and 'chunk_type' keys.
    """
    body = _strip_frontmatter(text)
    if not body.strip():
        return []

    sections = _split_by_headings(body)
    chunks = []
    for heading, content in sections:
        # Include heading text in the chunk content for context
        full_text = (heading + "\n\n" + content).strip() if heading != "top-level" else content
        if not full_text.strip():
            continue
        chunks.extend(_chunk_text_block(full_text, heading, max_chunk_size))

    return chunks
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: All tests pass.

**Step 4: Commit**

```bash
git add src/index_vault.py
git commit -m "feat: add structure-aware chunk_markdown with heading/paragraph/sentence fallbacks"
```

---

### Task 3: Update `index_file()` to use `chunk_markdown()` with enriched metadata

**Files:**
- Modify: `src/index_vault.py:60-83` (`index_file` function)

**Step 1: Write a test for index_file metadata**

Add to `tests/test_chunking.py`:

```python
from unittest.mock import MagicMock, patch


class TestIndexFileMetadata:
    """Tests for enriched metadata in index_file."""

    @patch("index_vault.get_collection")
    def test_index_file_stores_heading_and_chunk_type(self, mock_get_collection, tmp_path):
        md_file = tmp_path / "test.md"
        md_file.write_text("---\ntags: test\n---\n\n# Title\n\nContent.\n\n## Section\n\nMore.")

        mock_collection = MagicMock()
        mock_collection.get.return_value = {"ids": []}
        mock_get_collection.return_value = mock_collection

        from index_vault import index_file
        index_file(md_file)

        # Verify upsert was called with heading and chunk_type in metadata
        assert mock_collection.upsert.called
        for call in mock_collection.upsert.call_args_list:
            metadata = call[1]["metadatas"][0] if "metadatas" in call[1] else call[0][2][0]
            assert "heading" in metadata
            assert "chunk_type" in metadata
            assert "source" in metadata
            assert "chunk" in metadata
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestIndexFileMetadata -v`
Expected: FAIL — `index_file` still uses old `chunk_text`.

**Step 3: Update `index_file` to use `chunk_markdown`**

Replace the body of `index_file` in `src/index_vault.py`:

```python
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
    chunks = chunk_markdown(content)

    # Index each chunk
    for i, chunk in enumerate(chunks):
        doc_id = hashlib.md5(f"{md_file}_{i}".encode()).hexdigest()
        collection.upsert(
            ids=[doc_id],
            documents=[chunk["text"]],
            metadatas=[{
                "source": str(md_file),
                "chunk": i,
                "heading": chunk["heading"],
                "chunk_type": chunk["chunk_type"],
            }],
        )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py -v`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: index_file uses chunk_markdown with heading/chunk_type metadata"
```

---

### Task 4: Update `hybrid_search.py` to include heading in results

**Files:**
- Modify: `src/hybrid_search.py:28-31` (`semantic_search` return)
- Modify: `src/hybrid_search.py:82-90` (`keyword_search` return)

**Step 1: Write tests for heading in search results**

Add to `tests/test_chunking.py`:

```python
class TestSearchHeadingMetadata:
    """Tests for heading metadata in search results."""

    @patch("hybrid_search.get_collection")
    def test_semantic_search_includes_heading(self, mock_get_collection):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["Some content"]],
            "metadatas": [[{"source": "file.md", "heading": "## Notes", "chunk_type": "section"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test query", n_results=1)
        assert results[0]["heading"] == "## Notes"

    @patch("hybrid_search.get_collection")
    def test_semantic_search_missing_heading_defaults(self, mock_get_collection):
        """Old chunks without heading metadata should get a default."""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            "documents": [["Some content"]],
            "metadatas": [[{"source": "file.md"}]],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import semantic_search
        results = semantic_search("test query", n_results=1)
        assert results[0]["heading"] == ""

    @patch("hybrid_search.get_collection")
    def test_keyword_search_includes_heading(self, mock_get_collection):
        mock_collection = MagicMock()
        mock_collection.get.return_value = {
            "ids": ["id1"],
            "documents": ["Some searchable content"],
            "metadatas": [{"source": "file.md", "heading": "## Tasks", "chunk_type": "section"}],
        }
        mock_get_collection.return_value = mock_collection

        from hybrid_search import keyword_search
        results = keyword_search("searchable content", n_results=1)
        assert results[0]["heading"] == "## Tasks"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSearchHeadingMetadata -v`
Expected: FAIL — `heading` key not in results.

**Step 3: Update `semantic_search` return**

In `src/hybrid_search.py`, change the return statement in `semantic_search` (lines 28-31):

```python
    return [
        {"source": metadata["source"], "content": doc[:500], "heading": metadata.get("heading", "")}
        for doc, metadata in zip(results["documents"][0], results["metadatas"][0])
    ]
```

**Step 4: Update `keyword_search` return**

In `src/hybrid_search.py`, update the section where keyword results are accumulated (line 84) to also store the heading:

```python
            entry["content"] = doc[:500]
            entry["heading"] = metadata.get("heading", "")
```

And update the return (lines 87-90):

```python
    ranked = sorted(chunk_hits.values(), key=lambda x: x["hits"], reverse=True)
    return [
        {"source": r["source"], "content": r["content"], "heading": r["heading"]}
        for r in ranked[:n_results]
    ]
```

**Step 5: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/hybrid_search.py tests/test_chunking.py
git commit -m "feat: include heading metadata in search results"
```

---

### Task 5: Run full test suite and verify

**Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass, no regressions.

**Step 2: Verify the indexer runs**

Run: `.venv/bin/python src/index_vault.py --full` (or a dry-run on a small test vault)
Expected: Completes without errors, prints chunk count.

**Step 3: Final commit if any fixes needed, then done**
