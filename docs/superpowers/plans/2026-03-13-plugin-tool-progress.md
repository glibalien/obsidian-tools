# Plugin Tool Execution Progress Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add animated tool progress indicators and accumulated step history to the Obsidian chat plugin sidebar.

**Architecture:** Rewrite `showLoading()` to create a two-zone container (completed steps + current tool with CSS dot animation). Update `formatToolStatus()` to return structured `{label, detail}` with current tool names and key-arg extraction. Wire `tool_call`/`tool_result` SSE handlers to accumulate completed steps above the active tool, removed on response/error.

**Tech Stack:** TypeScript (Obsidian API), CSS keyframe animations

**Spec:** `docs/superpowers/specs/2026-03-13-plugin-tool-progress-design.md`

---

## Chunk 1: CSS animations and tool formatting

### Task 1: Add CSS for dot animation, step styling, and current-tool styling

**Files:**
- Modify: `plugin/styles.css:61-65` (replace `.chat-loading` block, add new rules)

- [ ] **Step 1: Remove old `.chat-loading` block and add all new CSS rules**

Remove the old `.chat-loading .chat-message-content` block (lines 61-65) entirely. Then add the new styles at the end of `plugin/styles.css`:

```css
/* --- Tool progress indicator --- */

@keyframes dots {
	0%   { content: ""; }
	25%  { content: "."; }
	50%  { content: ".."; }
	75%  { content: "..."; }
}

.chat-loading .chat-message-content {
	color: var(--text-muted);
}

.tool-steps {
	display: flex;
	flex-direction: column;
	gap: 2px;
}

.tool-step {
	font-size: 0.85em;
	color: var(--text-muted);
	display: flex;
	align-items: baseline;
	gap: 4px;
}

.tool-step-icon {
	display: inline-block;
	width: 1.2em;
	text-align: center;
	flex-shrink: 0;
}

.tool-step-success {
	color: var(--text-muted);
}

.tool-step-failure {
	color: var(--text-error);
}

.tool-step-detail {
	color: var(--text-faint);
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}

.tool-current {
	color: var(--text-muted);
	font-style: italic;
	margin-top: 2px;
}

.tool-current-detail {
	color: var(--text-faint);
}

.tool-dots::after {
	content: "";
	animation: dots 1.2s steps(1) infinite;
}
```

- [ ] **Step 2: Build plugin to verify CSS compiles**

Run: `cd plugin && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add plugin/styles.css
git commit -m "feat(plugin): add CSS for tool progress animation and step styling (#170)"
```

---

## Chunk 2: TypeScript changes (atomic — all three methods + call sites together)

### Task 2: Rewrite formatToolStatus, showLoading, helpers, and SSE handlers

All TypeScript changes are applied together as one atomic unit since the method signature changes and call sites must be consistent for the build to succeed.

**Files:**
- Modify: `plugin/src/ChatView.ts:92-99` (replace `showLoading`)
- Modify: `plugin/src/ChatView.ts:106-122` (replace `formatToolStatus`)
- Modify: `plugin/src/ChatView.ts:198` (destructuring)
- Modify: `plugin/src/ChatView.ts:230-236` (switch cases)

- [ ] **Step 1: Replace `formatToolStatus` (lines 106-122)**

```typescript
private formatToolStatus(toolName: string, args?: Record<string, unknown>): { label: string; detail: string } {
	const labels: Record<string, string> = {
		find_notes: "Searching vault",
		read_file: "Reading file",
		create_file: "Creating file",
		move_file: "Moving file",
		edit_file: "Editing file",
		get_note_info: "Getting note info",
		find_links: "Finding links",
		update_frontmatter: "Updating frontmatter",
		batch_update_frontmatter: "Updating frontmatter (batch)",
		batch_move_files: "Moving files (batch)",
		merge_files: "Merging files",
		batch_merge_files: "Merging files (batch)",
		manage_preferences: "Managing preferences",
		summarize_file: "Summarizing file",
		research: "Researching",
		transcribe_to_file: "Transcribing audio",
		compare_folders: "Comparing folders",
		web_search: "Searching the web",
		log_interaction: "Logging interaction",
	};

	const label = labels[toolName] ?? `Running ${toolName}`;

	// Extract the most relevant arg per tool
	const keyArgMap: Record<string, string[]> = {
		find_notes: ["query"],
		web_search: ["query"],
		read_file: ["path"],
		create_file: ["path"],
		move_file: ["source"],
		edit_file: ["path"],
		get_note_info: ["path"],
		find_links: ["path"],
		summarize_file: ["path"],
		transcribe_to_file: ["path"],
		update_frontmatter: ["field"],
		batch_update_frontmatter: ["field"],
		research: ["path", "topic"],
	};

	let detail = "";
	if (args) {
		const keys = keyArgMap[toolName];
		if (keys) {
			for (const key of keys) {
				const val = args[key];
				if (val && typeof val === "string") {
					const truncated = val.length > 50 ? val.slice(0, 50) + "\u2026" : val;
					detail = ` \u2014 ${truncated}`;
					break;
				}
			}
		}
	}

	return { label, detail };
}
```

- [ ] **Step 2: Replace `showLoading` (lines 92-99) and add helper methods after it**

```typescript
private showLoading(): { container: HTMLElement; stepsEl: HTMLElement; currentEl: HTMLElement } {
	const loadingEl = this.messagesContainer.createDiv({
		cls: "chat-message chat-message-assistant chat-loading"
	});
	const contentEl = loadingEl.createDiv({ cls: "chat-message-content" });
	const stepsEl = contentEl.createDiv({ cls: "tool-steps" });
	const currentEl = contentEl.createDiv({ cls: "tool-current" });
	currentEl.createSpan({ cls: "tool-current-label", text: "Thinking" });
	currentEl.createSpan({ cls: "tool-dots" });
	this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	return { container: loadingEl, stepsEl, currentEl };
}

private setCurrentTool(currentEl: HTMLElement, label: string, detail: string): void {
	currentEl.empty();
	currentEl.createSpan({ cls: "tool-current-label", text: label });
	if (detail) {
		currentEl.createSpan({ cls: "tool-current-detail", text: detail });
	}
	currentEl.createSpan({ cls: "tool-dots" });
}

private moveCurrentToSteps(stepsEl: HTMLElement, currentEl: HTMLElement, success: boolean | null): void {
	// Read current label/detail before clearing
	const labelEl = currentEl.querySelector(".tool-current-label");
	const detailEl = currentEl.querySelector(".tool-current-detail");
	if (!labelEl) return; // Nothing to move (still "Thinking")

	const label = labelEl.textContent ?? "";
	const detail = detailEl?.textContent ?? "";

	const stepEl = stepsEl.createDiv({ cls: "tool-step" });
	const iconEl = stepEl.createSpan({ cls: "tool-step-icon" });
	if (success === true) {
		iconEl.addClass("tool-step-success");
		iconEl.setText("\u2713");
	} else if (success === false) {
		iconEl.addClass("tool-step-failure");
		iconEl.setText("\u2717");
	} else {
		// Neutral — moved before result arrived
		iconEl.setText("\u2022");
	}
	stepEl.createSpan({ cls: "tool-step-label", text: label });
	if (detail) {
		stepEl.createSpan({ cls: "tool-step-detail", text: detail });
	}

	currentEl.empty();
}
```

- [ ] **Step 3: Update the `showLoading` destructuring (line 198)**

Change from:

```typescript
const { container: loadingEl, textEl: loadingText } = this.showLoading();
```

to:

```typescript
const { container: loadingEl, stepsEl, currentEl } = this.showLoading();
```

- [ ] **Step 4: Update the `tool_call` case (lines 231-234)**

```typescript
case "tool_call": {
	// If current shows a tool (not initial "Thinking"), move it to steps
	if (currentEl.querySelector(".tool-current-label")?.textContent !== "Thinking") {
		this.moveCurrentToSteps(stepsEl, currentEl, null);
	}
	const { label, detail } = this.formatToolStatus(event.tool, event.args);
	this.setCurrentTool(currentEl, label, detail);
	this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	break;
}
```

- [ ] **Step 5: Update the `tool_result` case (lines 235-236)**

```typescript
case "tool_result": {
	this.moveCurrentToSteps(stepsEl, currentEl, event.success);
	this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	break;
}
```

- [ ] **Step 6: Build to verify everything compiles**

Run: `cd plugin && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 7: Commit**

```bash
git add plugin/src/ChatView.ts
git commit -m "feat(plugin): animated tool progress with step accumulation (#170)"
```

---

## Chunk 3: Verification

### Task 3: Final build and manual test checklist

- [ ] **Step 1: Full build**

Run: `cd plugin && npm run build`
Expected: Build succeeds, `plugin/main.js` is generated.

- [ ] **Step 2: Manual test checklist**

With the API server running and the plugin installed:

1. Send a message that triggers a single tool call (e.g. "What files are in Daily Notes?")
   - Verify: animated dots on "Searching vault"
   - Verify: step appears with checkmark after completion
   - Verify: everything removed when response arrives

2. Send a message that triggers multiple tools (e.g. "Summarize today's daily note")
   - Verify: completed tools stack up with checkmarks
   - Verify: current tool has animated dots
   - Verify: key arg visible (e.g. path shown for read_file)

3. Send a simple question that needs no tools (e.g. "Hello")
   - Verify: "Thinking" with animated dots, removed on response

4. Trigger an error (e.g. stop the API server mid-request)
   - Verify: loading indicator cleaned up on error

- [ ] **Step 3: Commit any final fixes, then squash into feature branch**

```bash
git add -A
git commit -m "feat(plugin): tool execution progress with animation (#170)"
```
