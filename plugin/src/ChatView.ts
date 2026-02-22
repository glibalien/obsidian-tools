import { ItemView, MarkdownRenderer, WorkspaceLeaf } from "obsidian";

export const VIEW_TYPE_CHAT = "vault-chat-view";

interface ChatMessage {
	role: "user" | "assistant";
	content: string;
}

export class ChatView extends ItemView {
	private sessionId: string | null = null;
	private messages: ChatMessage[] = [];
	private messagesContainer: HTMLElement;
	private inputField: HTMLTextAreaElement;
	private sendButton: HTMLButtonElement;
	private isLoading = false;
	private pendingConfirmationEl: HTMLElement | null = null;

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

		// Welcome message
		await this.addMessage("assistant", "Hello! I'm your vault assistant. How can I help you today?");
	}

	async onClose(): Promise<void> {
		// Cleanup if needed
	}

	private async addMessage(role: "user" | "assistant", content: string, sourcePath = ""): Promise<void> {
		this.messages.push({ role, content });

		const messageEl = this.messagesContainer.createDiv({
			cls: `chat-message chat-message-${role}`
		});

		const contentEl = messageEl.createDiv({ cls: "chat-message-content" });

		if (role === "assistant") {
			await MarkdownRenderer.render(this.app, content, contentEl, sourcePath, this);
		} else {
			contentEl.setText(content);
		}

		// Auto-scroll to bottom
		this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
	}

	private showLoading(): { container: HTMLElement; textEl: HTMLElement } {
		const loadingEl = this.messagesContainer.createDiv({
			cls: "chat-message chat-message-assistant chat-loading"
		});
		const textEl = loadingEl.createDiv({ cls: "chat-message-content", text: "Thinking..." });
		this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
		return { container: loadingEl, textEl };
	}

	private getActiveFilePath(): string | null {
		const activeFile = this.app.workspace.getActiveFile();
		return activeFile?.path ?? null;
	}

	private formatToolStatus(toolName: string): string {
		const labels: Record<string, string> = {
			search_vault: "Searching vault...",
			read_file: "Reading file...",
			find_backlinks: "Finding backlinks...",
			find_outlinks: "Finding outlinks...",
			search_by_folder: "Listing folder...",
			list_files_by_frontmatter: "Searching frontmatter...",
			web_search: "Searching the web...",
			create_file: "Creating file...",
			move_file: "Moving file...",
			update_frontmatter: "Updating frontmatter...",
			log_interaction: "Logging interaction...",
			transcribe_audio: "Transcribing audio...",
		};
		return labels[toolName] ?? `Running ${toolName}...`;
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
		if (!message || this.isLoading) return;
		this.inputField.value = "";
		await this.sendMessageText(message);
	}
}
