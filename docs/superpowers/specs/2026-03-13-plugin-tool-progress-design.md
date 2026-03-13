# Plugin: Tool Execution Progress

**Date**: 2026-03-13
**Issue**: #170

## Problem

The Obsidian plugin shows a static "Running [tool]..." message during tool execution with no animation, ignores `tool_result` SSE events, and uses an outdated tool name map.

## Design

### Overview

Add an animated loading indicator with accumulated tool step history. When the agent calls multiple tools in sequence, completed steps stack as muted lines above the active tool. Everything is removed when the final response arrives.

### Files Changed

- `plugin/src/ChatView.ts` — event handling, loading indicator, tool formatting
- `plugin/styles.css` — animation keyframes, step styling

No backend changes required.

### Loading Indicator Structure

`showLoading()` creates a container with two zones:

```html
<div class="chat-message chat-message-assistant chat-loading">
  <div class="chat-message-content">
    <div class="tool-steps">
      <!-- Completed tools accumulate here -->
    </div>
    <div class="tool-current">
      Searching vault<span class="tool-dots"></span>
    </div>
  </div>
</div>
```

The return type changes from `{ container, textEl }` to `{ container, stepsEl, currentEl }` so callers can manipulate both zones.

### CSS Animation

Pure CSS cycling dots via `@keyframes` on `.tool-dots::after`:

```css
@keyframes dots {
  0%   { content: ""; }
  25%  { content: "."; }
  50%  { content: ".."; }
  75%  { content: "..."; }
}

.tool-dots::after {
  content: "";
  animation: dots 1.2s steps(1) infinite;
}
```

No JS timers needed; no cleanup required.

### Tool Status Formatting

Update `formatToolStatus()`:

1. **Refresh the label map** to current tool names: `find_notes`, `find_links`, `edit_file`, `get_note_info`, `merge_files`, `batch_merge_files`, `batch_update_frontmatter`, `batch_move_files`, `manage_preferences`, `summarize_file`, `research`, `transcribe_to_file`, `compare_folders`, `edit_file`.

2. **Accept optional `args` parameter**. Extract the single most relevant arg per tool and append as a muted suffix:
   - `find_notes` → `query`
   - `read_file`, `create_file`, `move_file`, `edit_file`, `get_note_info`, `find_links`, `summarize_file`, `transcribe_to_file` → `path`
   - `update_frontmatter`, `batch_update_frontmatter` → `field`
   - `web_search` → `query`
   - `research` → `path` or `topic`
   - Others → no suffix

   Format: `"Searching vault"` + args suffix `" \u2014 meeting notes"` (em-dash separated, truncated to ~50 chars).

3. **Return an object** `{ label: string, detail: string }` instead of a single string, so the caller can render the label and detail in separate elements (detail gets muted styling).

### SSE Event Handling

**`tool_call`**:
- If `currentEl` already shows a tool, move it to `stepsEl` as a completed step (neutral style — no success/failure icon yet since `tool_result` may not have arrived)
- Set `currentEl` to the new tool with animated dots

**`tool_result`**:
- Find the last step in `stepsEl` (or current if no step was moved yet) and update its icon to checkmark (success) or X (failure)
- Clear `currentEl` (next `tool_call` populates it, or `response` cleans up)

**`response` / `error`**:
- Remove the entire loading container as today — steps and current are discarded together

### Step Element Structure

Each completed step in `tool-steps`:

```html
<div class="tool-step">
  <span class="tool-step-icon tool-step-success">&#10003;</span>
  <span class="tool-step-label">Read file</span>
  <span class="tool-step-detail">Daily Notes/2026-03-13.md</span>
</div>
```

Failure variant uses class `tool-step-failure` with an X character.

### Step Styling

- `.tool-step` — `font-size: 0.85em`, `color: var(--text-muted)`, no italic
- `.tool-step-icon` — fixed-width inline element
- `.tool-step-success` — `color: var(--text-muted)` (subtle, not green — sidebar should be calm)
- `.tool-step-failure` — `color: var(--text-error)` (Obsidian's error color variable)
- `.tool-step-detail` — `color: var(--text-faint)`, truncated with `text-overflow: ellipsis`
- `.tool-current` — `color: var(--text-muted)`, `font-style: italic`

### Edge Cases

- **Single tool call**: One animated line, moves to step on result, removed on response. No empty steps div visible.
- **`tool_result` before next `tool_call`**: Current becomes a step, current is cleared. Next `tool_call` populates current again.
- **`response` arrives with `tool_result` still pending**: Entire container removed — no stale indicators.
- **Error mid-sequence**: Same cleanup as response — remove everything.
- **No tool calls** (direct text response): Loading shows "Thinking..." with dots, removed on response. No steps.

### Success Criteria

1. Animated dots cycle on the current tool status
2. Completed tools accumulate as muted lines with success/failure icons
3. Tool labels reflect current tool names (not legacy)
4. Most relevant arg shown as muted suffix on each tool status
5. Everything cleaned up when response/error arrives
6. No JS timers — pure CSS animation
