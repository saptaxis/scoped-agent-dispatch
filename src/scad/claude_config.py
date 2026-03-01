"""Claude Code configuration management.

Centralizes all Claude-specific config: settings.json, .claude.json,
credential mounts, CLAUDE.md handling, plugin pre-seeding, timezone detection.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scad.config import ScadConfig


DEFAULT_PLUGINS = [
    "superpowers@claude-plugins-official",
    "commit-commands@claude-plugins-official",
    "pyright-lsp@claude-plugins-official",
]

RUNS_DIR = Path.home() / ".scad" / "runs"


def get_host_timezone() -> str:
    """Get host IANA timezone (e.g., 'Asia/Kolkata'). Falls back to 'UTC'."""
    tz_file = Path("/etc/timezone")
    if tz_file.exists():
        tz = tz_file.read_text().strip()
        if tz:
            return tz
    localtime = Path("/etc/localtime")
    if localtime.exists() and localtime.is_symlink():
        target = str(localtime.resolve())
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    return "UTC"


def render_claude_json(config: ScadConfig) -> dict:
    """Return the .claude.json seed content for a container."""
    workdir_key = config.workdir_key
    return {
        "hasCompletedOnboarding": True,
        "effortCalloutDismissed": True,
        "installMethod": "native",
        "projects": {
            f"/workspace/{workdir_key}": {
                "hasTrustDialogAccepted": True,
            }
        },
    }


def render_settings_json(config: ScadConfig) -> dict:
    """Return the settings.json seed content for a container."""
    settings: dict = {
        "cleanupPeriodDays": 365,
        "attribution": {"commit": "", "pr": ""},
        "permissions": {
            "deny": [
                "Bash(rm -rf /)",
                "Bash(sudo *)",
                "Bash(mkfs*)",
                "Bash(dd if=*)",
                "Bash(git push * --force* main)",
                "Bash(git push * --force* master)",
                "Bash(git reset --hard*)",
            ],
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{
                        "type": "prompt",
                        "prompt": (
                            "Block if the command contains rm -rf / or pushes "
                            "directly to main/master branches. Allow everything else."
                        ),
                    }],
                },
            ],
            "Notification": [
                {
                    "matcher": "statusline",
                    "hooks": [{
                        "type": "command",
                        "command": "bash /home/scad/statusline.sh",
                    }],
                },
            ],
        },
        "enabledPlugins": {p: True for p in config.claude.plugins},
    }

    if config.claude.dangerously_skip_permissions:
        settings["permissions"]["defaultMode"] = "bypassPermissions"
        settings["skipDangerousModePermissionPrompt"] = True

    return settings
