#!/bin/bash
set -euo pipefail

# scad demo — shows the full dispatch-watch-fetch lifecycle
# Usage: ./demo.sh [branch-name] [prompt]

BRANCH="${1:-demo-$(date +%H%M)}"
PROMPT="${2:-Add a hello_world() function to src/scad/__init__.py that returns 'Hello from scad'. Write a test for it in tests/test_hello.py. Run the test to make sure it passes. Commit both files.}"

echo "=== scad demo ==="
echo "Config:  scad-test"
echo "Branch:  $BRANCH"
echo "Prompt:  $PROMPT"
echo ""

# Step 1: Dispatch
echo "--- Step 1: Dispatching agent ---"
OUTPUT=$(scad run scad-test --branch "$BRANCH" --prompt "$PROMPT" 2>&1)
echo "$OUTPUT"

# Extract run ID from output
RUN_ID=$(echo "$OUTPUT" | grep "Dispatching agent" | awk '{print $NF}')
CONTAINER="scad-${RUN_ID}"
echo ""
echo "Run ID:    $RUN_ID"
echo "Container: $CONTAINER"
echo ""

# Step 2: Watch logs
echo "--- Step 2: Watching container logs (Ctrl+C to stop watching) ---"
docker logs -f "$CONTAINER" 2>&1 || true
echo ""

# Step 3: Check artifacts
echo "--- Step 3: Checking artifacts ---"
LOGS_DIR="$HOME/.scad/logs"

echo ""
echo "Log file:"
cat "$LOGS_DIR/${RUN_ID}.log" 2>/dev/null || echo "  (not found)"

echo ""
echo "Status JSON:"
cat "$LOGS_DIR/${RUN_ID}.status.json" 2>/dev/null | python3 -m json.tool || echo "  (not found)"

echo ""
echo "Bundles:"
ls -la "$LOGS_DIR/${RUN_ID}"*.bundle 2>/dev/null || echo "  (none)"

# Step 4: Fetch bundle
echo ""
echo "--- Step 4: Fetching bundle into host repo ---"
BUNDLE="$LOGS_DIR/${RUN_ID}-code.bundle"
if [ -f "$BUNDLE" ]; then
    git bundle verify "$BUNDLE" 2>&1
    git fetch "$BUNDLE" "${BRANCH}:${BRANCH}" 2>&1
    echo ""
    echo "Branch fetched! Commits:"
    git log "$BRANCH" --oneline -5
    echo ""
    echo "Diff from main:"
    git diff main..."$BRANCH" --stat
else
    echo "No bundle found — agent may not have committed anything."
fi

# Step 5: Cleanup prompt
echo ""
echo "--- Cleanup (optional) ---"
echo "  git branch -D $BRANCH"
echo "  docker rm $CONTAINER 2>/dev/null"
echo "  rm $LOGS_DIR/${RUN_ID}*"
