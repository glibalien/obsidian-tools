# Unified read_file Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate audio transcription, image description, and Office document reading into `read_file` via extension-based dispatch.

**Architecture:** `read_file` in `tools/files.py` becomes a thin router — after resolving the path, it checks the file extension against a handler map and dispatches to the appropriate handler in a new `tools/readers.py` module. Text/markdown files fall through to the existing pagination logic unchanged.

**Tech Stack:** python-docx, openpyxl, python-pptx (Office), OpenAI client (Fireworks Whisper + vision)

---

### Task 1: Install Office Dependencies

**Files:**
- Modify: `requirements.txt` (or `pyproject.toml` — whichever exists)

**Step 1: Check current dependency file**

Run: `ls /home/barry/projects/obsidian-tools/requirements*.txt /home/barry/projects/obsidian-tools/pyproject.toml 2>/dev/null`

**Step 2: Add dependencies**

Add `python-docx`, `openpyxl`, `python-pptx` to the dependency file.

**Step 3: Install**

Run: `/home/barry/projects/obsidian-tools/.venv/bin/pip install python-docx openpyxl python-pptx`

**Step 4: Commit**

```bash
git add requirements.txt  # or pyproject.toml
git commit -m "chore: add Office document dependencies for read_file dispatch"
```

---

### Task 2: Add VISION_MODEL to Config

**Files:**
- Modify: `src/config.py:41` (after WHISPER_MODEL line)

**Step 1: Write the test**

In `tests/test_config.py`, add a test that verifies `VISION_MODEL` defaults and can be overridden via env var. Follow the existing pattern in that file (patch `dotenv.load_dotenv` before `importlib.reload(config)`).

```python
def test_vision_model_default(monkeypatch):
    monkeypatch.delenv("VISION_MODEL", raising=False)
    with patch("dotenv.load_dotenv"):
        importlib.reload(config)
    assert config.VISION_MODEL == "accounts/fireworks/models/qwen3-vl-30b-a3b-instruct"

def test_vision_model_override(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "custom-model")
    with patch("dotenv.load_dotenv"):
        importlib.reload(config)
    assert config.VISION_MODEL == "custom-model"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_vision_model_default -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'VISION_MODEL'`

**Step 3: Implement**

In `src/config.py`, after the `WHISPER_MODEL` line (line 41):

```python
VISION_MODEL = os.getenv("VISION_MODEL", "accounts/fireworks/models/qwen3-vl-30b-a3b-instruct")
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add VISION_MODEL config for image description handler"
```

---

### Task 3: Create readers.py — Audio Handler

Extract the single-file transcription logic from `tools/audio.py` into `tools/readers.py`. The handler takes a `Path` directly (not a note path with embed scanning).

**Files:**
- Create: `src/tools/readers.py`
- Reference: `src/tools/audio.py:32-56` (the `_transcribe_single_file` function)
- Test: `tests/test_tools_files.py` (add to existing file)

**Step 1: Write tests in `tests/test_tools_files.py`**

Add a new test class `TestReadFileAudio`:

```python
from unittest.mock import MagicMock, patch

class TestReadFileAudio:
    """Tests for read_file dispatching to audio handler."""

    def test_audio_no_api_key(self, vault_config, monkeypatch):
        """Audio files require FIREWORKS_API_KEY."""
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        audio = vault_config / "Attachments" / "test.m4a"
        audio.write_bytes(b"fake audio")
        result = json.loads(read_file("Attachments/test.m4a"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    @patch("tools.readers.OpenAI")
    def test_audio_successful(self, mock_openai_class, vault_config, monkeypatch):
        """Audio files are transcribed via Whisper."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "test.m4a"
        audio.write_bytes(b"fake audio")

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "Hello world"
        mock_client.audio.transcriptions.create.return_value = mock_response

        result = json.loads(read_file("Attachments/test.m4a"))
        assert result["success"] is True
        assert result["transcript"] == "Hello world"

    @patch("tools.readers.OpenAI")
    def test_audio_api_error(self, mock_openai_class, vault_config, monkeypatch):
        """API errors are returned gracefully."""
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        audio = vault_config / "Attachments" / "test.wav"
        audio.write_bytes(b"fake audio")

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.audio.transcriptions.create.side_effect = Exception("Rate limit")

        result = json.loads(read_file("Attachments/test.wav"))
        assert result["success"] is False
        assert "Rate limit" in result["error"]

    def test_audio_extensions_dispatched(self, vault_config, monkeypatch):
        """All audio extensions route to the audio handler."""
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        for ext in [".m4a", ".mp3", ".wav", ".ogg", ".webm"]:
            f = vault_config / "Attachments" / f"test{ext}"
            f.write_bytes(b"audio")
            result = json.loads(read_file(f"Attachments/test{ext}"))
            assert result["success"] is False
            assert "FIREWORKS_API_KEY" in result["error"], f"Extension {ext} not dispatched to audio handler"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileAudio -v`
Expected: FAIL

**Step 3: Create `src/tools/readers.py` with audio handler**

```python
"""File type handlers for read_file dispatch.

Each handler takes a resolved Path and returns ok()/err() JSON.
"""

import base64
import os
from pathlib import Path

from openai import OpenAI

from config import FIREWORKS_BASE_URL, VISION_MODEL, WHISPER_MODEL
from services.vault import err, ok


# Extension sets for dispatch
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}


def handle_audio(file_path: Path) -> str:
    """Transcribe an audio file using Fireworks Whisper API."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    try:
        with open(file_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                extra_body={"diarize": True},
            )
        return ok(transcript=response.text)
    except Exception as e:
        return err(f"Transcription failed: {e}")
```

**Step 4: Add dispatch in `read_file` (`tools/files.py`)**

At the top of `tools/files.py`, add import:
```python
from tools.readers import AUDIO_EXTENSIONS, handle_audio
```

In `read_file`, after the `resolve_file` call and error check, add extension dispatch before the text-reading logic:

```python
    # Extension-based dispatch for non-text files
    ext = file_path.suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return handle_audio(file_path)
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileAudio tests/test_tools_files.py::TestReadFile -v`
Expected: ALL PASS (new audio tests + existing text tests unchanged)

**Step 6: Commit**

```bash
git add src/tools/readers.py src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add audio handler to read_file dispatch"
```

---

### Task 4: Image Handler

**Files:**
- Modify: `src/tools/readers.py`
- Modify: `src/tools/files.py` (add image dispatch)
- Test: `tests/test_tools_files.py`

**Step 1: Write tests**

Add `TestReadFileImage` class in `tests/test_tools_files.py`:

```python
class TestReadFileImage:
    """Tests for read_file dispatching to image handler."""

    def test_image_no_api_key(self, vault_config, monkeypatch):
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        img = vault_config / "Attachments" / "photo.png"
        img.write_bytes(b"\x89PNG fake image")
        result = json.loads(read_file("Attachments/photo.png"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    @patch("tools.readers.OpenAI")
    def test_image_successful(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        img = vault_config / "Attachments" / "photo.jpg"
        img.write_bytes(b"fake image data")

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_choice = MagicMock()
        mock_choice.message.content = "A photo of a cat"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        result = json.loads(read_file("Attachments/photo.jpg"))
        assert result["success"] is True
        assert result["description"] == "A photo of a cat"

    @patch("tools.readers.OpenAI")
    def test_image_api_error(self, mock_openai_class, vault_config, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        img = vault_config / "Attachments" / "photo.webp"
        img.write_bytes(b"fake image")

        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("Model unavailable")

        result = json.loads(read_file("Attachments/photo.webp"))
        assert result["success"] is False
        assert "Model unavailable" in result["error"]

    def test_image_extensions_dispatched(self, vault_config, monkeypatch):
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]:
            f = vault_config / "Attachments" / f"test{ext}"
            f.write_bytes(b"img")
            result = json.loads(read_file(f"Attachments/test{ext}"))
            assert result["success"] is False
            assert "FIREWORKS_API_KEY" in result["error"], f"Extension {ext} not dispatched"
```

**Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileImage -v`

**Step 3: Implement `handle_image` in `readers.py`**

```python
def handle_image(file_path: Path) -> str:
    """Describe an image using Fireworks vision model."""
    api_key = os.getenv("FIREWORKS_API_KEY")
    if not api_key:
        return err("FIREWORKS_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url=FIREWORKS_BASE_URL)

    try:
        image_data = file_path.read_bytes()
        b64 = base64.b64encode(image_data).decode("utf-8")

        # Infer MIME type from extension
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        }
        mime = mime_map.get(file_path.suffix.lower(), "image/png")

        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in detail, including any visible text."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
        )
        description = response.choices[0].message.content
        return ok(description=description)
    except Exception as e:
        return err(f"Image description failed: {e}")
```

**Step 4: Add image dispatch in `files.py`**

Import `IMAGE_EXTENSIONS, handle_image` and add to the dispatch block:
```python
    if ext in IMAGE_EXTENSIONS:
        return handle_image(file_path)
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileImage tests/test_tools_files.py::TestReadFile -v`

**Step 6: Commit**

```bash
git add src/tools/readers.py src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add image handler to read_file dispatch"
```

---

### Task 5: Office Handler — Word (.docx)

**Files:**
- Modify: `src/tools/readers.py`
- Modify: `src/tools/files.py` (add office dispatch)
- Test: `tests/test_tools_files.py`

**Step 1: Write tests**

```python
from docx import Document as DocxDocument

class TestReadFileOffice:
    """Tests for read_file dispatching to office handler."""

    def test_docx_basic(self, vault_config):
        """Read a simple Word document."""
        doc = DocxDocument()
        doc.add_heading("Test Heading", level=1)
        doc.add_paragraph("Hello world")
        doc.add_paragraph("Second paragraph")
        path = vault_config / "Attachments" / "test.docx"
        doc.save(str(path))

        result = json.loads(read_file("Attachments/test.docx"))
        assert result["success"] is True
        assert "Test Heading" in result["content"]
        assert "Hello world" in result["content"]
        assert "Second paragraph" in result["content"]

    def test_docx_empty(self, vault_config):
        """Empty Word document returns empty content."""
        doc = DocxDocument()
        path = vault_config / "Attachments" / "empty.docx"
        doc.save(str(path))

        result = json.loads(read_file("Attachments/empty.docx"))
        assert result["success"] is True

    def test_docx_with_table(self, vault_config):
        """Word documents with tables extract table text."""
        doc = DocxDocument()
        doc.add_paragraph("Before table")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "A1"
        table.cell(0, 1).text = "B1"
        table.cell(1, 0).text = "A2"
        table.cell(1, 1).text = "B2"
        path = vault_config / "Attachments" / "table.docx"
        doc.save(str(path))

        result = json.loads(read_file("Attachments/table.docx"))
        assert result["success"] is True
        assert "A1" in result["content"]
        assert "B2" in result["content"]
```

**Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice -v`

**Step 3: Implement office handler in `readers.py`**

```python
def handle_office(file_path: Path) -> str:
    """Extract text content from Office documents (.docx, .xlsx, .pptx)."""
    ext = file_path.suffix.lower()
    try:
        if ext == ".docx":
            return _read_docx(file_path)
        elif ext == ".xlsx":
            return _read_xlsx(file_path)
        elif ext == ".pptx":
            return _read_pptx(file_path)
        else:
            return err(f"Unsupported office format: {ext}")
    except Exception as e:
        return err(f"Failed to read {file_path.name}: {e}")


def _read_docx(file_path: Path) -> str:
    """Extract text from a Word document, preserving headings and tables."""
    from docx import Document as DocxDocument

    doc = DocxDocument(str(file_path))
    parts = []

    for element in doc.element.body:
        tag = element.tag.split("}")[-1]  # strip namespace
        if tag == "p":
            # Check if it's a heading
            from docx.oxml.ns import qn
            style = element.find(qn("w:pPr"))
            if style is not None:
                pstyle = style.find(qn("w:pStyle"))
                if pstyle is not None:
                    val = pstyle.get(qn("w:val"), "")
                    if val.startswith("Heading"):
                        level = val.replace("Heading", "").strip()
                        try:
                            level = int(level)
                        except ValueError:
                            level = 1
                        text = element.text or ""
                        if text.strip():
                            parts.append(f"{'#' * level} {text.strip()}")
                            continue
            text = element.text or ""
            if text.strip():
                parts.append(text.strip())
        elif tag == "tbl":
            # Extract table as markdown
            from docx.table import Table
            table = Table(element, doc)
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                # Add header separator after first row
                header_sep = "| " + " | ".join("---" for _ in table.rows[0].cells) + " |"
                rows.insert(1, header_sep)
                parts.append("\n".join(rows))

    content = "\n\n".join(parts)
    return ok(content=content)
```

**Step 4: Add office dispatch in `files.py`**

Import `OFFICE_EXTENSIONS, handle_office` and add:
```python
    if ext in OFFICE_EXTENSIONS:
        return handle_office(file_path)
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice tests/test_tools_files.py::TestReadFile -v`

**Step 6: Commit**

```bash
git add src/tools/readers.py src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add Word document handler to read_file dispatch"
```

---

### Task 6: Office Handler — Excel (.xlsx)

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write tests**

```python
from openpyxl import Workbook

# Add to TestReadFileOffice class:

    def test_xlsx_basic(self, vault_config):
        """Read a simple Excel workbook as markdown tables."""
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["Name", "Age"])
        ws.append(["Alice", 30])
        ws.append(["Bob", 25])
        path = vault_config / "Attachments" / "test.xlsx"
        wb.save(str(path))

        result = json.loads(read_file("Attachments/test.xlsx"))
        assert result["success"] is True
        assert "## Data" in result["content"]
        assert "Alice" in result["content"]
        assert "| Name | Age |" in result["content"]

    def test_xlsx_multiple_sheets(self, vault_config):
        """Multiple sheets are each rendered with a heading."""
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "Sheet1"
        ws1.append(["A", "B"])
        ws2 = wb.create_sheet("Sheet2")
        ws2.append(["C", "D"])
        path = vault_config / "Attachments" / "multi.xlsx"
        wb.save(str(path))

        result = json.loads(read_file("Attachments/multi.xlsx"))
        assert result["success"] is True
        assert "## Sheet1" in result["content"]
        assert "## Sheet2" in result["content"]

    def test_xlsx_empty(self, vault_config):
        """Empty workbook returns empty content."""
        wb = Workbook()
        path = vault_config / "Attachments" / "empty.xlsx"
        wb.save(str(path))

        result = json.loads(read_file("Attachments/empty.xlsx"))
        assert result["success"] is True
```

**Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice::test_xlsx_basic -v`

**Step 3: Implement `_read_xlsx` in `readers.py`**

```python
def _read_xlsx(file_path: Path) -> str:
    """Extract Excel data as markdown tables, one per sheet."""
    from openpyxl import load_workbook

    wb = load_workbook(str(file_path), read_only=True, data_only=True)
    parts = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(cell) if cell is not None else "" for cell in row]
            if any(c for c in cells):  # skip fully empty rows
                rows.append(cells)

        if not rows:
            continue

        parts.append(f"## {sheet_name}")
        table_rows = ["| " + " | ".join(cells) + " |" for cells in rows]
        header_sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
        table_rows.insert(1, header_sep)
        parts.append("\n".join(table_rows))

    wb.close()
    content = "\n\n".join(parts)
    return ok(content=content)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice -v`

**Step 5: Commit**

```bash
git add src/tools/readers.py tests/test_tools_files.py
git commit -m "feat: add Excel handler to read_file dispatch"
```

---

### Task 7: Office Handler — PowerPoint (.pptx)

**Files:**
- Modify: `src/tools/readers.py`
- Test: `tests/test_tools_files.py`

**Step 1: Write tests**

```python
from pptx import Presentation

# Add to TestReadFileOffice class:

    def test_pptx_basic(self, vault_config):
        """Read a simple PowerPoint presentation."""
        prs = Presentation()
        slide_layout = prs.slide_layouts[1]  # title + content
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "Slide Title"
        slide.placeholders[1].text = "Bullet point content"
        path = vault_config / "Attachments" / "test.pptx"
        prs.save(str(path))

        result = json.loads(read_file("Attachments/test.pptx"))
        assert result["success"] is True
        assert "Slide Title" in result["content"]
        assert "Bullet point content" in result["content"]

    def test_pptx_multiple_slides(self, vault_config):
        """Multiple slides each get a heading."""
        prs = Presentation()
        for i in range(3):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = f"Slide {i+1}"
        path = vault_config / "Attachments" / "multi.pptx"
        prs.save(str(path))

        result = json.loads(read_file("Attachments/multi.pptx"))
        assert result["success"] is True
        assert "## Slide 1" in result["content"]
        assert "## Slide 2" in result["content"]
        assert "## Slide 3" in result["content"]

    def test_pptx_empty(self, vault_config):
        """Empty presentation returns empty content."""
        prs = Presentation()
        path = vault_config / "Attachments" / "empty.pptx"
        prs.save(str(path))

        result = json.loads(read_file("Attachments/empty.pptx"))
        assert result["success"] is True
```

**Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice::test_pptx_basic -v`

**Step 3: Implement `_read_pptx` in `readers.py`**

```python
def _read_pptx(file_path: Path) -> str:
    """Extract text from PowerPoint slides."""
    from pptx import Presentation

    prs = Presentation(str(file_path))
    parts = []

    for i, slide in enumerate(prs.slides, 1):
        slide_parts = []
        title = None

        # Extract title if present
        if slide.shapes.title and slide.shapes.title.text.strip():
            title = slide.shapes.title.text.strip()

        heading = f"## {title}" if title else f"## Slide {i}"
        slide_parts.append(heading)

        # Extract text from all shapes (excluding title to avoid duplication)
        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    slide_parts.append(text)

        if len(slide_parts) > 1:  # has content beyond heading
            parts.append("\n\n".join(slide_parts))
        elif title:  # title-only slide still worth including
            parts.append(heading)

    content = "\n\n".join(parts)
    return ok(content=content)
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py::TestReadFileOffice -v`

**Step 5: Commit**

```bash
git add src/tools/readers.py tests/test_tools_files.py
git commit -m "feat: add PowerPoint handler to read_file dispatch"
```

---

### Task 8: Retire transcribe_audio MCP Tool

**Files:**
- Modify: `src/mcp_server.py:56,103-104` — remove transcribe_audio import and registration
- Delete: `src/tools/audio.py`
- Modify: `tests/test_tools_audio.py` — rewrite tests to use `read_file` instead
- Modify: `tests/conftest.py:109,130` — remove `tools.audio` import and monkeypatch
- Modify: `tests/test_agent.py:309-366` — update the truncation test that references `transcribe_audio`

**Step 1: Update `mcp_server.py`**

Remove line 56 (`from tools.audio import transcribe_audio`) and lines 103-104 (`# Audio tools` comment and `mcp.tool()(transcribe_audio)`).

**Step 2: Update `conftest.py`**

Remove `import tools.audio` (line 109) and `monkeypatch.setattr(tools.audio, "ATTACHMENTS_DIR", attachments_dir)` (line 130).

The readers.py module imports `FIREWORKS_BASE_URL` and `WHISPER_MODEL` from config, not `ATTACHMENTS_DIR` — it receives the full resolved Path directly from `read_file`. However, check if readers.py needs any monkeypatching in conftest (it shouldn't — it receives paths, not config).

**Step 3: Update `test_tools_audio.py`**

The embed-scanning tests (`TestExtractAudioEmbeds`, `TestTranscribeAudio`) are no longer needed since `read_file` processes single files. The resolution tests (`TestResolveAudioFile`) test `services.vault.resolve_file` and remain valid.

Rewrite `test_tools_audio.py` to:
- Keep `TestResolveAudioFile` (tests vault path resolution — still useful)
- Remove `TestExtractAudioEmbeds` and `TestTranscribeAudio` (embed-scanning behavior retired)
- The single-file audio tests are already covered by `TestReadFileAudio` in `test_tools_files.py`

**Step 4: Update `test_agent.py` truncation test**

The test at line 309 uses `transcribe_audio` as the tool name. Change it to use `read_file` instead (or any other tool — it's testing generic truncation, not audio-specific logic). Update:
- `mock_tool_call_1.function.name = "read_file"`
- `mock_tool_call_1.function.arguments = '{"path": "large_file.md"}'`
- The assertion on `mock_session.call_tool` to match `"read_file"`

**Step 5: Delete `src/tools/audio.py`**

**Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/mcp_server.py tests/conftest.py tests/test_tools_audio.py tests/test_agent.py
git rm src/tools/audio.py
git commit -m "refactor: retire transcribe_audio MCP tool, audio handled by read_file"
```

---

### Task 9: Update System Prompt

**Files:**
- Modify: `system_prompt.txt.example:197-199` — replace `transcribe_audio` entry

**Step 1: Update system prompt**

Replace the `transcribe_audio` entry (lines 197-199) with updated `read_file` description that mentions the new capabilities. Also update the read_file entry if there is one, or add to its description.

The `read_file` entry in the tool reference should note:
- Reads any vault file — auto-detects type by extension
- Markdown/text: paginated text content (unchanged)
- Audio (.m4a, .mp3, .wav, etc.): Whisper transcription
- Images (.png, .jpg, .webp, etc.): vision model description
- Office (.docx, .xlsx, .pptx): extracted text content

Remove the separate `transcribe_audio` entry entirely.

**Step 2: Verify no other system prompt references**

Search for `transcribe_audio` in the system prompt and ensure all references are removed.

**Step 3: Commit**

```bash
git add system_prompt.txt.example
git commit -m "docs: update system prompt for unified read_file dispatch"
```

---

### Task 10: Update CLAUDE.md and Compaction

**Files:**
- Modify: `CLAUDE.md` — update tool table, remove `transcribe_audio` row, update `read_file` description
- Modify: `src/services/compaction.py:110` — the `read_file` stub builder already exists and will work for all dispatch types (it looks for `content`, `path`, `transcript`, `description` fields and the generic stub handles the rest)

**Step 1: Check if compaction needs updates**

The `_build_read_file_stub` (compaction.py:67-81) looks for `content` and `path` fields. Audio returns `transcript`, image returns `description`. These will fall through to `_base_stub` fields (`message`). This is acceptable — the stub will capture `status` and `message`. No changes needed to compaction unless we want richer stubs for audio/image results.

Optionally: update `_build_read_file_stub` to also capture `transcript` and `description` fields for better compaction of non-text read_file results.

**Step 2: Update CLAUDE.md tool table**

- Remove the `transcribe_audio` row
- Update `read_file` description to mention "Read any vault file (text, audio, image, Office)" and note that audio/image require `FIREWORKS_API_KEY`
- Add `VISION_MODEL` to the config table

**Step 3: Commit**

```bash
git add CLAUDE.md src/services/compaction.py
git commit -m "docs: update CLAUDE.md and compaction for unified read_file"
```

---

### Task 11: Final Verification

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS

**Step 2: Verify no stale references**

Run: `grep -r "transcribe_audio" src/ tests/ --include="*.py" -l`
Expected: no results (or only in tests that explicitly reference the old tool name in a comment)

Run: `grep -r "tools.audio" src/ tests/ --include="*.py" -l`
Expected: no results

**Step 3: Verify MCP tool count**

Count tools registered in `mcp_server.py`. Should be one fewer than before (removed `transcribe_audio`, no new tools added — just extended `read_file`).
