# Low-Priority Fixes Design

Addresses the two low-priority issues from `docs/analyses/tool_analysis.txt`.

## 1. add_wikilinks Regex Matches in Protected Zones (2e)

**Problem:** `log_chat.py:27` — The `add_wikilinks` regex uses negative lookbehind for `[[` and backtick, but still matches note names inside fenced code blocks, inline code spans, URLs, and existing markdown links.

**Fix:** Strip-and-restore. Before wikilink substitution, extract all protected zones into numbered placeholders (`\x00N\x00`). Run substitution on the remaining text. Restore placeholders.

**Protected zones** (extraction order):
1. Fenced code blocks (` ```...``` ` and `~~~...~~~`)
2. Inline code spans (`` `...` ``)
3. URLs (`https?://\S+`)
4. Existing wikilinks (`[[...]]`)

**Testing:** New `tests/test_log_chat.py` file (log_chat.py has no tests; this is cross-cutting logging logic, not a tool).

## 2. Code Fence Detection Inconsistency (2d)

**Problem:** `index_vault.py:79` uses `stripped.startswith("```")` while `vault.py:355` uses `re.compile(r"^(`{3,}|~{3,})")`. The approaches are inconsistent and the `startswith` version is fragile.

**Fix:** Extract `is_fence_line(line: str) -> bool` in `services/vault.py` using the existing `_FENCE_PATTERN`. Update `index_vault.py` to import and use it. Update `find_section` to use the same helper internally.

**Testing:** Add `is_fence_line` tests to `tests/test_vault_service.py`.
