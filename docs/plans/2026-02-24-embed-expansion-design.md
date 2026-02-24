# Auto-expand embeds in read_file

## Summary

When `read_file` reads a `.md` file, auto-expand `![[...]]` embeds inline before pagination so the LLM sees one unified document.

## Decisions

- **Depth**: 1 level only (no recursive expansion)
- **Default**: Always on for `.md` files, no opt-in parameter
- **Format**: Labeled blockquote (`> [Embedded: file.ext]` + prefixed content)
- **Caching**: In-memory cache for binary embed results, keyed by `(path, mtime)`

## Embed Types

| Embed syntax | Resolution |
|---|---|
| `![[file.docx]]` | Binary dispatch (audio/image/office via readers.py handlers) |
| `![[note]]` | Read full note, strip frontmatter, return body |
| `![[note#heading]]` | Read note, extract section via `find_section()` |
| `![[note#^blockid]]` | Read note, find `^blockid` line + indented children |

## Integration Point

In `read_file`, after `file_path.read_text()` but before pagination:

```python
content = file_path.read_text(...)
content = _expand_embeds(content, file_path)
# ... existing pagination ...
```

## Embed Regex

`!\[\[([^\]]+)\]\]` — captures inner reference. Must skip matches inside fenced code blocks (reuse `is_fence_line()`).

## Resolution Logic

1. Split captured reference on first `#` to get `(filename, fragment)`.
2. No extension -> append `.md`, resolve as markdown.
3. Binary extension -> resolve via vault root, fallback to `ATTACHMENTS_DIR`.
4. Resolution failure -> inline error marker.

## Output Format

Expanded:
```
> [Embedded: filename.ext]
> content line 1
> content line 2
```

Error:
```
> [Embed error: filename.ext - reason]
```

## Block ID Extraction

New helper `_extract_block(lines, block_id)`:
- Find line ending with ` ^blockid` (strip trailing whitespace).
- Remove the `^blockid` suffix from that line.
- Collect subsequent lines with greater indentation.
- Return anchor + children as string.

## Cache

`_embed_cache: dict[tuple[str, float], str]` — binary embeds only. Keyed by `(resolved_path_str, mtime)`. No TTL; mtime change invalidates naturally.

## Edge Cases

- **Code blocks**: Skip `![[...]]` inside fenced code (track fence state with `is_fence_line()`).
- **Self-embed**: Skip with error marker if embed references the file being read.
- **Pagination**: Offsets refer to expanded content positions.

## Code Location

All new code in `tools/files.py`: `_expand_embeds()`, `_extract_block()`, `_embed_cache`. No changes to `readers.py` or `vault.py`.

## Tests

In `test_tools_files.py`:
- Markdown embed (full note, frontmatter stripping)
- Heading embed via `find_section`
- Block ID embed (subtree extraction)
- Binary embed (mocked handlers)
- Unresolved embed (error marker)
- Self-embed protection
- Cache hit/miss
- Multiple embeds in one file
- Embed inside code block (not expanded)
