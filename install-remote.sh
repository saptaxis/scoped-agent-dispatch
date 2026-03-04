#!/bin/bash
set -euo pipefail

# Remote installer for scad (scoped-agent-dispatch)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/saptaxis/scoped-agent-dispatch/main/install-remote.sh | bash
#   curl -fsSL ... | bash -s -- --prefix ~/my-scad-src
#   curl -fsSL ... | bash -s -- --no-plugin --no-completions
#
# Clones the repo, then runs install.sh. All flags except --prefix
# are forwarded to install.sh.

REPO_URL="https://github.com/saptaxis/scoped-agent-dispatch.git"
PREFIX="${HOME}/.scad/src"
INSTALL_ARGS=()

# Parse arguments — pull out --prefix, forward the rest
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        *)
            INSTALL_ARGS+=("$1")
            shift
            ;;
    esac
done

echo "[scad] Remote installer"
echo ""

# Check prerequisites
if ! command -v git &>/dev/null; then
    echo "[scad] Error: git is required but not found"
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "[scad] Error: python3 is required but not found"
    exit 1
fi

# Clone or update
if [[ -d "$PREFIX/.git" ]]; then
    echo "[scad] Updating existing clone at $PREFIX..."
    git -C "$PREFIX" pull --ff-only --quiet
else
    echo "[scad] Cloning to $PREFIX..."
    mkdir -p "$(dirname "$PREFIX")"
    git clone --quiet "$REPO_URL" "$PREFIX"
fi

# Run the real installer
echo "[scad] Running install.sh..."
echo ""
bash "$PREFIX/install.sh" "${INSTALL_ARGS[@]}"
