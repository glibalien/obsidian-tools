# Chat Plugin Markdown Rendering - Design

**Issue:** #63
**Date:** 2026-02-17

## Problem

The chat plugin sidebar displays agent responses as raw markdown text. `ChatView.ts` uses `contentEl.setText(content)` which escapes all markup.

## Solution

Use Obsidian's built-in `MarkdownRenderer.render()` for assistant messages. User messages stay as plain text.

### Changes

1. **`plugin/src/ChatView.ts`**
   - Import `MarkdownRenderer` from obsidian
   - In `addMessage()`: use `MarkdownRenderer.render(content, contentEl, '', this)` for assistant role, keep `setText` for user role

2. **`plugin/styles.css`**
   - Remove `white-space: pre-wrap` from `.chat-message-content` (rendered HTML handles spacing)
   - Add scoped styles for rendered markdown elements (code blocks, lists, etc.) to fit sidebar width

### Why MarkdownRenderer

- Zero added dependencies
- Handles headers, bold, italic, lists, blockquotes, fenced code blocks with syntax highlighting
- Renders wikilinks natively within Obsidian
- Consistent with vault rendering everywhere else
