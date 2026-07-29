"""Bootstrap installer helpers.

The main install flow is in install.sh (bash). This module provides
the plugin registration helper that install.sh calls via Python.
"""

import json
import subprocess
import sys
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


def _read_settings(settings_file: Path) -> dict:
    if settings_file.exists():
        return json.loads(settings_file.read_text())
    return {}


def _write_settings(settings_file: Path, settings: dict) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=4) + "\n")


def _run_plugin_cli(args: list, claude_home: Path) -> bool:
    """Run `claude plugin ...` scoped to `claude_home`. True if it succeeded.

    CLAUDE_CONFIG_DIR is non-negotiable: without it the CLI edits the real
    ~/.claude, which would make every test a live mutation of the user's config.
    """
    import os

    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(claude_home)
    try:
        result = subprocess.run(
            ["claude", "plugin"] + args,
            capture_output=True, text=True, env=env, timeout=120,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Claude Code prunes transcripts after 30 days by default. The archive can only
# preserve what still exists, so on a machine that has been running a while the
# default has already destroyed history before scad ever sees it — that is what
# cost this machine February to June.
#
# But ~/.claude/settings.json belongs to Claude Code, not to us. Installing a
# session indexer does not imply consent to rewrite the harness's retention
# policy for a decade, and a long window is a real disk commitment (~400 MB per
# three weeks here). So we ask, and we only ask when the user has expressed no
# preference at all.
RETENTION_DAYS = 3650

RETENTION_PROMPT = """\
[scad] Claude Code deletes agent transcripts after {current} days by default.
       scad archives them, but it can only keep what still exists — anything
       already pruned is gone for good.

       Raise the retention window to {days} days (~10 years)?
       This edits cleanupPeriodDays in ~/.claude/settings.json. Transcripts
       accumulate on disk: roughly 400 MB per three weeks of heavy use.

       [Y/n] """


def set_transcript_retention(
    claude_home: Path,
    days: int = RETENTION_DAYS,
    *,
    assume_yes: bool = False,
    ask=None,
) -> str:
    """Offer to raise Claude Code's transcript retention. Never decides silently.

    Returns one of:
      "kept"     the user already set a value — any value — so we leave it alone
      "set"      it was unset and consent was given
      "declined" it was unset and the user said no
      "skipped"  it was unset and nobody could be asked (no tty, scripted install)
      "failed"   settings.json could not be read or written

    Only an ABSENT key counts as "no preference". A present value is a decision,
    including a short one: someone who chose 60 days meant 60, and overriding
    that upwards is as much an override as shortening it would be.

    `ask` is injected for testing; by default it prompts on a tty and refuses to
    guess when there is none.
    """
    settings_file = claude_home / "settings.json"
    try:
        settings = _read_settings(settings_file)
    except (OSError, ValueError):
        return "failed"

    if "cleanupPeriodDays" in settings:
        return "kept"

    if not assume_yes:
        if ask is None:
            if not sys.stdin.isatty():
                return "skipped"
            ask = _prompt_yes
        if not ask(RETENTION_PROMPT.format(current=30, days=days)):
            return "declined"

    settings["cleanupPeriodDays"] = days
    try:
        _write_settings(settings_file, settings)
    except OSError:
        return "failed"
    return "set"


def _prompt_yes(text: str) -> bool:
    try:
        answer = input(text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")


def register_claude_plugin(
    claude_home: Path, plugin_path: Path, use_cli: bool = True
) -> bool:
    """Register scad as a Claude Code plugin, durably.

    The declaration goes in settings.json, not installed_plugins.json:

    - `extraKnownMarketplaces["scad"]` points at the repo as a directory-source
      marketplace, so `scad@scad` resolves. scad used to register as a bare
      `scad`, which names no marketplace at all — that is the "Marketplace
      'inline' not found" symptom, and why `/remember` never loaded even though
      `claude --plugin-dir <repo>` loads it fine.
    - `enabledPlugins["scad@scad"]` enables it under the qualified id every
      working plugin uses. Any stale bare `"scad"` key is removed.

    installed_plugins.json is deliberately not written. It is not durable — an
    official-plugin update was observed rewriting it wholesale mid-session,
    resetting all six entries and dropping scad. settings.json survived that
    wipe, and the harness re-materialises the install from it on next start.
    Measured against a sandbox CLAUDE_CONFIG_DIR: delete scad's entry, restart,
    and it is rebuilt with `/remember` available.

    Prefers the `claude plugin` CLI, which makes exactly these settings writes
    and also materialises the marketplace cache; falls back to editing
    settings.json directly when the CLI is missing or fails. Both paths are
    idempotent and preserve every other key in the file.

    Args:
        claude_home: Path to ~/.claude directory.
        plugin_path: The plugin root, or its `.claude-plugin/` manifest
            directory — either is accepted, and the root is what gets declared.
        use_cli: Try the `claude plugin` CLI first. Off in unit tests.

    Returns:
        True if registration succeeded, False if skipped (no claude home).
    """
    if not claude_home.exists():
        return False

    root = _plugin_root(plugin_path)
    name = _read_manifest(root)["name"]
    plugin_id = f"{name}@{name}"

    if use_cli:
        # `marketplace add` is idempotent; `install` is a no-op once installed.
        if _run_plugin_cli(["marketplace", "add", str(root)], claude_home):
            _run_plugin_cli(["install", plugin_id], claude_home)

    # Always assert the durable declaration, whether or not the CLI ran. This
    # is the part that has to be true, and re-stating it costs nothing.
    settings_file = claude_home / "settings.json"
    settings = _read_settings(settings_file)

    marketplaces = settings.setdefault("extraKnownMarketplaces", {})
    marketplaces[name] = {"source": {"source": "directory", "path": str(root)}}

    enabled = settings.setdefault("enabledPlugins", {})
    enabled.pop(name, None)  # stale bare key resolves to no marketplace
    enabled[plugin_id] = True

    _write_settings(settings_file, settings)
    return True


def deregister_claude_plugin(claude_home: Path, use_cli: bool = True) -> bool:
    """Remove scad's Claude Code plugin registration — the exact inverse.

    Undoes everything `register_claude_plugin` writes: the marketplace
    declaration, the enabled entry under both the qualified and the stale bare
    key, and whatever install the harness materialised from them. Leaving the
    marketplace behind would point it at a directory uninstall just deleted, and
    the user would get errors from a tool they removed.

    Only a *directory*-source `scad` marketplace is ours. A `scad` entry from
    some other source belongs to someone else and is left alone.

    A container emptied by our own removal is pruned, so a register/deregister
    round trip restores settings.json byte for byte. A container that was
    already empty is left as it was found — it was never ours to touch.

    Args:
        claude_home: Path to ~/.claude directory.
        use_cli: Try the `claude plugin` CLI first. Off in unit tests.

    Returns:
        True if deregistration succeeded, False if skipped.
    """
    if not claude_home.exists():
        return False

    name = "scad"
    plugin_id = f"{name}@{name}"

    if use_cli:
        _run_plugin_cli(["uninstall", plugin_id], claude_home)
        _run_plugin_cli(["marketplace", "remove", name], claude_home)

    settings_file = claude_home / "settings.json"
    if settings_file.exists():
        settings = _read_settings(settings_file)

        marketplaces = settings.get("extraKnownMarketplaces")
        if isinstance(marketplaces, dict):
            entry = marketplaces.get(name)
            if isinstance(entry, dict) and \
                    entry.get("source", {}).get("source") == "directory":
                marketplaces.pop(name)
                if not marketplaces:
                    settings.pop("extraKnownMarketplaces")

        enabled = settings.get("enabledPlugins")
        if isinstance(enabled, dict):
            removed = [enabled.pop(key, None) for key in (plugin_id, name)]
            if any(v is not None for v in removed) and not enabled:
                settings.pop("enabledPlugins")

        _write_settings(settings_file, settings)

    plugins_file = claude_home / "plugins" / "installed_plugins.json"
    if plugins_file.exists():
        data = json.loads(plugins_file.read_text())
        for key in (plugin_id, name):
            data.get("plugins", {}).pop(key, None)
        plugins_file.write_text(json.dumps(data, indent=4) + "\n")

    return True
