"""MCP tool implementations organized by category."""

from tools.files import (
    create_file,
    move_file,
    batch_move_files,
    read_file,
)
from tools.frontmatter import (
    batch_update_frontmatter,
    list_files,
    search_by_date_range,
    update_frontmatter,
)
from tools.links import (
    find_backlinks,
    find_outlinks,
)
from tools.preferences import (
    manage_preferences,
)
from tools.search import (
    search_vault,
    web_search,
)
from tools.editing import edit_file
from tools.utility import (
    log_interaction,
)

__all__ = [
    # files
    "create_file",
    "move_file",
    "batch_move_files",
    "read_file",
    # frontmatter
    "batch_update_frontmatter",
    "list_files",
    "search_by_date_range",
    "update_frontmatter",
    # links
    "find_backlinks",
    "find_outlinks",
    # preferences
    "manage_preferences",
    # search
    "search_vault",
    "web_search",
    # editing
    "edit_file",
    # utility
    "log_interaction",
]
