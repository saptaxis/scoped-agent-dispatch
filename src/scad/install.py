"""Bootstrap installer helpers.

The main install flow is in install.sh (bash). This module provides
the plugin registration helper that install.sh calls via Python.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def _plugin_root(plugin_path: Path) -> Path:
    """The directory Claude Code loads a plugin's components from.

    `installPath` names the plugin ROOT — the directory that CONTAINS
    `.claude-plugin/`, alongside `commands/`, `skills/` and `agents/`. Measured
    against every working entry in a real installed_plugins.json, all of which
    point at a root of that shape.

    install.sh has always passed `$REPO_DIR/.claude-plugin`, one level too deep,
    so Claude Code looked for `commands/` inside the manifest directory, found
    none, and `/remember` never loaded — with the entry present and the plugin
    enabled, which is precisely what made it read as a registration failure
    rather than a path error. Accepting either spelling costs one line and makes
    the caller impossible to get wrong.
    """
    return plugin_path.parent if plugin_path.name == ".claude-plugin" else plugin_path


def _read_manifest(root: Path) -> dict:
    """plugin.json, whether it sits at the root or under `.claude-plugin/`."""
    for candidate in (root / ".claude-plugin" / "plugin.json", root / "plugin.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"no plugin.json under {root}")


def register_claude_plugin(claude_home: Path, plugin_path: Path) -> bool:
    """Register scad as a Claude Code plugin.

    Adds scad to installed_plugins.json and enables it in settings.json.
    Idempotent — safe to run multiple times.

    Read-modify-write on both files, preserving every other entry: an official
    plugin update once overwrote installed_plugins.json and took scad's entry
    with it, and returning the favour would break six working plugins to fix one.

    Args:
        claude_home: Path to ~/.claude directory.
        plugin_path: The plugin root, or its `.claude-plugin/` manifest
            directory — either is accepted, and the root is what gets recorded.

    Returns:
        True if registration succeeded, False if skipped (no claude home).
    """
    if not claude_home.exists():
        return False

    plugins_dir = claude_home / "plugins"
    plugins_file = plugins_dir / "installed_plugins.json"
    settings_file = claude_home / "settings.json"

    plugin_path = _plugin_root(plugin_path)
    manifest = _read_manifest(plugin_path)
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
