#!/usr/bin/env python3
"""CLI agent client connecting Qwen (via Fireworks) to MCP server."""

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

from config import VAULT_PATH

# Configuration
load_dotenv()

PREFERENCES_FILE = VAULT_PATH / "Preferences.md"

PROJECT_ROOT = Path(__file__).parent.parent
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
MODEL = "accounts/fireworks/models/deepseek-v3p2"

SYSTEM_PROMPT = """You are a helpful assistant with access to an Obsidian vault.

You have tools for:
- Searching the vault (semantic, keyword, by date, by folder)
- Reading and creating files
- Modifying frontmatter (single and batch)
- Moving files (single and batch)
- Finding backlinks and outlinks between notes
- Logging interactions to daily notes

When answering questions about the vault:
1. Use search_vault to find relevant notes
2. Cite which files the information came from

Be concise and helpful.

## Interaction Logging

Every interaction must be logged to the daily note using log_interaction.

At the end of every conversation turn that completes a user request, call log_interaction with:
- task_description: Brief description of the task performed
- query: The user's original query
- summary: Summary of the outcome (or "n/a" if using full_response)
- files: List of referenced vault notes (optional)
- full_response: Your full response text (optional, for lengthy responses)

Guidelines:
- For lengthy responses (search results, explanations, multi-paragraph answers): pass summary="n/a" and provide your full conversational output in full_response instead.
- For short responses (confirmations, one-liners): use the summary field with a concise description.
- Include relevant files when the interaction references specific vault notes.

## Tool Orchestration

- Always use exact file paths returned by tools. Never invent or guess filenames.
- When performing multi-step operations, complete each step fully before moving to the next.
- For batch operations, pass the actual paths from previous tool results, not examples.
- If a tool returns an error, report it accurately - don't claim success."""


def load_preferences() -> str | None:
    """Load user preferences from Preferences.md if it exists.

    Returns:
        Preferences section to append to system prompt, or None if no preferences.
    """
    if not PREFERENCES_FILE.exists():
        return None

    content = PREFERENCES_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return None

    return f"""

## User Preferences

The following are user preferences and corrections. Always follow these:

{content}"""


def create_llm_client() -> OpenAI:
    """Create OpenAI client configured for Fireworks API."""
    if not FIREWORKS_API_KEY:
        print("Error: FIREWORKS_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=FIREWORKS_API_KEY, base_url=FIREWORKS_BASE_URL)


def mcp_tool_to_openai_function(tool) -> dict:
    """Convert MCP Tool to OpenAI function calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def extract_text_content(content) -> str:
    """Extract text from MCP content blocks."""
    text_parts = []
    for block in content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    return "\n".join(text_parts) if text_parts else str(content)


async def execute_tool_call(
    session: ClientSession, tool_name: str, arguments: dict
) -> str:
    """Execute a tool call via MCP and return the result."""
    try:
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            return f"Tool error: {extract_text_content(result.content)}"
        return extract_text_content(result.content)
    except Exception as e:
        return f"Failed to execute tool {tool_name}: {e}"


async def agent_turn(
    client: OpenAI,
    session: ClientSession,
    messages: list[dict],
    tools: list[dict],
) -> str:
    """Execute one agent turn, handling tool calls until final response."""
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
        )

        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))

        if not assistant_message.tool_calls:
            return assistant_message.content or ""

        # Execute each tool call
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            print(f"  [Calling {tool_name}...]")
            result = await execute_tool_call(session, tool_name, arguments)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )


async def chat_loop():
    """Main chat loop - handles user input and agent responses."""
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

        tool_names = [t["function"]["name"] for t in tools]
        print(f"Connected to MCP server. Tools: {', '.join(tool_names)}")
        print("Type 'quit' or Ctrl+C to exit.\n")

        # Set up LLM client
        client = create_llm_client()

        # Build system prompt with preferences if available
        system_prompt = SYSTEM_PROMPT
        preferences = load_preferences()
        if preferences:
            system_prompt += preferences
            print("Loaded user preferences from Preferences.md")

        # Conversation history
        messages = [{"role": "system", "content": system_prompt}]

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            if not user_input:
                continue

            messages.append({"role": "user", "content": user_input})

            try:
                response = await agent_turn(client, session, messages, tools)
                print(f"\nAssistant: {response}\n")
            except Exception as e:
                print(f"\nError: {e}\n", file=sys.stderr)
                # Remove failed user message to keep history clean
                messages.pop()


def main():
    """Entry point for the agent."""
    anyio.run(chat_loop)


if __name__ == "__main__":
    main()
