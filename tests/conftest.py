"""Pytest configuration and fixtures for obsidian-tools tests."""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def temp_vault(tmp_path):
    """Create a temporary vault directory with sample files.

    Returns:
        Path to the temporary vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    # Create sample files with frontmatter
    (vault / "note1.md").write_text(
        """---
tags:
  - project
  - work
Date: 2024-01-15
---

# Note 1

This is the first note with a [[wikilink]] to another note.
"""
    )

    (vault / "note2.md").write_text(
        """---
tags:
  - meeting
company: Acme Corp
---

# Note 2

This note references [[note1]] and [[note3|alias]].

## Section A

Content in section A.

## Section B

Content in section B.
"""
    )

    (vault / "note3.md").write_text(
        """# Note 3

A simple note without frontmatter.
"""
    )

    # Create a subdirectory with notes
    subdir = vault / "projects"
    subdir.mkdir()
    (subdir / "project1.md").write_text(
        """---
tags:
  - project
status: active
---

# Project 1

Project details here.
"""
    )

    # Create Daily Notes directory
    daily = vault / "Daily Notes"
    daily.mkdir()
    (daily / "2024-01-15.md").write_text(
        """# 2024-01-15

## Tasks

- [x] Task 1
- [ ] Task 2
"""
    )

    return vault


@pytest.fixture
def vault_config(temp_vault, monkeypatch):
    """Patch config module to use temporary vault.

    This fixture patches VAULT_PATH and EXCLUDED_DIRS in both the config module
    and any modules that import them, so tests use the temporary vault.
    """
    import config
    import services.vault
    import tools.files
    import tools.frontmatter
    import tools.links
    import tools.audio

    # Create Attachments directory
    attachments_dir = temp_vault / "Attachments"
    attachments_dir.mkdir(exist_ok=True)

    # Use a temp chroma path so tests don't read/write the real link index
    temp_chroma = str(temp_vault / ".chroma_db")

    # Patch in config module
    monkeypatch.setattr(config, "VAULT_PATH", temp_vault)
    monkeypatch.setattr(config, "EXCLUDED_DIRS", {".git", ".obsidian"})
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", attachments_dir)
    monkeypatch.setattr(config, "CHROMA_PATH", temp_chroma)

    # Patch in services.vault (which imports from config at load time)
    monkeypatch.setattr(services.vault, "VAULT_PATH", temp_vault)
    monkeypatch.setattr(services.vault, "EXCLUDED_DIRS", {".git", ".obsidian"})

    # Patch in tools modules that import from config
    monkeypatch.setattr(tools.files, "VAULT_PATH", temp_vault)
    monkeypatch.setattr(tools.files, "EXCLUDED_DIRS", {".git", ".obsidian"})
    monkeypatch.setattr(tools.frontmatter, "VAULT_PATH", temp_vault)
    monkeypatch.setattr(tools.links, "VAULT_PATH", temp_vault)
    monkeypatch.setattr(tools.links, "EXCLUDED_DIRS", {".git", ".obsidian"})
    monkeypatch.setattr(tools.audio, "ATTACHMENTS_DIR", attachments_dir)

    return temp_vault


@pytest.fixture
def sample_markdown_with_sections():
    """Return sample markdown content with multiple sections."""
    return """---
title: Test Document
---

# Main Title

Introduction paragraph.

## Section One

Content of section one.

### Subsection 1.1

Subsection content.

## Section Two

Content of section two.

```python
# Code block with heading-like content
## This is not a heading
```

## Section Three

Final section content.
"""


@pytest.fixture
def sample_frontmatter_file(tmp_path):
    """Create a sample file with YAML frontmatter."""
    file_path = tmp_path / "frontmatter.md"
    file_path.write_text(
        """---
title: Test Note
tags:
  - test
  - sample
author: Test Author
---

# Content

Body of the note.
"""
    )
    return file_path


@pytest.fixture
def empty_file(tmp_path):
    """Create an empty markdown file."""
    file_path = tmp_path / "empty.md"
    file_path.write_text("")
    return file_path


@pytest.fixture
def file_without_frontmatter(tmp_path):
    """Create a file without YAML frontmatter."""
    file_path = tmp_path / "no_frontmatter.md"
    file_path.write_text(
        """# Just a Title

Some content without frontmatter.
"""
    )
    return file_path


@pytest.fixture
def note_with_audio_embeds(vault_config):
    """Create a note with audio embeds and corresponding audio files."""
    # Create note with audio embeds
    note_path = vault_config / "audio_note.md"
    note_path.write_text(
        """# Meeting Recording

Here's the recording from our meeting:

![[meeting.m4a]]

And here's a follow-up voice note:

![[followup.mp3]]

Some text after.
"""
    )

    # Create dummy audio files in Attachments
    attachments = vault_config / "Attachments"
    (attachments / "meeting.m4a").write_bytes(b"fake audio data")
    (attachments / "followup.mp3").write_bytes(b"more fake audio")

    return note_path
