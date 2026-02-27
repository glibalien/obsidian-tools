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
    batch_merge_files,
    batch_move_files,
    create_file,
    merge_files,
    move_file,
    read_file,
)
from tools.frontmatter import (
    batch_update_frontmatter,
    update_frontmatter,
)
from tools.links import (
    compare_folders,
    find_links,
)
from tools.preferences import (
    manage_preferences,
)
from tools.search import (
    find_notes,
    web_search,
)
from tools.editing import edit_file
from tools.utility import (
    log_interaction,
)

# Create MCP server instance
mcp = FastMCP("obsidian-tools")

# Register all tools with the MCP server
# Each tool function is wrapped with @mcp.tool() decorator

# Search tools
mcp.tool()(find_notes)
mcp.tool()(web_search)

# File tools
mcp.tool()(read_file)
mcp.tool()(create_file)
mcp.tool()(move_file)
mcp.tool()(batch_move_files)
mcp.tool()(merge_files)
mcp.tool()(batch_merge_files)

# Frontmatter tools
mcp.tool()(update_frontmatter)
mcp.tool()(batch_update_frontmatter)

# Link tools
mcp.tool()(compare_folders)
mcp.tool()(find_links)


# Editing tools
mcp.tool()(edit_file)

# Preference tools
mcp.tool()(manage_preferences)

# Utility tools
mcp.tool()(log_interaction)


if __name__ == "__main__":
    from config import setup_logging

    setup_logging("mcp")
    mcp.run()
