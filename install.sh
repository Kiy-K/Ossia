#!/bin/sh
# =============================================================================
# Ossia — package-runner installer (Kilo / DeepAgents Code style)
# =============================================================================
# One-command install of the latest released version of Ossia. Detects
# platform + Python tooling, downloads the source tarball from the
# GitHub release, and installs the `ossia` command into a uv tool
# environment (or a pip venv fallback).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Kiy-K/Ossia/master/install.sh | bash
#
#   # Pin a specific version:
#   curl -fsSL ... | OSSIA_VERSION=0.9.0 bash
#
#   # Custom install location (default: $XDG_BIN_HOME or $HOME/.local/bin):
#   curl -fsSL ... | OSSIA_INSTALL_DIR=/usr/local/bin bash
#
# After install:
#   ossia --help        # shows usage
#   ossia --port 9000   # start backend + TUI on a custom port
# =============================================================================

set -eu

# ── Configuration (overridable via env) ─────────────────────────────────────
OSSIA_REPO="${OSSIA_REPO:-Kiy-K/Ossia}"
OSSIA_VERSION="${OSSIA_VERSION:-}"
OSSIA_INSTALL_DIR="${OSSIA_INSTALL_DIR:-}"
OSSIA_EXTRAS="${OSSIA_EXTRAS:-dev,notebook}"

# ── Colors (no-op when piped) ────────────────────────────────────────────────
if [ -t 1 ]; then
    BOLD="\033[1m"; DIM="\033[2m"; GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; RESET="\033[0m"
else
    BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
say()  { printf "%b\n" "$*" >&2; }
warn() { say "${YELLOW}$*${RESET}"; }
fail() { say "${RED}$*${RESET}"; exit 1; }
ok()   { say "${GREEN}$*${RESET}"; }

# ── Pick install location ───────────────────────────────────────────────────
pick_install_dir() {
    if [ -n "$OSSIA_INSTALL_DIR" ]; then
        echo "$OSSIA_INSTALL_DIR"
        return
    fi
    if [ -n "${XDG_BIN_HOME:-}" ]; then
        echo "$XDG_BIN_HOME"
        return
    fi
    echo "$HOME/.local/bin"
}

# ── Tool detection ──────────────────────────────────────────────────────────
have_uv()  { command -v uv    >/dev/null 2>&1; }
have_pip() { command -v pip3  >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; }
have_curl(){ command -v curl  >/dev/null 2>&1; }
have_jq()  { command -v jq    >/dev/null 2>&1; }

# ── Get latest version from GitHub API ──────────────────────────────────────
latest_version() {
    if [ -n "$OSSIA_VERSION" ]; then
        echo "$OSSIA_VERSION" | sed 's/^v//'
        return
    fi
    have_curl || fail "ERROR: curl is required to fetch the latest version."
    # Use jq if available, fall back to grep/sed.
    local api="https://api.github.com/repos/${OSSIA_REPO}/releases/latest"
    if have_curl && have_jq; then
        curl -fsSL "$api" | jq -r '.tag_name // empty' | sed 's/^v//'
    else
        curl -fsSL "$api" \
            | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
            | head -1 \
            | sed -E 's/.*"v?([^"]+)".*/\1/'
    fi
}

# ── Download source tarball ──────────────────────────────────────────────────
download_tarball() {
    local version="$1" dest="$2"
    have_curl || fail "ERROR: curl is required to download the tarball."
    local url="https://github.com/${OSSIA_REPO}/archive/refs/tags/v${version}.tar.gz"
    say "  ${DIM}→ $url${RESET}"
    curl -fsSL "$url" -o "$dest" || fail "ERROR: download failed from $url"
}

# ── Install with uv (preferred) or pip (fallback) ───────────────────────────
install_ossia() {
    local tarball="$1"
    if have_uv; then
        say "  using uv (isolated tool environment)"
        # --force replaces an existing install; --no-cache avoids
        # stale wheel caches when testing prereleases.
        uv tool install --force --no-cache "ossia @ ${tarball}[${OSSIA_EXTRAS}]" \
            || fail "ERROR: uv tool install failed. Run with OSSIA_NO_UV=1 to use pip."
        # uv's bin dir; defaults to ~/.local/bin on Linux/macOS.
        uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"
    elif have_pip; then
        say "  using pip (uv not detected; install uv for a faster path)"
        local venv="${OSSIA_HOME:-$HOME/.ossia}"
        python3 -m venv "$venv"
        "$venv/bin/pip" install --upgrade "${tarball}[${OSSIA_EXTRAS}]" \
            || fail "ERROR: pip install failed."
        echo "$venv/bin"
    else
        fail "ERROR: need either 'uv' or 'pip' on PATH. Install uv: https://docs.astral.sh/uv/"
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    say "${BOLD}Ossia installer${RESET}"
    say ""

    local version
    version=$(latest_version)
    [ -n "$version" ] || fail "ERROR: could not determine latest version (set OSSIA_VERSION to override)."
    say "  ${DIM}latest:${RESET}  v${version}"

    local install_dir
    install_dir=$(pick_install_dir)
    say "  ${DIM}target:${RESET}  ${install_dir}"
    say ""

    local tarball
    tarball=$(mktemp -t "ossia-${version}.XXXXXX.tar.gz")
    # Ensure cleanup on exit.
    trap 'rm -f "$tarball" 2>/dev/null || true' EXIT INT TERM

    say "${BOLD}→${RESET} downloading tarball"
    download_tarball "$version" "$tarball"
    ok "  downloaded"

    say "${BOLD}→${RESET} installing (extras: ${OSSIA_EXTRAS})"
    local bin_dir
    bin_dir=$(install_ossia "$tarball")
    ok "  installed"

    # Symlink the entry point into the requested PATH location.
    if [ -n "$bin_dir" ] && [ "$bin_dir" != "$install_dir" ] && [ -x "$bin_dir/ossia" ]; then
        mkdir -p "$install_dir"
        ln -sf "$bin_dir/ossia" "$install_dir/ossia"
        ok "  linked → ${install_dir}/ossia"
    fi

    say ""
    ok "${BOLD}ossia ${version} installed${RESET}"

    # PATH nudge (only when install_dir isn't on PATH already).
    case ":$PATH:" in
        *":$install_dir:"*) ;;
        *)
            say ""
            warn "  ${install_dir} is not on your PATH."
            warn "  Add it to your shell profile:"
            warn "    export PATH=\"${install_dir}:\$PATH\""
            ;;
    esac

    say ""
    say "  ${BOLD}next steps:${RESET}"
    say "    export OSSIA_API_KEY=dev      # any non-empty value for dev"
    say "    ossia --help                  # show CLI options"
    say "    ossia --port 9000             # start backend + TUI"
}

main "$@"
