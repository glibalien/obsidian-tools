#!/usr/bin/env python3
"""CLI agent client connecting an LLM (via Fireworks) to the MCP server."""

import ast
import copy
import json
import logging
import os
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Awaitable, Callable

import anyio
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

from config import FIREWORKS_BASE_URL, LLM_MODEL, PREFERENCES_FILE, VAULT_PATH, setup_logging
from services.compaction import compact_tool_messages

logger = logging.getLogger(__name__)

# Configuration
load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
MAX_TOOL_RESULT_CHARS = 100_000
TOOL_TIMEOUT = 60  # seconds


SYSTEM_PROMPT_FILE = PROJECT_ROOT / "system_prompt.txt"
SYSTEM_PROMPT_EXAMPLE = PROJECT_ROOT / "system_prompt.txt.example"


def truncate_tool_result(result: str, result_id: str | None = None) -> str:
    """Truncate tool result if it exceeds the character limit.

    When result_id is provided, the truncation marker includes it
    so the LLM can call get_continuation to retrieve more.
    """
    if len(result) <= MAX_TOOL_RESULT_CHARS:
        return result
    truncated = result[:MAX_TOOL_RESULT_CHARS]
    if result_id:
        truncated += (
            f"\n\n[truncated — showing {MAX_TOOL_RESULT_CHARS}/{len(result)} chars. "
            f'Call get_continuation with id="{result_id}" to read more]'
        )
    else:
        truncated += "\n\n[truncated]"
    return truncated


def load_system_prompt() -> str:
    """Load system prompt from system_prompt.txt, falling back to .example."""
    if SYSTEM_PROMPT_FILE.exists():
        return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()

    if SYSTEM_PROMPT_EXAMPLE.exists():
        logger.warning(
            "system_prompt.txt not found — using system_prompt.txt.example. "
            "Copy it to system_prompt.txt and customize for your vault."
        )
        return SYSTEM_PROMPT_EXAMPLE.read_text(encoding="utf-8").strip()

    logger.error("No system prompt file found. Using minimal fallback.")
    return "You are a helpful assistant with access to an Obsidian vault."


SYSTEM_PROMPT = load_system_prompt()


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


def _parse_tool_arguments(raw: str) -> dict:
    """Parse tool call arguments with fallbacks for common model quirks.

    Known issues handled:
    - gpt-oss-120b appends ``\\t<|call|>`` control tokens after the JSON
    - Some models emit Python-style dicts (single quotes, True/False/None)
    - Trailing commas before } or ]
    """
    if not raw or not raw.strip():
        return {}

    # Strip model control tokens like <|call|>, <|end|>, etc.
    cleaned = re.sub(r"<\|[^|]+\|>", "", raw).strip()

    # Fast path: valid JSON
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: Python literal syntax (single quotes, True/False/None)
    try:
        parsed = ast.literal_eval(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, SyntaxError):
        pass

    # Last resort: strip trailing commas before } or ] and retry JSON
    no_trailing = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if no_trailing != cleaned:
        try:
            parsed = json.loads(no_trailing)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return {}


def _simplify_schema(schema: dict) -> dict:
    """Inline $ref references and simplify anyOf nullable patterns.

    Pydantic/FastMCP generates $defs + $ref for Pydantic models and
    anyOf: [T, {type: null}] for Optional types.  Weaker models struggle
    with the indirection — inline everything so the schema is flat.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def _resolve(node):
        if isinstance(node, dict):
            # Resolve $ref → inline the referenced definition
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                if ref_name in defs:
                    return _resolve(copy.deepcopy(defs[ref_name]))
                return node

            # Simplify anyOf[T, null] → T (keep default/title/description)
            if "anyOf" in node:
                non_null = [o for o in node["anyOf"] if o != {"type": "null"}]
                if len(non_null) == 1:
                    merged = {k: v for k, v in node.items() if k != "anyOf"}
                    merged.update(_resolve(non_null[0]))
                    return merged
                node["anyOf"] = [_resolve(o) for o in node["anyOf"]]

            return {k: _resolve(v) for k, v in node.items()}

        if isinstance(node, list):
            return [_resolve(item) for item in node]

        return node

    return _resolve(schema)


def mcp_tool_to_openai_function(tool) -> dict:
    """Convert MCP Tool to OpenAI function calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": _simplify_schema(tool.inputSchema),
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
        with anyio.fail_after(TOOL_TIMEOUT):
            result = await session.call_tool(tool_name, arguments)
        if result.isError:
            return f"Tool error: {extract_text_content(result.content)}"
        return extract_text_content(result.content)
    except TimeoutError:
        logger.warning("Tool '%s' timed out after %ds", tool_name, TOOL_TIMEOUT)
        return f"Tool error: '{tool_name}' timed out after {TOOL_TIMEOUT}s"
    except Exception as e:
        return f"Failed to execute tool {tool_name}: {e}"


async def ensure_interaction_logged(
    session: ClientSession,
    messages: list[dict],
    turn_start: int,
    user_query: str,
    response: str,
) -> None:
    """Auto-log interaction if agent didn't call log_interaction during the turn.

    Scans messages added during the turn for tool calls. If any tool calls
    were made but none named ``log_interaction``, fires a log_interaction
    call via MCP so the interaction is recorded in the daily note.
    """
    tool_names_called: list[str] = []
    for msg in messages[turn_start:]:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                name = tc.get("function", {}).get("name", "")
                if name:
                    tool_names_called.append(name)

    if not tool_names_called:
        return  # Conversation only — no action taken

    if "log_interaction" in tool_names_called:
        return  # Agent already logged

    logger.warning("Agent did not call log_interaction — auto-logging")
    result = await execute_tool_call(session, "log_interaction", {
        "task_description": "(auto-logged)",
        "query": user_query,
        "summary": response[:2000],
    })
    if result.startswith(("Tool error:", "Failed to execute tool")):
        logger.error("Auto-log failed: %s", result)


GET_CONTINUATION_TOOL = {
    "type": "function",
    "function": {
        "name": "get_continuation",
        "description": (
            "Retrieve the next chunk of a truncated tool result. "
            "Use when a previous tool result shows [truncated]."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The id from the truncation message",
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to read from",
                },
            },
            "required": ["id"],
        },
    },
}


def _handle_get_continuation(cache: dict[str, str], arguments: dict) -> str:
    """Serve the next chunk of a cached truncated tool result."""
    result_id = arguments.get("id", "")
    offset = arguments.get("offset", MAX_TOOL_RESULT_CHARS)

    full_result = cache.get(result_id)
    if full_result is None:
        return json.dumps({"error": f"No cached result for id '{result_id}'"})

    chunk = full_result[offset : offset + MAX_TOOL_RESULT_CHARS]
    if not chunk:
        return json.dumps({"error": "Offset beyond end of result"})

    end = offset + len(chunk)
    remaining = len(full_result) - end
    if remaining > 0:
        chunk += (
            f"\n\n[truncated — showing {offset}-{end}/{len(full_result)} chars. "
            f"{remaining} chars remaining. Call get_continuation with "
            f'id="{result_id}" offset={end} to read more]'
        )

    return chunk


EventCallback = Callable[[str, dict], Awaitable[None]]


async def _process_tool_calls(
    tool_calls,
    session: ClientSession,
    messages: list[dict],
    truncated_results: dict[str, str],
    next_result_id: int,
    emit: EventCallback | None,
    last_tool_call: dict | None = None,
) -> tuple[int, bool, dict | None]:
    """Execute tool calls from an assistant message and append results to messages.

    Returns (updated next_result_id, confirmation_required, preview_data).
    ``last_tool_call`` is a mutable dict tracking the previous call for dedup.
    ``preview_data`` is non-None when a confirmation preview should be emitted
    by the caller after the response event (to ensure correct SSE ordering).
    """

    async def _emit(event_type: str, data: dict) -> None:
        if emit is not None:
            await emit(event_type, data)

    confirmation_required = False
    preview_data = None

    for i, tool_call in enumerate(tool_calls):
        tool_name = tool_call.function.name
        raw_args = tool_call.function.arguments or ""
        arguments = _parse_tool_arguments(raw_args)

        if not arguments and raw_args.strip():
            logger.warning(
                "Failed to parse arguments for %s: %r", tool_name, raw_args
            )

        logger.info("Tool call: %s args=%s", tool_name, arguments)
        brief_args = {
            k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v)
            for k, v in arguments.items()
        }
        await _emit("tool_call", {"tool": tool_name, "args": brief_args})

        # Detect duplicate consecutive tool calls
        try:
            args_key = json.dumps(arguments, sort_keys=True)
        except (TypeError, ValueError):
            try:
                args_key = repr(sorted(arguments.items()))
            except TypeError:
                args_key = repr(arguments)
        call_key = (tool_name, args_key)
        prev_succeeded = last_tool_call and not last_tool_call.get("failed", False)
        if prev_succeeded and call_key == last_tool_call.get("key"):
            prev_result = last_tool_call["result"]
            result = (
                f"Duplicate call: you just called {tool_name} with the same "
                f"arguments and it returned: {prev_result[:500]}"
                "\nIf the operation isn't working as expected, try a "
                "different approach."
            )
            logger.info("Duplicate tool call detected: %s — skipping", tool_name)
        elif tool_name == "get_continuation":
            result = _handle_get_continuation(truncated_results, arguments)
            logger.info("Tool result: %s chars=%d", tool_name, len(result))
        else:
            result = await execute_tool_call(session, tool_name, arguments)
            raw_len = len(result)
            if raw_len > MAX_TOOL_RESULT_CHARS:
                rid = str(next_result_id)
                next_result_id += 1
                truncated_results[rid] = result
                result = truncate_tool_result(result, result_id=rid)
            else:
                result = truncate_tool_result(result)
            logger.info(
                "Tool result: %s chars=%d truncated=%s",
                tool_name, raw_len, raw_len > MAX_TOOL_RESULT_CHARS,
            )

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            }
        )
        try:
            parsed = json.loads(result)
            success = parsed.get("success", True)
            if parsed.get("confirmation_required"):
                confirmation_required = True
                preview_data = {
                    "tool": tool_name,
                    "message": parsed.get("preview_message", ""),
                    "files": parsed.get("files", []),
                }
                await _emit("tool_result", {"tool": tool_name, "success": success})
                # Stub remaining tool calls so the API doesn't reject missing results
                for remaining in tool_calls[i + 1:]:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": remaining.id,
                        "content": '{"skipped": "Awaiting user confirmation"}',
                    })
                break
        except (json.JSONDecodeError, AttributeError):
            success = not result.startswith(("Tool error:", "Failed to execute tool"))
        await _emit("tool_result", {"tool": tool_name, "success": success})

        if last_tool_call is not None:
            last_tool_call["key"] = call_key
            last_tool_call["result"] = result
            last_tool_call["failed"] = not success

    return next_result_id, confirmation_required, preview_data


async def agent_turn(
    client: OpenAI,
    session: ClientSession,
    messages: list[dict],
    tools: list[dict],
    max_iterations: int = 20,
    on_event: EventCallback | None = None,
) -> str:
    """Execute one agent turn, handling tool calls until final response."""
    turn_prompt_tokens = 0
    turn_completion_tokens = 0
    llm_calls = 0
    last_content = ""
    truncated_results: dict[str, str] = {}
    next_result_id = 1
    # Tool names excluded from the iteration cap count
    UNCOUNTED_TOOLS = {"log_interaction", "get_continuation"}
    all_tools = tools + [GET_CONTINUATION_TOOL]
    force_text_only = False
    text_only_retries = 0
    MAX_TEXT_ONLY_RETRIES = 3
    last_tool_call: dict = {}
    pending_preview: dict | None = None

    async def _emit(event_type: str, data: dict) -> None:
        if on_event is not None:
            await on_event(event_type, data)

    while True:
        if llm_calls >= max_iterations:
            logger.warning(
                "Agent turn hit iteration cap (%d). Stopping.", max_iterations
            )
            content = last_content + "\n\n[Tool call limit reached]"
            await _emit("response", {"content": content})
            return content

        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=all_tools if all_tools else None,
            tool_choice="none" if force_text_only else ("auto" if all_tools else None),
        )

        # Count this iteration unless all tool calls are uncounted tools
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls or not all(
            tc.function.name in UNCOUNTED_TOOLS for tc in tool_calls
        ):
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

        # Enforce text-only: strip tool calls if model ignores tool_choice="none"
        if force_text_only and assistant_message.tool_calls:
            logger.warning(
                "Model returned %d tool call(s) despite tool_choice='none' — stripping",
                len(assistant_message.tool_calls),
            )
            assistant_message.tool_calls = None
            if not assistant_message.content:
                text_only_retries += 1
                if text_only_retries < MAX_TEXT_ONLY_RETRIES:
                    logger.warning("Stripped response has no content — retrying (%d/%d)",
                                   text_only_retries, MAX_TEXT_ONLY_RETRIES)
                    continue
                # Model refuses to produce text — use preview message as fallback
                logger.warning("Model produced no text after %d retries — using fallback",
                               MAX_TEXT_ONLY_RETRIES)
                fallback = (pending_preview or {}).get("message", "")
                content = fallback or "Please review the preview above and confirm or cancel."
                assistant_message.content = content

        messages.append(assistant_message.model_dump(exclude_none=True))
        last_content = assistant_message.content or ""

        if not assistant_message.tool_calls:
            logger.info(
                "Turn complete: calls=%d prompt_total=%d completion_total=%d "
                "turn_total=%d",
                llm_calls,
                turn_prompt_tokens,
                turn_completion_tokens,
                turn_prompt_tokens + turn_completion_tokens,
            )
            content = assistant_message.content or ""
            await _emit("response", {"content": content})
            if pending_preview:
                await _emit("confirmation_preview", pending_preview)
            return content

        if last_content:
            logger.info("Assistant text: %s", last_content)

        next_result_id, confirmation_required, preview_data = await _process_tool_calls(
            assistant_message.tool_calls, session, messages,
            truncated_results, next_result_id, on_event, last_tool_call,
        )

        if confirmation_required:
            logger.info("Confirmation required — forcing text-only response")
            force_text_only = True
            pending_preview = preview_data



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

        # Build initial system prompt with preferences
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

            # Reload preferences each turn so mid-session changes take effect
            updated_prompt = SYSTEM_PROMPT
            preferences = load_preferences()
            if preferences:
                updated_prompt += preferences
            messages[0]["content"] = updated_prompt

            turn_start = len(messages)
            messages.append({"role": "user", "content": user_input})

            # Strip _compacted flags before LLM call (Fireworks rejects them),
            # remembering which messages were already compacted.
            compacted_indices = {
                i for i, msg in enumerate(messages) if msg.get("_compacted")
            }
            for msg in messages:
                msg.pop("_compacted", None)

            def _restore_compacted_flags():
                for i in compacted_indices:
                    if i < len(messages):
                        messages[i]["_compacted"] = True

            try:
                response = await agent_turn(client, session, messages, tools)
                await ensure_interaction_logged(
                    session, messages, turn_start, user_input, response,
                )
                _restore_compacted_flags()
                compact_tool_messages(messages)
                print(f"\nAssistant: {response}\n")
            except Exception as e:
                _restore_compacted_flags()
                print(f"\nError: {e}\n", file=sys.stderr)
                # Remove failed user message to keep history clean
                messages.pop()


def main():
    """Entry point for the agent."""
    setup_logging("agent")
    anyio.run(chat_loop)


if __name__ == "__main__":
    main()
