# PDF Support for read_file — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add PDF text extraction to `read_file` using PyMuPDF, with page-delimited output.

**Architecture:** New `handle_pdf` handler in `readers.py` following the existing audio/image/office pattern. `files.py` adds `.pdf` to the binary extensions set and dispatches to the handler. PyMuPDF extracts text per page with `## Page N` headings. This automatically enables PDF embeds (`![[doc.pdf]]`) and PDF content in `summarize_file`.

**Tech Stack:** PyMuPDF (`pymupdf>=1.25.0`)

---

### Task 1: Add PyMuPDF dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add pymupdf to requirements.txt**

Add `pymupdf>=1.25.0` after the `python-pptx` line:

```
pymupdf>=1.25.0
```

**Step 2: Install the dependency**

Run: `.venv/bin/pip install pymupdf>=1.25.0`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add pymupdf for PDF text extraction"
```

---

### Task 2: Add handle_pdf handler in readers.py

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write the failing tests**

Add a new `TestPdfReading` class in `test_tools_files.py` after `TestPptxReading` (around line 1156), following the docx/xlsx/pptx test patterns. Tests use real PyMuPDF objects (like the Office tests use real python-docx/openpyxl/python-pptx objects):

```python
class TestPdfReading:
    """Tests for PDF file reading via read_file."""

    def test_pdf_basic(self, vault_config):
        """Should extract text from a PDF with page headings."""
        import pymupdf

        doc = pymupdf.Document()
        page = doc.new_page()
        page.insert_text((72, 72), "Hello from page one.")
        doc.save(str(vault_config / "test.pdf"))
        doc.close()

        result = json.loads(read_file("test.pdf"))
        assert result["success"] is True
        assert "## Page 1" in result["content"]
        assert "Hello from page one." in result["content"]

    def test_pdf_multiple_pages(self, vault_config):
        """Multiple pages each get their own heading."""
        import pymupdf

        doc = pymupdf.Document()
        for i, text in enumerate(["First page.", "Second page."], 1):
            page = doc.new_page()
            page.insert_text((72, 72), text)
        doc.save(str(vault_config / "multi.pdf"))
        doc.close()

        result = json.loads(read_file("multi.pdf"))
        assert result["success"] is True
        content = result["content"]
        assert "## Page 1" in content
        assert "## Page 2" in content
        assert "First page." in content
        assert "Second page." in content

    def test_pdf_empty(self, vault_config):
        """Empty PDF should return ok with empty content."""
        import pymupdf

        doc = pymupdf.Document()
        doc.new_page()  # blank page
        doc.save(str(vault_config / "empty.pdf"))
        doc.close()

        result = json.loads(read_file("empty.pdf"))
        assert result["success"] is True

    def test_pdf_skip_blank_pages(self, vault_config):
        """Pages with no text content should be skipped."""
        import pymupdf

        doc = pymupdf.Document()
        doc.new_page()  # blank
        page2 = doc.new_page()
        page2.insert_text((72, 72), "Only content page.")
        doc.new_page()  # blank
        doc.save(str(vault_config / "sparse.pdf"))
        doc.close()

        result = json.loads(read_file("sparse.pdf"))
        assert result["success"] is True
        content = result["content"]
        assert "## Page 2" in content
        assert "Only content page." in content
        assert "## Page 1" not in content
        assert "## Page 3" not in content

    def test_pdf_corrupt_file(self, vault_config):
        """Corrupt PDF should return error."""
        (vault_config / "corrupt.pdf").write_bytes(b"not a real pdf")

        result = json.loads(read_file("corrupt.pdf"))
        assert result["success"] is False
        assert "error" in result
```

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPdfReading -v`
Expected: FAIL — `PDF_EXTENSIONS` not importable, no dispatch to `handle_pdf`.

**Step 2: Add PDF_EXTENSIONS and handle_pdf to readers.py**

In `src/tools/readers.py`, add after line 22 (`OFFICE_EXTENSIONS`):

```python
PDF_EXTENSIONS = {".pdf"}
```

Add `handle_pdf` function after `handle_office` (after line 123):

```python
def handle_pdf(file_path: Path) -> str:
    """Extract text content from a PDF file, page by page."""
    import pymupdf

    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0

    logger.info("Extracting PDF: %s (%d bytes)", file_path.name, size)
    start = time.time()
    try:
        doc = pymupdf.Document(str(file_path))
        parts = []
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if text:
                parts.append(f"## Page {i}\n\n{text}")
        doc.close()
        elapsed = time.time() - start
        logger.info("Extracted %s in %.2fs", file_path.name, elapsed)
        content = "\n\n".join(parts)
        return ok(content=content)
    except Exception as e:
        logger.warning("PDF extraction failed for %s: %s", file_path.name, e)
        return err(f"Failed to read {file_path.name}: {e}")
```

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPdfReading -v`
Expected: FAIL — `files.py` doesn't dispatch `.pdf` yet.

**Step 3: Wire up PDF dispatch in files.py**

In `src/tools/files.py`:

1. Update the import from `tools.readers` (line 12-18) to include `PDF_EXTENSIONS` and `handle_pdf`:

```python
from tools.readers import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    handle_audio,
    handle_image,
    handle_office,
    handle_pdf,
)
```

2. Update `_BINARY_EXTENSIONS` (line 45) to include PDF:

```python
_BINARY_EXTENSIONS = AUDIO_EXTENSIONS | IMAGE_EXTENSIONS | OFFICE_EXTENSIONS | PDF_EXTENSIONS
```

3. Add PDF dispatch in `read_file` (after line 380, the office dispatch):

```python
    if ext in PDF_EXTENSIONS:
        return handle_pdf(file_path)
```

4. Add PDF dispatch in `_expand_binary` (after the office elif on line 232):

```python
        elif ext in PDF_EXTENSIONS:
            logger.debug("Cache miss: %s — calling PDF handler", file_path.name)
            raw = handle_pdf(file_path)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPdfReading -v`
Expected: All 5 tests PASS.

**Step 5: Commit**

```bash
git add src/tools/readers.py src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add PDF text extraction to read_file"
```

---

### Task 3: Add tests for PDF embeds and logging

**Files:**
- Test: `tests/test_tools_files.py`

**Step 1: Write PDF embed test**

Add in the `TestExpandEmbeds` class (after `test_binary_embed_audio`, around line 1346):

```python
    def test_binary_embed_pdf(self, vault_config):
        """PDF embeds call handle_pdf and format the result."""
        pdf = vault_config / "Attachments" / "doc.pdf"
        pdf.write_bytes(b"fake pdf")

        from unittest.mock import patch as _patch
        with _patch("tools.files.handle_pdf") as mock_pdf:
            mock_pdf.return_value = '{"success": true, "content": "## Page 1\\n\\nHello"}'
            content = "![[doc.pdf]]"
            source = vault_config / "parent.md"
            _embed_cache.clear()
            result = _expand_embeds(content, source)
            assert "> [Embedded: doc.pdf]" in result
            assert "> ## Page 1" in result or "> Hello" in result
```

**Step 2: Write PDF logging tests**

Add in the `TestBinaryHandlerLogging` class (after `test_handle_office_logs_warning_on_failure`):

```python
    def test_handle_pdf_logs_entry_and_success(self, tmp_path, caplog):
        """handle_pdf logs file name, size, and duration on success."""
        import pymupdf

        pdf = tmp_path / "report.pdf"
        doc = pymupdf.Document()
        page = doc.new_page()
        page.insert_text((72, 72), "Hello")
        doc.save(str(pdf))
        doc.close()
        size = pdf.stat().st_size

        with caplog.at_level(logging.INFO, logger="tools.readers"):
            result = handle_pdf(pdf)

        assert json.loads(result)["success"] is True
        messages = [r.message for r in caplog.records]
        assert any("report.pdf" in m and str(size) in m for m in messages), \
            f"Expected entry log with filename and size, got: {messages}"
        assert any("Extracted" in m and "report.pdf" in m for m in messages), \
            f"Expected success log with 'Extracted' and filename, got: {messages}"

    def test_handle_pdf_logs_warning_on_failure(self, tmp_path, caplog):
        """handle_pdf logs a WARNING when extraction fails."""
        bad = tmp_path / "corrupt.pdf"
        bad.write_bytes(b"not a real pdf")

        with caplog.at_level(logging.WARNING, logger="tools.readers"):
            result = handle_pdf(bad)

        assert json.loads(result)["success"] is False
        assert any("corrupt.pdf" in r.message and r.levelname == "WARNING"
                    for r in caplog.records)
```

**Step 3: Write PDF attachments fallback test**

Add in the `TestReadFile` class (after `test_bare_image_name_resolves_to_attachments`):

```python
    def test_bare_pdf_name_resolves_to_attachments(self, vault_config):
        """Bare PDF filename resolves to Attachments directory."""
        import pymupdf

        pdf = vault_config / "Attachments" / "doc.pdf"
        doc = pymupdf.Document()
        page = doc.new_page()
        page.insert_text((72, 72), "Attachment content.")
        doc.save(str(pdf))
        doc.close()

        result = json.loads(read_file("doc.pdf"))
        assert result["success"] is True
        assert "Attachment content." in result["content"]
```

**Step 4: Run all new tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestPdfReading tests/test_tools_files.py::TestExpandEmbeds::test_binary_embed_pdf tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_pdf_logs_entry_and_success tests/test_tools_files.py::TestBinaryHandlerLogging::test_handle_pdf_logs_warning_on_failure tests/test_tools_files.py::TestReadFile::test_bare_pdf_name_resolves_to_attachments -v`
Expected: All PASS.

**Step 5: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (existing + new).

**Step 6: Commit**

```bash
git add tests/test_tools_files.py
git commit -m "test: add PDF embed, logging, and attachments fallback tests"
```
