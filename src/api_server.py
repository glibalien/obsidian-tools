#!/usr/bin/env python3
"""FastAPI HTTP wrapper for the LLM agent."""

import asyncio
import json
import logging
import sys
import uuid
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

from config import API_PORT, MAX_SESSIONS, MAX_SESSION_MESSAGES
from services.compaction import compact_tool_messages
from agent import (
    PROJECT_ROOT,
    SYSTEM_PROMPT,
    agent_turn,
    create_llm_client,
    load_preferences,
    mcp_tool_to_openai_function,
)

@dataclass
class Session:
    """A chat session tied to an active file."""

    session_id: str
    active_file: str | None
    messages: list[dict] = field(default_factory=list)


# File-keyed session storage: active_file -> Session (LRU order)
file_sessions: OrderedDict[str | None, Session] = OrderedDict()


def get_or_create_session(active_file: str | None, system_prompt: str) -> Session:
    """Get existing session for a file or create a new one.

    Uses LRU eviction: accessed sessions move to end, oldest evicted
    when MAX_SESSIONS is exceeded.
    """
    if active_file in file_sessions:
        file_sessions.move_to_end(active_file)
        return file_sessions[active_file]

    # Evict oldest session if at capacity
    while len(file_sessions) >= MAX_SESSIONS:
        file_sessions.popitem(last=False)

    session = Session(
        session_id=str(uuid.uuid4()),
        active_file=active_file,
        messages=[{"role": "system", "content": system_prompt}],
    )
    file_sessions[active_file] = session
    return session


def trim_messages(messages: list[dict]) -> None:
    """Trim messages to MAX_SESSION_MESSAGES, preserving system prompt.

    Keeps messages[0] (system prompt) + the most recent messages.
    Avoids splitting tool call groups by advancing the trim point
    to the next user message.
    """
    if len(messages) <= MAX_SESSION_MESSAGES:
        return

    # How many non-system messages to keep
    keep = MAX_SESSION_MESSAGES - 1
    trim_index = len(messages) - keep

    # Don't trim the system prompt
    if trim_index <= 1:
        return

    # Advance trim point to avoid splitting a tool call group:
    # find the first 'user' message at or after trim_index
    while trim_index < len(messages) and messages[trim_index].get("role") != "user":
        trim_index += 1

    if trim_index >= len(messages):
        return

    del messages[1:trim_index]


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

        # Store in app state
        app.state.mcp_session = session
        app.state.llm_client = client
        app.state.tools = tools
        app.state.system_prompt = SYSTEM_PROMPT

        yield
        # Cleanup happens automatically when exiting the context


app = FastAPI(
    title="Obsidian Tools API",
    description="HTTP API for interacting with the Obsidian vault agent",
    lifespan=lifespan,
)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Process a chat message and return the agent's response."""
    # Reload preferences each request so mid-session changes take effect
    system_prompt = app.state.system_prompt
    preferences = load_preferences()
    if preferences:
        system_prompt += preferences

    session = get_or_create_session(request.active_file, system_prompt)
    messages = session.messages
    messages[0]["content"] = system_prompt

    # Remember which messages were already compacted, then strip the
    # internal flag so it isn't sent to the LLM API (Fireworks rejects it).
    compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
    for msg in messages:
        msg.pop("_compacted", None)

    # Add user message with context prefix
    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    def _restore_compacted_flags():
        for i in compacted_indices:
            if i < len(messages):
                messages[i]["_compacted"] = True

    try:
        response = await agent_turn(
            app.state.llm_client,
            app.state.mcp_session,
            messages,
            app.state.tools,
        )
        _restore_compacted_flags()
        compact_tool_messages(messages)
        trim_messages(messages)
        return ChatResponse(response=response, session_id=session.session_id)
    except Exception as e:
        _restore_compacted_flags()
        messages.pop()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Process a chat message and stream events as SSE."""
    system_prompt = app.state.system_prompt
    preferences = load_preferences()
    if preferences:
        system_prompt += preferences

    session = get_or_create_session(request.active_file, system_prompt)
    messages = session.messages
    messages[0]["content"] = system_prompt

    compacted_indices = {i for i, msg in enumerate(messages) if msg.get("_compacted")}
    for msg in messages:
        msg.pop("_compacted", None)

    context_prefix = format_context_prefix(request.active_file)
    messages.append({"role": "user", "content": context_prefix + request.message})

    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_event(event_type: str, data: dict) -> None:
        await queue.put({"type": event_type, **data})

    def _restore_compacted_flags():
        for i in compacted_indices:
            if i < len(messages):
                messages[i]["_compacted"] = True

    async def run_agent():
        try:
            await agent_turn(
                app.state.llm_client,
                app.state.mcp_session,
                messages,
                app.state.tools,
                on_event=on_event,
            )
            _restore_compacted_flags()
            compact_tool_messages(messages)
            trim_messages(messages)
        except Exception as e:
            _restore_compacted_flags()
            messages.pop()
            await queue.put({"type": "error", "error": str(e)})
        finally:
            await queue.put({"type": "done", "session_id": session.session_id})
            await queue.put(None)  # sentinel

    async def event_generator():
        task = asyncio.create_task(run_agent())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if not task.done():
                await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
