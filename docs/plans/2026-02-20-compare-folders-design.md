# Compare Folders Tool Design

## Problem

The agent frequently needs to check if files in one folder already exist in another (by name), typically followed by a batch move of the non-duplicates. Currently this requires 2+ `search_by_folder` calls plus LLM reasoning to compare the lists — error-prone and wasteful.

## Solution

A single `compare_folders` MCP tool that returns a full set comparison (only-in-source, only-in-target, in-both) in one call.

## Tool Signature

```python
async def compare_folders(
    source: str,          # folder to check FROM
    target: str,          # folder to check AGAINST
    recursive: bool = False,
) -> str:  # JSON via ok()/err()
```

## Return Shape

```json
{
  "success": true,
  "message": "Compared 'source' with 'target': 12 only in source, 3 only in target, 8 in both",
  "only_in_source": ["source/Alice.md", "source/Bob.md"],
  "only_in_target": ["target/Charlie.md"],
  "in_both": [
    {"name": "Dave.md", "source_path": "source/Dave.md", "target_path": "target/Dave.md"}
  ],
  "counts": {"only_in_source": 12, "only_in_target": 3, "in_both": 8}
}
```

## Design Decisions

- **Matching**: Filename stem only, case-insensitive (Obsidian treats case variants as the same note)
- **No pagination**: Returns three categorized lists of paths (lightweight). Bounded by vault size.
- **Full comparison**: All three categories returned so the agent has everything for follow-up decisions (batch move, delete, etc.)
- **`in_both` uses objects**: Includes both paths so the agent can decide which to keep
- **`only_in_source`/`only_in_target` are flat path lists**: Feeds directly into batch_move input
- **Recursive off by default**: Matches `search_by_folder` convention. Uncommon but supported.

## Implementation

- Lives in `src/tools/links.py` alongside `search_by_folder`
- Uses `resolve_dir()` for both paths
- Scans with `glob("*.md")` / `rglob("*.md")` depending on `recursive`
- Builds `{stem_lower: relative_path}` dict per folder, set operations on keys
- Results sorted alphabetically within each category
- Registered in `mcp_server.py`

## Error Cases

- Either folder doesn't exist: `err("Source folder not found: ...")`
- Same folder for both: `err("Source and target folders are the same")`

## Tests (in `test_tools_links.py`)

- Basic comparison with overlapping files
- No overlap (disjoint folders)
- Complete overlap (identical contents)
- Empty folder(s)
- Recursive mode
- Case-insensitive stem matching
- Same folder error
- Invalid folder error
