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
