"""Shared validation helpers for tool inputs."""


def validate_pagination(
    offset: int,
    limit: int,
    *,
    max_limit: int = 500,
) -> tuple[int | None, int | None, str | None]:
    """Validate and coerce pagination parameters.

    Args:
        offset: Number of results to skip.
        limit: Maximum number of results to return.
        max_limit: Upper bound for limit to avoid huge payloads.

    Returns:
        Tuple of (coerced_offset, coerced_limit, error_message).
        On error, offset/limit are None and error_message is populated.
    """
    try:
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        return None, None, "Invalid pagination: offset must be an integer"

    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        return None, None, "Invalid pagination: limit must be an integer"

    if parsed_offset < 0:
        return None, None, "Invalid pagination: offset must be >= 0"

    if parsed_limit < 1:
        return None, None, "Invalid pagination: limit must be >= 1"

    if parsed_limit > max_limit:
        return None, None, f"Invalid pagination: limit must be <= {max_limit}"

    return parsed_offset, parsed_limit, None

