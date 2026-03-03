"""Bootstrap installer helpers.

The main install flow is in install.sh (bash). This module provides
the plugin registration helper that install.sh calls via Python.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def register_claude_plugin(claude_home: Path, plugin_path: Path) -> bool:
    """Register scad as a Claude Code plugin.

    Adds scad to installed_plugins.json and enables it in settings.json.
    Idempotent — safe to run multiple times.

    Args:
        claude_home: Path to ~/.claude directory.
        plugin_path: Path to the directory containing plugin.json.

    Returns:
        True if registration succeeded, False if skipped (no claude home).
    """
    if not claude_home.exists():
        return False

    plugins_dir = claude_home / "plugins"
    plugins_file = plugins_dir / "installed_plugins.json"
    settings_file = claude_home / "settings.json"

    # Read plugin.json to get name and version
    manifest = json.loads((plugin_path / "plugin.json").read_text())
    name = manifest["name"]
    version = manifest.get("version", "0.0.0")

    # --- installed_plugins.json ---
    if plugins_file.exists():
        data = json.loads(plugins_file.read_text())
    else:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 2, "plugins": {}}

    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "scope": "user",
        "installPath": str(plugin_path),
        "version": version,
        "installedAt": now,
        "lastUpdated": now,
    }

    # Replace existing or add new — always exactly one entry
    data["plugins"][name] = [entry]
    plugins_file.write_text(json.dumps(data, indent=4) + "\n")

    # --- settings.json ---
    if settings_file.exists():
        settings = json.loads(settings_file.read_text())
    else:
        settings = {}

    if "enabledPlugins" not in settings:
        settings["enabledPlugins"] = {}
    settings["enabledPlugins"][name] = True
    settings_file.write_text(json.dumps(settings, indent=4) + "\n")

    return True


def deregister_claude_plugin(claude_home: Path) -> bool:
    """Remove scad from Claude Code plugin registration.

    Removes from installed_plugins.json and disables in settings.json.

    Args:
        claude_home: Path to ~/.claude directory.

    Returns:
        True if deregistration succeeded, False if skipped.
    """
    if not claude_home.exists():
        return False

    plugins_file = claude_home / "plugins" / "installed_plugins.json"
    settings_file = claude_home / "settings.json"

    # --- installed_plugins.json ---
    if plugins_file.exists():
        data = json.loads(plugins_file.read_text())
        data["plugins"].pop("scad", None)
        plugins_file.write_text(json.dumps(data, indent=4) + "\n")

    # --- settings.json ---
    if settings_file.exists():
        settings = json.loads(settings_file.read_text())
        if "enabledPlugins" in settings:
            settings["enabledPlugins"].pop("scad", None)
        settings_file.write_text(json.dumps(settings, indent=4) + "\n")

    return True
