"""Tests for session management: tool compaction."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api_server import app
from services.compaction import build_tool_stub, compact_tool_messages


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


    # --- Tool-specific stub tests ---

    def test_search_vault_stub_preserves_headings_and_snippets(self):
        """search_vault stub keeps source, heading, and content snippet."""
        content = json.dumps({
            "success": True,
            "results": [
                {"source": "Notes/meeting.md", "content": "Discussed the quarterly review and budget allocations for Q3", "heading": "## Meeting Notes"},
                {"source": "Notes/project.md", "content": "Project timeline updated", "heading": "### Timeline"},
            ],
        })
        stub = build_tool_stub(content, "search_vault")
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["result_count"] == 2
        assert len(parsed["results"]) == 2
        assert parsed["results"][0]["source"] == "Notes/meeting.md"
        assert parsed["results"][0]["heading"] == "## Meeting Notes"
        assert parsed["results"][0]["snippet"].startswith("Discussed")

    def test_search_vault_stub_truncates_long_content(self):
        """search_vault snippet is capped at SNIPPET_LENGTH chars."""
        content = json.dumps({
            "success": True,
            "results": [{"source": "a.md", "content": "x" * 200, "heading": ""}],
        })
        stub = build_tool_stub(content, "search_vault")
        parsed = json.loads(stub)
        assert len(parsed["results"][0]["snippet"]) == 80

    def test_search_vault_stub_empty_results(self):
        """search_vault with no results preserves message."""
        content = json.dumps({
            "success": True,
            "message": "No matching documents found",
            "results": [],
        })
        stub = build_tool_stub(content, "search_vault")
        parsed = json.loads(stub)
        assert parsed["result_count"] == 0
        assert parsed["message"] == "No matching documents found"

    def test_read_file_stub_preserves_preview(self):
        """read_file stub keeps first 100 chars as preview."""
        file_content = "# My Note\n\nThis is the beginning of a very long file with lots of content that goes on and on and on..."
        content = json.dumps({"success": True, "content": file_content})
        stub = build_tool_stub(content, "read_file")
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["content_length"] == len(file_content)
        assert parsed["content_preview"] == file_content[:100]

    def test_read_file_stub_preserves_truncation_marker(self):
        """read_file stub preserves pagination truncation markers."""
        file_content = "Some content here...\n\n[... truncated at char 4000 of 12000. Use offset=4000 to read more.]"
        content = json.dumps({"success": True, "content": file_content})
        stub = build_tool_stub(content, "read_file")
        parsed = json.loads(stub)
        assert "truncation_marker" in parsed
        assert "offset=4000" in parsed["truncation_marker"]

    def test_read_file_stub_no_truncation(self):
        """read_file stub without truncation omits truncation_marker."""
        content = json.dumps({"success": True, "content": "Short file"})
        stub = build_tool_stub(content, "read_file")
        parsed = json.loads(stub)
        assert "truncation_marker" not in parsed
        assert parsed["content_preview"] == "Short file"

    def test_list_stub_preserves_total(self):
        """List tool stubs preserve total for pagination context."""
        content = json.dumps({
            "success": True,
            "results": ["file1.md", "file2.md", "file3.md"],
            "total": 25,
        })
        for tool in ["find_backlinks", "find_outlinks", "search_by_folder",
                      "list_files_by_frontmatter", "search_by_date_range"]:
            stub = build_tool_stub(content, tool)
            parsed = json.loads(stub)
            assert parsed["total"] == 25, f"Failed for {tool}"
            assert parsed["result_count"] == 3
            assert parsed["results"] == ["file1.md", "file2.md", "file3.md"]

    def test_list_stub_empty_results(self):
        """List tool stub with empty results preserves total=0."""
        content = json.dumps({
            "success": True,
            "message": "No backlinks found",
            "results": [],
            "total": 0,
        })
        stub = build_tool_stub(content, "find_backlinks")
        parsed = json.loads(stub)
        assert parsed["total"] == 0
        assert parsed["result_count"] == 0

    def test_web_search_stub_keeps_title_url(self):
        """web_search stub keeps title and URL but drops snippet."""
        content = json.dumps({
            "success": True,
            "results": [
                {"title": "Example Page", "url": "https://example.com", "snippet": "A very long snippet..."},
            ],
        })
        stub = build_tool_stub(content, "web_search")
        parsed = json.loads(stub)
        assert parsed["results"][0]["title"] == "Example Page"
        assert parsed["results"][0]["url"] == "https://example.com"
        assert "snippet" not in parsed["results"][0]

    def test_unknown_tool_falls_back_to_generic(self):
        """Unknown tool name uses generic stub builder."""
        content = json.dumps({"success": True, "path": "new/note.md"})
        stub = build_tool_stub(content, "create_file")
        parsed = json.loads(stub)
        assert parsed["status"] == "success"
        assert parsed["path"] == "new/note.md"

    def test_none_tool_name_uses_generic(self):
        """None tool_name uses generic stub builder (backward compat)."""
        content = json.dumps({
            "success": True,
            "results": [{"source": "a.md", "content": "..."}],
        })
        stub = build_tool_stub(content)
        parsed = json.loads(stub)
        assert "files" in parsed
        assert "results" not in parsed


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

    def test_resolves_tool_name_from_assistant_messages(self):
        """compact_tool_messages uses tool name for tool-specific stubs."""
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({
                 "success": True,
                 "results": [{"source": "note.md", "content": "long text here", "heading": "## Intro"}],
             })},
        ]
        compact_tool_messages(messages)
        parsed = json.loads(messages[1]["content"])
        # search_vault stub has "results" list with heading/snippet, not generic "files"
        assert "results" in parsed
        assert parsed["results"][0]["heading"] == "## Intro"
        assert "snippet" in parsed["results"][0]

    def test_missing_tool_name_uses_generic(self):
        """Tool message without matching assistant message uses generic stub."""
        messages = [
            {"role": "tool", "tool_call_id": "orphan_call",
             "content": json.dumps({"success": True, "path": "test.md"})},
        ]
        compact_tool_messages(messages)
        parsed = json.loads(messages[0]["content"])
        assert parsed["path"] == "test.md"

    def test_multiple_tool_calls_resolved_correctly(self):
        """Multiple tool calls in one assistant message are all resolved."""
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"},
                {"id": "call_2", "function": {"name": "read_file"}, "type": "function"},
            ]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": json.dumps({
                 "success": True,
                 "results": [{"source": "a.md", "content": "text", "heading": "## H"}],
             })},
            {"role": "tool", "tool_call_id": "call_2",
             "content": json.dumps({"success": True, "content": "File content here"})},
        ]
        compact_tool_messages(messages)

        search_stub = json.loads(messages[1]["content"])
        assert "results" in search_stub

        read_stub = json.loads(messages[2]["content"])
        assert "content_preview" in read_stub


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


class TestChatEndpointIntegration:
    """Integration tests for /chat with file-keyed sessions."""

    def setup_method(self):
        file_sessions.clear()
        app.state.mcp_session = AsyncMock()
        app.state.llm_client = MagicMock()
        app.state.tools = []
        app.state.system_prompt = "test system prompt"

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_same_file_continues_session(self, mock_agent_turn):
        """Two requests with same active_file share a session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "note.md"})
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "more", "active_file": "note.md"})
            sid2 = r2.json()["session_id"]

        assert sid1 == sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_different_file_new_session(self, mock_agent_turn):
        """Different active_file gets a different session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "a.md"})
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "hi", "active_file": "b.md"})
            sid2 = r2.json()["session_id"]

        assert sid1 != sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_null_file_works(self, mock_agent_turn):
        """Null active_file creates and continues a session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi"})
            assert r1.status_code == 200
            sid1 = r1.json()["session_id"]

            r2 = client.post("/chat", json={"message": "more"})
            sid2 = r2.json()["session_id"]

        assert sid1 == sid2

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_switch_back_resumes_session(self, mock_agent_turn):
        """Switching away and back resumes the original session."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            r1 = client.post("/chat", json={"message": "hi", "active_file": "a.md"})
            sid_a = r1.json()["session_id"]

            client.post("/chat", json={"message": "hi", "active_file": "b.md"})

            r3 = client.post("/chat", json={"message": "back", "active_file": "a.md"})
            sid_a2 = r3.json()["session_id"]

        assert sid_a == sid_a2
