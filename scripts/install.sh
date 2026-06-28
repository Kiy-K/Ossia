#!/usr/bin/env bash
# =============================================================================
# Ossia — One-command install script
# =============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Kiy-K/Ossia/master/scripts/install.sh | bash
#
# Or from a local checkout:
#   bash scripts/install.sh
#
# What it does:
#   1. Checks prerequisites (git, python3 3.11+, uv/pip, bun)
#   2. Clones the Ossia repo to ~/.ossia (or updates if already present)
#   3. Runs `make ossia-setup` — installs Python deps, TUI deps, creates .env
#   4. Optionally adds the `ossia` command to PATH
#   5. Prints next steps
#
# Flags:
#   --help          Show this help message
#   --no-path       Skip adding ossia to PATH
#   --dir <path>    Install to a custom directory (default: ~/.ossia)
#   --branch <ref>  Git branch/tag to clone (default: master)
#   --server-only   Skip TUI (Bun) dependency installation
# =============================================================================

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Defaults ─────────────────────────────────────────────────────────────────
INSTALL_DIR="${HOME}/.ossia"
GIT_BRANCH="master"
MODIFY_PATH=true
SERVER_ONLY=false

# ── Parse flags ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help)
            sed -n '/^# =/,/^$/p' "$0" | grep -v '^#!/' | sed 's/^# //; s/^#$//'
            exit 0
            ;;
        --no-path) MODIFY_PATH=false; shift ;;
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --branch) GIT_BRANCH="$2"; shift 2 ;;
        --server-only) SERVER_ONLY=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Header ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      ${BOLD}Ossia — AI Support Agent${NC}${BLUE}         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Helper functions ─────────────────────────────────────────────────────────
info()  { echo -e "${CYAN}  →${NC} $1"; }
ok()    { echo -e "${GREEN}  ✓${NC} $1"; }
warn()  { echo -e "${YELLOW}  ⚠${NC} $1"; }
fail()  { echo -e "${RED}  ✗${NC} $1"; exit 1; }

check_cmd() {
    if command -v "$1" &>/dev/null; then
        ok "$1 found: $(command -v "$1")"
        return 0
    else
        warn "$1 not found"
        return 1
    fi
}

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
echo -e "${BOLD}Checking prerequisites...${NC}"

# Git
check_cmd git || fail "Git is required. Install: https://git-scm.com/downloads"

# Python 3.11+
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        full_ver=$("$cmd" --version 2>&1)
        # Extract major.minor — works on both GNU grep and BSD/macOS
        ver=$(echo "$full_ver" | awk '{for(i=1;i<=NF;i++){if($i~/^[0-9]+\.[0-9]+/){print $i}}}')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -gt 3 ]] || [[ "$major" -eq 3 && "$minor" -ge 11 ]] 2>/dev/null; then
            PYTHON_CMD="$cmd"
            ok "$cmd $full_ver"
            break
        fi
    fi
done
if [[ -z "$PYTHON_CMD" ]]; then
    fail "Python 3.11+ is required. Install: https://www.python.org/downloads/"
fi

# uv (preferred) or pip
UV_CMD=""
if check_cmd uv; then
    UV_CMD="uv"
    ok "Using uv (fast package manager)"
else
    if check_cmd pip3; then
        warn "uv not found — falling back to pip3 (slower). Install uv for faster installs:"
        warn "  curl -fsSL https://astral.sh/uv/install.sh | bash"
    else
        warn "pip3 not found — will use python -m pip"
    fi
fi

# Bun (optional, only for TUI)
if [[ "$SERVER_ONLY" == "false" ]]; then
    if check_cmd bun; then
        BUN_CMD="bun"
    else
        warn "Bun is optional — needed only for the Terminal UI (TUI)."
        warn "  Install: curl -fsSL https://bun.sh/install | bash"
        warn "  Or run with --server-only to skip the TUI."
        echo ""
        # Don't fail — user can still run backend-only
    fi
fi

echo ""

# ── Step 2: Clone / update repo ──────────────────────────────────────────────
echo -e "${BOLD}Setting up Ossia in ${INSTALL_DIR}...${NC}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git fetch origin
    git checkout "$GIT_BRANCH"
    git pull origin "$GIT_BRANCH"
    ok "Updated to latest $(git describe --tags --always 2>/dev/null || echo 'commit')"
else
    info "Cloning repository (branch: ${GIT_BRANCH})..."
    git clone --branch "$GIT_BRANCH" --depth 1 \
        https://github.com/Kiy-K/Ossia.git "$INSTALL_DIR"
    ok "Repository cloned"
fi

cd "$INSTALL_DIR"
echo ""

# ── Step 3: Install dependencies ─────────────────────────────────────────────
echo -e "${BOLD}Installing dependencies...${NC}"

info "Creating Python virtual environment..."
_install_log=$(mktemp)
if [[ -n "$UV_CMD" ]]; then
    "$UV_CMD" venv 2>/dev/null || true
    if ! "$UV_CMD" pip install -e ".[dev,notebook]" >"$_install_log" 2>&1; then
        cat "$_install_log"
        fail "Python dependency installation failed"
    fi
else
    "$PYTHON_CMD" -m venv .venv 2>/dev/null || true
    if ! .venv/bin/pip install -e ".[dev,notebook]" >"$_install_log" 2>&1; then
        cat "$_install_log"
        fail "Python dependency installation failed"
    fi
fi
rm -f "$_install_log"
ok "Python dependencies installed"

if [[ "$SERVER_ONLY" == "false" ]] && command -v bun &>/dev/null; then
    info "Installing TUI dependencies..."
    _tui_log=$(mktemp)
    if ! (cd src/tui && bun install >"$_tui_log" 2>&1); then
        cat "$_tui_log"
        warn "TUI dependency installation failed — run 'cd src/tui && bun install' manually"
    else
        ok "TUI dependencies installed"
    fi
    rm -f "$_tui_log"
fi

echo ""

# ── Step 4: Create .env ──────────────────────────────────────────────────────
echo -e "${BOLD}Setting up environment...${NC}"

if [[ -f .env ]]; then
    warn ".env already exists — keeping existing configuration"
else
    cp .env.example .env
    ok "Created .env from .env.example"
    info "Edit ${INSTALL_DIR}/.env to set your API keys:"
    info "  OSSIA_API_KEY — secret for authenticating requests"
    info "  OPENROUTER_API_KEY or another provider key"
fi

echo ""

# ── Step 5: Add to PATH ──────────────────────────────────────────────────────
if [[ "$MODIFY_PATH" == "true" ]]; then
    # Symlink the ossia command into ~/.local/bin for PATH access
    LOCAL_BIN="${HOME}/.local/bin"
    mkdir -p "$LOCAL_BIN"

    OSSIA_CMD="${INSTALL_DIR}/.venv/bin/ossia"
    SYMLINK="${LOCAL_BIN}/ossia"

    if [[ -L "$SYMLINK" ]] || [[ ! -e "$SYMLINK" ]]; then
        ln -sf "$OSSIA_CMD" "$SYMLINK"
        ok "Linked ossia command to ${SYMLINK}"
    else
        warn "${SYMLINK} already exists and is not a symlink — skipping"
    fi

    # Add ~/.local/bin to PATH if not already there
    SHELL_CONFIG=""
    if [[ -n "$BASH_VERSION" ]]; then
        [[ -f "${HOME}/.bashrc" ]] && SHELL_CONFIG="${HOME}/.bashrc"
    elif [[ -n "$ZSH_VERSION" ]]; then
        [[ -f "${HOME}/.zshrc" ]] && SHELL_CONFIG="${HOME}/.zshrc"
    fi

    if [[ -n "$SHELL_CONFIG" ]]; then
        if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
            echo "" >> "$SHELL_CONFIG"
            echo '# Add ~/.local/bin to PATH (Ossia)' >> "$SHELL_CONFIG"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_CONFIG"
            ok "Added ~/.local/bin to PATH in ${SHELL_CONFIG}"
        fi
    else
        warn "Could not detect shell config — add ~/.local/bin to your PATH manually"
    fi
fi

echo ""

# ── Done ─────────────────────────────────────────────────────────────────────
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo -e "  ${CYAN}ossia${NC} command installed at:"
echo -e "    ${INSTALL_DIR}"
echo ""

echo -e "${BOLD}Quick start:${NC}"
echo ""
echo -e "  1. Edit your API keys:"
echo -e "     ${CYAN}vim ${INSTALL_DIR}/.env${NC}"
echo ""
echo -e "  2. Start the server:"
echo -e "     ${CYAN}cd ${INSTALL_DIR} && make dev${NC}"
echo ""
echo -e "  3. Or with the unified CLI (backend + TUI):"
echo -e "     ${CYAN}cd ${INSTALL_DIR} && make ossia${NC}"
echo ""
echo -e "  4. Test it:"
echo -e "     ${CYAN}curl -X POST http://localhost:8000/v1/chat \\${NC}"
echo -e "     ${CYAN}  -H \"X-API-Key: dev\" \\${NC}"
echo -e "     ${CYAN}  -H \"Content-Type: application/json\" \\${NC}"
echo -e "     ${CYAN}  -d '{\"message\": \"Hello!\"}'${NC}"
echo ""
echo -e "${BOLD}Docs:${NC}  ${CYAN}https://github.com/Kiy-K/Ossia${NC}"
echo ""
