"""Tests for session management: tool compaction."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import build_tool_stub, compact_tool_messages


class TestBuildToolStub:
    """Tests for build_tool_stub."""

    def test_search_vault_success(self):
        """Search results produce stub with file list and count."""
        content = json.dumps({
            "success": True,
            "results": [
                {"source": "Notes/foo.md", "content": "long content here..."},
                {"source": "Notes/bar.md", "content": "more content..."},
            ],
        })
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 2
        assert "Notes/foo.md" in parsed["files"]
        assert "Notes/bar.md" in parsed["files"]

    def test_error_response(self):
        """Error responses preserve the error message."""
        content = json.dumps({"success": False, "error": "File not found"})
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "error"
        assert parsed["error"] == "File not found"

    def test_success_with_path(self):
        """Success with path field (e.g., create_file, move_file)."""
        content = json.dumps({"success": True, "path": "new/note.md"})
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["path"] == "new/note.md"

    def test_success_with_message(self):
        """Success with message field (e.g., no results found)."""
        content = json.dumps({
            "success": True,
            "message": "No matching documents found",
            "results": [],
        })
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert "No matching" in parsed["message"]

    def test_non_json_content(self):
        """Non-JSON content is summarized to first 200 chars."""
        content = "x" * 500
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["status"] == "unknown"
        assert len(parsed["summary"]) <= 200

    def test_plain_text_short(self):
        """Short plain text is kept as-is in summary."""
        content = "Tool error: connection refused"
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert parsed["summary"] == content


class TestCompactToolMessages:
    """Tests for compact_tool_messages."""

    def test_compacts_tool_messages(self):
        """Tool messages are replaced with stubs."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "search for X"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({"success": True, "results": [{"source": "a.md", "content": "..."}]})},
            {"role": "assistant", "content": "Found 1 result."},
        ]
        compact_tool_messages(messages)

        tool_msg = messages[3]
        assert tool_msg["_compacted"] is True
        parsed = json.loads(tool_msg["content"])
        assert parsed["result_count"] == 1

    def test_skips_already_compacted(self):
        """Already-compacted messages are not re-processed."""
        stub_content = json.dumps({"status": "success", "result_count": 1})
        messages = [
            {"role": "tool", "tool_call_id": "call_1",
             "content": stub_content, "_compacted": True},
        ]
        compact_tool_messages(messages)
        assert messages[0]["content"] == stub_content

    def test_preserves_non_tool_messages(self):
        """System, user, and assistant messages are untouched."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        original = [m.copy() for m in messages]
        compact_tool_messages(messages)
        assert messages == original


from api_server import Session, get_or_create_session, file_sessions


class TestSessionRouting:
    """Tests for file-keyed session routing."""

    def setup_method(self):
        file_sessions.clear()

    def test_new_session_created(self):
        """First request for a file creates a new session."""
        session = get_or_create_session("notes/foo.md", "system prompt")
        assert session.active_file == "notes/foo.md"
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "system"

    def test_same_file_returns_existing(self):
        """Second request for same file returns the same session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s1.messages.append({"role": "user", "content": "hello"})

        s2 = get_or_create_session("notes/foo.md", "system prompt")
        assert s2.session_id == s1.session_id
        assert len(s2.messages) == 2

    def test_different_file_creates_new(self):
        """Different file creates a separate session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s2 = get_or_create_session("notes/bar.md", "system prompt")
        assert s1.session_id != s2.session_id
        assert s1.active_file != s2.active_file

    def test_switch_back_resumes(self):
        """Switching back to a previously used file resumes that session."""
        s1 = get_or_create_session("notes/foo.md", "system prompt")
        s1.messages.append({"role": "user", "content": "first"})
        original_id = s1.session_id

        get_or_create_session("notes/bar.md", "system prompt")
        s3 = get_or_create_session("notes/foo.md", "system prompt")

        assert s3.session_id == original_id
        assert len(s3.messages) == 2

    def test_null_file_creates_session(self):
        """None active_file gets its own session."""
        session = get_or_create_session(None, "system prompt")
        assert session.active_file is None
        assert session.session_id is not None
