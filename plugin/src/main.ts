import { Plugin, WorkspaceLeaf } from "obsidian";
import { ChatView, VIEW_TYPE_CHAT } from "./ChatView";

export default class VaultChatPlugin extends Plugin {
	async onload(): Promise<void> {
		// Register the chat view
		this.registerView(
			VIEW_TYPE_CHAT,
			(leaf: WorkspaceLeaf) => new ChatView(leaf)
		);

		// Add ribbon icon
		this.addRibbonIcon("message-circle", "Open Vault Chat", () => {
			this.activateView();
		});

		// Add command
		this.addCommand({
			id: "open-vault-chat",
			name: "Open Vault Chat",
			callback: () => {
				this.activateView();
			}
		});
	}

	async onunload(): Promise<void> {
		// Detach all leaves with our view
		this.app.workspace.detachLeavesOfType(VIEW_TYPE_CHAT);
	}

	private async activateView(): Promise<void> {
		const { workspace } = this.app;

		// Check if view already exists
		let leaf = workspace.getLeavesOfType(VIEW_TYPE_CHAT)[0];

		if (!leaf) {
			// Create new leaf in right sidebar
			const rightLeaf = workspace.getRightLeaf(false);
			if (rightLeaf) {
				leaf = rightLeaf;
				await leaf.setViewState({
					type: VIEW_TYPE_CHAT,
					active: true
				});
			}
		}

		// Reveal the leaf
		if (leaf) {
			workspace.revealLeaf(leaf);
		}
	}
}
