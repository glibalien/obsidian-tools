"""Preference tools - manage user preferences."""

from config import PREFERENCES_FILE
from services.vault import ok, err


def _read_preferences() -> list[str]:
    """Read preferences from Preferences.md, returning list of preference lines."""
    if not PREFERENCES_FILE.exists():
        return []

    content = PREFERENCES_FILE.read_text(encoding="utf-8")
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        # Only include lines that are bullet points
        if stripped.startswith("- "):
            lines.append(stripped[2:])  # Remove "- " prefix
    return lines


def _write_preferences(preferences: list[str]) -> None:
    """Write preferences list to Preferences.md."""
    content = "\n".join(f"- {pref}" for pref in preferences)
    if content:
        content += "\n"
    PREFERENCES_FILE.write_text(content, encoding="utf-8")


def manage_preferences(
    operation: str,
    preference: str | None = None,
    line_number: int | None = None,
) -> str:
    """Manage user preferences stored in Preferences.md.

    Args:
        operation: "list", "add", or "remove".
        preference: The preference text (required for "add").
        line_number: 1-indexed line number (required for "remove").
    """
    if operation == "list":
        preferences = _read_preferences()
        if not preferences:
            return ok("No preferences saved.", results=[])
        return ok(results=[f"{i}. {pref}" for i, pref in enumerate(preferences, start=1)])

    if operation == "add":
        if not preference or not preference.strip():
            return err("preference cannot be empty")
        preference = preference.strip()
        preferences = _read_preferences()
        preferences.append(preference)
        _write_preferences(preferences)
        return ok(f"Saved preference: {preference}")

    if operation == "remove":
        if line_number is None:
            return err("line_number is required for remove operation")
        preferences = _read_preferences()
        if not preferences:
            return err("No preferences to remove")
        if line_number < 1 or line_number > len(preferences):
            return err(f"Invalid line number. Must be between 1 and {len(preferences)}")
        removed = preferences.pop(line_number - 1)
        _write_preferences(preferences)
        return ok(f"Removed preference: {removed}")

    return err(f"Unknown operation: {operation}. Must be 'list', 'add', or 'remove'")
