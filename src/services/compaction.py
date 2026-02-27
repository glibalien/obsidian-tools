"""Tool message compaction for managing conversation token usage."""

import json
from typing import Callable

from config import COMPACTION_CONTENT_PREVIEW_LENGTH, COMPACTION_SNIPPET_LENGTH


def _base_stub(data: dict) -> dict:
    """Extract common fields shared by all stubs."""
    stub: dict = {}
    if "success" in data:
        stub["status"] = "success" if data["success"] else "error"
    else:
        stub["status"] = "unknown"
    if "error" in data:
        stub["error"] = data["error"]
    if "message" in data:
        stub["message"] = data["message"]
    return stub


def _build_generic_stub(data: dict) -> str:
    """Generic stub builder for tools without specific handlers."""
    stub = _base_stub(data)

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


def _build_search_vault_stub(data: dict) -> str:
    """Compact search_vault: keep source, heading, and content snippet per result."""
    stub = _base_stub(data)
    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        stub["results"] = [
            {
                "source": r["source"],
                "heading": r.get("heading", ""),
                "snippet": r.get("content", "")[:COMPACTION_SNIPPET_LENGTH],
            }
            for r in data["results"]
            if isinstance(r, dict) and "source" in r
        ]
    return json.dumps(stub)


def _build_read_file_stub(data: dict) -> str:
    """Compact read_file: keep content preview and pagination markers.

    Also handles non-text dispatches (audio transcript, image description).
    """
    stub = _base_stub(data)
    if "content" in data:
        content = data["content"]
        stub["content_length"] = len(content)
        stub["content_preview"] = content[:COMPACTION_CONTENT_PREVIEW_LENGTH]
        trunc_marker = "[... truncated at char"
        if trunc_marker in content:
            idx = content.rfind(trunc_marker)
            if idx != -1:
                stub["truncation_marker"] = content[idx:]
    if "transcript" in data:
        stub["transcript_preview"] = data["transcript"][:COMPACTION_CONTENT_PREVIEW_LENGTH]
    if "description" in data:
        stub["description_preview"] = data["description"][:COMPACTION_CONTENT_PREVIEW_LENGTH]
    if "path" in data:
        stub["path"] = data["path"]
    return json.dumps(stub)


def _build_list_stub(data: dict) -> str:
    """Compact list tools: preserve full results list (already compact) and total."""
    stub = _base_stub(data)
    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        stub["results"] = data["results"]
    if "total" in data:
        stub["total"] = data["total"]
    return json.dumps(stub)


def _build_find_notes_stub(data: dict) -> str:
    """Compact find_notes: detect result shape and use appropriate format."""
    stub = _base_stub(data)
    if "total" in data:
        stub["total"] = data["total"]
    if "results" in data and isinstance(data["results"], list):
        results = data["results"]
        stub["result_count"] = len(results)
        if results and isinstance(results[0], dict) and "content" in results[0]:
            # Semantic results: snippet format
            stub["results"] = [
                {
                    "source": r["source"],
                    "heading": r.get("heading", ""),
                    "snippet": r.get("content", "")[:COMPACTION_SNIPPET_LENGTH],
                }
                for r in results
                if isinstance(r, dict) and "source" in r
            ]
        else:
            # Vault scan results: preserve as-is (paths or field projections)
            stub["results"] = results
    return json.dumps(stub)


def _build_find_links_stub(data: dict) -> str:
    """Compact find_links: handle both single-direction and both-mode responses."""
    stub = _base_stub(data)
    # Single direction: top-level results/total
    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        stub["results"] = data["results"]
    if "total" in data:
        stub["total"] = data["total"]
    # Both mode: nested backlinks/outlinks sections
    for key in ("backlinks", "outlinks"):
        if key in data and isinstance(data[key], dict):
            stub[key] = data[key]
    return json.dumps(stub)


def _build_web_search_stub(data: dict) -> str:
    """Compact web_search: keep title and URL, drop snippets."""
    stub = _base_stub(data)
    if "results" in data and isinstance(data["results"], list):
        stub["result_count"] = len(data["results"])
        stub["results"] = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in data["results"]
            if isinstance(r, dict)
        ]
    return json.dumps(stub)


_TOOL_STUB_BUILDERS: dict[str, Callable[[dict], str]] = {
    "find_notes": _build_find_notes_stub,
    "read_file": _build_read_file_stub,
    "web_search": _build_web_search_stub,
    "find_links": _build_find_links_stub,
}


def build_tool_stub(content: str, tool_name: str | None = None) -> str:
    """Build a compact stub from a tool result string.

    Dispatches to tool-specific extractors when tool_name is known,
    falling back to generic extraction otherwise.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        summary = content[:200] if len(content) > 200 else content
        return json.dumps({"status": "unknown", "summary": summary})

    if tool_name and tool_name in _TOOL_STUB_BUILDERS:
        return _TOOL_STUB_BUILDERS[tool_name](data)

    return _build_generic_stub(data)


def compact_tool_messages(messages: list[dict]) -> None:
    """Replace tool results with compact stubs in-place."""
    # Build tool_call_id -> tool_name mapping from assistant messages
    tool_name_map: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                call_id = tc.get("id")
                name = tc.get("function", {}).get("name")
                if call_id and name:
                    tool_name_map[call_id] = name

    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and not msg.get("_compacted"):
            call_id = msg["tool_call_id"]
            tool_name = tool_name_map.get(call_id)
            messages[i] = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": build_tool_stub(msg["content"], tool_name),
                "_compacted": True,
            }
