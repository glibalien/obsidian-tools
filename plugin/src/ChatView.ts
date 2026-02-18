import { ItemView, MarkdownRenderer, WorkspaceLeaf, requestUrl } from "obsidian";

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
		this.addMessage("assistant", "Hello! I'm your vault assistant. How can I help you today?");
	}

	async onClose(): Promise<void> {
		// Cleanup if needed
	}

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

	private showLoading(): HTMLElement {
		const loadingEl = this.messagesContainer.createDiv({
			cls: "chat-message chat-message-assistant chat-loading"
		});
		loadingEl.createDiv({ cls: "chat-message-content", text: "Thinking..." });
		this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
		return loadingEl;
	}

	private getActiveFilePath(): string | null {
		const activeFile = this.app.workspace.getActiveFile();
		return activeFile?.path ?? null;
	}

	private async sendMessage(): Promise<void> {
		const message = this.inputField.value.trim();
		if (!message || this.isLoading) return;

		this.inputField.value = "";
		this.isLoading = true;
		this.sendButton.disabled = true;

		// Add user message
		this.addMessage("user", message);

		// Show loading
		const loadingEl = this.showLoading();

		try {
			const response = await requestUrl({
				url: "http://127.0.0.1:8000/chat",
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					message: message,
					session_id: this.sessionId,
					active_file: this.getActiveFilePath()
				})
			});

			// Remove loading indicator
			loadingEl.remove();

			const data = response.json;
			this.sessionId = data.session_id;
			this.addMessage("assistant", data.response);

		} catch (error) {
			loadingEl.remove();
			const errorMessage = error instanceof Error ? error.message : "Failed to connect to server";
			this.addMessage("assistant", `Error: ${errorMessage}. Is the API server running?`);
		} finally {
			this.isLoading = false;
			this.sendButton.disabled = false;
			this.inputField.focus();
		}
	}
}
