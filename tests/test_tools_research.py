"""Tests for tools/research.py - topic extraction, research gathering, and synthesis."""

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.compaction import build_tool_stub
from tools.research import (
    _extract_topics,
    _fetch_page,
    _gather_research,
    _get_completion_content,
    _pinned_get,
    _resolve_public_host,
    _research_topic,
    _synthesize_research,
    research_note,
)


class TestExtractTopics:
    """Tests for _extract_topics function."""

    def test_happy_path(self):
        """Should extract topics from content and return list of dicts."""
        topics = [
            {"topic": "Project planning", "context": "Q1 roadmap discussion", "type": "theme"},
            {"topic": "Budget review", "context": "Annual budget cycle", "type": "task"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some note content about projects.")

        assert len(result) == 2
        assert result[0]["topic"] == "Project planning"
        assert result[1]["type"] == "task"
        assert result[0]["context"] == "Q1 roadmap discussion"

        # Verify the LLM was called with the correct structure
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert any(m["role"] == "system" for m in messages)
        assert any(m["role"] == "user" for m in messages)

    def test_focus_included_in_prompt(self):
        """Should prepend focus guidance to the user message."""
        topics = [
            {"topic": "Timeline", "context": "Delivery dates", "type": "theme"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _extract_topics(mock_client, "Some content", focus="project timeline")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Focus especially on: project timeline" in user_msg["content"]

    def test_topic_cap(self):
        """Should truncate topics to MAX_RESEARCH_TOPICS."""
        # Generate more topics than the cap
        topics = [
            {"topic": f"Topic {i}", "context": f"Context {i}", "type": "theme"}
            for i in range(20)
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("tools.research.MAX_RESEARCH_TOPICS", 5):
            result = _extract_topics(mock_client, "Lots of content.")

        assert len(result) == 5
        # Should keep the first 5
        assert result[0]["topic"] == "Topic 0"
        assert result[4]["topic"] == "Topic 4"

    def test_llm_returns_none(self):
        """Should return empty list when LLM response content is None."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_llm_returns_invalid_json(self):
        """Should return empty list when LLM response is not valid JSON."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Here are some topics: blah blah"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_llm_exception_returns_empty_list(self):
        """Should return empty list when LLM call raises an exception."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = _extract_topics(mock_client, "Some content.")

        assert result == []

    def test_no_focus_omits_focus_line(self):
        """Without focus, user message should not contain focus guidance."""
        topics = [
            {"topic": "General", "context": "Overview", "type": "theme"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(topics)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _extract_topics(mock_client, "Some content")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Focus especially on:" not in user_msg["content"]

    def test_empty_choices_returns_empty_list(self):
        """Should return empty list when LLM response has no choices."""
        mock_response = MagicMock()
        mock_response.choices = []

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _extract_topics(mock_client, "Some content.")

        assert result == []


class TestGetCompletionContent:
    """Tests for _get_completion_content helper."""

    def test_normal_response(self):
        """Should extract content from a standard response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"

        assert _get_completion_content(mock_response) == "Hello"

    def test_empty_choices(self):
        """Should return None when choices is empty."""
        mock_response = MagicMock()
        mock_response.choices = []

        assert _get_completion_content(mock_response) is None

    def test_none_content(self):
        """Should return None when content is None."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        assert _get_completion_content(mock_response) is None


class TestSSRFProtection:
    """Tests for DNS-pinned URL validation and SSRF prevention."""

    def test_public_host_returns_ips(self):
        """_resolve_public_host should return all IPs for a public host."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
            assert _resolve_public_host("example.com") == ["93.184.216.34"]

    def test_public_host_deduplicates(self):
        """_resolve_public_host should deduplicate IPs preserving order."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("1.2.3.4", 0)),
            ]
            assert _resolve_public_host("example.com") == ["93.184.216.34", "1.2.3.4"]

    def test_localhost_blocked(self):
        """Loopback addresses should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
            assert _resolve_public_host("localhost") == []

    def test_private_ip_blocked(self):
        """Private network addresses should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("192.168.1.1", 0))]
            assert _resolve_public_host("internal.corp") == []

    def test_link_local_blocked(self):
        """Link-local addresses should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
            assert _resolve_public_host("metadata.internal") == []

    def test_dns_failure_blocked(self):
        """DNS resolution failure should be treated as blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.side_effect = socket.gaierror("Name resolution failed")
            assert _resolve_public_host("nonexistent.invalid") == []

    def test_mixed_ips_blocked_if_any_non_global(self):
        """If any resolved IP is non-global, the host should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("10.0.0.1", 0)),
            ]
            assert _resolve_public_host("dual-homed.example") == []

    def test_carrier_grade_nat_blocked(self):
        """Carrier-grade NAT (100.64.0.0/10) should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("100.64.0.1", 0))]
            assert _resolve_public_host("cgnat.internal") == []

    def test_multicast_blocked(self):
        """Multicast addresses should be blocked."""
        with patch("tools.research.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(None, None, None, None, ("224.0.0.1", 0))]
            assert _resolve_public_host("multicast.local") == []

    def test_pinned_get_connects_to_resolved_ip(self):
        """_pinned_get should connect to the validated IP, not re-resolve."""
        with patch("tools.research._resolve_public_host", return_value=["93.184.216.34"]), \
             patch("tools.research.http.client.HTTPConnection") as mock_conn_cls:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_conn_cls.return_value.getresponse.return_value = mock_response

            result = _pinned_get("http://example.com/page", timeout=10)

        assert result == (200, mock_response)
        mock_conn_cls.assert_called_once_with("93.184.216.34", 80, timeout=10)

    def test_pinned_get_uses_tls_sni_for_https(self):
        """HTTPS requests should use original hostname for TLS SNI."""
        with patch("tools.research._resolve_public_host", return_value=["93.184.216.34"]), \
             patch("tools.research._PinnedHTTPSConnection") as mock_conn_cls:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_conn_cls.return_value.getresponse.return_value = mock_response

            _pinned_get("https://example.com/page", timeout=10)

        mock_conn_cls.assert_called_once_with(
            "93.184.216.34", 443, sni_hostname="example.com", timeout=10,
        )

    def test_pinned_get_tries_next_ip_on_failure(self):
        """_pinned_get should fall back to the next IP if the first fails."""
        with patch("tools.research._resolve_public_host", return_value=["fd00::1", "93.184.216.34"]), \
             patch("tools.research.http.client.HTTPConnection") as mock_conn_cls:
            first_conn = MagicMock()
            first_conn.request.side_effect = OSError("Network unreachable")
            second_conn = MagicMock()
            mock_response = MagicMock()
            mock_response.status = 200
            second_conn.getresponse.return_value = mock_response
            mock_conn_cls.side_effect = [first_conn, second_conn]

            result = _pinned_get("http://example.com/page", timeout=10)

        assert result == (200, mock_response)
        assert mock_conn_cls.call_count == 2
        mock_conn_cls.assert_any_call("fd00::1", 80, timeout=10)
        mock_conn_cls.assert_any_call("93.184.216.34", 80, timeout=10)

    def test_pinned_get_blocks_non_public_host(self):
        """_pinned_get returns None when host resolves to non-public IP."""
        with patch("tools.research._resolve_public_host", return_value=[]):
            assert _pinned_get("http://127.0.0.1:8080/admin", timeout=10) is None

    def test_fetch_page_blocks_non_public_host(self):
        """_fetch_page returns None for non-public URLs without connecting."""
        with patch("tools.research._pinned_get", return_value=None):
            result = _fetch_page("http://127.0.0.1:8080/admin")
        assert result is None

    def test_fetch_page_blocks_redirect_to_non_public(self):
        """_fetch_page blocks when redirect target resolves to non-public IP."""
        redirect_response = MagicMock()
        redirect_response.status = 302
        redirect_response.getheader.return_value = "http://169.254.169.254/latest/meta-data"
        redirect_response.read.return_value = b""

        with patch("tools.research._pinned_get") as mock_get:
            # First call succeeds with redirect, second call blocked
            mock_get.side_effect = [(302, redirect_response), None]
            result = _fetch_page("https://evil.com/redirect")

        assert result is None
        assert mock_get.call_count == 2

    def test_fetch_page_follows_safe_redirects(self):
        """_fetch_page should follow redirects when all targets are public."""
        redirect_response = MagicMock()
        redirect_response.status = 301
        redirect_response.getheader.return_value = "https://safe.example.com/page"
        redirect_response.read.return_value = b""

        final_response = MagicMock()
        final_response.status = 200
        final_response.read.return_value = b"<html><body>Content</body></html>"

        with patch("tools.research._pinned_get") as mock_get:
            mock_get.side_effect = [(301, redirect_response), (200, final_response)]
            result = _fetch_page("https://example.com/old")

        assert result is not None
        assert "Content" in result
        assert mock_get.call_count == 2

    def test_fetch_page_too_many_redirects(self):
        """_fetch_page should abort after too many redirects."""
        redirect_response = MagicMock()
        redirect_response.status = 301
        redirect_response.getheader.return_value = "https://example.com/loop"
        redirect_response.read.return_value = b""

        with patch("tools.research._pinned_get") as mock_get:
            mock_get.return_value = (301, redirect_response)
            result = _fetch_page("https://example.com/loop")

        assert result is None


class TestGatherResearch:
    """Tests for _gather_research and _research_topic functions."""

    def _make_web_search_ok(self, results):
        """Helper to create web_search ok() JSON response."""
        return json.dumps({"success": True, "results": results})

    def _make_find_notes_ok(self, results):
        """Helper to create find_notes ok() JSON response."""
        return json.dumps({"success": True, "results": results, "total": len(results)})

    def test_shallow_searches_web_and_vault(self):
        """Shallow mode calls web_search and find_notes per topic, returns structured results."""
        topics = [
            {"topic": "Machine learning", "context": "ML discussion", "type": "concept"},
            {"topic": "Python decorators", "context": "Code review", "type": "concept"},
        ]

        web_results = [
            {"title": "ML Guide", "url": "https://example.com/ml", "snippet": "ML overview"},
        ]
        vault_results = [
            {"path": "notes/ml.md", "content": "ML content", "source": "notes/ml.md"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = self._make_web_search_ok(web_results)
            mock_vault.return_value = self._make_find_notes_ok(vault_results)

            results = _gather_research(topics, depth="shallow")

        assert len(results) == 2
        # Both topics should have results
        assert results[0]["topic"] == "Machine learning"
        assert results[1]["topic"] == "Python decorators"
        # Structure check
        for r in results:
            assert "web_results" in r
            assert "vault_results" in r
            assert "context" in r
            assert "type" in r
        # web_search called once per topic
        assert mock_web.call_count == 2
        # find_notes called once per topic
        assert mock_vault.call_count == 2

    def test_deep_fetches_pages(self):
        """Deep mode fetches top web result URLs and extracts content via LLM."""
        topics = [
            {"topic": "Rust ownership", "context": "Language study", "type": "concept"},
        ]

        web_results = [
            {"title": "Rust Book", "url": "https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html", "snippet": "Ownership"},
            {"title": "Rust Blog", "url": "https://blog.rust-lang.org/ownership", "snippet": "Ownership blog"},
            {"title": "Third result", "url": "https://example.com/third", "snippet": "Not fetched"},
        ]
        vault_results = []

        mock_client = MagicMock()

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault, \
             patch("tools.research._fetch_page") as mock_fetch, \
             patch("tools.research._extract_page_content") as mock_extract:
            mock_web.return_value = self._make_web_search_ok(web_results)
            mock_vault.return_value = self._make_find_notes_ok(vault_results)
            mock_fetch.return_value = "Rust ownership explained in markdown"
            mock_extract.return_value = "Ownership means each value has one owner."

            results = _gather_research(topics, depth="deep", client=mock_client)

        assert len(results) == 1
        r = results[0]
        assert "page_extracts" in r
        assert len(r["page_extracts"]) == 2  # Only top 2 URLs fetched
        assert r["page_extracts"][0]["content"] == "Ownership means each value has one owner."
        assert "url" in r["page_extracts"][0]
        # _fetch_page called for top 2 URLs only
        assert mock_fetch.call_count == 2
        assert mock_extract.call_count == 2

    def test_web_search_failure_skipped(self):
        """When web_search returns an error, results still returned with empty web_results."""
        topics = [
            {"topic": "Quantum computing", "context": "Physics notes", "type": "concept"},
        ]

        vault_results = [
            {"path": "notes/quantum.md", "content": "Quantum stuff"},
        ]

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_web.return_value = json.dumps({"success": False, "error": "Network error"})
            mock_vault.return_value = self._make_find_notes_ok(vault_results)

            results = _gather_research(topics, depth="shallow")

        assert len(results) == 1
        r = results[0]
        assert r["topic"] == "Quantum computing"
        assert r["web_results"] == []
        assert len(r["vault_results"]) == 1

    def test_page_fetch_failure_skipped(self):
        """When page fetch fails, page_extracts is empty but topic still in results."""
        topics = [
            {"topic": "GraphQL", "context": "API design", "type": "concept"},
        ]

        web_results = [
            {"title": "GraphQL Docs", "url": "https://graphql.org/learn", "snippet": "Learn GraphQL"},
        ]
        vault_results = []

        mock_client = MagicMock()

        with patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault, \
             patch("tools.research._fetch_page", return_value=None):
            mock_web.return_value = self._make_web_search_ok(web_results)
            mock_vault.return_value = self._make_find_notes_ok(vault_results)

            results = _gather_research(topics, depth="deep", client=mock_client)

        assert len(results) == 1
        r = results[0]
        assert r["topic"] == "GraphQL"
        assert r["page_extracts"] == []
        # Still has the web results from search
        assert len(r["web_results"]) == 1


class TestSynthesizeResearch:
    """Tests for _synthesize_research function."""

    def test_sends_all_material_to_llm(self):
        """Should include note content, web results, vault results, and page extracts in prompt."""
        research_results = [
            {
                "topic": "Machine learning",
                "context": "ML discussion",
                "type": "concept",
                "web_results": [
                    {"title": "ML Guide", "url": "https://example.com/ml", "snippet": "ML overview"},
                ],
                "vault_results": [
                    {"path": "notes/ml-basics.md", "content": "ML fundamentals explained"},
                ],
                "page_extracts": [
                    {"url": "https://example.com/ml", "content": "Deep learning is a subset of ML."},
                ],
            },
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "### Machine Learning\nSynthesized research."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _synthesize_research(mock_client, "Note about ML topics", research_results)

        assert result == "### Machine Learning\nSynthesized research."

        # Verify the LLM prompt includes all material
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]

        # Note content should be present
        assert "Note about ML topics" in content
        # Web results should be present
        assert "ML Guide" in content
        assert "https://example.com/ml" in content
        # Vault results should reference note name as wikilink-friendly
        assert "ml-basics" in content
        # Page extracts should be present
        assert "Deep learning is a subset of ML" in content

    def test_vault_results_with_source_key(self):
        """Semantic search results use 'source' not 'path'; wikilinks should still resolve."""
        research_results = [
            {
                "topic": "Rust ownership",
                "context": "Language study",
                "type": "concept",
                "web_results": [],
                "vault_results": [
                    {"source": "/vault/notes/rust-guide.md", "content": "Ownership rules"},
                    {"source": "/vault/daily/2026-01-15.md", "content": "Studied Rust today"},
                ],
                "page_extracts": [],
            },
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "### Rust Ownership\nSynthesized."

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        _synthesize_research(mock_client, "Note about Rust", research_results)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]

        # Should produce wikilinks from source paths, not empty [[]]
        assert "[[rust-guide]]" in content
        assert "[[2026-01-15]]" in content
        assert "[[]]" not in content

    def test_llm_returns_none(self):
        """Should return None when LLM returns empty response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        result = _synthesize_research(mock_client, "Some content", [])

        assert result is None


class TestResearchNote:
    """Tests for research_note main function."""

    def _make_mock_response(self, content):
        """Helper to create a mock LLM response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = content
        return mock_response

    def _make_web_search_ok(self, results):
        """Helper to create web_search ok() JSON response."""
        return json.dumps({"success": True, "results": results})

    def _make_find_notes_ok(self, results):
        """Helper to create find_notes ok() JSON response."""
        return json.dumps({"success": True, "results": results, "total": len(results)})

    def test_happy_path(self, vault_config):
        """Full pipeline: read, extract topics, research, synthesize, append ## Research."""
        topics = [
            {"topic": "Project planning", "context": "Q1 roadmap", "type": "theme"},
        ]

        # LLM call 1: topic extraction, call 2: synthesis
        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response(
            "### Project Planning\nResearch findings about project planning."
        )

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note("note1.md"))

        assert result["success"] is True
        assert result["path"]
        assert result["topics_researched"] == 1
        assert "Project Planning" in result["preview"]

        # Verify file was modified
        content = (vault_config / "note1.md").read_text()
        assert "## Research" in content
        assert "### Project Planning" in content
        assert "Research findings about project planning." in content

    def test_replaces_existing_research_section(self, vault_config):
        """When ## Research already exists, should replace it instead of duplicating."""
        # Write a file with an existing ## Research section
        note_path = vault_config / "note1.md"
        original = note_path.read_text()
        note_path.write_text(original + "\n## Research\n\nOld research content.\n")

        topics = [
            {"topic": "Budget review", "context": "Finance", "type": "task"},
        ]

        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Budget\nNew research content.")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note("note1.md"))

        assert result["success"] is True
        content = note_path.read_text()
        # Old content should be replaced
        assert "Old research content." not in content
        # New content should be present
        assert "New research content." in content
        # Only one ## Research heading
        assert content.count("## Research") == 1

    def test_file_not_found(self, vault_config):
        """Should return error for missing file."""
        result = json.loads(research_note("nonexistent.md"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_binary_file_rejected(self, vault_config):
        """Should reject non-text files to prevent corruption."""
        attachments = vault_config / "Attachments"
        (attachments / "recording.m4a").write_bytes(b"fake audio")

        result = json.loads(research_note("Attachments/recording.m4a"))
        assert result["success"] is False
        assert "markdown/text" in result["error"].lower()

    def test_no_api_key(self, vault_config):
        """Should return error when FIREWORKS_API_KEY is not set."""
        with patch("os.getenv", return_value=None):
            result = json.loads(research_note("note1.md"))
        assert result["success"] is False
        assert "FIREWORKS_API_KEY" in result["error"]

    def test_no_topics_extracted(self, vault_config):
        """Should return error when no topics found."""
        # LLM returns empty list for topic extraction
        topic_response = self._make_mock_response("[]")

        with patch("tools.research.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.return_value = topic_response

            result = json.loads(research_note("note1.md"))

        assert result["success"] is False
        assert "topic" in result["error"].lower()

    def test_synthesis_failure(self, vault_config):
        """Should return error when synthesis fails; file should be unchanged."""
        original = (vault_config / "note1.md").read_text()

        topics = [
            {"topic": "AI safety", "context": "Discussion", "type": "concept"},
        ]
        topic_response = self._make_mock_response(json.dumps(topics))
        # Synthesis returns None (LLM failure)
        synthesis_response = self._make_mock_response(None)

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note("note1.md"))

        assert result["success"] is False
        assert "synth" in result["error"].lower()
        # File should be unchanged
        assert (vault_config / "note1.md").read_text() == original

    def test_duplicate_research_headings_returns_error(self, vault_config):
        """When file has multiple ## Research headings, should error not append another."""
        note_path = vault_config / "note1.md"
        original = note_path.read_text()
        # Create a file with two ## Research sections (e.g. from a prior bug)
        note_path.write_text(
            original
            + "\n## Research\n\nFirst block.\n\n## Research\n\nSecond block.\n"
        )

        topics = [
            {"topic": "Testing", "context": "QA notes", "type": "task"},
        ]
        topic_response = self._make_mock_response(json.dumps(topics))
        synthesis_response = self._make_mock_response("### Testing\nNew findings.")

        with patch("tools.research.OpenAI") as mock_openai, \
             patch("tools.research.web_search") as mock_web, \
             patch("tools.research.find_notes") as mock_vault:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            mock_client.chat.completions.create.side_effect = [
                topic_response, synthesis_response,
            ]
            mock_web.return_value = self._make_web_search_ok([])
            mock_vault.return_value = self._make_find_notes_ok([])

            result = json.loads(research_note("note1.md"))

        assert result["success"] is False
        assert "multiple" in result["error"].lower()
        # File should NOT have a third ## Research section
        content = note_path.read_text()
        assert content.count("## Research") == 2

    def test_invalid_depth(self, vault_config):
        """Should return error for invalid depth value."""
        result = json.loads(research_note("note1.md", depth="extreme"))
        assert result["success"] is False
        assert "depth" in result["error"].lower()


class TestResearchNoteCompaction:
    """Tests for research_note compaction stub."""

    def test_stub_keeps_path_and_topics(self):
        """Should keep path and topics_researched, drop preview."""
        content = json.dumps({
            "success": True,
            "path": "notes/test.md",
            "topics_researched": 3,
            "preview": "Long preview text that should be dropped...",
        })

        stub = json.loads(build_tool_stub(content, "research_note"))
        assert stub["path"] == "notes/test.md"
        assert stub["topics_researched"] == 3
        assert "preview" not in stub
