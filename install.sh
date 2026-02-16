#!/usr/bin/env bash
set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────────

MIN_MINOR=11
MAX_MINOR=13
PREFERRED_VERSION="3.12.8"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Helpers ──────────────────────────────────────────────────────────────────

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m==> %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m==> %s\033[0m\n' "$*" >&2; }
# Print prompt to stderr to avoid buffering issues, and read from /dev/tty
# in case stdin is redirected (e.g., curl | bash)
ask()   { printf '\033[1;32m==> %s\033[0m ' "$1" >&2; }
read_input() { read -r "$@" </dev/tty; }

detect_os() {
    case "$(uname -s)" in
        Darwin) echo "macos" ;;
        Linux)  echo "linux" ;;
        *)      error "Unsupported OS: $(uname -s)"; exit 1 ;;
    esac
}

# Check if a Python binary is compatible (>=3.11, <=3.13).
# Returns 0 if compatible, 1 otherwise.
check_python_version() {
    local py="$1"
    local version
    version="$("$py" --version 2>/dev/null | awk '{print $2}')" || return 1
    local minor
    minor="$(echo "$version" | cut -d. -f2)"
    [[ "$minor" -ge "$MIN_MINOR" && "$minor" -le "$MAX_MINOR" ]]
}

# Search PATH for a compatible Python binary.
# Prints the path if found, returns 1 otherwise.
find_compatible_python() {
    local candidates=(python3.12 python3.13 python3.11 python3)
    for cmd in "${candidates[@]}"; do
        local path
        path="$(command -v "$cmd" 2>/dev/null)" || continue
        if check_python_version "$path"; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

# If the Python binary is a pyenv shim, resolve the real binary path.
resolve_real_python() {
    local py="$1"
    local resolved="$py"

    # Detect pyenv shim
    if [[ "$py" == *".pyenv/shims/"* ]] || [[ "$(file "$py" 2>/dev/null)" == *"script"* && -d "$HOME/.pyenv" ]]; then
        local version
        version="$("$py" --version 2>/dev/null | awk '{print $2}')"
        local minor
        minor="$(echo "$version" | cut -d. -f2)"

        if command -v pyenv &>/dev/null; then
            resolved="$(pyenv which "python3.${minor}" 2>/dev/null)" || resolved="$py"
        fi
    fi

    # Final realpath resolution
    if command -v realpath &>/dev/null; then
        resolved="$(realpath "$resolved")"
    elif command -v readlink &>/dev/null; then
        resolved="$(readlink -f "$resolved" 2>/dev/null)" || true
    fi

    echo "$resolved"
}

# Offer to install a compatible Python via pyenv.
offer_pyenv_install() {
    local os="$1"

    if command -v pyenv &>/dev/null; then
        warn "No compatible Python (3.${MIN_MINOR}–3.${MAX_MINOR}) found."
        ask "Install Python ${PREFERRED_VERSION} via pyenv? [Y/n]"
        read_input answer
        if [[ "${answer:-Y}" =~ ^[Yy]$ ]]; then
            pyenv install "$PREFERRED_VERSION"
            pyenv local "$PREFERRED_VERSION"
            local py
            py="$(pyenv which python3.12)"
            echo "$py"
            return 0
        fi
    elif [[ "$os" == "macos" ]]; then
        if command -v brew &>/dev/null; then
            warn "No compatible Python found. pyenv is recommended for managing Python versions."
            ask "Install pyenv via Homebrew, then install Python ${PREFERRED_VERSION}? [Y/n]"
            read_input answer
            if [[ "${answer:-Y}" =~ ^[Yy]$ ]]; then
                brew install pyenv
                eval "$(pyenv init -)"
                pyenv install "$PREFERRED_VERSION"
                pyenv local "$PREFERRED_VERSION"
                local py
                py="$(pyenv which python3.12)"
                echo "$py"
                return 0
            fi
        else
            error "No compatible Python found."
            echo "Install pyenv (https://github.com/pyenv/pyenv) or Python 3.12 from python.org." >&2
        fi
    else
        error "No compatible Python (3.${MIN_MINOR}–3.${MAX_MINOR}) found."
        echo "Options:" >&2
        echo "  - Install pyenv: https://github.com/pyenv/pyenv#installation" >&2
        echo "  - Install from your distro: sudo apt install python3.12 (Debian/Ubuntu)" >&2
        echo "                              sudo dnf install python3.12 (Fedora)" >&2
    fi

    return 1
}

# Prompt for a .env value. Usage: prompt_value "LABEL" "VAR_NAME" "default" "required"
prompt_value() {
    local label="$1" var="$2" default="${3:-}" required="${4:-false}"

    while true; do
        if [[ -n "$default" ]]; then
            ask "${label} [${default}]:"
        else
            ask "${label}:"
        fi
        read_input value
        value="${value:-$default}"

        if [[ -z "$value" && "$required" == "true" ]]; then
            warn "This value is required."
            continue
        fi
        echo "$value"
        return
    done
}

# Prompt for VAULT_PATH with directory validation.
prompt_vault_path() {
    while true; do
        ask "Path to your Obsidian vault:"
        read_input vault_path
        vault_path="${vault_path/#\~/$HOME}"

        if [[ -z "$vault_path" ]]; then
            warn "This value is required."
        elif [[ ! -d "$vault_path" ]]; then
            warn "Directory not found: ${vault_path}"
        else
            echo "$vault_path"
            return
        fi
    done
}

# Write a key=value pair into the .env file.
set_env_value() {
    local key="$1" value="$2" env_file="${PROJECT_DIR}/.env"

    if grep -q "^${key}=" "$env_file" 2>/dev/null; then
        # Use different sed syntax for macOS vs Linux
        if [[ "$(uname -s)" == "Darwin" ]]; then
            sed -i '' "s|^${key}=.*|${key}=${value}|" "$env_file"
        else
            sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
        fi
    else
        echo "${key}=${value}" >> "$env_file"
    fi
}

# ── Service installation ────────────────────────────────────────────────────

install_services_macos() {
    local venv_python="$1" interval_min="$2"
    local username
    username="$(whoami)"
    local interval_sec=$((interval_min * 60))
    local launch_agents="$HOME/Library/LaunchAgents"

    info "Installing launchd services..."
    mkdir -p "$launch_agents"

    for plist in com.obsidian-tools.api.plist com.obsidian-tools.indexer.plist; do
        local src="${PROJECT_DIR}/services/launchd/${plist}"
        local dest="${launch_agents}/${plist}"

        # Unload if already loaded
        launchctl unload "$dest" 2>/dev/null || true

        sed \
            -e "s|__VENV_PYTHON__|${venv_python}|g" \
            -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
            -e "s|__USERNAME__|${username}|g" \
            -e "s|__INDEX_INTERVAL_SEC__|${interval_sec}|g" \
            "$src" > "$dest"

        launchctl load "$dest"
    done

    echo "  Installed: ${launch_agents}/com.obsidian-tools.api.plist"
    echo "  Installed: ${launch_agents}/com.obsidian-tools.indexer.plist"
}

install_services_linux() {
    local venv_python="$1" interval_min="$2"
    local user_units="$HOME/.config/systemd/user"

    info "Installing systemd user services..."
    mkdir -p "$user_units"

    # Install service units
    for unit in obsidian-tools-api.service obsidian-tools-indexer.service; do
        sed \
            -e "s|__VENV_PYTHON__|${venv_python}|g" \
            -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
            "${PROJECT_DIR}/services/systemd/${unit}" > "${user_units}/${unit}"
        echo "  Installed: ${user_units}/${unit}"
    done

    # Install timer
    local timer="obsidian-tools-indexer-scheduler.timer"
    sed \
        -e "s|__INDEX_INTERVAL__|${interval_min}|g" \
        "${PROJECT_DIR}/services/systemd/${timer}" > "${user_units}/${timer}"
    echo "  Installed: ${user_units}/${timer}"

    systemctl --user daemon-reload
    systemctl --user enable --now obsidian-tools-api.service
    systemctl --user enable --now obsidian-tools-indexer-scheduler.timer

    echo ""
    info "Hint: to keep services running after logout, run:"
    echo "  sudo loginctl enable-linger $USER"
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "  Obsidian Tools Installer"
    echo "  ========================"
    echo ""

    local os
    os="$(detect_os)"
    info "Detected OS: ${os}"

    # ── Step 1: Find Python ──────────────────────────────────────────────

    info "Looking for a compatible Python (3.${MIN_MINOR}–3.${MAX_MINOR})..."
    local python_path=""

    if python_path="$(find_compatible_python)"; then
        info "Found: ${python_path} ($("$python_path" --version))"
    else
        python_path="$(offer_pyenv_install "$os")" || {
            error "Cannot continue without a compatible Python."
            exit 1
        }
        info "Installed: ${python_path} ($("$python_path" --version))"
    fi

    # Resolve real binary (not pyenv shim)
    local real_python
    real_python="$(resolve_real_python "$python_path")"
    if [[ "$real_python" != "$python_path" ]]; then
        info "Resolved real binary: ${real_python}"
    fi

    # ── Step 2: Create virtual environment ───────────────────────────────

    local venv_python="${PROJECT_DIR}/.venv/bin/python"

    if [[ -f "$venv_python" ]]; then
        if check_python_version "$venv_python"; then
            info "Existing venv is compatible ($("$venv_python" --version)), keeping it."
        else
            warn "Existing venv uses $("$venv_python" --version 2>/dev/null || echo 'unknown version'), recreating..."
            rm -rf "${PROJECT_DIR}/.venv"
            "$real_python" -m venv "${PROJECT_DIR}/.venv"
            info "Created virtual environment."
        fi
    else
        "$real_python" -m venv "${PROJECT_DIR}/.venv"
        info "Created virtual environment."
    fi

    # ── Step 3: Install dependencies ─────────────────────────────────────

    info "Installing Python dependencies..."
    "${PROJECT_DIR}/.venv/bin/pip" install --upgrade pip --quiet
    "${PROJECT_DIR}/.venv/bin/pip" install -r "${PROJECT_DIR}/requirements.txt" --quiet
    info "Dependencies installed."

    # ── Step 4: Configure .env ───────────────────────────────────────────

    if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
        cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
        info "Created .env from .env.example"
    else
        info "Existing .env found, updating values."
    fi

    if [[ ! -f "${PROJECT_DIR}/system_prompt.txt" ]]; then
        cp "${PROJECT_DIR}/system_prompt.txt.example" "${PROJECT_DIR}/system_prompt.txt"
        info "Created system_prompt.txt from system_prompt.txt.example"
        warn "Edit system_prompt.txt to match your vault's folder structure and frontmatter conventions."
    fi

    echo ""
    info "Configure your environment:"
    echo ""

    local api_key vault_path fireworks_model chroma_path api_port index_interval

    api_key="$(prompt_value "Fireworks API key" "FIREWORKS_API_KEY" "" "true")"
    fireworks_model="$(prompt_value "Fireworks model (default: DeepSeek V3.1)" "FIREWORKS_MODEL" "accounts/fireworks/models/deepseek-v3p1")"
    vault_path="$(prompt_vault_path)"
    chroma_path="$(prompt_value "ChromaDB path" "CHROMA_PATH" "./.chroma_db")"
    api_port="$(prompt_value "API server port" "API_PORT" "8000")"
    index_interval="$(prompt_value "Index interval (minutes)" "INDEX_INTERVAL" "60")"

    set_env_value "FIREWORKS_API_KEY" "$api_key"
    set_env_value "FIREWORKS_MODEL" "$fireworks_model"
    set_env_value "VAULT_PATH" "$vault_path"
    set_env_value "CHROMA_PATH" "$chroma_path"
    set_env_value "API_PORT" "$api_port"
    set_env_value "INDEX_INTERVAL" "$index_interval"

    info ".env configured."

    # ── Step 5: Install services ─────────────────────────────────────────

    echo ""
    ask "Install background services (API server + vault indexer)? [Y/n]"
    read_input install_svc
    if [[ "${install_svc:-Y}" =~ ^[Yy]$ ]]; then
        case "$os" in
            macos) install_services_macos "$venv_python" "$index_interval" ;;
            linux) install_services_linux "$venv_python" "$index_interval" ;;
        esac
    else
        info "Skipped service installation."
    fi

    # ── Step 6: Initial index ────────────────────────────────────────────

    echo ""
    ask "Run the vault indexer now? (recommended for first install) [Y/n]"
    read_input run_index
    if [[ "${run_index:-Y}" =~ ^[Yy]$ ]]; then
        info "Indexing vault (this may take a minute)..."
        "${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/src/index_vault.py"
        info "Indexing complete."
    fi

    # ── Summary ──────────────────────────────────────────────────────────

    echo ""
    echo "  ========================"
    echo "  Installation complete!"
    echo "  ========================"
    echo ""
    echo "  Project:     ${PROJECT_DIR}"
    echo "  Python:      ${real_python}"
    echo "  Venv:        ${PROJECT_DIR}/.venv/"
    echo "  Vault:       ${vault_path}"
    echo "  API port:    ${api_port}"
    echo "  Index every: ${index_interval} min"
    echo ""

    if [[ "${install_svc:-Y}" =~ ^[Yy]$ ]]; then
        case "$os" in
            macos)
                echo "  Services (launchd):"
                echo "    Check:   launchctl list | grep obsidian-tools"
                echo "    Logs:    tail -f ~/Library/Logs/obsidian-tools-api.log"
                echo "    Stop:    launchctl unload ~/Library/LaunchAgents/com.obsidian-tools.api.plist"
                echo "    Start:   launchctl load ~/Library/LaunchAgents/com.obsidian-tools.api.plist"
                ;;
            linux)
                echo "  Services (systemd):"
                echo "    Status:  systemctl --user status obsidian-tools-api"
                echo "    Logs:    journalctl --user -u obsidian-tools-api -f"
                echo "    Stop:    systemctl --user stop obsidian-tools-api"
                echo "    Start:   systemctl --user start obsidian-tools-api"
                echo "    Timer:   systemctl --user status obsidian-tools-indexer-scheduler.timer"
                ;;
        esac
    fi

    echo ""
    echo "  Uninstall:   ./uninstall.sh"
    echo ""
}

main "$@"
