"""MCP tool implementations organized by category."""

from tools.files import (
    append_to_file,
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
    search_by_folder,
)
from tools.preferences import (
    manage_preferences,
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
    log_interaction,
)

__all__ = [
    # files
    "append_to_file",
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
    "search_by_folder",
    # preferences
    "manage_preferences",
    # search
    "search_vault",
    "web_search",
    # sections
    "append_to_section",
    "prepend_to_file",
    "replace_section",
    # utility
    "log_interaction",
]
