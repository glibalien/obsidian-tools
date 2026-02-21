# Confirmation Preview UI Design

## Problem

Batch operations (`batch_update_frontmatter`, `batch_move_files`) require user confirmation when affecting >5 files. The current flow forces the agent to present the preview — but the agent doesn't reliably show the file list. The preview data exists in the tool result; it just never reaches the user directly.

## Solution

Emit a dedicated `confirmation_preview` SSE event from `_process_tool_calls` so the plugin can render a structured preview with Confirm/Cancel buttons, independent of what the LLM says.

## Architecture

```
Tool returns confirmation_required JSON
  → _process_tool_calls detects it
  → Emits "confirmation_preview" SSE event with message + files
  → Breaks tool loop, sets force_text_only (existing behavior)
  → LLM generates text response (existing behavior)

Plugin receives confirmation_preview event
  → Renders preview box with action description + file list
  → Shows Confirm / Cancel buttons
  → Confirm sends "Yes, proceed with the batch operation" as chat message
  → Cancel sends "Cancel this operation" as chat message
  → Agent resumes normally in the next turn
```

Chained batch operations work naturally: each confirmation starts a new agent turn, and the agent picks up the next step from conversation context.

## Backend Changes

### agent.py — `_process_tool_calls`

Split the confirmation tool result into user-facing and LLM-facing parts. When `confirmation_required` is detected, emit a new event:

```python
if parsed.get("confirmation_required"):
    confirmation_required = True
    await _emit("confirmation_preview", {
        "tool": tool_name,
        "message": parsed.get("preview_message", ""),
        "files": parsed.get("files", []),
    })
    await _emit("tool_result", {"tool": tool_name, "success": success})
    # ... existing stub + break logic
```

No other changes to `agent_turn` or `api_server.py` — the existing `force_text_only` flow continues to work.

### tools/frontmatter.py — `_confirmation_preview`

Split the message into `preview_message` (user-facing) and `message` (LLM instruction):

```python
def _confirmation_preview(
    operation: str, field: str, value: str | None, paths: list, context: str,
) -> str:
    desc = f"{operation} '{field}'" + (f" = '{value}'" if value else "")
    return ok(
        f"Show the file list to the user and call again with confirm=true to proceed.",
        confirmation_required=True,
        preview_message=f"This will {desc} on {len(paths)} files{context}.",
        files=paths,
    )
```

### tools/files.py — `batch_move_files`

Same pattern — split into `preview_message` and `message`:

```python
return ok(
    "Show the file list to the user and call again with confirm=true to proceed.",
    confirmation_required=True,
    preview_message=f"This will move {len(moves)} files.",
    files=files,
)
```

## Plugin Changes

### ChatView.ts

#### New SSE event handler

In the SSE switch statement, add a `confirmation_preview` case. Insert the preview box before the loading indicator (which stays visible until the LLM's text response arrives via the existing `response` event):

```typescript
case "confirmation_preview":
    this.addConfirmationPreview(event.message, event.files);
    break;
```

#### `addConfirmationPreview(message, files)` method

Renders a preview box in the messages container:

1. **Container**: `chat-confirmation-preview` class, styled distinct from regular messages (bordered, full-width)
2. **Description**: The `message` text (e.g. "This will set 'status' = 'done' on 12 files.")
3. **File list**:
   - If ≤10 files: show all
   - If >10 files: show first 10, then a clickable "and N more..." that expands
   - Each file on its own line
4. **Buttons**: Confirm (accent color) and Cancel (muted) side by side
5. Insert before `loadingEl` (not at end of container) so it appears in the right position

#### Button behavior

- **Confirm**: Calls `sendMessageText("Yes, proceed with the batch operation")` (a new helper extracted from `sendMessage` that takes a string directly instead of reading from the input field)
- **Cancel**: Calls `sendMessageText("Cancel, do not proceed")`
- Both buttons disable themselves on click and add a visual "clicked" state
- Both buttons get a reference stored on the class (e.g. `this.pendingConfirmationEl`) so they can be disabled when any other message is sent

#### `sendMessageText(text)` helper

Extract the core send logic from `sendMessage` into a helper:

```typescript
private async sendMessageText(text: string): Promise<void> {
    if (this.isLoading) return;
    // ... same logic as sendMessage but takes text param directly
}

private async sendMessage(): Promise<void> {
    const message = this.inputField.value.trim();
    if (!message) return;
    this.inputField.value = "";
    await this.sendMessageText(message);
}
```

#### Stale preview handling

When `sendMessageText` is called (from any source — input field, confirm button, cancel button):
- Disable all confirmation buttons in `this.pendingConfirmationEl` if it exists
- Clear `this.pendingConfirmationEl`

This prevents clicking Confirm on an old preview after the conversation has moved on.

### styles.css

```css
.chat-confirmation-preview {
    align-self: stretch;
    max-width: 100%;
    padding: 12px 14px;
    border: 1px solid var(--background-modifier-border);
    border-radius: 8px;
    background-color: var(--background-secondary);
}

.chat-confirmation-preview .preview-message {
    font-weight: 500;
    margin-bottom: 8px;
    color: var(--text-normal);
}

.chat-confirmation-preview .preview-files {
    font-size: 0.85em;
    color: var(--text-muted);
    margin-bottom: 12px;
    max-height: 200px;
    overflow-y: auto;
}

.chat-confirmation-preview .preview-files div {
    padding: 2px 0;
}

.chat-confirmation-preview .preview-expand {
    color: var(--text-accent);
    cursor: pointer;
    font-size: 0.85em;
    margin-bottom: 12px;
}

.chat-confirmation-preview .preview-buttons {
    display: flex;
    gap: 8px;
}

.chat-confirmation-preview .preview-confirm {
    padding: 6px 16px;
    border: none;
    border-radius: 6px;
    background-color: var(--interactive-accent);
    color: var(--text-on-accent);
    cursor: pointer;
    font-weight: 500;
}

.chat-confirmation-preview .preview-cancel {
    padding: 6px 16px;
    border: 1px solid var(--background-modifier-border);
    border-radius: 6px;
    background-color: transparent;
    color: var(--text-muted);
    cursor: pointer;
}

.chat-confirmation-preview .preview-confirm:disabled,
.chat-confirmation-preview .preview-cancel:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

## Testing

### Backend (Python)

In existing test files:

- **test_agent.py**: Mock `on_event` callback, trigger a batch tool that returns `confirmation_required`, assert `confirmation_preview` event is emitted with correct `message` and `files`
- **test_tools_frontmatter.py**: Assert `_confirmation_preview` returns `preview_message` field in JSON
- **test_tools_files.py**: Assert `batch_move_files` preview returns `preview_message` field

### Plugin

Manual testing:
1. Trigger a batch operation affecting >5 files
2. Verify preview box appears with correct file list
3. Click Confirm → operation proceeds
4. Click Cancel → agent acknowledges
5. Chained operations: trigger two batch ops → verify two sequential preview boxes
6. Stale preview: see preview, type a different message instead → verify buttons disabled

## Files Modified

| File | Change |
|------|--------|
| `src/agent.py` | Emit `confirmation_preview` event in `_process_tool_calls` |
| `src/tools/frontmatter.py` | Add `preview_message` field to `_confirmation_preview` |
| `src/tools/files.py` | Add `preview_message` field to `batch_move_files` preview |
| `plugin/src/ChatView.ts` | Handle `confirmation_preview` event, `addConfirmationPreview`, `sendMessageText` helper, stale preview handling |
| `plugin/styles.css` | Confirmation preview styles |
| `tests/test_agent.py` | Test `confirmation_preview` event emission |
| `tests/test_tools_frontmatter.py` | Test `preview_message` field |
| `tests/test_tools_files.py` | Test `preview_message` field |
