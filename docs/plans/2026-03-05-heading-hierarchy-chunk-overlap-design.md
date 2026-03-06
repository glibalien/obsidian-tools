# Heading Hierarchy + Chunk Overlap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve search quality by adding heading hierarchy to chunk prefixes (#153) and sentence-level overlap at chunk boundaries (#162).

**Architecture:** `_split_by_headings` gains a heading stack to track nesting, returning `heading_chain` lists. `_chunk_sentences` is refactored to track sentences in a list for 2-sentence carry-forward overlap. Cross-section overlap is applied in `chunk_markdown`. `_prepare_file_chunks` builds prefixes from heading chains. Both changes alter indexed document text, requiring `--full` reindex.

**Tech Stack:** Pure Python, no new dependencies.

---

### Task 1: Add heading chain to `_split_by_headings`

**Files:**
- Modify: `src/chunking.py:92-125` (`_split_by_headings`)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py` — new import and test class:

```python
# Add _split_by_headings to the import block at line 15:
from chunking import (
    _fixed_chunk_text,
    _split_by_headings,
    _split_sentences,
    _strip_wikilink_brackets,
    chunk_markdown,
    format_frontmatter_for_indexing,
)


class TestSplitByHeadings:
    """Tests for heading hierarchy chain construction."""

    def test_flat_headings(self):
        """Sequential same-level headings each get their own chain."""
        text = "## A\n\nContent A.\n\n## B\n\nContent B."
        sections = _split_by_headings(text)
        assert sections[0] == ("## A", ["A"], "\n\nContent A.\n\n")
        assert sections[1] == ("## B", ["B"], "\n\nContent B.")

    def test_nested_headings(self):
        """Child headings include parent in chain."""
        text = "## Parent\n\nP content.\n\n### Child\n\nC content."
        sections = _split_by_headings(text)
        assert sections[0] == ("## Parent", ["Parent"], "\n\nP content.\n\n")
        assert sections[1] == ("### Child", ["Parent", "Child"], "\n\nC content.")

    def test_level_reset(self):
        """A same-or-higher level heading resets the stack."""
        text = "## A\n\nA.\n\n### B\n\nB.\n\n## C\n\nC."
        sections = _split_by_headings(text)
        assert sections[0][1] == ["A"]         # ## A
        assert sections[1][1] == ["A", "B"]    # ### B
        assert sections[2][1] == ["C"]         # ## C (pops A and B)

    def test_deeply_nested(self):
        """Deeply nested headings build a full chain."""
        text = "# L1\n\n.\n\n## L2\n\n.\n\n### L3\n\n.\n\n#### L4\n\n."
        sections = _split_by_headings(text)
        assert sections[0][1] == ["L1"]
        assert sections[1][1] == ["L1", "L2"]
        assert sections[2][1] == ["L1", "L2", "L3"]
        assert sections[3][1] == ["L1", "L2", "L3", "L4"]

    def test_top_level_content(self):
        """Content before first heading has empty chain."""
        text = "Intro text.\n\n## Heading\n\nBody."
        sections = _split_by_headings(text)
        assert sections[0] == ("top-level", [], "Intro text.\n\n")
        assert sections[1] == ("## Heading", ["Heading"], "\n\nBody.")

    def test_no_headings(self):
        """Text with no headings returns one top-level section with empty chain."""
        text = "Just some text."
        sections = _split_by_headings(text)
        assert len(sections) == 1
        assert sections[0] == ("top-level", [], "Just some text.")

    def test_skip_level(self):
        """Skipping levels (h2 -> h4) still builds correct chain."""
        text = "## A\n\n.\n\n#### D\n\n."
        sections = _split_by_headings(text)
        assert sections[0][1] == ["A"]
        assert sections[1][1] == ["A", "D"]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSplitByHeadings -v`
Expected: FAIL — `_split_by_headings` returns 2-tuples, not 3-tuples.

**Step 3: Implement heading chain in `_split_by_headings`**

Replace `_split_by_headings` in `src/chunking.py:92-125`:

```python
def _split_by_headings(text: str) -> list[tuple[str, list[str], str]]:
    """Split text on markdown headings, respecting code fences.

    Returns list of (heading, heading_chain, content) tuples.
    heading is the raw heading line (e.g. "## Section").
    heading_chain is a list of clean heading names showing nesting
    (e.g. ["Architecture", "Database Layer"]).
    Content before the first heading gets heading="top-level" and chain=[].
    """
    lines = text.split("\n")
    sections: list[tuple[str, list[str], str]] = []
    current_heading = "top-level"
    current_chain: list[str] = []
    current_lines: list[str] = []
    in_fence = False
    # Stack of (level, clean_name) for heading hierarchy
    heading_stack: list[tuple[int, str]] = []

    for line in lines:
        if is_fence_line(line):
            in_fence = not in_fence

        if not in_fence and re.match(r"^#{1,6} ", line):
            # Save previous section
            content = "\n".join(current_lines)
            if content.strip() or current_heading != "top-level":
                sections.append((current_heading, list(current_chain), content))

            # Parse heading level and clean name
            stripped = line.strip()
            hashes = len(stripped) - len(stripped.lstrip("#"))
            clean_name = stripped[hashes:].strip()

            # Update stack: pop entries at same or deeper level
            while heading_stack and heading_stack[-1][0] >= hashes:
                heading_stack.pop()
            heading_stack.append((hashes, clean_name))

            current_heading = stripped
            current_chain = [name for _, name in heading_stack]
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    content = "\n".join(current_lines)
    if content.strip() or current_heading != "top-level":
        sections.append((current_heading, list(current_chain), content))

    return sections
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSplitByHeadings -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/chunking.py tests/test_chunking.py
git commit -m "feat: add heading chain to _split_by_headings (#153)"
```

---

### Task 2: Propagate heading chain through chunk dicts

**Files:**
- Modify: `src/chunking.py:161-301` (`_chunk_sentences`, `_chunk_text_block`, `chunk_markdown`)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py`:

```python
class TestHeadingChainPropagation:
    """Tests for heading_chain flowing through chunk dicts."""

    def test_section_chunk_has_chain(self):
        """A small section that fits in one chunk includes heading_chain."""
        text = "## Architecture\n\nSmall content."
        chunks = chunk_markdown(text)
        assert chunks[0]["heading_chain"] == ["Architecture"]

    def test_nested_chunk_has_full_chain(self):
        """Nested heading produces full chain in chunk dict."""
        text = "## Parent\n\nP.\n\n### Child\n\nC."
        chunks = chunk_markdown(text)
        child = [c for c in chunks if c["heading"] == "### Child"]
        assert len(child) == 1
        assert child[0]["heading_chain"] == ["Parent", "Child"]

    def test_top_level_has_empty_chain(self):
        """Content before first heading has empty heading_chain."""
        text = "Intro text.\n\n# Heading\n\nBody."
        chunks = chunk_markdown(text)
        top = [c for c in chunks if c["heading"] == "top-level"]
        assert len(top) == 1
        assert top[0]["heading_chain"] == []

    def test_frontmatter_has_empty_chain(self):
        """Frontmatter chunk has empty heading_chain."""
        text = "---\ntitle: Test\n---\n\n# Heading\n\nBody."
        chunks = chunk_markdown(text, frontmatter={"title": "Test"})
        fm = [c for c in chunks if c["chunk_type"] == "frontmatter"]
        assert len(fm) == 1
        assert fm[0]["heading_chain"] == []

    def test_paragraph_chunks_inherit_chain(self):
        """Large sections split into paragraphs keep the heading_chain."""
        paras = ["Paragraph %d. " % i + "x" * 200 for i in range(10)]
        body = "\n\n".join(paras)
        text = "## Section\n\n" + body
        chunks = chunk_markdown(text, max_chunk_size=500)
        for c in chunks:
            assert c["heading_chain"] == ["Section"]

    def test_sentence_chunks_inherit_chain(self):
        """Sentence-split chunks inherit the heading_chain."""
        sentences = " ".join(f"Sentence number {i} is here." for i in range(50))
        text = "## Topic\n\n" + sentences
        chunks = chunk_markdown(text, max_chunk_size=200)
        for c in chunks:
            assert c["heading_chain"] == ["Topic"]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestHeadingChainPropagation -v`
Expected: FAIL — chunk dicts don't have `heading_chain` key.

**Step 3: Propagate heading_chain through chunking functions**

Update `_chunk_sentences` signature and body in `src/chunking.py`:

```python
def _chunk_sentences(
    text: str, heading: str, heading_chain: list[str], max_chunk_size: int
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
            if current:
                chunks.append({
                    "text": current,
                    "heading": heading,
                    "heading_chain": heading_chain,
                    "chunk_type": "sentence",
                })
                current = ""
            if len(sentence) <= max_chunk_size:
                current = sentence
            else:
                for fragment in _fixed_chunk_text(sentence, chunk_size=max_chunk_size, overlap=50):
                    if fragment.strip():
                        chunks.append({
                            "text": fragment,
                            "heading": heading,
                            "heading_chain": heading_chain,
                            "chunk_type": "fragment",
                        })

    if current.strip():
        chunks.append({
            "text": current,
            "heading": heading,
            "heading_chain": heading_chain,
            "chunk_type": "sentence",
        })

    return chunks
```

Update `_chunk_text_block` signature and body:

```python
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

    paragraphs = re.split(r"\n\n+", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if len(paragraphs) > 1:
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

    return _chunk_sentences(text, heading, heading_chain, max_chunk_size)
```

Update `chunk_markdown` to unpack 3-tuples and pass `heading_chain`:

```python
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
        for heading, heading_chain, content in sections:
            if heading == "top-level":
                block = content.strip()
            else:
                block = (heading + "\n" + content).strip()
            if not block:
                continue
            all_chunks.extend(
                _chunk_text_block(block, heading, heading_chain, max_chunk_size)
            )

    return all_chunks
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestHeadingChainPropagation tests/test_chunking.py::TestChunkMarkdownHeadings -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/chunking.py tests/test_chunking.py
git commit -m "feat: propagate heading_chain through chunk dicts (#153)"
```

---

### Task 3: Use heading chain in index prefix

**Files:**
- Modify: `src/index_vault.py:118-120` (`_prepare_file_chunks`)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing test**

Add to `tests/test_chunking.py`:

```python
from index_vault import _prepare_file_chunks


class TestPrepareFileChunksPrefix:
    """Tests for heading hierarchy in indexed document prefixes."""

    def test_flat_heading_prefix(self, tmp_path):
        """Section under a heading gets [Note > Section] prefix."""
        f = tmp_path / "My Note.md"
        f.write_text("## Architecture\n\nContent here.")
        _, _, docs, _ = _prepare_file_chunks(f)
        assert docs[0].startswith("[My Note > Architecture]")

    def test_nested_heading_prefix(self, tmp_path):
        """Nested heading gets full chain prefix."""
        f = tmp_path / "My Note.md"
        f.write_text("## Architecture\n\nA.\n\n### Database\n\nB.")
        _, _, docs, _ = _prepare_file_chunks(f)
        db_doc = [d for d in docs if "Database" in d.split("]")[0]]
        assert len(db_doc) == 1
        assert db_doc[0].startswith("[My Note > Architecture > Database]")

    def test_top_level_prefix(self, tmp_path):
        """Content before first heading gets [Note] prefix only."""
        f = tmp_path / "My Note.md"
        f.write_text("Just some intro text.")
        _, _, docs, _ = _prepare_file_chunks(f)
        assert docs[0].startswith("[My Note] ")

    def test_frontmatter_prefix(self, tmp_path):
        """Frontmatter chunk gets [Note] prefix only."""
        f = tmp_path / "My Note.md"
        f.write_text("---\ntitle: Test\n---\n\nBody.")
        _, _, docs, _ = _prepare_file_chunks(f)
        assert docs[0].startswith("[My Note] ")

    def test_level_reset_prefix(self, tmp_path):
        """Heading at same level resets the chain."""
        f = tmp_path / "Note.md"
        f.write_text("## A\n\nA.\n\n### B\n\nB.\n\n## C\n\nC.")
        _, _, docs, _ = _prepare_file_chunks(f)
        c_doc = [d for d in docs if d.startswith("[Note > C]")]
        assert len(c_doc) == 1
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestPrepareFileChunksPrefix -v`
Expected: FAIL — prefix still uses old `[{stem}]` format.

**Step 3: Update `_prepare_file_chunks` prefix construction**

In `src/index_vault.py`, replace line 120:

```python
documents.append(f"[{md_file.stem}] {chunk['text']}")
```

with:

```python
chain = [md_file.stem] + chunk.get("heading_chain", [])
prefix = " > ".join(chain)
documents.append(f"[{prefix}] {chunk['text']}")
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestPrepareFileChunksPrefix -v`
Expected: PASS

**Step 5: Run full test suite to catch regressions**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS (existing tests don't assert on exact prefix format)

**Step 6: Commit**

```bash
git add src/index_vault.py tests/test_chunking.py
git commit -m "feat: use heading chain in index prefix (#153)"
```

---

### Task 4: Sentence-level overlap in `_chunk_sentences`

**Files:**
- Modify: `src/chunking.py` (add `OVERLAP_SENTENCES`, refactor `_chunk_sentences`)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py`:

```python
class TestSentenceOverlap:
    """Tests for sentence carry-forward overlap in _chunk_sentences."""

    def test_overlap_between_chunks(self):
        """Last 2 sentences of chunk N appear at start of chunk N+1."""
        # Build text with enough sentences to force multiple chunks
        sentences = [f"Sentence {i} has some content." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_markdown("## S\n\n" + text, max_chunk_size=200)
        # Find consecutive sentence-type chunks
        sentence_chunks = [c for c in chunks if c["chunk_type"] == "sentence"]
        assert len(sentence_chunks) >= 2
        # Second chunk should start with overlap from first
        first_text = sentence_chunks[0]["text"]
        second_text = sentence_chunks[1]["text"]
        # Extract last 2 sentences from first chunk
        first_sentences = _split_sentences(first_text)
        overlap = first_sentences[-2:] if len(first_sentences) >= 2 else first_sentences[-1:]
        for sent in overlap:
            assert sent in second_text

    def test_first_chunk_no_overlap(self):
        """First chunk has no carry-forward prefix."""
        sentences = [f"Sentence {i} is here." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_markdown("## S\n\n" + text, max_chunk_size=200)
        # First chunk should start with the heading or first sentence
        assert chunks[0]["text"].startswith("## S")

    def test_single_chunk_no_overlap(self):
        """A section that fits in one chunk has no overlap artifacts."""
        text = "## S\n\nShort content here."
        chunks = chunk_markdown(text)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "## S\nShort content here."

    def test_fragment_keeps_own_overlap(self):
        """Oversized sentences fall back to _fixed_chunk_text with its own 50-char overlap."""
        giant = "x" * 3000
        text = "## S\n\n" + giant
        chunks = chunk_markdown(text, max_chunk_size=500)
        fragment_chunks = [c for c in chunks if c["chunk_type"] == "fragment"]
        assert len(fragment_chunks) >= 2
        # Fixed chunks have 50-char overlap
        assert fragment_chunks[0]["text"][-50:] == fragment_chunks[1]["text"][:50]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSentenceOverlap -v`
Expected: FAIL — no overlap exists yet.

**Step 3: Add constant and refactor `_chunk_sentences`**

Add constant after `FRONTMATTER_EXCLUDE` in `src/chunking.py`:

```python
OVERLAP_SENTENCES = 2
```

Replace `_chunk_sentences` in `src/chunking.py`:

```python
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

            # Check if this single sentence fits
            if len(sentence) <= max_chunk_size:
                added_len = len(sentence) + (1 if buffer else 0)
                buffer.append(sentence)
                buf_len += added_len
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestSentenceOverlap -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/chunking.py tests/test_chunking.py
git commit -m "feat: add sentence-level overlap in _chunk_sentences (#162)"
```

---

### Task 5: Cross-section overlap in `chunk_markdown`

**Files:**
- Modify: `src/chunking.py` (`chunk_markdown`)
- Test: `tests/test_chunking.py`

**Step 1: Write the failing tests**

Add to `tests/test_chunking.py`:

```python
class TestCrossSectionOverlap:
    """Tests for overlap between heading sections."""

    def test_overlap_between_sections(self):
        """First chunk of section N+1 contains trailing sentences from section N."""
        text = (
            "## First\n\nAlpha sentence one. Alpha sentence two. Alpha sentence three.\n\n"
            "## Second\n\nBeta content here."
        )
        chunks = chunk_markdown(text)
        second = [c for c in chunks if c["heading"] == "## Second"]
        assert len(second) == 1
        # Should contain overlap from First section
        assert "Alpha sentence two." in second[0]["text"]
        assert "Alpha sentence three." in second[0]["text"]

    def test_no_overlap_after_frontmatter(self):
        """First body section does not get overlap from frontmatter."""
        text = "---\ntitle: Test\n---\n\n## First\n\nBody."
        chunks = chunk_markdown(text, frontmatter={"title": "Test"})
        body = [c for c in chunks if c["heading"] == "## First"]
        assert len(body) == 1
        assert "title" not in body[0]["text"]

    def test_no_overlap_on_first_section(self):
        """Very first section has no overlap prefix."""
        text = "## Only\n\nJust content."
        chunks = chunk_markdown(text)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "## Only\nJust content."

    def test_overlap_across_three_sections(self):
        """Overlap chains across multiple sections."""
        text = (
            "## A\n\nA one. A two. A three.\n\n"
            "## B\n\nB one. B two. B three.\n\n"
            "## C\n\nC content."
        )
        chunks = chunk_markdown(text)
        b_chunk = [c for c in chunks if c["heading"] == "## B"][0]
        c_chunk = [c for c in chunks if c["heading"] == "## C"][0]
        # B should have overlap from A
        assert "A two." in b_chunk["text"]
        assert "A three." in b_chunk["text"]
        # C should have overlap from B
        assert "B two." in c_chunk["text"]
        assert "B three." in c_chunk["text"]

    def test_single_sentence_section_overlap(self):
        """Section with only 1 sentence provides just that 1 sentence as overlap."""
        text = (
            "## A\n\nOnly one sentence.\n\n"
            "## B\n\nB content."
        )
        chunks = chunk_markdown(text)
        b_chunk = [c for c in chunks if c["heading"] == "## B"][0]
        assert "Only one sentence." in b_chunk["text"]
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestCrossSectionOverlap -v`
Expected: FAIL — no cross-section overlap exists.

**Step 3: Add cross-section overlap to `chunk_markdown`**

Add a helper function and update `chunk_markdown` in `src/chunking.py`:

```python
def _trailing_sentences(text: str, n: int) -> str:
    """Extract the last n sentences from text."""
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    tail = sentences[-n:]
    return " ".join(tail)
```

Update the section loop in `chunk_markdown`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chunking.py::TestCrossSectionOverlap -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/chunking.py tests/test_chunking.py
git commit -m "feat: add cross-section overlap in chunk_markdown (#162)"
```

---

### Task 6: Full regression test + export updates

**Files:**
- Test: `tests/test_chunking.py` (full suite)
- Modify: `src/chunking.py` (export `OVERLAP_SENTENCES` if needed)

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS — verify no regressions from signature changes or new `heading_chain` key.

**Step 2: Fix any failing tests**

Existing tests that unpack `_split_by_headings` 2-tuples or assert on chunk dict keys without `heading_chain` may need updating. Check failures and fix. Most existing tests access chunks via `chunk_markdown` which returns dicts — they access `["heading"]` and `["text"]` which are unchanged, so they should pass.

**Step 3: Update chunking.py import in test file**

If `OVERLAP_SENTENCES` or `_trailing_sentences` need to be tested directly, add them to the import block. Otherwise no changes needed.

**Step 4: Commit if any fixes were made**

```bash
git add -u
git commit -m "fix: update tests for heading chain and overlap changes"
```

---

### Task 7: Create feature branch, squash, and PR

**Step 1: Create GitHub issue (if not already linked)**

Issues #153 and #162 already exist.

**Step 2: Create feature branch and PR**

```bash
git checkout -b feature/heading-hierarchy-chunk-overlap
git push -u origin feature/heading-hierarchy-chunk-overlap
gh pr create --title "feat: heading hierarchy prefixes + chunk overlap (#153, #162)" \
  --body "$(cat <<'EOF'
## Summary
- Add heading hierarchy to chunk prefixes for richer embedding context (#153)
- Add 2-sentence overlap at chunk boundaries for continuity (#162)

Closes #153, closes #162

## Changes
- `_split_by_headings` returns heading chain (list of clean heading names)
- `heading_chain` propagated through `_chunk_text_block` / `_chunk_sentences`
- `_prepare_file_chunks` builds `[Note > Section > Subsection]` prefixes
- `_chunk_sentences` refactored for 2-sentence carry-forward overlap
- Cross-section overlap prepends trailing sentences to next section's first chunk
- `OVERLAP_SENTENCES = 2` constant in chunking.py

## Test plan
- [ ] Heading chain construction (flat, nested, level resets, deep nesting)
- [ ] Prefix format in indexed documents
- [ ] Sentence overlap between consecutive chunks
- [ ] Cross-section overlap at heading boundaries
- [ ] No overlap on first chunk or after frontmatter
- [ ] Full regression suite passes
- [ ] Requires `--full` reindex after deployment

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
