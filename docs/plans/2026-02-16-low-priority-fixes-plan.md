# Low-Priority Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix two low-priority issues: protect `add_wikilinks` from matching inside code blocks/URLs, and unify code fence detection into a shared helper.

**Architecture:** Extract `is_fence_line()` helper in `services/vault.py`, used by both `index_vault.py` and `find_section`. Add strip-and-restore preprocessing to `add_wikilinks` in `log_chat.py` to skip protected zones.

**Tech Stack:** Python, regex, pytest

---

### Task 1: Shared `is_fence_line` Helper

**Files:**
- Modify: `src/services/vault.py:355` (add helper near `_FENCE_PATTERN`)
- Modify: `src/index_vault.py:79` (replace `startswith` with import)
- Test: `tests/test_vault_service.py`

**Step 1: Write failing tests for `is_fence_line`**

Add to `tests/test_vault_service.py`:

```python
from services.vault import is_fence_line

class TestIsFenceLine:
    """Tests for is_fence_line helper."""

    def test_backtick_fence(self):
        assert is_fence_line("```") is True

    def test_backtick_fence_with_language(self):
        assert is_fence_line("```python") is True

    def test_tilde_fence(self):
        assert is_fence_line("~~~") is True

    def test_tilde_fence_with_language(self):
        assert is_fence_line("~~~markdown") is True

    def test_four_backticks(self):
        assert is_fence_line("````") is True

    def test_indented_fence(self):
        assert is_fence_line("  ```") is True

    def test_not_a_fence_two_backticks(self):
        assert is_fence_line("``not a fence``") is False

    def test_not_a_fence_plain_text(self):
        assert is_fence_line("hello world") is False

    def test_empty_line(self):
        assert is_fence_line("") is False
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestIsFenceLine -v`
Expected: FAIL with `ImportError` (is_fence_line not yet exported)

**Step 3: Implement `is_fence_line` in vault.py**

In `src/services/vault.py`, add after `_FENCE_PATTERN` (line 355):

```python
def is_fence_line(line: str) -> bool:
    """Check if a line is a code fence opener/closer (``` or ~~~)."""
    return bool(_FENCE_PATTERN.match(line.strip()))
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_service.py::TestIsFenceLine -v`
Expected: PASS

**Step 5: Update `index_vault.py` to use `is_fence_line`**

In `src/index_vault.py`, add to imports (line 14):

```python
from services.vault import get_vault_files, is_fence_line
```

Replace lines 79-80:

```python
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
```

With:

```python
        if is_fence_line(line):
            in_fence = not in_fence
```

**Step 6: Update `find_section` in vault.py to use `is_fence_line`**

In `src/services/vault.py`, replace line 391:

```python
        if _FENCE_PATTERN.match(line):
```

With:

```python
        if is_fence_line(line):
```

And replace line 423:

```python
        if _FENCE_PATTERN.match(line):
```

With:

```python
        if is_fence_line(line):
```

**Step 7: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass (existing `find_section` and chunking tests validate no regressions)

**Step 8: Commit**

```bash
git add src/services/vault.py src/index_vault.py tests/test_vault_service.py
git commit -m "refactor: extract shared is_fence_line helper for code fence detection"
```

---

### Task 2: Protect `add_wikilinks` from Matching in Code/URLs

**Files:**
- Modify: `src/log_chat.py:13-31` (add strip-and-restore to `add_wikilinks`)
- Create: `tests/test_log_chat.py`

**Step 1: Write failing tests**

Create `tests/test_log_chat.py`:

```python
"""Tests for log_chat.py - wikilink insertion and protected zones."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from log_chat import add_wikilinks


class TestAddWikilinks:
    """Tests for add_wikilinks function."""

    def test_basic_replacement(self):
        """Should replace known note names with wikilinks."""
        result = add_wikilinks("See ProjectAlpha for details", {"ProjectAlpha"})
        assert "[[ProjectAlpha]]" in result

    def test_skip_short_names(self):
        """Should skip names shorter than 3 characters."""
        result = add_wikilinks("See AI for details", {"AI"})
        assert "[[AI]]" not in result

    def test_no_double_wrap(self):
        """Should not wrap already-linked names."""
        result = add_wikilinks("See [[ProjectAlpha]] here", {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1

    def test_skip_fenced_code_block(self):
        """Should not match inside fenced code blocks."""
        text = "Before\n```\nProjectAlpha is here\n```\nAfter ProjectAlpha"
        result = add_wikilinks(text, {"ProjectAlpha"})
        # Only the one outside the fence should be linked
        assert result.count("[[ProjectAlpha]]") == 1
        assert "```\nProjectAlpha is here\n```" in result

    def test_skip_tilde_fence(self):
        """Should not match inside tilde fenced code blocks."""
        text = "~~~\nProjectAlpha\n~~~\nProjectAlpha outside"
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1

    def test_skip_inline_code(self):
        """Should not match inside inline code spans."""
        text = "Use `ProjectAlpha` to run it. ProjectAlpha is great."
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1
        assert "`ProjectAlpha`" in result

    def test_skip_url(self):
        """Should not match inside URLs."""
        text = "Visit https://example.com/ProjectAlpha for info. ProjectAlpha rocks."
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1
        assert "https://example.com/ProjectAlpha" in result

    def test_skip_existing_wikilinks(self):
        """Should not double-wrap existing wikilinks."""
        text = "See [[ProjectAlpha]] and also ProjectAlpha"
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 2
        assert "[[[[ProjectAlpha]]]]" not in result

    def test_multiple_protected_zones(self):
        """Should handle multiple protected zone types in one text."""
        text = (
            "```\nProjectAlpha in fence\n```\n"
            "`ProjectAlpha inline`\n"
            "https://example.com/ProjectAlpha\n"
            "ProjectAlpha should be linked"
        )
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1

    def test_empty_note_names(self):
        """Should return text unchanged with empty note names."""
        text = "Hello world"
        assert add_wikilinks(text, set()) == text

    def test_fence_with_language(self):
        """Should protect code blocks with language specifiers."""
        text = "```python\nProjectAlpha = 1\n```\nProjectAlpha outside"
        result = add_wikilinks(text, {"ProjectAlpha"})
        assert result.count("[[ProjectAlpha]]") == 1
```

**Step 2: Run tests to verify expected failures**

Run: `.venv/bin/python -m pytest tests/test_log_chat.py -v`
Expected: `test_basic_replacement`, `test_skip_short_names`, `test_no_double_wrap`, `test_empty_note_names` PASS; `test_skip_fenced_code_block`, `test_skip_inline_code`, `test_skip_url`, `test_skip_tilde_fence`, `test_fence_with_language`, `test_multiple_protected_zones` FAIL

**Step 3: Implement strip-and-restore in `add_wikilinks`**

Replace the `add_wikilinks` function in `src/log_chat.py` (lines 13-31):

```python
# Patterns for protected zones (order matters: fenced blocks first)
_FENCED_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_INLINE_CODE = re.compile(r"`[^`]+`")
_URL = re.compile(r"https?://\S+")
_WIKILINK = re.compile(r"\[\[[^\]]+\]\]")


def _protect_zones(text: str) -> tuple[str, list[str]]:
    """Replace protected zones with placeholders. Returns (text, originals)."""
    originals: list[str] = []

    def _replace(match: re.Match) -> str:
        originals.append(match.group(0))
        return f"\x00{len(originals) - 1}\x00"

    for pattern in (_FENCED_BLOCK, _INLINE_CODE, _URL, _WIKILINK):
        text = pattern.sub(_replace, text)
    return text, originals


def _restore_zones(text: str, originals: list[str]) -> str:
    """Restore placeholders with original content."""
    for i, original in enumerate(originals):
        text = text.replace(f"\x00{i}\x00", original)
    return text


def add_wikilinks(text: str, note_names: set[str]) -> str:
    """Replace references to known notes with wikilinks.

    Protects fenced code blocks, inline code, URLs, and existing
    wikilinks from being modified.
    """
    if not note_names:
        return text

    # Strip protected zones
    text, originals = _protect_zones(text)

    # Sort by length descending to match longer names first
    sorted_names = sorted(note_names, key=len, reverse=True)

    for name in sorted_names:
        # Skip very short names (likely false positives)
        if len(name) < 3:
            continue

        # Match whole words, not already in wikilinks or backticks
        pattern = r'(?<!\[\[)(?<!`)\b' + re.escape(name) + r'\b(?!\]\])(?!`)'
        replacement = f'[[{name}]]'
        text = re.sub(pattern, replacement, text)

    # Restore protected zones
    text = _restore_zones(text, originals)
    return text
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_log_chat.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

**Step 6: Commit**

```bash
git add src/log_chat.py tests/test_log_chat.py
git commit -m "fix: protect add_wikilinks from matching inside code blocks and URLs"
```
