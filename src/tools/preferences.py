"""Preference tools - save, list, remove user preferences."""

from config import PREFERENCES_FILE
from services.vault import err, ok


def _read_preferences() -> list[str]:
    """Read preferences from Preferences.md, returning list of preference lines."""
    if not PREFERENCES_FILE.exists():
        return []

    content = PREFERENCES_FILE.read_text(encoding="utf-8")
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:])
    return lines


def _write_preferences(preferences: list[str]) -> None:
    """Write preferences list to Preferences.md."""
    content = "\n".join(f"- {pref}" for pref in preferences)
    if content:
        content += "\n"
    PREFERENCES_FILE.write_text(content, encoding="utf-8")


def save_preference(preference: str) -> str:
    """Save a user preference to Preferences.md in the vault root."""
    if not preference or not preference.strip():
        return err("preference cannot be empty")

    preference = preference.strip()
    preferences = _read_preferences()
    preferences.append(preference)
    _write_preferences(preferences)

    item = {"index": len(preferences), "value": preference}
    return ok(message=f"Saved preference: {preference}", item=item)


def list_preferences() -> str:
    """List all saved user preferences from Preferences.md."""
    preferences = _read_preferences()

    if not preferences:
        return ok(message="No preferences saved.", results=[], total=0)

    results = [{"index": i, "value": pref} for i, pref in enumerate(preferences, start=1)]
    legacy = [f"{item['index']}. {item['value']}" for item in results]
    return ok(message=f"Found {len(results)} preferences", results=results, legacy_results=legacy, total=len(results))


def remove_preference(line_number: int) -> str:
    """Remove a preference by its line number."""
    preferences = _read_preferences()

    if not preferences:
        return err("No preferences to remove")

    if line_number < 1 or line_number > len(preferences):
        return err(f"Invalid line number. Must be between 1 and {len(preferences)}")

    removed = preferences.pop(line_number - 1)
    _write_preferences(preferences)

    return ok(message=f"Removed preference: {removed}", item={"index": line_number, "value": removed})
