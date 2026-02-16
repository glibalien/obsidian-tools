"""Tool message compaction for managing conversation token usage."""

import json


def build_tool_stub(content: str) -> str:
    """Build a compact stub from a tool result string.

    Parses JSON tool results and extracts key metadata (status, file paths,
    result count, errors). Non-JSON content is summarized to 200 chars.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        summary = content[:200] if len(content) > 200 else content
        return json.dumps({"status": "unknown", "summary": summary})

    stub: dict = {}

    if "success" in data:
        stub["status"] = "success" if data["success"] else "error"
    else:
        stub["status"] = "unknown"

    if "error" in data:
        stub["error"] = data["error"]

    if "message" in data:
        stub["message"] = data["message"]

    if "path" in data:
        stub["path"] = data["path"]

    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        files = [
            r["source"]
            for r in data["results"]
            if isinstance(r, dict) and "source" in r
        ]
        if files:
            stub["files"] = files

    if "content" in data:
        stub["has_content"] = True
        stub["content_length"] = len(data["content"])

    if "date" in data:
        stub["date"] = data["date"]

    return json.dumps(stub)


def compact_tool_messages(messages: list[dict]) -> None:
    """Replace tool results with compact stubs in-place."""
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and not msg.get("_compacted"):
            messages[i] = {
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": build_tool_stub(msg["content"]),
                "_compacted": True,
            }
