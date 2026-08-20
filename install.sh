#!/usr/bin/env bash
# =============================================================================
# Agent Harness — Installation Script
#
# Cross-platform installer for macOS and Ubuntu/Debian Linux.
# This script:
#   1. Detects the operating system
#   2. Installs system dependencies (Python 3.11+, git, curl)
#   3. Installs uv (Python package manager)
#   4. Installs pipx (isolated CLI tool installer)
#   5. Installs agent-harness globally via pipx
#   6. Runs initial setup (ah init)
#   7. Optionally installs as a system service
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<org>/agent-harness/main/install.sh | bash
#   # or
#   ./install.sh
#   # or with options:
#   ./install.sh --no-service    # Skip service installation
#   ./install.sh --from-source   # Install from local directory instead of git
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_URL="${AGENT_HARNESS_REPO:-https://github.com/<org>/agent-harness.git}"
BRANCH="${AGENT_HARNESS_BRANCH:-main}"
INSTALL_DIR="${AGENT_HARNESS_INSTALL_DIR:-/tmp/agent-harness-install}"
MIN_PYTHON_VERSION="3.11"

# CLI flags
INSTALL_SERVICE=true
FROM_SOURCE=false
SOURCE_DIR=""

# ---------------------------------------------------------------------------
# Colors and output helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}→ $*${RESET}"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --no-service)
                INSTALL_SERVICE=false
                shift
                ;;
            --from-source)
                FROM_SOURCE=true
                if [[ -n "${2:-}" && ! "$2" == --* ]]; then
                    SOURCE_DIR="$2"
                    shift
                fi
                shift
                ;;
            --repo)
                REPO_URL="$2"
                shift 2
                ;;
            --branch)
                BRANCH="$2"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat <<EOF
Agent Harness Installer

Usage: ./install.sh [OPTIONS]

Options:
  --no-service      Skip system service installation
  --from-source     Install from local source directory (default: current dir)
  --from-source /p  Install from specified source path
  --repo URL        Git repository URL (default: $REPO_URL)
  --branch NAME     Git branch to clone (default: $BRANCH)
  --help, -h        Show this help message

Environment Variables:
  AGENT_HARNESS_REPO         Override default repository URL
  AGENT_HARNESS_BRANCH       Override default branch
  AGENT_HARNESS_INSTALL_DIR  Override temp install directory

Examples:
  ./install.sh                        # Full installation from git
  ./install.sh --from-source          # Install from current directory
  ./install.sh --from-source ~/code/agent-harness
  ./install.sh --no-service           # Install without system service
EOF
}

# ---------------------------------------------------------------------------
# OS Detection
# ---------------------------------------------------------------------------
detect_os() {
    local os
    os="$(uname -s)"

    case "$os" in
        Darwin)
            OS="macos"
            PACKAGE_MANAGER="brew"
            ;;
        Linux)
            if [[ -f /etc/os-release ]]; then
                # shellcheck source=/dev/null
                . /etc/os-release
                case "$ID" in
                    ubuntu|debian|pop|linuxmint|elementary)
                        OS="linux"
                        PACKAGE_MANAGER="apt"
                        ;;
                    fedora|rhel|centos|rocky|alma)
                        OS="linux"
                        PACKAGE_MANAGER="dnf"
                        ;;
                    arch|manjaro)
                        OS="linux"
                        PACKAGE_MANAGER="pacman"
                        ;;
                    *)
                        OS="linux"
                        PACKAGE_MANAGER="unknown"
                        warn "Unknown Linux distribution: $ID. Will attempt apt-based installation."
                        PACKAGE_MANAGER="apt"
                        ;;
                esac
            else
                OS="linux"
                PACKAGE_MANAGER="apt"
            fi
            ;;
        *)
            error "Unsupported operating system: $os"
            error "This script supports macOS and Linux only."
            exit 1
            ;;
    esac

    info "Detected OS: ${BOLD}$OS${RESET} (package manager: $PACKAGE_MANAGER)"
}

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
command_exists() {
    command -v "$1" &>/dev/null
}

version_ge() {
    # Returns 0 (true) if $1 >= $2 (semver comparison)
    printf '%s\n%s' "$2" "$1" | sort -V | head -n1 | grep -qx "$2"
}

check_python() {
    local python_cmd=""
    local python_version=""

    # Try python3 first, then python
    for cmd in python3 python; do
        if command_exists "$cmd"; then
            python_version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            if [[ -n "$python_version" ]] && version_ge "$python_version" "$MIN_PYTHON_VERSION"; then
                python_cmd="$cmd"
                break
            fi
        fi
    done

    if [[ -z "$python_cmd" ]]; then
        return 1
    fi

    PYTHON_CMD="$python_cmd"
    PYTHON_VERSION="$python_version"
    return 0
}

# ---------------------------------------------------------------------------
# Installation functions
# ---------------------------------------------------------------------------
install_system_deps_macos() {
    step "Checking system dependencies (macOS)"

    # Check if Homebrew is installed
    if ! command_exists brew; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add brew to PATH for current session
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f "/usr/local/bin/brew" ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        success "Homebrew installed"
    else
        success "Homebrew already installed"
    fi

    # Install Python if needed
    if ! check_python; then
        info "Installing Python 3.11+..."
        brew install python@3.12
        success "Python installed"
    else
        success "Python $PYTHON_VERSION found ($PYTHON_CMD)"
    fi

    # Install git if needed
    if ! command_exists git; then
        info "Installing git..."
        brew install git
        success "git installed"
    fi
}

install_system_deps_linux() {
    step "Checking system dependencies (Linux)"

    local need_install=false
    local packages=()

    if ! check_python; then
        need_install=true
        case "$PACKAGE_MANAGER" in
            apt) packages+=(python3.12 python3.12-venv python3-pip) ;;
            dnf) packages+=(python3.12) ;;
            pacman) packages+=(python) ;;
        esac
    else
        success "Python $PYTHON_VERSION found ($PYTHON_CMD)"
    fi

    if ! command_exists git; then
        need_install=true
        packages+=(git)
    fi

    if ! command_exists curl; then
        need_install=true
        packages+=(curl)
    fi

    if [[ "$need_install" == true && ${#packages[@]} -gt 0 ]]; then
        info "Installing packages: ${packages[*]}"
        case "$PACKAGE_MANAGER" in
            apt)
                sudo apt-get update -qq
                sudo apt-get install -y -qq "${packages[@]}"
                ;;
            dnf)
                sudo dnf install -y -q "${packages[@]}"
                ;;
            pacman)
                sudo pacman -S --noconfirm "${packages[@]}"
                ;;
        esac
        success "System packages installed"
    fi

    # Re-check python after installation
    if ! check_python; then
        error "Python $MIN_PYTHON_VERSION+ is required but could not be installed."
        error "Please install Python manually and re-run this script."
        exit 1
    fi
}

install_uv() {
    step "Installing uv (Python package manager)"

    if command_exists uv; then
        local uv_version
        uv_version=$(uv --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        success "uv already installed (v$uv_version)"
        return
    fi

    info "Downloading and installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Add to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"

    if command_exists uv; then
        success "uv installed successfully"
    else
        error "uv installation failed. Please install manually:"
        error "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

install_pipx() {
    step "Installing pipx (isolated CLI installer)"

    if command_exists pipx; then
        success "pipx already installed"
        return
    fi

    # Install pipx via uv (preferred) or pip
    if command_exists uv; then
        info "Installing pipx via uv..."
        uv tool install pipx
    elif command_exists pip3; then
        info "Installing pipx via pip..."
        pip3 install --user pipx
    elif command_exists pip; then
        info "Installing pipx via pip..."
        pip install --user pipx
    else
        error "Cannot install pipx: neither uv nor pip found."
        exit 1
    fi

    # Ensure pipx bin dir is in PATH
    if command_exists pipx; then
        pipx ensurepath 2>/dev/null || true
    fi

    # Add common pipx paths to current session
    export PATH="$HOME/.local/bin:$PATH"

    if command_exists pipx; then
        success "pipx installed successfully"
    else
        warn "pipx installed but not in PATH. You may need to restart your shell."
        warn "Or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
}

get_source_directory() {
    # Determine where to install from
    if [[ "$FROM_SOURCE" == true ]]; then
        if [[ -n "$SOURCE_DIR" ]]; then
            INSTALL_DIR="$(cd "$SOURCE_DIR" && pwd)"
        else
            INSTALL_DIR="$(pwd)"
        fi

        if [[ ! -f "$INSTALL_DIR/pyproject.toml" ]]; then
            error "No pyproject.toml found in $INSTALL_DIR"
            error "Make sure you're in the agent-harness project directory."
            exit 1
        fi

        info "Installing from local source: $INSTALL_DIR"
    else
        step "Cloning agent-harness repository"
        if [[ -d "$INSTALL_DIR" ]]; then
            rm -rf "$INSTALL_DIR"
        fi
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        success "Repository cloned to $INSTALL_DIR"
    fi
}

install_agent_harness() {
    step "Installing Agent Harness via pipx"

    # Uninstall previous version if exists
    if pipx list 2>/dev/null | grep -q "agent-harness"; then
        info "Removing previous installation..."
        pipx uninstall agent-harness 2>/dev/null || true
    fi

    info "Installing agent-harness from $INSTALL_DIR..."
    pipx install "$INSTALL_DIR" --python "${PYTHON_CMD:-python3}"

    # Verify installation
    if command_exists ah; then
        local version
        version=$(ah --version 2>&1 || echo "unknown")
        success "Agent Harness installed: $version"
    else
        # Try adding pipx bin path
        export PATH="$HOME/.local/bin:$PATH"
        if command_exists ah; then
            success "Agent Harness installed (at ~/.local/bin/ah)"
        else
            error "Installation completed but 'ah' command not found in PATH."
            error "Try: export PATH=\"\$HOME/.local/bin:\$PATH\""
            exit 1
        fi
    fi
}

setup_env_file() {
    step "Setting up environment file"

    local harness_home="${AGENT_HARNESS_HOME:-$HOME/.agent-harness}"
    local env_file="$harness_home/.env"

    mkdir -p "$harness_home"

    if [[ -f "$env_file" ]]; then
        success "Environment file already exists: $env_file"
        return
    fi

    # Copy .env.example if available
    if [[ -f "$INSTALL_DIR/.env.example" ]]; then
        cp "$INSTALL_DIR/.env.example" "$env_file"
        info "Copied .env.example to $env_file"
    else
        # Create a minimal .env template
        cat > "$env_file" <<'ENVEOF'
# Agent Harness — Environment Variables
# Fill in the values below and restart the service.

# Required: Telegram bot token (from @BotFather)
TELEGRAM_TOKEN=

# Required: PostgreSQL connection string
DATABASE_URL=postgresql://harness:harness@localhost:5455/harness

# Required: Comma-separated Telegram user IDs allowed to use the bot
ALLOWED_USER_IDS=

# LLM Configuration (default: Ollama local)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=ollama_chat/llama3.2

# Optional: OpenAI or Anthropic (overrides Ollama)
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
ENVEOF
        info "Created template .env at $env_file"
    fi

    warn "Please edit $env_file with your credentials before starting the service."
}

run_init() {
    step "Running initial setup"

    if command_exists ah; then
        info "Running 'ah init'..."
        # Run non-interactively if possible, otherwise skip
        ah init 2>/dev/null || {
            warn "Interactive setup skipped. Run 'ah init' manually to configure."
        }
    fi
}

install_service() {
    if [[ "$INSTALL_SERVICE" != true ]]; then
        info "Skipping service installation (--no-service flag)"
        return
    fi

    step "Installing system service"

    echo ""
    echo -e "${YELLOW}Would you like to install Agent Harness as a system service?${RESET}"
    echo -e "${DIM}This will auto-start the bot on login and restart on failure.${RESET}"
    echo ""
    read -rp "Install as service? [y/N] " response

    case "$response" in
        [yY]|[yY][eE][sS])
            if command_exists ah; then
                ah install-service
            else
                warn "Cannot install service: 'ah' command not found."
            fi
            ;;
        *)
            info "Skipping service installation."
            info "You can install it later with: ah install-service"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Post-install PATH setup
# ---------------------------------------------------------------------------
setup_shell_path() {
    step "Ensuring PATH configuration"

    local shell_rc=""
    local path_line='export PATH="$HOME/.local/bin:$PATH"'
    local needs_update=false

    # Determine shell config file
    case "${SHELL:-}" in
        */zsh)  shell_rc="$HOME/.zshrc" ;;
        */bash)
            if [[ -f "$HOME/.bash_profile" ]]; then
                shell_rc="$HOME/.bash_profile"
            else
                shell_rc="$HOME/.bashrc"
            fi
            ;;
        */fish)
            # Fish uses a different syntax
            shell_rc="$HOME/.config/fish/config.fish"
            path_line='fish_add_path $HOME/.local/bin'
            ;;
        *)
            shell_rc="$HOME/.profile"
            ;;
    esac

    # Check if already in RC file
    if [[ -f "$shell_rc" ]] && grep -qF '.local/bin' "$shell_rc" 2>/dev/null; then
        success "PATH already configured in $shell_rc"
    else
        needs_update=true
    fi

    if [[ "$needs_update" == true && -n "$shell_rc" ]]; then
        echo "" >> "$shell_rc"
        echo "# Agent Harness (pipx)" >> "$shell_rc"
        echo "$path_line" >> "$shell_rc"
        success "Added PATH to $shell_rc"
        info "Run 'source $shell_rc' or open a new terminal to use 'ah'."
    fi
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
cleanup() {
    if [[ "$FROM_SOURCE" != true && -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    local harness_home="${AGENT_HARNESS_HOME:-$HOME/.agent-harness}"

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════${RESET}"
    echo -e "${GREEN}  Agent Harness — Installation Complete${RESET}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════${RESET}"
    echo ""
    echo -e "  Config directory:  ${CYAN}$harness_home${RESET}"
    echo -e "  Environment file:  ${CYAN}$harness_home/.env${RESET}"
    echo -e "  Logs directory:    ${CYAN}$harness_home/logs/${RESET}"
    echo ""
    echo -e "  ${BOLD}Next steps:${RESET}"
    echo -e "  1. Edit ${CYAN}$harness_home/.env${RESET} with your credentials"
    echo -e "  2. Start PostgreSQL: ${CYAN}docker compose up -d db${RESET}"
    echo -e "  3. Verify setup:     ${CYAN}ah doctor${RESET}"
    echo -e "  4. Start the bot:    ${CYAN}ah start${RESET}"
    echo ""
    echo -e "  ${BOLD}Useful commands:${RESET}"
    echo -e "    ${CYAN}ah --help${RESET}              — Show all commands"
    echo -e "    ${CYAN}ah status${RESET}              — Check bot status"
    echo -e "    ${CYAN}ah install-service${RESET}     — Install as system service"
    echo -e "    ${CYAN}ah uninstall-service${RESET}   — Remove system service"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo -e "${BOLD}${CYAN}"
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║     Agent Harness — Installer             ║"
    echo "  ║     Multi-agent AI System                 ║"
    echo "  ╚═══════════════════════════════════════════╝"
    echo -e "${RESET}"

    parse_args "$@"
    detect_os

    # Install system dependencies
    if [[ "$OS" == "macos" ]]; then
        install_system_deps_macos
    else
        install_system_deps_linux
    fi

    # Re-check Python after potential installation
    check_python || {
        error "Python $MIN_PYTHON_VERSION+ is required but not found."
        exit 1
    }

    # Install tooling
    install_uv
    install_pipx

    # Get source code
    get_source_directory

    # Install agent-harness
    install_agent_harness

    # Setup
    setup_env_file
    setup_shell_path

    # Optional: install as service
    install_service

    # Cleanup temp files
    cleanup

    # Done
    print_summary
}

# Trap for cleanup on error
trap 'error "Installation failed. Check the output above for details."; cleanup' ERR

main "$@"
