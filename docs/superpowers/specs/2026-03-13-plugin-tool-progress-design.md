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
      <span class="tool-current-label">Thinking</span>
      <span class="tool-dots"></span>
    </div>
  </div>
</div>
```

Initial state shows "Thinking" with animated dots (not static "Thinking..." text). When the first `tool_call` arrives, the label and optional detail are updated in place.

The return type changes from `{ container, textEl }` to `{ container, stepsEl, currentEl }` so callers can manipulate both zones.

**Current-tool DOM structure** (after a `tool_call` event updates it):

```html
<div class="tool-current">
  <span class="tool-current-label">Searching vault</span>
  <span class="tool-current-detail"> — meeting notes</span>
  <span class="tool-dots"></span>
</div>
```

The `tool-current-detail` span is omitted when there is no relevant arg.

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

1. **Refresh the label map** with human-readable labels for all current MCP tools:

   | Tool | Label |
   |------|-------|
   | `find_notes` | Searching vault |
   | `read_file` | Reading file |
   | `create_file` | Creating file |
   | `move_file` | Moving file |
   | `edit_file` | Editing file |
   | `get_note_info` | Getting note info |
   | `find_links` | Finding links |
   | `update_frontmatter` | Updating frontmatter |
   | `batch_update_frontmatter` | Updating frontmatter (batch) |
   | `batch_move_files` | Moving files (batch) |
   | `merge_files` | Merging files |
   | `batch_merge_files` | Merging files (batch) |
   | `manage_preferences` | Managing preferences |
   | `summarize_file` | Summarizing file |
   | `research` | Researching |
   | `transcribe_to_file` | Transcribing audio |
   | `compare_folders` | Comparing folders |
   | `web_search` | Searching the web |
   | `log_interaction` | Logging interaction |

   Fallback for unlisted tools: `"Running <tool_name>"`.

2. **Accept optional `args` parameter**. Extract the single most relevant arg per tool and append as a muted suffix:
   - `find_notes`, `web_search` → `query`
   - `read_file`, `create_file`, `move_file`, `edit_file`, `get_note_info`, `find_links`, `summarize_file`, `transcribe_to_file` → `path`
   - `update_frontmatter`, `batch_update_frontmatter` → `field`
   - `research` → `path` or `topic`
   - Others → no suffix
   - If `args` is empty/missing or the key doesn't exist, omit the suffix.

   Format: `"Searching vault"` + args suffix `" \u2014 meeting notes"` (em-dash separated, truncated to 50 chars with ellipsis).

3. **Return an object** `{ label: string, detail: string }` instead of a single string, so the caller can render the label and detail in separate elements (detail gets muted styling). Call sites in `sendMessageText` that previously used `textEl` must be updated to use the new `stepsEl`/`currentEl` structure.

### SSE Event Handling

Tool events always arrive sequentially from the backend (never interleaved): `tool_call_1` → `tool_result_1` → `tool_call_2` → `tool_result_2` → etc.

**`tool_call`**:
- If `currentEl` already shows a tool, move it to `stepsEl` as a completed step (neutral style — no success/failure icon yet since `tool_result` may not have arrived)
- Set `currentEl` to the new tool with animated dots

**`tool_result`**:
- Update the icon on the item currently in `currentEl` to checkmark (success) or X (failure)
- Move it from `currentEl` to `stepsEl` as a completed step
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
- **No tool calls** (direct text response): Loading shows "Thinking" with animated dots, removed on response. No steps.
- **Empty or missing args**: Suffix omitted — just the label with dots.
- **Confirmation preview interleave**: Batch tool triggers `tool_call` → `tool_result` → `response` → `confirmation_preview`. Loading container is removed on `response`; confirmation preview is handled separately by existing `addConfirmationPreview`.

### Success Criteria

1. Animated dots cycle on the current tool status
2. Completed tools accumulate as muted lines with success/failure icons
3. Tool labels reflect current tool names (not legacy)
4. Most relevant arg shown as muted suffix on each tool status
5. Everything cleaned up when response/error arrives
6. No JS timers — pure CSS animation
