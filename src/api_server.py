#!/usr/bin/env python3
"""FastAPI HTTP wrapper for the Qwen agent."""

import uuid
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

from qwen_agent import (
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


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    response: str
    session_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize MCP session and LLM client at startup."""
    server_params = StdioServerParameters(
        command=str(PROJECT_ROOT / ".venv" / "bin" / "python"),
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

    # Add user message
    messages.append({"role": "user", "content": request.message})

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
    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
