"""Tests for session management: tool compaction."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


from api_server import Session, get_or_create_session, file_sessions, trim_messages


@pytest.fixture(autouse=False)
def clear_sessions():
    """Clear file_sessions before each test."""
    file_sessions.clear()
    yield
    file_sessions.clear()


@pytest.fixture(autouse=False)
def mock_app(clear_sessions):
    """Clear sessions and set up mock app state for endpoint tests."""
    app.state.mcp_session = AsyncMock()
    app.state.llm_client = MagicMock()
    app.state.tools = []
    app.state.system_prompt = "test system prompt"


@pytest.mark.usefixtures("clear_sessions")
class TestSessionRouting:
    """Tests for file-keyed session routing."""

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


@pytest.mark.usefixtures("mock_app")
class TestChatEndpointIntegration:
    """Integration tests for /chat with file-keyed sessions."""

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
    def test_compacted_stubs_survive_across_requests(self, mock_agent_turn):
        """Already-compacted tool stubs are not re-compacted on subsequent requests.

        Regression test: _compacted flags were stripped before LLM calls and not
        restored, causing compact_tool_messages to re-process stubs. Re-compaction
        degrades stubs (e.g. status becomes 'unknown', read_file loses preview).
        """
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            # First request — agent_turn adds tool messages to the session
            r1 = client.post("/chat", json={"message": "hi", "active_file": "recompact.md"})
            assert r1.status_code == 200

            # Simulate what agent_turn would have appended: an assistant tool_call
            # and a tool result, then compact them (as the /chat handler does).
            session = file_sessions["recompact.md"]
            session.messages.append({
                "role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_rc", "function": {"name": "read_file"}, "type": "function"},
                ],
            })
            original_tool_content = json.dumps({
                "success": True, "content": "A" * 200, "path": "note.md",
            })
            session.messages.append({
                "role": "tool", "tool_call_id": "call_rc",
                "content": original_tool_content,
            })
            compact_tool_messages(session.messages)

            # Verify the stub after first compaction
            tool_msg = next(m for m in session.messages if m.get("tool_call_id") == "call_rc")
            stub_after_first = json.loads(tool_msg["content"])
            assert stub_after_first["status"] == "success"
            assert "content_preview" in stub_after_first
            assert stub_after_first["content_length"] == 200

            # Second request — triggers strip + restore + compact cycle
            r2 = client.post("/chat", json={"message": "more", "active_file": "recompact.md"})
            assert r2.status_code == 200

            # The stub should be unchanged after the second request
            tool_msg = next(m for m in session.messages if m.get("tool_call_id") == "call_rc")
            stub_after_second = json.loads(tool_msg["content"])
            assert stub_after_second["status"] == "success", (
                "Re-compaction degraded status to 'unknown'"
            )
            assert "content_preview" in stub_after_second, (
                "Re-compaction lost content_preview"
            )
            assert stub_after_second["content_length"] == 200, (
                "Re-compaction lost content_length"
            )

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

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    @patch("api_server.load_preferences")
    def test_preferences_reloaded_per_request(self, mock_load_prefs, mock_agent_turn):
        """Preferences should be reloaded each request, not cached from startup."""
        mock_agent_turn.return_value = "response"
        # First request: no preferences
        mock_load_prefs.return_value = None

        with TestClient(app, raise_server_exceptions=True) as client:
            client.post("/chat", json={"message": "hi", "active_file": "prefs.md"})
            session = file_sessions["prefs.md"]
            assert "User Preferences" not in session.messages[0]["content"]

            # Second request: preferences now exist
            mock_load_prefs.return_value = "\n\n## User Preferences\n\n- Always be concise"
            client.post("/chat", json={"message": "more", "active_file": "prefs.md"})
            assert "User Preferences" in session.messages[0]["content"]
            assert "Always be concise" in session.messages[0]["content"]

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    @patch("api_server.load_preferences")
    def test_system_prompt_includes_current_date(self, mock_load_prefs, mock_agent_turn):
        """System prompt should include the current date."""
        mock_agent_turn.return_value = "response"
        mock_load_prefs.return_value = None

        with TestClient(app, raise_server_exceptions=True) as client:
            client.post("/chat", json={"message": "hi", "active_file": "date.md"})
            session = file_sessions["date.md"]
            system_content = session.messages[0]["content"]
            today = datetime.now().strftime("%Y-%m-%d")
            assert f"Current date: {today}" in system_content

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_chat_error_does_not_leak_details(self, mock_agent_turn):
        """Error responses return generic message, not exception internals."""
        mock_agent_turn.side_effect = Exception(
            "Connection to /home/user/vault failed: timeout"
        )

        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/chat", json={"message": "hi", "active_file": "err.md"})
            assert r.status_code == 500
            body = r.json()
            assert body["detail"] == "Internal server error"
            assert "/home/user/vault" not in json.dumps(body)


@pytest.mark.usefixtures("clear_sessions")
class TestLRUEviction:
    """Tests for LRU session eviction."""

    @patch("api_server.MAX_SESSIONS", 3)
    def test_oldest_evicted_when_full(self):
        """When MAX_SESSIONS is reached, the least recently used session is evicted."""
        get_or_create_session("a.md", "prompt")
        get_or_create_session("b.md", "prompt")
        get_or_create_session("c.md", "prompt")
        assert len(file_sessions) == 3

        # Adding a 4th should evict "a.md" (oldest)
        get_or_create_session("d.md", "prompt")
        assert len(file_sessions) == 3
        assert "a.md" not in file_sessions
        assert "d.md" in file_sessions

    @patch("api_server.MAX_SESSIONS", 3)
    def test_accessed_session_not_evicted(self):
        """Accessing a session moves it to end, protecting it from eviction."""
        get_or_create_session("a.md", "prompt")
        get_or_create_session("b.md", "prompt")
        get_or_create_session("c.md", "prompt")

        # Access "a.md" to make it most recently used
        get_or_create_session("a.md", "prompt")

        # Adding new session should evict "b.md" (now the oldest)
        get_or_create_session("d.md", "prompt")
        assert "a.md" in file_sessions
        assert "b.md" not in file_sessions

    @patch("api_server.MAX_SESSIONS", 2)
    def test_null_file_participates_in_lru(self):
        """None active_file sessions are subject to LRU eviction too."""
        get_or_create_session(None, "prompt")
        get_or_create_session("a.md", "prompt")

        # This should evict the None session
        get_or_create_session("b.md", "prompt")
        assert None not in file_sessions
        assert "b.md" in file_sessions


class TestTrimMessages:
    """Tests for per-session message trimming."""

    @patch("api_server.MAX_SESSION_MESSAGES", 5)
    def test_trims_old_messages(self):
        """Messages beyond MAX_SESSION_MESSAGES are trimmed, keeping system prompt."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "resp3"},
        ]
        trim_messages(messages)
        assert len(messages) == 5
        assert messages[0]["role"] == "system"
        assert messages[1]["content"] == "msg2"

    @patch("api_server.MAX_SESSION_MESSAGES", 10)
    def test_no_trim_when_under_limit(self):
        """Messages under the limit are not trimmed."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
        ]
        trim_messages(messages)
        assert len(messages) == 3

    @patch("api_server.MAX_SESSION_MESSAGES", 5)
    def test_preserves_tool_call_groups(self):
        """Trim point advances past tool call groups to avoid orphaning tool messages."""
        messages = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            # This is a tool call group that would be split by naive trimming
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "search_vault"}, "type": "function"}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            {"role": "assistant", "content": "Found it."},
            # Recent messages
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]
        trim_messages(messages)
        # Should skip past the tool group and trim at "msg2"
        assert messages[0]["role"] == "system"
        # No orphaned tool messages — first non-system message should be user
        roles = [m["role"] for m in messages[1:]]
        assert roles[0] == "user"
        # Tool message should not appear without its preceding assistant+tool_calls
        for i, m in enumerate(messages[1:], 1):
            if m.get("role") == "tool":
                # Preceding message should be assistant with tool_calls
                assert messages[i - 1].get("tool_calls") is not None

    @patch("api_server.MAX_SESSION_MESSAGES", 5)
    def test_system_prompt_always_preserved(self):
        """System prompt is never trimmed regardless of message count."""
        messages = [
            {"role": "system", "content": "important system prompt"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            for i in range(10)
        ]
        trim_messages(messages)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "important system prompt"


@pytest.mark.usefixtures("mock_app")
class TestStreamEndpoint:
    """Tests for POST /chat/stream SSE endpoint."""

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_returns_sse_events(self, mock_agent_turn):
        """Stream endpoint returns SSE-formatted events."""
        async def fake_agent_turn(client, session, messages, tools, on_event=None):
            if on_event:
                await on_event("tool_call", {"tool": "search_vault", "args": {"query": "test"}})
                await on_event("tool_result", {"tool": "search_vault", "success": True})
                await on_event("response", {"content": "Found results."})
            return "Found results."

        mock_agent_turn.side_effect = fake_agent_turn

        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post(
                "/chat/stream",
                json={"message": "search", "active_file": "note.md"},
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            # Parse SSE events from response body
            lines = response.text.strip().split("\n")
            events = []
            for line in lines:
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            event_types = [e["type"] for e in events]
            assert "tool_call" in event_types
            assert "tool_result" in event_types
            assert "response" in event_types
            assert "done" in event_types

            # Verify done event has session_id
            done_event = next(e for e in events if e["type"] == "done")
            assert "session_id" in done_event

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_shares_sessions_with_chat(self, mock_agent_turn):
        """Stream endpoint shares the same session store as /chat."""
        mock_agent_turn.return_value = "response"

        with TestClient(app, raise_server_exceptions=True) as client:
            # Create session via /chat
            r1 = client.post("/chat", json={"message": "hi", "active_file": "shared.md"})
            sid1 = r1.json()["session_id"]

            # Continue via /chat/stream — need to handle the on_event kwarg
            async def fake_stream(client_arg, session, messages, tools, on_event=None):
                if on_event:
                    await on_event("response", {"content": "streamed"})
                return "streamed"
            mock_agent_turn.side_effect = fake_stream

            r2 = client.post(
                "/chat/stream",
                json={"message": "more", "active_file": "shared.md"},
            )
            # Parse session_id from SSE events
            events = []
            for line in r2.text.strip().split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            done_event = next(e for e in events if e["type"] == "done")
            assert done_event["session_id"] == sid1

    @patch("api_server.agent_turn", new_callable=AsyncMock)
    def test_stream_error_sends_sanitized_error_event(self, mock_agent_turn):
        """Errors during agent_turn produce a sanitized error SSE event."""
        mock_agent_turn.side_effect = Exception(
            "Connection to /home/user/vault failed: timeout"
        )

        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post(
                "/chat/stream",
                json={"message": "hi", "active_file": "err.md"},
            )
            assert response.status_code == 200  # SSE always returns 200

            events = []
            for line in response.text.strip().split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

            event_types = [e["type"] for e in events]
            assert "error" in event_types
            assert "done" in event_types

            error_event = next(e for e in events if e["type"] == "error")
            assert error_event["error"] == "Internal server error"
            assert "/home/user/vault" not in json.dumps(events)


@pytest.mark.usefixtures("mock_app")
class TestConcurrentRequests:
    """Tests for per-session locking under concurrent requests."""

    def test_same_session_requests_are_serialized(self):
        """Two concurrent requests for the same session must not interleave."""
        call_log = []

        async def mock_agent_turn(*args, **kwargs):
            call_log.append("enter")
            await asyncio.sleep(0.05)
            call_log.append("exit")
            return "response"

        async def run():
            with patch("api_server.agent_turn", side_effect=mock_agent_turn), \
                 patch("api_server.ensure_interaction_logged", new_callable=AsyncMock):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await asyncio.gather(
                        client.post("/chat", json={"message": "first", "active_file": "test.md"}),
                        client.post("/chat", json={"message": "second", "active_file": "test.md"}),
                    )

        asyncio.run(run())
        # With serialization: enter, exit, enter, exit (never overlapping)
        # Without it: enter, enter, exit, exit
        assert call_log == ["enter", "exit", "enter", "exit"]

    def test_different_session_requests_run_in_parallel(self):
        """Concurrent requests for different sessions must not block each other."""
        counter = [0, 0]  # [currently_active, max_active]

        async def mock_agent_turn(*args, **kwargs):
            counter[0] += 1
            counter[1] = max(counter[1], counter[0])
            await asyncio.sleep(0.05)
            counter[0] -= 1
            return "response"

        async def run():
            with patch("api_server.agent_turn", side_effect=mock_agent_turn), \
                 patch("api_server.ensure_interaction_logged", new_callable=AsyncMock):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await asyncio.gather(
                        client.post("/chat", json={"message": "msg_a", "active_file": "a.md"}),
                        client.post("/chat", json={"message": "msg_b", "active_file": "b.md"}),
                    )

        asyncio.run(run())
        # Both agent_turn calls should have been active at the same time
        assert counter[1] == 2
