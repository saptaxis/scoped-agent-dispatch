#!/bin/bash
set -euo pipefail

# Scad end-to-end demo.
# Runs start to finish — creates toy repos, registers config, builds,
# starts a session, shows status/info, stops, cleans up.
#
# Usage: ./examples/demo.sh [--step] [base_dir]
#   --step     step-through mode: press Enter between steps instead of sleeping
#   base_dir   defaults to ~/vsr-tmp/scad-demo

STEP=false
if [[ "${1:-}" == "--step" ]]; then
    STEP=true
    shift
fi

BASE="${1:-$HOME/tmp/scad-demo}"
PAUSE=${PAUSE:-2}  # seconds between steps (set PAUSE=0 for fast)

# ── Helpers ──────────────────────────────────────────────────────────
banner() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

explain() {
    echo "  → $1"
}

run() {
    echo ""
    echo "  \$ $*"
    echo ""
    "$@" 2>&1 | sed 's/^/    /'
    echo ""
}

pause() {
    if $STEP; then
        echo "  Press Enter to continue..."
        read -r
    else
        sleep "$PAUSE"
    fi
}

# ── 1. Create toy repos ─────────────────────────────────────────────
banner "Step 1: Create toy repos"

explain "Creating two git repos to simulate a multi-repo project."
explain "demo-code: a Python project (workdir — Claude works here)"
explain "demo-docs: a docs repo (add-dir — Claude can read it)"

rm -rf "$BASE/demo-code" "$BASE/demo-docs" "$BASE/demo.yml"
mkdir -p "$BASE"

# demo-code
mkdir -p "$BASE/demo-code"
(
    cd "$BASE/demo-code"
    git init -q
    cat > hello.py << 'EOF'
def hello():
    return "Hello from demo-code"

if __name__ == "__main__":
    print(hello())
EOF
    cat > utils.py << 'EOF'
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
EOF
    git add -A && git commit -q -m "init: demo code with hello and utils"
    cat > config.py << 'EOF'
APP_NAME = "demo"
VERSION = "0.1"
EOF
    git add -A && git commit -q -m "feat: add config"
)

# demo-docs
mkdir -p "$BASE/demo-docs/projects/demo"
(
    cd "$BASE/demo-docs"
    git init -q
    cat > projects/demo/overview.md << 'EOF'
# Demo Project

A toy project for testing scad end-to-end.

## Status
- Initial setup complete
EOF
    git add -A && git commit -q -m "init: demo docs"
    cat > projects/demo/notes.md << 'EOF'
# Notes

- hello.py: entry point
- utils.py: math helpers
- config.py: app constants
EOF
    git add -A && git commit -q -m "docs: add notes"
)

explain "Repos created:"
run git -C "$BASE/demo-code" log --oneline
run git -C "$BASE/demo-docs" log --oneline
pause

# ── 2. Config registration ──────────────────────────────────────────
banner "Step 2: Register config with scad"

explain "Configs can live anywhere — in your project repo, version controlled."
explain "scad config add creates a SYMLINK in ~/.scad/configs/ pointing to your file."
explain "No copying. The config stays where you put it."

# Generate config with resolved paths
cat > "$BASE/demo.yml" << YEOF
name: demo
repos:
  demo-code:
    path: $BASE/demo-code
    workdir: true
  demo-docs:
    path: $BASE/demo-docs
    add_dir: true
python:
  version: "3.11"
claude:
  dangerously_skip_permissions: true
YEOF

explain "Config file lives in the demo directory:"
run cat "$BASE/demo.yml"

explain "Registering with scad:"
run scad config add "$BASE/demo.yml"

explain "What happened — a symlink was created:"
run ls -la ~/.scad/configs/demo.yml

explain "scad config list resolves it transparently:"
run scad config list

explain "scad config view reads through the symlink:"
run scad config view demo

pause

# ── 3. Build ─────────────────────────────────────────────────────────
banner "Step 3: Build Docker image"

explain "scad build renders a Dockerfile from config (Python, deps, Claude Code)"
explain "Quiet mode shows Step N/M progress. Use -v for full output."

if ! docker info &>/dev/null 2>&1; then
    explain "Docker not running — skipping container steps."
    explain "Start Docker and re-run to see the full demo."
    exit 0
fi

run scad build demo

pause

# ── 4. Start session ─────────────────────────────────────────────────
banner "Step 4: Start a session"

explain "scad session start creates local clones on a new branch,"
explain "starts a Docker container, and launches Claude inside tmux."
explain "Branch name auto-generated: scad-MonDD-HHMM."

run scad session start demo

# Capture run-id from the worktrees dir (most recent)
RUN_ID=$(ls -t ~/.scad/worktrees/ | grep "^demo-" | head -1)
if [ -z "$RUN_ID" ]; then
    echo "  ERROR: Could not find run ID. Exiting."
    exit 1
fi
explain "Run ID: $RUN_ID"

explain "What was created on the host:"
run ls ~/.scad/worktrees/"$RUN_ID"/
run ls ~/.scad/runs/"$RUN_ID"/

pause

# ── 5. Status ─────────────────────────────────────────────────────────
banner "Step 5: Session status + info"

explain "scad session status shows running sessions (default)."
explain "scad session info shows a full dashboard for one session."

run scad session status
run scad session info "$RUN_ID"

pause

# ── 6. Stop ──────────────────────────────────────────────────────────
banner "Step 6: Stop session (preserves state)"

explain "scad session stop stops the container but does NOT remove it."
explain "Session data, clones, run dir — all preserved."

run scad session stop "$RUN_ID"

explain "Container stopped. Clones still exist:"
run ls ~/.scad/worktrees/"$RUN_ID"/

explain "Session data still exists:"
run ls ~/.scad/runs/"$RUN_ID"/

explain "Status with --all shows stopped sessions:"
run scad session status --all

pause

# ── 7. Clean ─────────────────────────────────────────────────────────
banner "Step 7: Clean up (destructive)"

explain "scad session clean removes EVERYTHING: container, clones, run dir."
explain "This is the point of no return."

run scad session clean "$RUN_ID"

explain "Clones gone:"
echo "  \$ ls ~/.scad/worktrees/$RUN_ID/ 2>&1"
ls ~/.scad/worktrees/"$RUN_ID"/ 2>&1 | sed 's/^/    /' || true
echo ""

explain "Run dir gone:"
echo "  \$ ls ~/.scad/runs/$RUN_ID/ 2>&1"
ls ~/.scad/runs/"$RUN_ID"/ 2>&1 | sed 's/^/    /' || true
echo ""

explain "Status --all shows cleaned:"
run scad session status --all

pause

# ── 8. Unregister config ────────────────────────────────────────────
banner "Step 8: Unregister config"

explain "scad config remove removes the symlink. The original file is untouched."

run scad config remove demo

explain "Symlink gone:"
echo "  \$ ls -la ~/.scad/configs/demo.yml 2>&1"
ls -la ~/.scad/configs/demo.yml 2>&1 | sed 's/^/    /' || true
echo ""

explain "Original file still exists:"
run ls -la "$BASE/demo.yml"

pause

# ── Done ─────────────────────────────────────────────────────────────
banner "Demo complete"

echo "  What you saw:"
echo "    1. Config registration via symlink (version-controlled configs)"
echo "    2. Docker image build with Step N/M progress"
echo "    3. Session start (clone + branch + container + Claude)"
echo "    4. Session status and info dashboard"
echo "    5. Stop preserving state vs clean destroying everything"
echo "    6. Config unregistration (source file preserved)"
echo ""
echo "  For interactive features (attach, Claude, detach, fetch, sync, refresh),"
echo "  see examples/walkthrough.md"
echo ""
