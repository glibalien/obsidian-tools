"""File operation tools - read, create, move, append."""

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import yaml

import config
from tools.readers import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    handle_audio,
    handle_image,
    handle_office,
)
from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    FilterCondition,
    HEADING_PATTERN,
    NO_VALUE_MATCH_TYPES,
    VALID_MATCH_TYPES,
    _find_matching_files,
    _validate_filters,
    consume_preview,
    do_move_file,
    err,
    format_batch_result,
    get_file_creation_time,
    get_relative_path,
    is_fence_line,
    ok,
    parse_frontmatter_date,
    resolve_dir,
    resolve_file,
    resolve_vault_path,
    store_preview,
)

logger = logging.getLogger(__name__)

_BINARY_EXTENSIONS = AUDIO_EXTENSIONS | IMAGE_EXTENSIONS | OFFICE_EXTENSIONS

_BLOCK_ID_RE = re.compile(r"\s\^(\S+)\s*$")


def _extract_block(lines: list[str], block_id: str) -> str | None:
    """Extract a block by its ^blockid suffix and all indented children.

    Args:
        lines: File content split into lines.
        block_id: The block ID to find (without ^ prefix).

    Returns:
        The anchor line (suffix stripped) plus indented children, or None if not found.
    """
    anchor_idx = None
    for i, line in enumerate(lines):
        m = _BLOCK_ID_RE.search(line)
        if m and m.group(1) == block_id:
            anchor_idx = i
            break

    if anchor_idx is None:
        return None

    # Strip the ^blockid suffix from the anchor line
    anchor_line = _BLOCK_ID_RE.sub("", lines[anchor_idx]).rstrip()

    # Determine the indentation of the anchor line
    anchor_indent = len(anchor_line) - len(anchor_line.lstrip())

    # Collect indented children
    result_lines = [anchor_line]
    for i in range(anchor_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            break
        line_indent = len(line) - len(line.lstrip())
        if line_indent > anchor_indent:
            result_lines.append(line)
        else:
            break

    return "\n".join(result_lines)


# In-memory cache for binary embed results: (path_str, mtime) -> content
_embed_cache: dict[tuple[str, float], str] = {}

_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
_EMBED_CACHE_MAX = 128


def _expand_embeds(content: str, source_path: Path) -> str:
    """Expand ![[...]] embeds inline in markdown content.

    Scans for embed patterns outside code fences, resolves each file,
    and replaces the embed syntax with a labeled blockquote.

    Args:
        content: The raw markdown text.
        source_path: Path of the file being read (to detect self-embeds).

    Returns:
        Content with embeds replaced by expanded blockquotes.
    """
    lines = content.split("\n")
    result_lines: list[str] = []
    in_fence = False

    for line in lines:
        if is_fence_line(line):
            in_fence = not in_fence
            result_lines.append(line)
            continue

        if in_fence:
            result_lines.append(line)
            continue

        # Check for embeds on this line
        if "![[" not in line:
            result_lines.append(line)
            continue

        # Protect inline code spans from expansion
        new_line = _expand_line_embeds(line, source_path)
        result_lines.append(new_line)

    return "\n".join(result_lines)


def _expand_line_embeds(line: str, source_path: Path) -> str:
    """Expand embeds on a single line, protecting inline code spans."""
    # Strip inline code spans, expand embeds in remaining segments, restore
    code_spans: list[str] = []

    def _save_code(m: re.Match) -> str:
        code_spans.append(m.group(0))
        return f"\x00CODE{len(code_spans) - 1}\x00"

    protected = _INLINE_CODE_RE.sub(_save_code, line)

    expanded = _EMBED_RE.sub(
        lambda m: _resolve_and_format(m.group(1), source_path),
        protected,
    )

    # Restore code spans
    for i, span in enumerate(code_spans):
        expanded = expanded.replace(f"\x00CODE{i}\x00", span)

    return expanded


def _resolve_and_format(reference: str, source_path: Path) -> str:
    """Resolve an embed reference and return formatted blockquote."""
    # Strip display alias: ![[note|alias]] or ![[note#heading|alias]]
    target = reference.split("|", 1)[0] if "|" in reference else reference

    # Parse reference: split on first #
    if "#" in target:
        filename, fragment = target.split("#", 1)
    else:
        filename, fragment = target, None

    # Determine file extension — use Path.suffix to check the actual filename,
    # not the whole path (folders can contain dots, e.g. "2026.02/daily")
    if not Path(filename).suffix:
        lookup_name = filename + ".md"
    else:
        lookup_name = filename

    ext = Path(lookup_name).suffix.lower()

    # Resolve the file
    file_path = _resolve_embed_file(lookup_name, ext)
    if file_path is None:
        return f"> [Embed error: {reference} — File not found]"

    # Self-embed check
    try:
        if file_path.resolve() == source_path.resolve():
            return f"> [Embed error: {reference} — Self-reference skipped]"
    except (OSError, ValueError):
        pass

    # Expand based on type
    if ext in _BINARY_EXTENSIONS:
        return _expand_binary(file_path, reference)

    return _expand_markdown(file_path, reference, fragment)


def _resolve_embed_file(lookup_name: str, ext: str) -> Path | None:
    """Resolve an embed filename to a Path, with Attachments fallback for binaries."""
    file_path, error = resolve_file(lookup_name)
    if error and ext in _BINARY_EXTENSIONS:
        file_path, error = resolve_file(lookup_name, base_path=config.ATTACHMENTS_DIR)
    if error:
        return None
    return file_path


def _expand_binary(file_path: Path, reference: str) -> str:
    """Expand a binary embed (audio/image/office) with caching."""
    path_str = str(file_path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return f"> [Embed error: {reference} — Cannot stat file]"

    cache_key = (path_str, mtime)
    if cache_key in _embed_cache:
        logger.debug("Cache hit: %s", file_path.name)
        expanded = _embed_cache[cache_key]
    else:
        ext = file_path.suffix.lower()
        if ext in AUDIO_EXTENSIONS:
            logger.debug("Cache miss: %s — calling audio handler", file_path.name)
            raw = handle_audio(file_path)
        elif ext in IMAGE_EXTENSIONS:
            logger.debug("Cache miss: %s — calling image handler", file_path.name)
            raw = handle_image(file_path)
        elif ext in OFFICE_EXTENSIONS:
            logger.debug("Cache miss: %s — calling office handler", file_path.name)
            raw = handle_office(file_path)
        else:
            return f"> [Embed error: {reference} — Unsupported binary type]"

        result = json.loads(raw)
        if not result.get("success"):
            return f"> [Embed error: {reference} — {result.get('error', 'Unknown error')}]"

        expanded = (
            result.get("transcript")
            or result.get("description")
            or result.get("content")
            or ""
        )
        # Evict oldest entries if cache is full
        if len(_embed_cache) >= _EMBED_CACHE_MAX:
            oldest = next(iter(_embed_cache))
            del _embed_cache[oldest]
        _embed_cache[cache_key] = expanded

    return _format_embed(reference, expanded)


def _expand_markdown(file_path: Path, reference: str, fragment: str | None) -> str:
    """Expand a markdown embed (full note, heading section, or block ID)."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return f"> [Embed error: {reference} — Cannot read file]"

    # Strip frontmatter
    fm_match = re.match(r"^---\n.*?^---(?:\n|$)", text, re.DOTALL | re.MULTILINE)
    body = text[fm_match.end() :] if fm_match else text

    if fragment is None:
        return _format_embed(reference, body.strip())

    if fragment.startswith("^"):
        block_id = fragment[1:]
        lines = body.split("\n")
        extracted = _extract_block(lines, block_id)
        if extracted is None:
            return f"> [Embed error: {reference} — Block ID not found]"
        return _format_embed(reference, extracted)

    # Heading section
    heading_text = fragment
    lines = body.split("\n")
    section_start, section_end, error = _find_section_by_text(lines, heading_text)
    if error:
        return f"> [Embed error: {reference} — {error}]"

    section_lines = lines[section_start:section_end]
    return _format_embed(reference, "\n".join(section_lines).strip())


def _find_section_by_text(
    lines: list[str], heading_text: str,
) -> tuple[int | None, int | None, str | None]:
    """Find a section by heading text (without # prefix).

    Searches all heading levels for a case-insensitive match.
    """
    target = heading_text.lower().strip()
    matches = []
    in_fence = False

    for i, line in enumerate(lines):
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_PATTERN.match(line)
        if m and m.group(2).strip().lower() == target:
            matches.append((i, len(m.group(1)), line))

    if not matches:
        return None, None, f"Heading not found: {heading_text}"

    if len(matches) > 1:
        line_nums = ", ".join(str(m[0] + 1) for m in matches)
        return None, None, f"Multiple headings match '{heading_text}': lines {line_nums}"

    start_idx, level, _ = matches[0]

    section_end = len(lines)
    in_fence = False
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_PATTERN.match(line)
        if m and len(m.group(1)) <= level:
            section_end = i
            break

    return start_idx, section_end, None


def _format_embed(reference: str, content: str) -> str:
    """Format expanded embed as a labeled blockquote."""
    if not content.strip():
        return f"> [Embedded: {reference}]\n> (empty)"

    quoted_lines = [f"> {line}" if line.strip() else ">" for line in content.split("\n")]
    return f"> [Embedded: {reference}]\n" + "\n".join(quoted_lines)


def read_file(path: str, offset: int = 0, length: int = 30000) -> str:
    """Read content of a vault note with optional pagination.

    Args:
        path: Path to the note, either relative to vault root or absolute.
        offset: Character position to start reading from (default 0).
        length: Maximum characters to return (default 30000).

    Returns:
        The text content of the note, with pagination markers if truncated.
    """
    # Normalize non-breaking spaces that LLMs sometimes generate in paths
    path = path.replace("\xa0", " ")
    file_path, error = resolve_file(path)

    # For binary files (audio/image/office), fall back to Attachments directory
    # when the path doesn't resolve from vault root. Obsidian stores embeds
    # like ![[file.docx]] in the configured attachments folder.
    if error:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if f".{ext}" in _BINARY_EXTENSIONS:
            file_path, att_error = resolve_file(path, base_path=config.ATTACHMENTS_DIR)
            if att_error:
                return err(error)  # return original error
        else:
            return err(error)

    # Extension-based dispatch for non-text files
    ext = file_path.suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return handle_audio(file_path)

    if ext in IMAGE_EXTENSIONS:
        return handle_image(file_path)

    if ext in OFFICE_EXTENSIONS:
        return handle_office(file_path)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading file: {e}")

    if ext == ".md":
        content = _expand_embeds(content, file_path)

    total = len(content)

    # Short file with no offset — return as-is
    if offset == 0 and total <= length:
        return ok(content=content)

    # Offset past end of file
    if offset >= total:
        return err(f"offset {offset} exceeds file length {total}")

    # Slice the content
    chunk = content[offset:offset + length]
    end_pos = offset + length

    # Build result with markers
    parts = []
    if offset > 0:
        parts.append(f"[Continuing from char {offset} of {total}]\n\n")
    parts.append(chunk)
    if end_pos < total:
        parts.append(f"\n\n[... truncated at char {end_pos} of {total}. Use offset={end_pos} to read more.]")

    return ok(content="".join(parts))


def create_file(
    path: str,
    content: str = "",
    frontmatter: str | None = None,
) -> str:
    """Create a new markdown note in the vault.

    Args:
        path: Path for the new file (relative to vault or absolute).
              Parent directories will be created if they don't exist.
        content: The body content of the note (markdown).
        frontmatter: Optional YAML frontmatter as JSON string, e.g., '{"tags": ["meeting"]}'.
                    Will be converted to YAML and wrapped in --- delimiters.

    Returns:
        Confirmation message or error.
    """
    # Validate path
    try:
        file_path = resolve_vault_path(path)
    except ValueError as e:
        return err(str(e))

    if file_path.exists():
        return err(f"File already exists: {path}")

    # Parse frontmatter if provided
    frontmatter_yaml = ""
    if frontmatter:
        fm_dict, parse_error = _parse_frontmatter(frontmatter)
        if parse_error:
            return err(parse_error)

        frontmatter_yaml = yaml.dump(fm_dict, default_flow_style=False, allow_unicode=True)

    # Build file content
    if frontmatter_yaml:
        file_content = f"---\n{frontmatter_yaml}---\n\n{content}"
    else:
        file_content = content

    # Create parent directories if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the file
    try:
        file_path.write_text(file_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing file: {e}")

    rel = str(get_relative_path(file_path))
    return ok(f"Created {rel}", path=rel)


def _parse_frontmatter(frontmatter: dict | str | None) -> tuple[dict, str | None]:
    """Normalize frontmatter input into a dictionary.

    Accepts None, a native dict, or a JSON object string.
    """
    if frontmatter is None:
        return {}, None

    if isinstance(frontmatter, dict):
        return frontmatter, None

    if not isinstance(frontmatter, str):
        return {}, (
            "Invalid frontmatter type: expected dict, JSON object string, or null. "
            f"Got {type(frontmatter).__name__}."
        )

    try:
        parsed = json.loads(frontmatter)
    except json.JSONDecodeError as e:
        return {}, f"Invalid frontmatter JSON: {e}"

    if not isinstance(parsed, dict):
        return {}, (
            "Invalid frontmatter JSON: expected a JSON object "
            f"(e.g., {{\"tags\": [\"meeting\"]}}), got {type(parsed).__name__}."
        )

    return parsed, None


def _split_frontmatter_body(content: str) -> tuple[dict, str]:
    """Split a markdown file's content into frontmatter dict and body string.

    Returns:
        Tuple of (frontmatter_dict, body_string). Frontmatter is empty dict
        if the file has no valid YAML frontmatter block.
    """
    match = re.match(r"^---\n(.*?)^---(?:\n|$)", content, re.DOTALL | re.MULTILINE)
    if not match:
        return {}, content

    body = content[match.end():]

    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, body

    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        return {}, body

    return fm, body


def _merge_frontmatter(source_fm: dict, dest_fm: dict) -> dict:
    """Merge source frontmatter into destination frontmatter.

    Rules:
    - Fields only in source are added to result.
    - Fields only in destination are kept as-is.
    - Both are lists: union (destination order first, then unique source items).
    - Both exist but destination is scalar: destination wins.
    - Identical values: kept as-is.
    """
    merged = {k: list(v) if isinstance(v, list) else v for k, v in dest_fm.items()}
    for key, src_val in source_fm.items():
        if key not in merged:
            merged[key] = src_val
        elif isinstance(merged[key], list) and isinstance(src_val, list):
            # Fall back to linear scan when items are unhashable (e.g. dicts)
            try:
                existing = set()
                for item in merged[key]:
                    existing.add(item if not isinstance(item, list) else tuple(item))
                for item in src_val:
                    hashable = item if not isinstance(item, list) else tuple(item)
                    if hashable not in existing:
                        merged[key].append(item)
                        existing.add(hashable)
            except TypeError:
                for item in src_val:
                    if item not in merged[key]:
                        merged[key].append(item)
        # else: dest wins (scalar conflict, or type mismatch)
    return merged


_HEADING_RE = re.compile(r"^(#+\s+.*)$", re.MULTILINE)


def _split_blocks(body: str) -> list[tuple[str | None, str]]:
    """Split a markdown body into blocks by headings.

    Each block is a (heading, content) tuple where heading is the full heading
    line (e.g. "## Tasks") or None for content before the first heading.
    Content includes the heading line itself and all text until the next heading.

    Returns:
        List of (heading_context, block_content) tuples. Empty list for empty/whitespace body.
    """
    if not body or not body.strip():
        return []

    headings = list(_HEADING_RE.finditer(body))

    if not headings:
        return [(None, body)]

    blocks = []
    first_pos = headings[0].start()
    if first_pos > 0:
        pre_content = body[:first_pos]
        if pre_content.strip():
            blocks.append((None, pre_content))

    for i, match in enumerate(headings):
        pos = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        block_text = body[pos:end]
        heading_line = match.group(1).strip()
        blocks.append((heading_line, block_text))

    return blocks


def _normalize_block(text: str) -> str:
    """Normalize a block for comparison: strip, collapse whitespace."""
    return " ".join(text.split())


def _merge_bodies(source_body: str, dest_body: str) -> tuple[str, dict]:
    """Merge unique blocks from source into destination body.

    Blocks from source that already exist in destination (after normalization)
    are skipped. Unique source blocks are placed under matching headings in
    destination if possible, otherwise appended at the end.

    Returns:
        Tuple of (merged_body, stats_dict) where stats_dict has "blocks_added" count.
    """
    source_blocks = _split_blocks(source_body)
    dest_blocks = _split_blocks(dest_body)

    if not source_blocks:
        return dest_body, {"blocks_added": 0}

    dest_normalized = {_normalize_block(content) for _, content in dest_blocks}

    unique_blocks: list[tuple[str | None, str]] = []
    for heading, content in source_blocks:
        if _normalize_block(content) not in dest_normalized:
            unique_blocks.append((heading, content))

    if not unique_blocks:
        return dest_body, {"blocks_added": 0}

    # Build a map of dest heading -> index for insertion
    dest_heading_indices: dict[str, int] = {}
    for i, (heading, _) in enumerate(dest_blocks):
        if heading is not None:
            dest_heading_indices[heading.lower()] = i

    # Insert unique blocks: after matching heading section, or append
    appended: list[tuple[str | None, str]] = []
    insertions: dict[int, list[tuple[str | None, str]]] = {}

    for heading, content in unique_blocks:
        if heading is not None and heading.lower() in dest_heading_indices:
            idx = dest_heading_indices[heading.lower()]
            insertions.setdefault(idx, []).append((heading, content))
        else:
            appended.append((heading, content))

    # Rebuild: interleave dest blocks with insertions
    result_blocks: list[str] = []
    for i, (_, content) in enumerate(dest_blocks):
        result_blocks.append(content)
        if i in insertions:
            for _, ins_content in insertions[i]:
                result_blocks.append(ins_content)

    for _, content in appended:
        result_blocks.append(content)

    merged = "".join(result_blocks)
    return merged, {"blocks_added": len(unique_blocks)}


def merge_files(
    source: str,
    destination: str,
    strategy: str = "smart",
    delete_source: bool | None = None,
) -> str:
    """Merge a source file into a destination file.

    Args:
        source: Path to the source ("from") file.
        destination: Path to the destination ("to") file. Must exist.
        strategy: "smart" (content-aware dedup) or "concat" (simple concatenation).
        delete_source: Delete source after merge. Defaults to True for smart, False for concat.

    Returns:
        JSON response describing what happened.
    """
    if strategy not in ("smart", "concat"):
        return err(f"Invalid strategy: {strategy!r}. Must be 'smart' or 'concat'.")

    if delete_source is None:
        delete_source = strategy == "smart"

    source_path, src_err = resolve_file(source)
    if src_err:
        return err(src_err)

    dest_path, dst_err = resolve_file(destination)
    if dst_err:
        return err(dst_err)

    if source_path == dest_path:
        return err("Source and destination are the same file.")

    try:
        src_content = source_path.read_text(encoding="utf-8", errors="ignore")
        dst_content = dest_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading files: {e}")

    if strategy == "concat":
        return _merge_concat(
            source_path, dest_path, src_content, dst_content, delete_source, source,
        )

    return _merge_smart(
        source_path, dest_path, src_content, dst_content, delete_source, source,
    )


def _merge_concat(
    source_path, dest_path, src_content, dst_content, delete_source, source_rel,
) -> str:
    """Simple concatenation merge with filename separator."""
    separator = f"\n\n---\n\n*Merged from {source_rel}:*\n\n"
    merged = dst_content.rstrip() + separator + src_content.lstrip()

    try:
        dest_path.write_text(merged, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing merged file: {e}")

    if delete_source:
        try:
            source_path.unlink()
        except OSError as e:
            dest_rel = str(get_relative_path(dest_path))
            return err(f"Merged into {dest_rel} but failed to delete source: {e}")

    dest_rel = str(get_relative_path(dest_path))
    return ok(
        f"Concatenated {source_rel} into {dest_rel}",
        action="concatenated",
        path=dest_rel,
    )


def _merge_smart(
    source_path, dest_path, src_content, dst_content, delete_source, source_rel,
) -> str:
    """Content-aware smart merge with dedup."""
    src_fm, src_body = _split_frontmatter_body(src_content)
    dst_fm, dst_body = _split_frontmatter_body(dst_content)

    bodies_identical = _normalize_block(src_body) == _normalize_block(dst_body)

    merged_fm = _merge_frontmatter(src_fm, dst_fm)
    fm_changed = merged_fm != dst_fm

    if bodies_identical:
        merged_body = dst_body
        blocks_added = 0
    else:
        merged_body, stats = _merge_bodies(src_body, dst_body)
        blocks_added = stats["blocks_added"]

    if not fm_changed and blocks_added == 0:
        action = "identical"
    elif fm_changed and blocks_added == 0:
        action = "frontmatter_merged"
    else:
        action = "content_merged"

    if merged_fm:
        fm_yaml = yaml.dump(merged_fm, default_flow_style=False, allow_unicode=True)
        new_content = f"---\n{fm_yaml}---\n{merged_body}"
    else:
        new_content = merged_body

    try:
        dest_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return err(f"Error writing merged file: {e}")

    if delete_source:
        try:
            source_path.unlink()
        except OSError as e:
            dest_rel = str(get_relative_path(dest_path))
            return err(f"Merged into {dest_rel} but failed to delete source: {e}")

    dest_rel = str(get_relative_path(dest_path))
    return ok(
        f"Merged {source_rel} into {dest_rel} ({action})",
        action=action,
        path=dest_rel,
        blocks_added=blocks_added,
        frontmatter_changed=fm_changed,
    )


def batch_merge_files(
    source_folder: str,
    destination_folder: str,
    recursive: bool = False,
    strategy: str = "smart",
    delete_source: bool | None = None,
    confirm: bool = False,
) -> str:
    """Merge duplicate files between two folders.

    Uses compare_folders to find files with matching names in both folders,
    then merges each source file into the corresponding destination file.
    Files only in source or only in destination are reported but not touched.

    Args:
        source_folder: Folder containing "from" files.
        destination_folder: Folder containing "to" files.
        recursive: Include subfolders. Default False.
        strategy: "smart" (content-aware dedup) or "concat".
        delete_source: Delete source after merge. Defaults to True for smart, False for concat.
        confirm: Must be true to execute when merging more than 5 file pairs.
    """
    from tools.links import compare_folders as _compare_folders

    if strategy not in ("smart", "concat"):
        return err(f"Invalid strategy: {strategy!r}. Must be 'smart' or 'concat'.")

    if delete_source is None:
        delete_source = strategy == "smart"

    comparison = json.loads(_compare_folders(source_folder, destination_folder, recursive=recursive))
    if not comparison.get("success"):
        return err(comparison.get("error", "Folder comparison failed"))

    in_both = comparison.get("in_both", [])
    only_in_source = comparison.get("only_in_source", [])
    only_in_target = comparison.get("only_in_target", [])

    if not in_both:
        return ok(
            f"No overlapping files between '{source_folder}' and '{destination_folder}'",
            merged=0,
            skipped_source_only=len(only_in_source),
            skipped_target_only=len(only_in_target),
        )

    # Build merge pairs: [(source_path, dest_path), ...]
    # Skip stems with multiple targets — ambiguous merge destination
    pairs = []
    skipped_ambiguous = []
    for entry in in_both:
        source_paths = entry["source_paths"]
        target_paths = entry["target_paths"]
        if len(target_paths) > 1:
            skipped_ambiguous.append(entry["name"])
            continue
        target = target_paths[0]
        for src in source_paths:
            pairs.append((src, target))

    # Confirmation gate for large batches
    if len(pairs) > BATCH_CONFIRM_THRESHOLD:
        pair_keys = tuple((s, d) for s, d in pairs)
        key = ("batch_merge_files", pair_keys, strategy, delete_source)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            files = [f"{s} → {d}" for s, d in pairs]
            return ok(
                "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=f"This will merge {len(pairs)} file pairs from '{source_folder}' into '{destination_folder}'.",
                files=files,
            )

    # Execute merges
    results = []
    for src_path, dst_path in pairs:
        result_json = merge_files(src_path, dst_path, strategy=strategy, delete_source=delete_source)
        result = json.loads(result_json)
        results.append(result)

    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    return ok(
        f"Batch merge: {len(succeeded)} merged, {len(failed)} failed",
        merged=len(succeeded),
        failed=len(failed),
        skipped_source_only=len(only_in_source),
        skipped_target_only=len(only_in_target),
        skipped_ambiguous=skipped_ambiguous,
        details=[
            {"action": r.get("action"), "path": r.get("path")}
            for r in succeeded
        ],
        errors=[r.get("error") for r in failed],
    )


def move_file(
    source: str,
    destination: str,
) -> str:
    """Move a vault file to a different location within the vault.

    Args:
        source: Current path of the file (relative to vault or absolute).
        destination: New path for the file (relative to vault or absolute).
                    Parent directories will be created if they don't exist.

    Returns:
        Confirmation message or error.
    """
    success, message = do_move_file(source, destination)
    if success:
        return ok(message)
    return err(message)


def batch_move_files(
    moves: list[dict] | None = None,
    destination_folder: str | None = None,
    target_field: str | None = None,
    target_value: str | None = None,
    target_match_type: str = "contains",
    target_filters: list[FilterCondition] | None = None,
    folder: str | None = None,
    recursive: bool = False,
    confirm: bool = False,
) -> str:
    """Move multiple vault files to new locations.

    Two input modes:
    - Explicit moves: provide a ``moves`` list of {source, destination} dicts.
    - Query-based: provide ``destination_folder`` and optional targeting params
      (``target_field``/``target_value``, ``target_filters``, ``folder``).

    Args:
        moves: List of move operations, each a dict with 'source' and 'destination' keys.
        destination_folder: Target folder for query-based moves. Files keep their filename.
        target_field: Frontmatter field to match for query-based targeting.
        target_value: Value to match for target_field.
        target_match_type: How to match - 'contains', 'equals', 'missing', 'exists',
            'not_contains', or 'not_equals' (default 'contains').
        target_filters: Additional targeting conditions (AND logic).
        folder: Restrict targeting to files within this folder.
        recursive: Include subfolders when folder is set (default false).
        confirm: Must be true to execute when moving more than 5 files.

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    # Mutual exclusivity checks
    has_explicit = moves is not None
    has_query = target_field is not None or folder is not None

    if has_explicit and has_query:
        return err("Cannot combine 'moves' with query-based targeting (target_field/folder)")
    if has_explicit and destination_folder is not None:
        return err("Cannot combine 'moves' with 'destination_folder'")

    if has_explicit:
        return _batch_move_explicit(moves, confirm)

    # Query-based mode
    if destination_folder is None:
        return err("'destination_folder' is required when using query-based targeting")
    if target_field is None and folder is None and not target_filters:
        return err(
            "Provide target_field, folder, or target_filters for query-based moves"
        )

    return _batch_move_query(
        destination_folder, target_field, target_value, target_match_type,
        target_filters, folder, recursive, confirm,
    )


def _batch_move_explicit(moves: list[dict], confirm: bool) -> str:
    """Execute batch moves from an explicit moves list."""
    if not moves:
        return err("moves list is empty")

    # Require confirmation for large batches
    if len(moves) > BATCH_CONFIRM_THRESHOLD:
        move_keys = tuple(
            (m.get("source", ""), m.get("destination", ""))
            for m in moves if isinstance(m, dict)
        )
        key = ("batch_move_files", move_keys)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            files = []
            for m in moves:
                if isinstance(m, dict) and m.get("source"):
                    files.append(f"{m['source']} → {m.get('destination', '?')}")
            return ok(
                "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=f"This will move {len(moves)} files.",
                files=files,
            )

    results = []
    for i, move in enumerate(moves):
        if not isinstance(move, dict):
            results.append((False, f"Item {i}: expected dict, got {type(move).__name__}"))
            continue

        source = move.get("source")
        destination = move.get("destination")

        if not source:
            results.append((False, f"Item {i}: missing 'source' key"))
            continue
        if not destination:
            results.append((False, f"Item {i}: missing 'destination' key"))
            continue

        success, message = do_move_file(source, destination)
        results.append((success, message))

    return ok(format_batch_result("move", results))


def _batch_move_query(
    destination_folder: str,
    target_field: str | None,
    target_value: str | None,
    target_match_type: str,
    target_filters: list[FilterCondition] | None,
    folder: str | None,
    recursive: bool,
    confirm: bool,
) -> str:
    """Execute batch moves from query-based targeting."""
    # Validate match type
    if target_field is not None:
        if target_match_type not in VALID_MATCH_TYPES:
            return err(
                f"target_match_type must be one of {VALID_MATCH_TYPES}, "
                f"got '{target_match_type}'"
            )
        if target_match_type not in NO_VALUE_MATCH_TYPES and target_value is None:
            return err(
                f"target_value is required for target_match_type '{target_match_type}'"
            )

    # Validate additional filters
    parsed_filters, filter_err = _validate_filters(target_filters)
    if filter_err:
        return err(filter_err)

    # Resolve folder constraint
    folder_path = None
    if folder is not None:
        folder_path, folder_err = resolve_dir(folder)
        if folder_err:
            return err(folder_err)

    # Find matching files
    matching = _find_matching_files(
        target_field, target_value or "", target_match_type,
        parsed_filters, folder=folder_path, recursive=recursive,
    )

    if not matching:
        return ok("No files matched the targeting criteria", results=[], total=0)

    # Build move pairs: (source_rel, destination_rel)
    move_pairs = []
    for rel_path in matching:
        filename = Path(rel_path).name
        dest_rel = f"{destination_folder}/{filename}"
        move_pairs.append((rel_path, dest_rel))

    # Confirmation gate for large batches
    if len(move_pairs) > BATCH_CONFIRM_THRESHOLD:
        pair_keys = tuple(sorted((s, d) for s, d in move_pairs))
        key = ("batch_move_files", destination_folder, pair_keys)
        if not (confirm and consume_preview(key)):
            store_preview(key)
            folder_note = f" from folder '{folder}'" if folder else ""
            if target_field:
                context = (
                    f"matching target_field='{target_field}', "
                    f"target_value='{target_value}'{folder_note}"
                )
            else:
                context = f"in folder '{folder}'"
            files = [f"{s} → {d}" for s, d in move_pairs]
            return ok(
                "Describe this pending change to the user. They will confirm or cancel, then call again with confirm=true.",
                confirmation_required=True,
                preview_message=(
                    f"This will move {len(move_pairs)} files {context} "
                    f"to '{destination_folder}'."
                ),
                files=files,
            )

    # Execute moves
    results = []
    for source_rel, dest_rel in move_pairs:
        success, message = do_move_file(source_rel, dest_rel)
        results.append((success, message))

    return ok(format_batch_result("move", results))


def _json_safe_value(val):
    """Convert a value to a JSON-serializable form.

    YAML auto-parses date-like strings (e.g. 2024-01-15) into datetime.date
    objects, which are not JSON serializable. Convert them to ISO strings.
    """
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, list):
        return [_json_safe_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _json_safe_value(v) for k, v in val.items()}
    return val


def _json_safe_frontmatter(fm: dict) -> dict:
    """Make a frontmatter dict JSON-serializable."""
    return {k: _json_safe_value(v) for k, v in fm.items()}


_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)")


def _extract_headings(content: str) -> list[str]:
    """Extract markdown headings from content, skipping frontmatter and code fences.

    Tracks fence delimiter character and length so ~~~ inside a ``` block
    (and vice versa) is not treated as a close marker, and a shorter fence
    cannot close a longer one (e.g. ``` cannot close ````).

    Args:
        content: Raw markdown text (may include frontmatter).

    Returns:
        List of heading lines with # prefixes (e.g. ["## Section 1", "### Sub"]).
    """
    # Strip frontmatter so YAML comments (# ...) aren't picked up as headings
    _, body = _split_frontmatter_body(content)

    headings = []
    fence_char: str | None = None
    fence_len: int = 0
    for line in body.split("\n"):
        m = _FENCE_RE.match(line)
        if m:
            delimiter = m.group(1)
            rest = m.group(2)
            char = delimiter[0]
            length = len(delimiter)
            if fence_char is None:
                fence_char = char
                fence_len = length
            elif char == fence_char and length >= fence_len and not rest.strip():
                # Closing fence: must match char/length and have no info string
                fence_char = None
                fence_len = 0
            continue
        if fence_char is not None:
            continue
        m = HEADING_PATTERN.match(line)
        if m:
            headings.append(line.rstrip())
    return headings


def get_note_info(path: str) -> str:
    """Get structured metadata about a vault note without returning content.

    Returns frontmatter, headings, file size, timestamps, and link counts.
    Useful for triaging notes before deciding whether to read them.

    Args:
        path: Path to the note (relative to vault or absolute).

    Returns:
        JSON with path, frontmatter, headings, size, modified, created,
        backlink_count, and outlink_count.
    """
    path = path.replace("\xa0", " ")
    file_path, error = resolve_file(path)
    if error:
        # Mirror read_file's attachment fallback for bare binary filenames
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if f".{ext}" in _BINARY_EXTENSIONS:
            file_path, att_error = resolve_file(path, base_path=config.ATTACHMENTS_DIR)
            if att_error:
                return err(error)
        else:
            return err(error)

    rel_path = get_relative_path(file_path)

    # File stats
    try:
        stat = file_path.stat()
    except OSError as e:
        return err(f"Cannot stat file: {e}")

    modified = datetime.fromtimestamp(stat.st_mtime).isoformat()

    is_md = file_path.suffix.lower() == ".md"

    if is_md:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return err(f"Error reading file: {e}")

        # Parse frontmatter from content (not file) — handles EOF without
        # trailing newline and guards against non-dict YAML values
        frontmatter, _ = _split_frontmatter_body(content)
        frontmatter = _json_safe_frontmatter(frontmatter)
        created_dt = parse_frontmatter_date(frontmatter.get("Date"))
        if not created_dt:
            created_dt = get_file_creation_time(file_path)
        headings = _extract_headings(content)
        size = len(content)
    else:
        frontmatter = {}
        created_dt = get_file_creation_time(file_path)
        headings = []
        size = stat.st_size

    created = created_dt.isoformat() if created_dt else modified

    # Link counts
    if is_md:
        from tools.links import _extract_outlinks, _scan_backlinks

        note_name = file_path.stem
        backlinks = _scan_backlinks(note_name, rel_path)
        outlinks = _extract_outlinks(file_path)
        backlink_count = len(backlinks)
        outlink_count = len(outlinks) if outlinks is not None else 0
    else:
        backlink_count = 0
        outlink_count = 0

    return ok(
        path=rel_path,
        frontmatter=_json_safe_frontmatter(frontmatter),
        headings=headings,
        size=size,
        modified=modified,
        created=created,
        backlink_count=backlink_count,
        outlink_count=outlink_count,
    )

