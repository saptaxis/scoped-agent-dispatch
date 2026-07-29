"""Bootstrap installer helpers.

The main install flow is in install.sh (bash). This module provides the
Python helpers it shells out to: removing any leftover Claude Code plugin
registration, and offering to raise transcript retention.

scad no longer ships as a Claude Code plugin. Skills install through the
shared agentskills convention instead, which reaches Claude, Codex, Kimi and
others from one copy — so there is nothing left to register, only the old
registration to clear away.
"""

import json
import subprocess
import sys
from pathlib import Path


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


def deregister_claude_plugin(claude_home: Path, use_cli: bool = True) -> bool:
    """Remove scad's Claude Code plugin registration.

    scad no longer registers a plugin, so this runs for two reasons, both of
    them cleanup:

    - **Migration.** Install calls it on every run. Machines installed before
      the switch still carry the registration, and plugin skills do not
      override `~/.claude/skills` entries — they stack, so a machine that keeps
      both ends up with two copies of every scad skill competing for the same
      trigger. Clearing the plugin is what makes the skills install correct.
    - **Uninstall.** Removing scad has to take the config back to where it was.

    It clears everything the old registration wrote: the marketplace
    declaration, the enabled entry under both the qualified `scad@scad` and the
    stale bare `scad` key, and whatever install the harness materialised from
    them. Leaving the marketplace behind would point it at a directory that
    uninstall just deleted, and the user would get errors from a tool they
    removed.

    Only a *directory*-source `scad` marketplace is ours. A `scad` entry from
    some other source belongs to someone else and is left alone.

    A container emptied by our own removal is pruned, so a settings.json that
    was registered comes back byte for byte identical to how it looked before.
    A container that was already empty is left as it was found — it was never
    ours to touch.

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
