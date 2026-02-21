# Chat Plugin Markdown Rendering - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Render markdown in assistant chat messages using Obsidian's built-in MarkdownRenderer.

**Architecture:** Replace `setText` with `MarkdownRenderer.render()` for assistant messages only. Add CSS to scope rendered markdown within chat bubbles.

**Tech Stack:** Obsidian API (`MarkdownRenderer`), TypeScript, CSS

---

### Task 1: Update ChatView to render markdown for assistant messages

**Files:**
- Modify: `plugin/src/ChatView.ts:1` (import)
- Modify: `plugin/src/ChatView.ts:72-84` (addMessage method)

**Step 1: Add MarkdownRenderer to import**

Change line 1 from:
```typescript
import { ItemView, WorkspaceLeaf, requestUrl } from "obsidian";
```
to:
```typescript
import { ItemView, MarkdownRenderer, WorkspaceLeaf, requestUrl } from "obsidian";
```

**Step 2: Update addMessage to render markdown for assistant messages**

Replace the `addMessage` method with:
```typescript
private addMessage(role: "user" | "assistant", content: string): void {
    this.messages.push({ role, content });

    const messageEl = this.messagesContainer.createDiv({
        cls: `chat-message chat-message-${role}`
    });

    const contentEl = messageEl.createDiv({ cls: "chat-message-content" });

    if (role === "assistant") {
        MarkdownRenderer.render(this.app, content, contentEl, "", this);
    } else {
        contentEl.setText(content);
    }

    // Auto-scroll to bottom
    this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
}
```

Note: `MarkdownRenderer.render()` signature is `(app, markdown, el, sourcePath, component)`. We pass `this.app` for the app instance, empty string for sourcePath (chat messages aren't files), and `this` (the view, which is a Component) for lifecycle management.

**Step 3: Commit**

```bash
git add plugin/src/ChatView.ts
git commit -m "feat: render markdown in assistant chat messages (#63)"
```

### Task 2: Update CSS for rendered markdown in chat bubbles

**Files:**
- Modify: `plugin/styles.css:36-39` (chat-message-content rule)

**Step 1: Replace the `.chat-message-content` rule and add markdown scoping**

Replace:
```css
.chat-message-content {
	white-space: pre-wrap;
	line-height: 1.4;
}
```

With:
```css
.chat-message-content {
	line-height: 1.4;
}

.chat-message-user .chat-message-content {
	white-space: pre-wrap;
}

.chat-message-assistant .chat-message-content p:first-child {
	margin-top: 0;
}

.chat-message-assistant .chat-message-content p:last-child {
	margin-bottom: 0;
}

.chat-message-assistant .chat-message-content pre {
	overflow-x: auto;
	max-width: 100%;
}

.chat-message-assistant .chat-message-content code {
	font-size: 0.9em;
}
```

Rationale:
- Remove `white-space: pre-wrap` from assistant messages (rendered HTML handles spacing) but keep it for user messages
- Collapse margins on first/last paragraphs so chat bubbles don't have excess padding
- Ensure code blocks scroll horizontally within the sidebar width

**Step 2: Commit**

```bash
git add plugin/styles.css
git commit -m "style: scope markdown styles for chat message bubbles (#63)"
```

### Task 3: Build and verify

**Step 1: Build the plugin**

```bash
cd plugin && npm run build
```

Expected: Build succeeds with no errors.

**Step 2: Commit build output**

```bash
git add plugin/main.js
git commit -m "build: rebuild plugin with markdown rendering"
```
