# Confirmation Preview UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Emit a `confirmation_preview` SSE event when batch tools require confirmation, and render it in the Obsidian plugin as a preview box with Confirm/Cancel buttons.

**Architecture:** Backend adds a `preview_message` field to batch confirmation responses and emits a new SSE event. Plugin renders a styled preview box with file list and action buttons. Confirm/Cancel send regular chat messages so the agent stays in the loop for chained operations.

**Tech Stack:** Python (agent.py, tools), TypeScript (Obsidian plugin), CSS

**Design doc:** `docs/plans/2026-02-20-confirmation-preview-ui-design.md`

---

### Task 1: Add `preview_message` field to frontmatter confirmation preview

**Files:**
- Modify: `src/tools/frontmatter.py:335-345`
- Test: `tests/test_tools_frontmatter.py`

**Step 1: Write the failing test**

Add to the `TestBatchConfirmationGate` class in `tests/test_tools_frontmatter.py`:

```python
def test_confirmation_preview_has_preview_message(self, vault_config):
    """Preview should include separate preview_message for UI display."""
    clear_pending_previews()
    paths = self._create_files(vault_config, 10)
    result = json.loads(
        batch_update_frontmatter(
            field="status", value="done", operation="set", paths=paths,
        )
    )
    assert "preview_message" in result
    assert "This will" in result["preview_message"]
    assert "10 files" in result["preview_message"]
    # preview_message should NOT contain LLM instructions
    assert "confirm=true" not in result["preview_message"]
    assert "Show the file list" not in result["preview_message"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchConfirmationGate::test_confirmation_preview_has_preview_message -v`
Expected: FAIL — `preview_message` key not in result

**Step 3: Implement the change**

In `src/tools/frontmatter.py`, modify `_confirmation_preview`:

```python
def _confirmation_preview(
    operation: str, field: str, value: str | None, paths: list, context: str,
) -> str:
    """Return a confirmation preview for a batch operation."""
    desc = f"{operation} '{field}'" + (f" = '{value}'" if value else "")
    return ok(
        "Show the file list to the user and call again with confirm=true to proceed.",
        confirmation_required=True,
        preview_message=f"This will {desc} on {len(paths)} files{context}.",
        files=paths,
    )
```

The `message` field (from the `ok()` first arg) keeps the LLM instruction. The new `preview_message` field has the user-facing description.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchConfirmationGate::test_confirmation_preview_has_preview_message -v`
Expected: PASS

**Step 5: Fix any broken existing tests**

Existing tests assert on `result["message"]` which now contains just the LLM instruction. Check:

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchConfirmationGate -v`

The tests `test_confirmation_preview_includes_operation_details` and `test_confirmation_not_required_for_remove` assert on `result["message"]` content like `"context" in result["message"]` and `"remove" in result["message"]`. These will break because `message` now only has the LLM instruction.

Update these tests to check `preview_message` instead:
- `test_confirmation_preview_includes_operation_details`: change `result["message"]` → `result["preview_message"]`
- `test_confirmation_not_required_for_remove`: change `result["message"]` → `result["preview_message"]`
- `test_requires_confirmation_over_threshold`: change `"10 files" in result["message"]` → `"10 files" in result["preview_message"]`

**Step 6: Run all confirmation tests**

Run: `.venv/bin/python -m pytest tests/test_tools_frontmatter.py::TestBatchConfirmationGate -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/tools/frontmatter.py tests/test_tools_frontmatter.py
git commit -m "feat: add preview_message to frontmatter confirmation response"
```

---

### Task 2: Add `preview_message` field to batch_move_files confirmation preview

**Files:**
- Modify: `src/tools/files.py:198-207`
- Test: `tests/test_tools_files.py`

**Step 1: Write the failing test**

Find the batch move confirmation tests in `tests/test_tools_files.py` and add:

```python
def test_batch_move_preview_has_preview_message(self, vault_config):
    """Preview should include separate preview_message for UI display."""
    clear_pending_previews()
    moves = []
    for i in range(10):
        path = f"move_preview_{i}.md"
        (vault_config / path).write_text(f"---\ntitle: test{i}\n---\n")
        moves.append({"source": path, "destination": f"dest/move_preview_{i}.md"})
    (vault_config / "dest").mkdir(exist_ok=True)
    result = json.loads(batch_move_files(moves=moves))
    assert "preview_message" in result
    assert "This will move 10 files" in result["preview_message"]
    assert "confirm=true" not in result["preview_message"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k "test_batch_move_preview_has_preview_message" -v`
Expected: FAIL — `preview_message` key not in result

**Step 3: Implement the change**

In `src/tools/files.py`, modify the confirmation return (around line 202-207):

```python
            return ok(
                "Show the file list to the user and call again with confirm=true to proceed.",
                confirmation_required=True,
                preview_message=f"This will move {len(moves)} files.",
                files=files,
            )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k "test_batch_move_preview_has_preview_message" -v`
Expected: PASS

**Step 5: Run all batch move tests**

Run: `.venv/bin/python -m pytest tests/test_tools_files.py -k "batch_move" -v`
Expected: All PASS (existing tests check `result["message"]` which now has the LLM instruction — check if any assert on the user-facing text and update if needed)

**Step 6: Commit**

```bash
git add src/tools/files.py tests/test_tools_files.py
git commit -m "feat: add preview_message to batch_move_files confirmation response"
```

---

### Task 3: Emit `confirmation_preview` SSE event from `_process_tool_calls`

**Files:**
- Modify: `src/agent.py:378-391`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

Add to `tests/test_agent.py`:

```python
@pytest.mark.anyio
async def test_confirmation_preview_event_emitted():
    """_process_tool_calls emits confirmation_preview event with preview data."""
    events = []

    async def on_event(event_type, data):
        events.append((event_type, data))

    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_batch"
    mock_tool_call.function.name = "batch_update_frontmatter"
    mock_tool_call.function.arguments = '{"field": "status", "value": "done"}'

    confirmation_result = json.dumps({
        "success": True,
        "confirmation_required": True,
        "message": "Show the file list to the user and call again with confirm=true to proceed.",
        "preview_message": "This will set 'status' = 'done' on 7 files.",
        "files": ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "g.md"],
    })
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(
        isError=False, content=[MagicMock(text=confirmation_result)]
    )

    messages = [{"role": "system", "content": "test"}]

    from src.agent import _process_tool_calls
    await _process_tool_calls(
        [mock_tool_call], mock_session, messages, {}, 0, on_event,
    )

    preview_events = [e for e in events if e[0] == "confirmation_preview"]
    assert len(preview_events) == 1
    assert preview_events[0][1]["tool"] == "batch_update_frontmatter"
    assert preview_events[0][1]["message"] == "This will set 'status' = 'done' on 7 files."
    assert preview_events[0][1]["files"] == ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "g.md"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_confirmation_preview_event_emitted -v`
Expected: FAIL — no `confirmation_preview` event emitted

**Step 3: Implement the change**

In `src/agent.py`, in `_process_tool_calls`, modify the `confirmation_required` block (around line 381):

```python
            if parsed.get("confirmation_required"):
                confirmation_required = True
                await _emit("confirmation_preview", {
                    "tool": tool_name,
                    "message": parsed.get("preview_message", ""),
                    "files": parsed.get("files", []),
                })
                await _emit("tool_result", {"tool": tool_name, "success": success})
                # Stub remaining tool calls so the API doesn't reject missing results
                for remaining in tool_calls[i + 1:]:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": remaining.id,
                        "content": '{"skipped": "Awaiting user confirmation"}',
                    })
                break
```

The only change is inserting the `_emit("confirmation_preview", ...)` call before the existing `_emit("tool_result", ...)`.

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_confirmation_preview_event_emitted -v`
Expected: PASS

**Step 5: Run all agent confirmation tests**

Run: `.venv/bin/python -m pytest tests/test_agent.py -k "confirmation" -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/agent.py tests/test_agent.py
git commit -m "feat: emit confirmation_preview SSE event from _process_tool_calls"
```

---

### Task 4: Extract `sendMessageText` helper in plugin

**Files:**
- Modify: `plugin/src/ChatView.ts:123-212`

**Step 1: Refactor `sendMessage` into `sendMessageText` helper**

In `plugin/src/ChatView.ts`, extract the core send logic:

```typescript
private async sendMessageText(text: string): Promise<void> {
    if (this.isLoading) return;

    // Disable any pending confirmation buttons
    this.disablePendingConfirmation();

    // Capture active file once at request time for consistent context
    const activeFile = this.getActiveFilePath();

    this.isLoading = true;
    this.sendButton.disabled = true;

    // Add user message
    await this.addMessage("user", text);

    // Show loading
    const { container: loadingEl, textEl: loadingText } = this.showLoading();

    try {
        const response = await fetch("http://127.0.0.1:8000/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: text,
                session_id: this.sessionId,
                active_file: activeFile
            })
        });

        if (!response.ok || !response.body) {
            throw new Error(`Server returned ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                try {
                    const event = JSON.parse(line.slice(6));
                    switch (event.type) {
                        case "tool_call":
                            loadingText.setText(this.formatToolStatus(event.tool));
                            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
                            break;
                        case "tool_result":
                            break;
                        case "confirmation_preview":
                            this.addConfirmationPreview(event.message, event.files);
                            break;
                        case "response":
                            loadingEl.remove();
                            await this.addMessage("assistant", event.content, activeFile ?? "");
                            break;
                        case "error":
                            loadingEl.remove();
                            await this.addMessage("assistant", `Error: ${event.error}. Is the API server running?`);
                            break;
                        case "done":
                            this.sessionId = event.session_id ?? this.sessionId;
                            break;
                    }
                } catch {
                    // Skip malformed SSE lines
                }
            }
        }

        // If loading indicator is still showing (no response event received), remove it
        if (loadingEl.parentElement) {
            loadingEl.remove();
            await this.addMessage("assistant", "No response received from server.");
        }

    } catch (error) {
        if (loadingEl.parentElement) {
            loadingEl.remove();
        }
        const errorMessage = error instanceof Error ? error.message : "Failed to connect to server";
        await this.addMessage("assistant", `Error: ${errorMessage}. Is the API server running?`);
    } finally {
        this.isLoading = false;
        this.sendButton.disabled = false;
        this.inputField.focus();
    }
}

private async sendMessage(): Promise<void> {
    const message = this.inputField.value.trim();
    if (!message) return;
    this.inputField.value = "";
    await this.sendMessageText(message);
}
```

**Step 2: Build to verify no regressions**

Run: `cd plugin && npm run build`
Expected: Build succeeds with no errors

**Step 3: Commit**

```bash
git add plugin/src/ChatView.ts
git commit -m "refactor: extract sendMessageText helper in ChatView"
```

---

### Task 5: Add confirmation preview rendering and button handlers

**Files:**
- Modify: `plugin/src/ChatView.ts`

**Step 1: Add state tracking and `disablePendingConfirmation` method**

Add a class property and helper:

```typescript
private pendingConfirmationEl: HTMLElement | null = null;

private disablePendingConfirmation(): void {
    if (!this.pendingConfirmationEl) return;
    this.pendingConfirmationEl.querySelectorAll("button").forEach(btn => {
        (btn as HTMLButtonElement).disabled = true;
    });
    this.pendingConfirmationEl = null;
}
```

**Step 2: Add `addConfirmationPreview` method**

```typescript
private addConfirmationPreview(message: string, files: string[]): void {
    const previewEl = this.messagesContainer.createDiv({ cls: "chat-confirmation-preview" });

    // Action description
    previewEl.createDiv({ cls: "preview-message", text: message });

    // File list
    const filesEl = previewEl.createDiv({ cls: "preview-files" });
    const visibleCount = 10;
    const visibleFiles = files.slice(0, visibleCount);
    for (const file of visibleFiles) {
        filesEl.createDiv({ text: file });
    }

    if (files.length > visibleCount) {
        const expandEl = previewEl.createDiv({
            cls: "preview-expand",
            text: `and ${files.length - visibleCount} more...`,
        });
        expandEl.addEventListener("click", () => {
            for (const file of files.slice(visibleCount)) {
                filesEl.createDiv({ text: file });
            }
            expandEl.remove();
        });
    }

    // Buttons
    const buttonsEl = previewEl.createDiv({ cls: "preview-buttons" });

    const confirmBtn = buttonsEl.createEl("button", {
        cls: "preview-confirm",
        text: "Confirm",
    });
    confirmBtn.addEventListener("click", () => {
        this.sendMessageText("Yes, proceed with the batch operation");
    });

    const cancelBtn = buttonsEl.createEl("button", {
        cls: "preview-cancel",
        text: "Cancel",
    });
    cancelBtn.addEventListener("click", () => {
        this.sendMessageText("Cancel, do not proceed");
    });

    this.pendingConfirmationEl = previewEl;
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
}
```

**Step 3: Build to verify**

Run: `cd plugin && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add plugin/src/ChatView.ts
git commit -m "feat: add confirmation preview rendering with confirm/cancel buttons"
```

---

### Task 6: Add confirmation preview styles

**Files:**
- Modify: `plugin/styles.css`

**Step 1: Add styles**

Append to `plugin/styles.css`:

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

**Step 2: Build plugin**

Run: `cd plugin && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add plugin/styles.css
git commit -m "feat: add confirmation preview CSS styles"
```

---

### Task 7: Run full test suite and verify

**Step 1: Run all Python tests**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: All tests pass

**Step 2: Build plugin**

Run: `cd plugin && npm run build`
Expected: Build succeeds

**Step 3: Final commit (if any fixups needed)**

If any tests needed fixing, commit the fixes.
