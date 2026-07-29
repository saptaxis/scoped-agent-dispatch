#!/bin/bash
set -euo pipefail

# Bootstrap installer for scad (scoped-agent-dispatch)
#
# Usage:
#   ./install.sh                    # install from repo checkout
#   ./install.sh --home ~/my-scad   # custom SCAD_HOME
#   ./install.sh --dry-run          # show what would happen
#   ./install.sh --no-plugin         # skip Claude Code plugin registration
#   ./install.sh --no-retention      # never touch cleanupPeriodDays
#   ./install.sh --yes               # accept prompts (scripted installs)
#   ./install.sh --no-completions    # skip shell completion setup
#   ./install.sh --uninstall         # remove scad (keeps SCAD_HOME data)
#   ./install.sh --no-vm             # skip Docker provider provisioning (macOS: no Colima)
#
# Assumes: Python 3.11+.
# Docker provider: Linux — verifies a reachable dockerd. macOS — installs Colima
# via Homebrew if missing and creates the dedicated `scad` profile (--no-vm skips).
# Creates a venv, installs scad, symlinks to ~/.local/bin,
# sets up shell completions, and registers the Claude Code plugin.
# Auto-detects: shell type (zsh/bash), Claude Code presence.
# Skips gracefully when optional deps are missing.

SCAD_HOME_DEFAULT="$HOME/.scad"
SCAD_HOME="${SCAD_HOME_DEFAULT}"
VENV_DIR="${SCAD_INSTALL_VENV:-$HOME/.local/share/scad/venv}"
SET_RETENTION="${SET_RETENTION:-ask}"
ASSUME_YES="${ASSUME_YES:-no}"
LOCAL_BIN="$HOME/.local/bin"
DRY_RUN=false
UNINSTALL=false
SKIP_PLUGIN=false
SKIP_COMPLETIONS=false
SKIP_VM=false
REPO_DIR=""
OS="$(uname -s 2>/dev/null || echo unknown)"
COLIMA_PROFILE="scad"

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
        --no-retention)
            SET_RETENTION="no"
            shift
            ;;
        -y|--yes)
            ASSUME_YES="yes"
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
        --no-vm)
            SKIP_VM=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: install.sh [--home PATH] [--dry-run] [--uninstall] [--no-plugin] [--no-completions] [--no-vm] [--no-retention] [-y|--yes]"
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

    # Remove shell config lines (marker-based). awk, not sed: BSD sed (macOS)
    # rejects `-i` without an argument and the GNU `,+N` address form.
    # Copy contents back over the original file (rather than `mv`ing the temp
    # file into place) so the original inode — and its permission bits — are
    # preserved instead of replaced.
    MARKER="# scad — managed by install.sh"
    for RC in "$HOME/.zshrc" "$HOME/.bashrc"; do
        if [[ -f "$RC" ]] && grep -qF "$MARKER" "$RC"; then
            awk -v marker="$MARKER" '
                skip > 0            { skip--; next }
                index($0, marker)==1 { skip = 2; blank = 0; next }
                /^[[:space:]]*$/    { blank++; next }
                                    { while (blank > 0) { print ""; blank-- }; print }
            ' "$RC" > "$RC.scad-tmp"
            cat "$RC.scad-tmp" > "$RC"
            rm "$RC.scad-tmp"
            echo "[scad] Removed scad lines from $RC"
        fi
    done

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
    if $SKIP_VM; then
        echo "[scad] Would skip Docker provider setup (--no-vm)"
    elif [[ "$OS" == "Linux" ]]; then
        echo "[scad] Would verify Docker provider: native dockerd reachable"
    elif [[ "$OS" == "Darwin" ]]; then
        echo "[scad] Would set up Docker provider: colima (brew install if missing)"
        echo "[scad] Would create Colima profile: $COLIMA_PROFILE"
    else
        echo "[scad] Unrecognised platform '$OS' — would skip Docker provider setup"
    fi
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

# --- Step 1.5: Docker provider (platform-branched) ---
#
# describe_docker_check_failure: prints a header line that matches the real
# cause. `get_docker_client()` fails two very different ways and they need
# very different advice:
#   - ModuleNotFoundError: scad.vm  -- install.sh ran against a PyPI release
#     that predates this branch (no scad.vm module at all). No amount of
#     dockerd/group wrangling fixes this; the venv has the wrong scad.
#   - anything else -- the daemon genuinely could not be reached (permission,
#     not running, wrong socket, ...).
describe_docker_check_failure() {
    local detail="$1"
    local default_header="$2"
    if [[ "$detail" == *"ModuleNotFoundError"*"scad.vm"* ]] || [[ "$detail" == *"No module named 'scad.vm'"* ]]; then
        echo "[scad] ERROR: installed scad predates macOS/Colima support (scad.vm module not found)."
        echo "[scad]   This venv installed an older scoped-agent-dispatch release from PyPI."
        echo "[scad]   Upgrade it:  $VENV_DIR/bin/pip install --upgrade scoped-agent-dispatch"
    else
        echo "$default_header"
    fi
}

if $SKIP_VM; then
    echo "[scad] Skipping Docker provider setup (--no-vm)"
elif [[ "$OS" == "Linux" ]]; then
    echo "[scad] Verifying Docker daemon..."
    if DOCKER_CHECK_ERR="$("$VENV_DIR/bin/python" -c "from scad.vm import get_docker_client; get_docker_client()" 2>&1)"; then
        echo "[scad] Docker daemon reachable"
    else
        # Non-fatal: standard Linux bootstrap is install Docker -> usermod -aG
        # docker $USER -> install scad -> log out/in. Group membership isn't
        # active in *this* session, so the check above fails here even on a
        # correct setup. Aborting would leave scad half-installed (pip install
        # already ran, but no symlink/completions/plugin) with no way for the
        # printed advice to un-stick the user -- re-running hits the exact
        # same not-yet-logged-in-again failure. Warn and keep going instead;
        # `scad run ls` / the first real command will tell them if it's still
        # broken after they log back in.
        describe_docker_check_failure "$DOCKER_CHECK_ERR" "[scad] WARNING: no reachable Docker daemon (yet)."
        echo "[scad]   Install Docker Engine, then:  sudo systemctl enable --now docker"
        echo "[scad]   Add yourself to the docker group:  sudo usermod -aG docker \$USER"
        echo "[scad]   Then log out and back in (group membership needs a fresh session)"
        echo "[scad]   Detail: $DOCKER_CHECK_ERR"
        echo "[scad] Continuing install — scad will not work until the daemon is reachable."
    fi
elif [[ "$OS" == "Darwin" ]]; then
    echo "[scad] macOS detected — scad uses a dedicated Colima VM"
    if ! command -v colima &>/dev/null; then
        if ! command -v brew &>/dev/null; then
            echo "[scad] ERROR: Homebrew not found and colima is not installed."
            echo "[scad]   Install Homebrew (https://brew.sh), then re-run ./install.sh"
            echo "[scad]   Or install colima yourself:  brew install colima docker"
            echo "[scad]   Or skip provisioning:        ./install.sh --no-vm"
            exit 1
        fi
        echo "[scad] Installing colima + docker CLI via Homebrew..."
        brew install colima docker
    else
        echo "[scad] colima already installed"
    fi

    if [[ -d "$HOME/.colima/$COLIMA_PROFILE" ]]; then
        echo "[scad] Colima profile '$COLIMA_PROFILE' already exists"
    else
        # Sizing comes from ~/.scad/settings.yml (defaults 2 CPU / 4 GiB / 60 GiB)
        # so bash and Python never disagree about the defaults.
        # Capture output explicitly (not inside `read <<<`) so a failing
        # Python call is caught even under `set -e`: a command substitution
        # feeding `read` directly does not propagate a non-zero exit.
        SIZING_OUTPUT="$("$VENV_DIR/bin/python" -c "
from scad.config import load_settings
c = load_settings().colima
print(c.cpu, c.memory, c.disk, c.vm_type, c.mount_type)
" 2>&1)" && SIZING_STATUS=0 || SIZING_STATUS=$?
        if [[ "$SIZING_STATUS" -ne 0 ]] || [[ -z "$SIZING_OUTPUT" ]]; then
            echo "[scad] ERROR: failed to read VM sizing from ~/.scad/settings.yml"
            echo "[scad]   $SIZING_OUTPUT"
            exit 1
        fi
        read -r VM_CPU VM_MEM VM_DISK VM_TYPE VM_MOUNT <<<"$SIZING_OUTPUT"
        echo "[scad] Creating Colima profile '$COLIMA_PROFILE' (${VM_CPU} CPU, ${VM_MEM} GiB RAM, ${VM_DISK} GiB disk)..."
        colima start "$COLIMA_PROFILE" \
            --cpu "$VM_CPU" --memory "$VM_MEM" --disk "$VM_DISK" \
            --vm-type "$VM_TYPE" --mount-type "$VM_MOUNT"
    fi

    echo "[scad] Verifying scad Docker daemon..."
    if DOCKER_CHECK_ERR="$("$VENV_DIR/bin/python" -c "from scad.vm import get_docker_client; get_docker_client()" 2>&1)"; then
        echo "[scad] scad Docker daemon reachable: $HOME/.colima/$COLIMA_PROFILE/docker.sock"
    else
        describe_docker_check_failure "$DOCKER_CHECK_ERR" "[scad] ERROR: the scad VM is not serving Docker."
        echo "[scad]   Try:  colima start $COLIMA_PROFILE"
        echo "[scad]   On macOS 12 or older, set qemu/sshfs in ~/.scad/settings.yml:"
        echo "[scad]     colima:"
        echo "[scad]       vm_type: qemu"
        echo "[scad]       mount_type: sshfs"
        echo "[scad]   Detail: $DOCKER_CHECK_ERR"
        exit 1
    fi
else
    echo "[scad] Unrecognised platform '$OS' — skipping Docker provider setup"
    echo "[scad] scad supports Linux (native Docker) and macOS (Colima)"
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
    # The plugin ROOT — the directory that contains .claude-plugin/, alongside
    # commands/ and skills/. That root is what gets declared as the marketplace
    # source, and it is where marketplace.json lives, so name it directly rather
    # than the manifest subdirectory one level down.
    if [[ -n "$REPO_DIR" ]] && [[ -d "$REPO_DIR/.claude-plugin" ]]; then
        PLUGIN_DIR="$REPO_DIR"
    elif [[ -d "$VENV_DIR/lib" ]]; then
        # Find installed package location for non-editable installs
        SITE_PKG=$("$VENV_DIR/bin/python" -c "import scad; print(scad.__file__)" 2>/dev/null | xargs dirname)
        if [[ -n "$SITE_PKG" ]] && [[ -d "$(dirname "$SITE_PKG")/.claude-plugin" ]]; then
            PLUGIN_DIR="$(dirname "$SITE_PKG")"
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

# --- Transcript retention (asks; never decides for you) ---
# Claude Code prunes transcripts after 30 days by default and the archive can only
# keep what still exists. But settings.json is Claude Code's file, not ours, so we
# offer rather than assume — and only when the user has expressed no preference.
# An existing value of any length is a decision and is left alone.
if [ -d "$HOME/.claude" ] && [ "$SET_RETENTION" != "no" ]; then
    RETENTION_ARGS=""
    [ "$ASSUME_YES" = "yes" ] && RETENTION_ARGS="assume_yes=True"
    RESULT=$("$VENV_DIR/bin/python" -c "
from pathlib import Path
from scad.install import set_transcript_retention
print(set_transcript_retention(Path('$HOME/.claude'), $RETENTION_ARGS))
" </dev/tty 2>/dev/null) || RESULT="skipped"
    case "$RESULT" in
        set)      echo "[scad] Transcript retention raised — nothing further will be pruned." ;;
        kept)     echo "[scad] cleanupPeriodDays already set; left as you had it." ;;
        declined) echo "[scad] Left retention alone. Claude Code will keep pruning after 30 days." ;;
        skipped)  echo "[scad] Retention unchanged (no prompt available)."
                  echo "       To keep transcripts, set cleanupPeriodDays in ~/.claude/settings.json." ;;
        *)        echo "[scad] Could not read ~/.claude/settings.json; retention unchanged." ;;
    esac
fi

# --- Bootstrap the session index ---
# Without this a fresh install has an empty index, so `scad view` shows nothing
# and looks broken. Archiving first matters most here: on a machine with months
# of history this is the moment those traces stop being one prune away from gone.
echo ""
echo "[scad] Archiving existing agent traces (first run can take a minute)..."
if scad archive >/dev/null 2>&1; then
    echo "[scad] Building the session index..."
    scad reindex --no-archive 2>&1 | sed 's/^/  /' || true
else
    echo "[scad] Skipped archive/index — run 'scad archive && scad reindex' by hand."
fi

# --- Done ---
echo ""
echo "[scad] Install complete!"
echo ""
echo "  Restart your shell or run: source ~/.zshrc"
echo "  Then try: scad view       # who is waiting on you"
echo "            scad --help"
