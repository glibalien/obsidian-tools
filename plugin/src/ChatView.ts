import { ItemView, MarkdownRenderer, TFile, WorkspaceLeaf } from "obsidian";

export const VIEW_TYPE_CHAT = "vault-chat-view";

interface ChatMessage {
	role: "user" | "assistant";
	content: string;
}

interface FileState {
	sessionId: string | null;
	messages: ChatMessage[];
}

export class ChatView extends ItemView {
	private sessionId: string | null = null;
	private messages: ChatMessage[] = [];
	private messagesContainer: HTMLElement;
	private inputField: HTMLTextAreaElement;
	private sendButton: HTMLButtonElement;
	private isLoading = false;
	private pendingConfirmationEl: HTMLElement | null = null;
	private fileStates = new Map<string | null, FileState>();
	private currentFile: string | null = null;
	private pendingFileSwitch: string | null | undefined = undefined;
	private contextBarName: HTMLElement;

	constructor(leaf: WorkspaceLeaf) {
		super(leaf);
	}

	getViewType(): string {
		return VIEW_TYPE_CHAT;
	}

	getDisplayText(): string {
		return "Vault Chat";
	}

	getIcon(): string {
		return "message-circle";
	}

	async onOpen(): Promise<void> {
		const container = this.containerEl.children[1];
		container.empty();
		container.addClass("vault-chat-container");

		// Context bar
		const contextBar = container.createDiv({ cls: "chat-context-bar" });
		this.contextBarName = contextBar.createSpan({ cls: "chat-context-name" });

		// Messages area
		this.messagesContainer = container.createDiv({ cls: "chat-messages" });

		// Input area
		const inputContainer = container.createDiv({ cls: "chat-input-container" });

		this.inputField = inputContainer.createEl("textarea", {
			cls: "chat-input",
			attr: { placeholder: "Type a message..." }
		});

		this.sendButton = inputContainer.createEl("button", {
			cls: "chat-send-button",
			text: "Send"
		});

		// Event listeners
		this.sendButton.addEventListener("click", () => this.sendMessage());
		this.inputField.addEventListener("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.sendMessage();
			}
		});

		// Initialize current file and context bar
		this.currentFile = this.getActiveFilePath();
		this.updateContextBar();

		// Listen for file changes
		this.registerEvent(
			this.app.workspace.on("file-open", (file) => this.onFileChange(file))
		);

		// Welcome message
		await this.addMessage("assistant", "Hello! I'm your vault assistant. How can I help you today?");
	}

	async onClose(): Promise<void> {
		// Cleanup if needed
	}

	private async renderMessage(msg: ChatMessage, sourcePath = ""): Promise<void> {
		const messageEl = this.messagesContainer.createDiv({
			cls: `chat-message chat-message-${msg.role}`
		});

		const contentEl = messageEl.createDiv({ cls: "chat-message-content" });

		if (msg.role === "assistant") {
			await MarkdownRenderer.render(this.app, msg.content, contentEl, sourcePath, this);
		} else {
			contentEl.setText(msg.content);
		}
	}

	private async addMessage(role: "user" | "assistant", content: string, sourcePath = ""): Promise<void> {
		const msg: ChatMessage = { role, content };
		this.messages.push(msg);
		await this.renderMessage(msg, sourcePath);

		// Auto-scroll to bottom
		this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	}

	private async renderMessages(): Promise<void> {
		this.messagesContainer.empty();
		const sourcePath = this.currentFile ?? "";
		for (const msg of this.messages) {
			await this.renderMessage(msg, sourcePath);
		}
		this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	}

	private updateContextBar(): void {
		if (this.currentFile) {
			// Show basename without extension
			const basename = this.currentFile.replace(/.*\//, "").replace(/\.[^.]+$/, "");
			this.contextBarName.setText(basename);
		} else {
			this.contextBarName.setText("No file open");
		}
	}

	private onFileChange(file: TFile | null): void {
		const newFile = file?.path ?? null;
		if (newFile === this.currentFile) return;
		if (this.isLoading) {
			this.pendingFileSwitch = newFile;
			return;
		}
		this.switchToFile(newFile);
	}

	private async switchToFile(newFile: string | null): Promise<void> {
		// Save current state
		this.fileStates.set(this.currentFile, {
			sessionId: this.sessionId,
			messages: [...this.messages],
		});

		// Restore or start fresh
		const existing = this.fileStates.get(newFile);
		if (existing) {
			this.sessionId = existing.sessionId;
			this.messages = [...existing.messages];
		} else {
			this.sessionId = null;
			this.messages = [];
		}

		this.currentFile = newFile;
		this.pendingConfirmationEl = null;
		this.updateContextBar();
		await this.renderMessages();

		// Welcome message for fresh conversations
		if (!existing) {
			await this.addMessage("assistant", "Hello! I'm your vault assistant. How can I help you today?");
		}
	}

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

	private getActiveFilePath(): string | null {
		const activeFile = this.app.workspace.getActiveFile();
		return activeFile?.path ?? null;
	}

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

	private disablePendingConfirmation(): void {
		if (!this.pendingConfirmationEl) return;
		this.pendingConfirmationEl.querySelectorAll("button").forEach(btn => {
			(btn as HTMLButtonElement).disabled = true;
		});
		this.pendingConfirmationEl = null;
	}

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

	private async sendMessageText(text: string): Promise<void> {
		if (this.isLoading) return;

		// Disable any pending confirmation buttons
		this.disablePendingConfirmation();

		// Use the displayed conversation's file context
		const activeFile = this.currentFile;

		this.isLoading = true;
		this.sendButton.disabled = true;

		// Add user message
		await this.addMessage("user", text);

		// Show loading
		const { container: loadingEl, stepsEl, currentEl } = this.showLoading();

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
							case "tool_result": {
								this.moveCurrentToSteps(stepsEl, currentEl, event.success);
								this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
								break;
							}
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

			if (this.pendingFileSwitch !== undefined) {
				const pending = this.pendingFileSwitch;
				this.pendingFileSwitch = undefined;
				await this.switchToFile(pending);
			}
		}
	}

	private async sendMessage(): Promise<void> {
		const message = this.inputField.value.trim();
		if (!message || this.isLoading) return;
		this.inputField.value = "";
		await this.sendMessageText(message);
	}
}
