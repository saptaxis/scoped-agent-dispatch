#!/bin/bash
set -euo pipefail

# Bootstrap installer for scad (scoped-agent-dispatch)
#
# Usage:
#   ./install.sh                    # install from repo checkout
#   ./install.sh --home ~/my-scad   # custom SCAD_HOME
#   ./install.sh --dry-run          # show what would happen
#   ./install.sh --no-plugin         # skip Claude Code plugin registration
#   ./install.sh --no-completions    # skip shell completion setup
#   ./install.sh --uninstall         # remove scad (keeps SCAD_HOME data)
#
# Assumes: Python 3.11+ and Docker already installed.
# Creates a venv, installs scad, symlinks to ~/.local/bin,
# sets up shell completions, and registers the Claude Code plugin.
# Auto-detects: shell type (zsh/bash), Claude Code presence.
# Skips gracefully when optional deps are missing.

SCAD_HOME_DEFAULT="$HOME/.scad"
SCAD_HOME="${SCAD_HOME_DEFAULT}"
VENV_DIR="${SCAD_INSTALL_VENV:-$HOME/.local/share/scad/venv}"
LOCAL_BIN="$HOME/.local/bin"
DRY_RUN=false
UNINSTALL=false
SKIP_PLUGIN=false
SKIP_COMPLETIONS=false
REPO_DIR=""

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --home)
            SCAD_HOME="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --no-plugin)
            SKIP_PLUGIN=true
            shift
            ;;
        --no-completions)
            SKIP_COMPLETIONS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: install.sh [--home PATH] [--dry-run] [--uninstall] [--no-plugin] [--no-completions]"
            exit 1
            ;;
    esac
done

# Detect if running from a repo checkout
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]] && grep -q "scoped-agent-dispatch" "$SCRIPT_DIR/pyproject.toml" 2>/dev/null; then
    REPO_DIR="$SCRIPT_DIR"
fi

# --- Uninstall flow ---
if $UNINSTALL; then
    echo "[scad] Uninstaller"
    echo ""
    echo "[scad] Will remove:"
    echo "  Symlink:  $LOCAL_BIN/scad"
    echo "  Venv:     $VENV_DIR"
    echo "  Shell:    scad lines from ~/.zshrc"
    echo "  Plugin:   Claude Code plugin registration"
    echo ""
    echo "[scad] Will NOT remove:"
    echo "  SCAD_HOME ($SCAD_HOME) — your configs, runs, and data"
    echo ""

    if $DRY_RUN; then
        echo "[scad] DRY RUN — no changes made"
        exit 0
    fi

    # Remove symlink
    if [[ -L "$LOCAL_BIN/scad" ]]; then
        rm "$LOCAL_BIN/scad"
        echo "[scad] Removed symlink: $LOCAL_BIN/scad"
    fi

    # Remove zshrc lines (marker-based)
    ZSHRC="$HOME/.zshrc"
    MARKER="# scad — managed by install.sh"
    if [[ -f "$ZSHRC" ]] && grep -qF "$MARKER" "$ZSHRC"; then
        # Remove marker line and the 2 lines after it (SCAD_HOME export + completion eval)
        sed -i "/$MARKER/,+2d" "$ZSHRC"
        # Remove trailing blank line if we left one
        sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$ZSHRC"
        echo "[scad] Removed scad lines from ~/.zshrc"
    fi

    # Deregister Claude Code plugin
    if [[ -d "$HOME/.claude" ]]; then
        if [[ -d "$VENV_DIR" ]]; then
            "$VENV_DIR/bin/python" -c "
from scad.install import deregister_claude_plugin
from pathlib import Path
deregister_claude_plugin(claude_home=Path('$HOME/.claude'))
print('[scad] Deregistered Claude Code plugin')
" 2>/dev/null || echo "[scad] Skipped plugin deregistration (python helper not available)"
        else
            echo "[scad] Skipped plugin deregistration (venv already removed)"
        fi
    fi

    # Remove venv (last — needed for plugin deregistration above)
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        echo "[scad] Removed venv: $VENV_DIR"
    fi

    echo ""
    echo "[scad] Uninstall complete."
    echo "[scad] Your data is still at: $SCAD_HOME"
    echo "[scad] To remove data too: rm -rf $SCAD_HOME"
    exit 0
fi

# --- Install flow ---
echo "[scad] Bootstrap installer"
echo ""

if $DRY_RUN; then
    echo "[scad] DRY RUN — no changes will be made"
    echo ""
fi

echo "[scad] Settings:"
echo "  SCAD_HOME:  $SCAD_HOME"
echo "  Venv:       $VENV_DIR"
echo "  Symlink:    $LOCAL_BIN/scad"
if [[ -n "$REPO_DIR" ]]; then
    echo "  Source:     $REPO_DIR (editable install)"
else
    echo "  Source:     PyPI (scoped-agent-dispatch)"
fi
echo ""

if $DRY_RUN; then
    echo "[scad] Would create: $VENV_DIR"
    echo "[scad] Would install: scoped-agent-dispatch into venv"
    echo "[scad] Would symlink: $VENV_DIR/bin/scad → $LOCAL_BIN/scad"
    echo "[scad] Would create: $SCAD_HOME/configs/"
    if $SKIP_COMPLETIONS; then
        echo "[scad] Skipping shell completions (--no-completions)"
    elif [[ -f "$HOME/.zshrc" ]]; then
        echo "[scad] Would add to ~/.zshrc: SCAD_HOME export + completion eval"
    elif [[ -f "$HOME/.bashrc" ]]; then
        echo "[scad] Would add to ~/.bashrc: SCAD_HOME export + completion eval"
    else
        echo "[scad] No .zshrc or .bashrc found — would skip shell completions"
    fi
    if $SKIP_PLUGIN; then
        echo "[scad] Skipping plugin registration (--no-plugin)"
    elif command -v claude &>/dev/null; then
        echo "[scad] Would register: Claude Code plugin"
    else
        echo "[scad] Claude Code not found — would skip plugin registration"
    fi
    exit 0
fi

# --- Step 1: Create venv + install ---
echo "[scad] Creating venv at $VENV_DIR..."
python3 -m venv "$VENV_DIR"

echo "[scad] Installing scoped-agent-dispatch..."
if [[ -n "$REPO_DIR" ]]; then
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_DIR"
else
    "$VENV_DIR/bin/pip" install --quiet scoped-agent-dispatch
fi

# --- Step 2: Symlink to PATH ---
mkdir -p "$LOCAL_BIN"
if [[ -L "$LOCAL_BIN/scad" ]]; then
    rm "$LOCAL_BIN/scad"
fi
ln -s "$VENV_DIR/bin/scad" "$LOCAL_BIN/scad"
echo "[scad] Symlinked: $LOCAL_BIN/scad"

# --- Step 3: Create SCAD_HOME ---
mkdir -p "$SCAD_HOME/configs"
echo "[scad] Created: $SCAD_HOME/configs/"

# --- Step 4: Shell config (auto-detect shell, respect --no-completions) ---
if $SKIP_COMPLETIONS; then
    echo "[scad] Skipping shell completions (--no-completions)"
else
    ZSHRC="$HOME/.zshrc"
    BASHRC="$HOME/.bashrc"
    MARKER="# scad — managed by install.sh"

    if [[ -f "$ZSHRC" ]]; then
        if grep -qF "$MARKER" "$ZSHRC"; then
            echo "[scad] Shell config already in ~/.zshrc (skipped)"
        else
            {
                echo ""
                echo "$MARKER"
                echo "export SCAD_HOME=\"$SCAD_HOME\""
                echo 'eval "$(_SCAD_COMPLETE=zsh_source scad)"'
            } >> "$ZSHRC"
            echo "[scad] Added SCAD_HOME + completions to ~/.zshrc"
        fi
    elif [[ -f "$BASHRC" ]]; then
        if grep -qF "$MARKER" "$BASHRC"; then
            echo "[scad] Shell config already in ~/.bashrc (skipped)"
        else
            {
                echo ""
                echo "$MARKER"
                echo "export SCAD_HOME=\"$SCAD_HOME\""
                echo 'eval "$(_SCAD_COMPLETE=bash_source scad)"'
            } >> "$BASHRC"
            echo "[scad] Added SCAD_HOME + completions to ~/.bashrc"
        fi
    else
        echo "[scad] No .zshrc or .bashrc found — skipping shell completions"
        echo "[scad] Add manually: export SCAD_HOME=\"$SCAD_HOME\""
    fi
fi

# --- Step 5: Register Claude Code plugin (auto-detect, respect --no-plugin) ---
if $SKIP_PLUGIN; then
    echo "[scad] Skipping plugin registration (--no-plugin)"
elif ! command -v claude &>/dev/null; then
    echo "[scad] Claude Code not found — skipping plugin registration"
    echo "[scad] Install Claude Code, then re-run: ./install.sh"
else
    PLUGIN_DIR=""
    if [[ -n "$REPO_DIR" ]] && [[ -d "$REPO_DIR/.claude-plugin" ]]; then
        PLUGIN_DIR="$REPO_DIR/.claude-plugin"
    elif [[ -d "$VENV_DIR/lib" ]]; then
        # Find installed package location for non-editable installs
        SITE_PKG=$("$VENV_DIR/bin/python" -c "import scad; print(scad.__file__)" 2>/dev/null | xargs dirname)
        if [[ -n "$SITE_PKG" ]] && [[ -d "$(dirname "$SITE_PKG")/.claude-plugin" ]]; then
            PLUGIN_DIR="$(dirname "$SITE_PKG")/.claude-plugin"
        fi
    fi

    if [[ -n "$PLUGIN_DIR" ]] && [[ -d "$HOME/.claude" ]]; then
        "$VENV_DIR/bin/python" -c "
from scad.install import register_claude_plugin
from pathlib import Path
result = register_claude_plugin(
    claude_home=Path('$HOME/.claude'),
    plugin_path=Path('$PLUGIN_DIR')
)
if result:
    print('[scad] Registered Claude Code plugin')
else:
    print('[scad] Skipped plugin registration (no ~/.claude)')
"
    elif [[ -z "$PLUGIN_DIR" ]]; then
        echo "[scad] Skipped plugin registration (plugin.json not found)"
    else
        echo "[scad] Skipped plugin registration (no ~/.claude directory)"
    fi
fi

# --- Done ---
echo ""
echo "[scad] Install complete!"
echo ""
echo "  Restart your shell or run: source ~/.zshrc"
echo "  Then try: scad --help"
