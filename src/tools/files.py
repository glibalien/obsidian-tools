"""File operation tools - read, create, move, append."""

import json
import re

import yaml

from services.vault import (
    BATCH_CONFIRM_THRESHOLD,
    consume_preview,
    do_move_file,
    err,
    format_batch_result,
    get_relative_path,
    ok,
    resolve_file,
    resolve_vault_path,
    store_preview,
)


def read_file(path: str, offset: int = 0, length: int = 3500) -> str:
    """Read content of a vault note with optional pagination.

    Args:
        path: Path to the note, either relative to vault root or absolute.
        offset: Character position to start reading from (default 0).
        length: Maximum characters to return (default 4000).

    Returns:
        The text content of the note, with pagination markers if truncated.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return err(f"Error reading file: {e}")

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

    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, content

    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        return {}, content

    body = content[match.end():]
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
                "Show the file list to the user and call again with confirm=true to proceed.",
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
    moves: list[dict],
    confirm: bool = False,
) -> str:
    """Move multiple vault files to new locations.

    Args:
        moves: List of move operations, each a dict with 'source' and 'destination' keys.
               Example: [{"source": "old/path.md", "destination": "new/path.md"}]
        confirm: Must be true to execute when moving more than 5 files.

    Returns:
        Summary of successes and failures, or confirmation preview for large batches.
    """
    if not moves:
        return err("moves list is empty")

    # Require confirmation for large batches
    if len(moves) > BATCH_CONFIRM_THRESHOLD:
        # Canonical key: stringify each move dict for tuple hashing
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
                "Show the file list to the user and call again with confirm=true to proceed.",
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


def append_to_file(path: str, content: str) -> str:
    """Append content to the end of an existing vault file.

    Args:
        path: Path to the note (relative to vault or absolute).
        content: Content to append to the file.

    Returns:
        Confirmation message or error.
    """
    file_path, error = resolve_file(path)
    if error:
        return err(error)

    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
    except Exception as e:
        return err(f"Appending to file failed: {e}")

    rel = str(get_relative_path(file_path))
    return ok(f"Appended to {rel}", path=rel)
