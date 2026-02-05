#!/usr/bin/env python3
"""MCP server exposing Obsidian vault tools.

This is the entry point that registers all tools with FastMCP.
Tool implementations are organized in the tools/ directory.
"""

import sys
from pathlib import Path

# Ensure src/ is on the import path when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

# Import all tool implementations
from tools.files import (
    append_to_file,
    batch_move_files,
    create_file,
    move_file,
    read_file,
)
from tools.frontmatter import (
    batch_update_frontmatter,
    list_files_by_frontmatter,
    search_by_date_range,
    update_frontmatter,
)
from tools.links import (
    find_backlinks,
    find_outlinks,
    search_by_folder,
)
from tools.preferences import (
    list_preferences,
    remove_preference,
    save_preference,
)
from tools.search import (
    search_vault,
    web_search,
)
from tools.sections import (
    append_to_section,
    prepend_to_file,
    replace_section,
)
from tools.utility import (
    get_current_date,
    log_interaction,
)
from tools.audio import transcribe_audio

# Create MCP server instance
mcp = FastMCP("obsidian-tools")

# Register all tools with the MCP server
# Each tool function is wrapped with @mcp.tool() decorator

# Search tools
mcp.tool()(search_vault)
mcp.tool()(web_search)

# File tools
mcp.tool()(read_file)
mcp.tool()(create_file)
mcp.tool()(move_file)
mcp.tool()(batch_move_files)
mcp.tool()(append_to_file)

# Frontmatter tools
mcp.tool()(list_files_by_frontmatter)
mcp.tool()(update_frontmatter)
mcp.tool()(batch_update_frontmatter)
mcp.tool()(search_by_date_range)

# Link tools
mcp.tool()(find_backlinks)
mcp.tool()(find_outlinks)
mcp.tool()(search_by_folder)

# Section tools
mcp.tool()(prepend_to_file)
mcp.tool()(replace_section)
mcp.tool()(append_to_section)

# Preference tools
mcp.tool()(save_preference)
mcp.tool()(list_preferences)
mcp.tool()(remove_preference)

# Utility tools
mcp.tool()(log_interaction)
mcp.tool()(get_current_date)

# Audio tools
mcp.tool()(transcribe_audio)


if __name__ == "__main__":
    mcp.run()
