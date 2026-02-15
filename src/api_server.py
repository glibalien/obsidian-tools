#!/usr/bin/env python3
"""FastAPI HTTP wrapper for the LLM agent."""

import json
import logging
import sys
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

from config import API_PORT
from agent import (
    PROJECT_ROOT,
    SYSTEM_PROMPT,
    agent_turn,
    create_llm_client,
    load_preferences,
    mcp_tool_to_openai_function,
)

# Session storage: session_id -> messages list
sessions: dict[str, list[dict]] = {}


class ChatRequest(BaseModel):
    """Request body for POST /chat."""

    message: str
    session_id: str | None = None
    active_file: str | None = None


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    response: str
    session_id: str


def format_context_prefix(active_file: str | None) -> str:
    """Format context prefix for the user message."""
    if not active_file:
        return ""
    return f"[Context: Currently viewing '{active_file}']\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP session and LLM client at startup."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "src" / "mcp_server.py")],
        cwd=str(PROJECT_ROOT),
    )

    async with AsyncExitStack() as stack:
        # Set up MCP connection
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        # Get available tools
        tools_result = await session.list_tools()
        tools = [mcp_tool_to_openai_function(t) for t in tools_result.tools]

        # Set up LLM client
        client = create_llm_client()

        # Build system prompt with preferences
        system_prompt = SYSTEM_PROMPT
        preferences = load_preferences()
        if preferences:
            system_prompt += preferences

        # Store in app state
        app.state.mcp_session = session
        app.state.llm_client = client
        app.state.tools = tools
        app.state.system_prompt = system_prompt

        yield
        # Cleanup happens automatically when exiting the context


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


app = FastAPI(
    title="Obsidian Tools API",
    description="HTTP API for interacting with the Obsidian vault agent",
    lifespan=lifespan,
)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat message and return the agent's response.

    If session_id is provided, continues an existing conversation.
    If session_id is omitted, creates a new session.
    """
    # Get or create session
    if request.session_id:
        if request.session_id not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session_id = request.session_id
        messages = sessions[session_id]
    else:
        session_id = str(uuid.uuid4())
        messages = [{"role": "system", "content": app.state.system_prompt}]
        sessions[session_id] = messages

    # Add user message with context prefix if active file is provided
    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    try:
        response = await agent_turn(
            app.state.llm_client,
            app.state.mcp_session,
            messages,
            app.state.tools,
        )
        return ChatResponse(response=response, session_id=session_id)
    except Exception as e:
        # Remove failed user message to keep history clean
        messages.pop()
        raise HTTPException(status_code=500, detail=str(e))


def main():
    """Run the API server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=API_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
