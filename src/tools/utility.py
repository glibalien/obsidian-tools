"""Utility tools - logging, date."""

from datetime import datetime

from log_chat import log_chat


def log_interaction(
    task_description: str,
    query: str,
    summary: str,
    files: list[str] | None = None,
    full_response: str | None = None,
) -> str:
    """Log a Claude interaction to today's Obsidian daily note.

    Args:
        task_description: Brief description of the task performed.
        query: The original query or prompt.
        summary: Summary of the response (use 'n/a' if full_response provided).
        files: List of referenced file paths (optional).
        full_response: Full response text for conversational logs (optional).

    Returns:
        Confirmation message with the daily note path.
    """
    try:
        path = log_chat(task_description, query, summary, files, full_response)
    except Exception as e:
        return f"Logging failed: {e}"

    return f"Logged to {path}"


def get_current_date() -> str:
    """Get the current date in YYYY-MM-DD format.

    Returns:
        Current date as a string in YYYY-MM-DD format.
    """
    return datetime.now().strftime("%Y-%m-%d")
