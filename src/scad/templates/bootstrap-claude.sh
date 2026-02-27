#!/usr/bin/env bash
set -euo pipefail

# Claude Code Bootstrap — installs marketplaces and plugins from config.
# Called by entrypoint on first container start.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_file="${1:-$SCRIPT_DIR/bootstrap-claude.conf}"

if [[ ! -f "$config_file" ]]; then
  echo "[scad-bootstrap] No config at $config_file, skipping plugin install"
  exit 0
fi

source "$config_file"

echo "[scad-bootstrap] Installing Claude plugins..."

# Add marketplaces
for entry in "${MARKETPLACES[@]}"; do
  name="${entry%%|*}"
  source="${entry#*|}"
  if claude plugin marketplace list 2>/dev/null | grep -q "$name"; then
    echo "[scad-bootstrap] Marketplace $name (already added)"
  else
    claude plugin marketplace add "$source" 2>/dev/null && \
      echo "[scad-bootstrap] Added marketplace $name" || \
      echo "[scad-bootstrap] Warning: failed to add marketplace $name"
  fi
done

# Install plugins
for plugin in "${PLUGINS[@]}"; do
  if claude plugin list 2>/dev/null | grep -q "${plugin%%@*}"; then
    echo "[scad-bootstrap] Plugin $plugin (already installed)"
  else
    claude plugin install "$plugin" 2>/dev/null && \
      echo "[scad-bootstrap] Installed $plugin" || \
      echo "[scad-bootstrap] Warning: failed to install $plugin"
  fi
done

echo "[scad-bootstrap] Plugin setup complete"
