"""Tests for src/tools/preferences.py."""

import json
from pathlib import Path

import pytest

import tools.preferences as preferences_module
from tools.preferences import manage_preferences


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """Monkeypatch PREFERENCES_FILE to a temp path."""
    pref_path = tmp_path / "Preferences.md"
    monkeypatch.setattr(preferences_module, "PREFERENCES_FILE", pref_path)
    return pref_path


def test_save_preference_basic(prefs_file):
    """Save one preference, verify file created with bullet format."""
    result = json.loads(manage_preferences(operation="add", preference="Use metric units"))
    assert result["success"] is True
    assert "Use metric units" in result["message"]
    assert prefs_file.exists()
    content = prefs_file.read_text(encoding="utf-8")
    assert content == "- Use metric units\n"


def test_save_preference_empty(prefs_file):
    """Empty string returns error."""
    result = json.loads(manage_preferences(operation="add", preference=""))
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    assert not prefs_file.exists()


def test_save_preference_whitespace_only(prefs_file):
    """Whitespace-only string returns error."""
    result = json.loads(manage_preferences(operation="add", preference="   "))
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    assert not prefs_file.exists()


def test_save_preference_strips_whitespace(prefs_file):
    """Leading and trailing whitespace is stripped from preference."""
    result = json.loads(manage_preferences(operation="add", preference="  always cite sources  "))
    assert result["success"] is True
    content = prefs_file.read_text(encoding="utf-8")
    assert content == "- always cite sources\n"
    assert "  " not in content.strip()


def test_save_preference_unicode(prefs_file):
    """Unicode content is saved and read back correctly."""
    result = json.loads(manage_preferences(operation="add", preference="Prefer Mandarin: 你好"))
    assert result["success"] is True
    content = prefs_file.read_text(encoding="utf-8")
    assert "你好" in content


def test_save_multiple_preferences(prefs_file):
    """Save multiple preferences; all are preserved in the file."""
    manage_preferences(operation="add", preference="First preference")
    manage_preferences(operation="add", preference="Second preference")
    manage_preferences(operation="add", preference="Third preference")

    content = prefs_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert lines == [
        "- First preference",
        "- Second preference",
        "- Third preference",
    ]


def test_list_preferences_empty(prefs_file):
    """When no file exists, list returns success with empty results."""
    result = json.loads(manage_preferences(operation="list"))
    assert result["success"] is True
    assert result["results"] == []


def test_list_preferences_numbered(prefs_file):
    """list_preferences returns results as a numbered list."""
    manage_preferences(operation="add", preference="Alpha")
    manage_preferences(operation="add", preference="Beta")
    manage_preferences(operation="add", preference="Gamma")

    result = json.loads(manage_preferences(operation="list"))
    assert result["success"] is True
    assert result["results"] == ["1. Alpha", "2. Beta", "3. Gamma"]


def test_remove_preference_basic(prefs_file):
    """Remove a middle item; remaining items are preserved in order."""
    manage_preferences(operation="add", preference="Keep A")
    manage_preferences(operation="add", preference="Remove B")
    manage_preferences(operation="add", preference="Keep C")

    result = json.loads(manage_preferences(operation="remove", line_number=2))
    assert result["success"] is True
    assert "Remove B" in result["message"]

    content = prefs_file.read_text(encoding="utf-8")
    assert content == "- Keep A\n- Keep C\n"


def test_remove_preference_no_preferences(prefs_file):
    """Error returned when there are no preferences to remove."""
    result = json.loads(manage_preferences(operation="remove", line_number=1))
    assert result["success"] is False
    assert "no preferences" in result["error"].lower()


def test_remove_preference_out_of_range_high(prefs_file):
    """Error returned when line_number exceeds the number of preferences."""
    manage_preferences(operation="add", preference="Only one")

    result = json.loads(manage_preferences(operation="remove", line_number=2))
    assert result["success"] is False
    assert "invalid line number" in result["error"].lower()


def test_remove_preference_out_of_range_zero(prefs_file):
    """Error returned when line_number is 0 (1-indexed, so invalid)."""
    manage_preferences(operation="add", preference="Only one")

    result = json.loads(manage_preferences(operation="remove", line_number=0))
    assert result["success"] is False
    assert "invalid line number" in result["error"].lower()


def test_remove_preference_out_of_range_negative(prefs_file):
    """Error returned when line_number is negative."""
    manage_preferences(operation="add", preference="Only one")

    result = json.loads(manage_preferences(operation="remove", line_number=-1))
    assert result["success"] is False
    assert "invalid line number" in result["error"].lower()


def test_round_trip(prefs_file):
    """Save several preferences, list them, remove one, list again."""
    manage_preferences(operation="add", preference="Pref one")
    manage_preferences(operation="add", preference="Pref two")
    manage_preferences(operation="add", preference="Pref three")

    listed = json.loads(manage_preferences(operation="list"))
    assert listed["success"] is True
    assert len(listed["results"]) == 3
    assert listed["results"][1] == "2. Pref two"

    removed = json.loads(manage_preferences(operation="remove", line_number=2))
    assert removed["success"] is True
    assert "Pref two" in removed["message"]

    listed_after = json.loads(manage_preferences(operation="list"))
    assert listed_after["success"] is True
    assert len(listed_after["results"]) == 2
    assert listed_after["results"] == ["1. Pref one", "2. Pref three"]


def test_manage_preferences_invalid_operation(prefs_file):
    """Unknown operation returns error."""
    result = json.loads(manage_preferences(operation="delete"))
    assert result["success"] is False
    assert "unknown operation" in result["error"].lower()


def test_manage_preferences_remove_missing_line_number(prefs_file):
    """Remove without line_number returns error."""
    manage_preferences(operation="add", preference="something")
    result = json.loads(manage_preferences(operation="remove"))
    assert result["success"] is False
    assert "line_number" in result["error"].lower()
