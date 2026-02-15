#!/usr/bin/env python3
"""CLI agent client connecting an LLM (via Fireworks) to the MCP server."""

import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

from config import LLM_MODEL, PREFERENCES_FILE, VAULT_PATH

logger = logging.getLogger(__name__)

# Configuration
load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"

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
    turn_prompt_tokens = 0
    turn_completion_tokens = 0
    llm_calls = 0

    while True:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
        )

        llm_calls += 1
        usage = response.usage
        if usage:
            turn_prompt_tokens += usage.prompt_tokens
            turn_completion_tokens += usage.completion_tokens
            logger.info(
                "LLM call %d: prompt=%d completion=%d total=%d messages=%d",
                llm_calls,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                len(messages),
            )

        assistant_message = response.choices[0].message
        messages.append(assistant_message.model_dump(exclude_none=True))

        if not assistant_message.tool_calls:
            logger.info(
                "Turn complete: calls=%d prompt_total=%d completion_total=%d "
                "turn_total=%d",
                llm_calls,
                turn_prompt_tokens,
                turn_completion_tokens,
                turn_prompt_tokens + turn_completion_tokens,
            )
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
            logger.debug(
                "Tool %s result: %d chars", tool_name, len(result)
            )

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    anyio.run(chat_loop)


if __name__ == "__main__":
    main()
