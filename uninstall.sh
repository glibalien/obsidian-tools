#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m==> %s\033[0m\n' "$*"; }
ask()   { printf '\033[1;32m==> %s\033[0m ' "$1"; }

detect_os() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      echo "unknown" ;;
    esac
}

uninstall_services_macos() {
    local launch_agents="$HOME/Library/LaunchAgents"
    local removed=0

    for plist in com.obsidian-tools.api.plist com.obsidian-tools.indexer.plist; do
        local path="${launch_agents}/${plist}"
        if [[ -f "$path" ]]; then
            launchctl unload "$path" 2>/dev/null || true
            rm -f "$path"
            echo "  Removed: ${path}"
            removed=$((removed + 1))
        fi
    done

    if [[ "$removed" -eq 0 ]]; then
        info "No launchd services found."
    else
        info "Removed ${removed} launchd service(s)."
    fi
}

uninstall_services_linux() {
    local user_units="$HOME/.config/systemd/user"
    local removed=0

    # Stop and disable
    for unit in obsidian-tools-api.service obsidian-tools-indexer-scheduler.timer; do
        if systemctl --user is-active "$unit" &>/dev/null; then
            systemctl --user stop "$unit" 2>/dev/null || true
        fi
        if systemctl --user is-enabled "$unit" &>/dev/null; then
            systemctl --user disable "$unit" 2>/dev/null || true
        fi
    done

    # Remove unit files
    for file in obsidian-tools-api.service obsidian-tools-indexer.service obsidian-tools-indexer-scheduler.timer; do
        local path="${user_units}/${file}"
        if [[ -f "$path" ]]; then
            rm -f "$path"
            echo "  Removed: ${path}"
            removed=$((removed + 1))
        fi
    done

    if [[ "$removed" -gt 0 ]]; then
        systemctl --user daemon-reload
        info "Removed ${removed} systemd unit(s)."
    else
        info "No systemd units found."
    fi
}

main() {
    echo ""
    echo "  Obsidian Tools Uninstaller"
    echo "  =========================="
    echo ""

    local os
    os="$(detect_os)"

    # ── Remove services ──────────────────────────────────────────────────

    info "Removing background services..."
    case "$os" in
        macos) uninstall_services_macos ;;
        linux) uninstall_services_linux ;;
        *)     warn "Unknown OS, skipping service removal." ;;
    esac

    # ── Remove venv ──────────────────────────────────────────────────────

    if [[ -d "${PROJECT_DIR}/.venv" ]]; then
        echo ""
        ask "Remove virtual environment (.venv)? [y/N]"
        read -r answer
        if [[ "${answer:-N}" =~ ^[Yy]$ ]]; then
            rm -rf "${PROJECT_DIR}/.venv"
            info "Removed .venv/"
        else
            info "Kept .venv/"
        fi
    fi

    # ── Summary ──────────────────────────────────────────────────────────

    echo ""
    info "Uninstall complete."
    echo ""
    echo "  Preserved:"
    echo "    .env          (your configuration)"
    echo "    .chroma_db/   (your search index)"
    echo ""
    echo "  To remove everything, delete the project directory:"
    echo "    rm -rf ${PROJECT_DIR}"
    echo ""
}

main "$@"
