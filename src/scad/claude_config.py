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


def get_volume_mounts(
    config: ScadConfig,
    run_id: str,
    home_dir: Path | None = None,
) -> dict:
    """Return all Claude-related volume mounts for run_container().

    Includes: claude dir, claude.json, credentials, CLAUDE.md, /etc/localtime.
    home_dir is for testing -- defaults to Path.home().
    """
    if home_dir is None:
        home_dir = Path.home()

    volumes: dict = {}

    # Persistent claude dir (~/.claude)
    run_claude_dir = RUNS_DIR / run_id / "claude"
    if run_claude_dir.exists():
        volumes[str(run_claude_dir)] = {"bind": "/home/scad/.claude", "mode": "rw"}

    # Persistent claude.json (~/.claude.json)
    claude_json = RUNS_DIR / run_id / "claude.json"
    if claude_json.exists():
        volumes[str(claude_json)] = {"bind": "/home/scad/.claude.json", "mode": "rw"}

    # Credentials -- staging path (entrypoint copies to final location)
    claude_creds = home_dir / ".claude" / ".credentials.json"
    if claude_creds.exists():
        volumes[str(claude_creds)] = {
            "bind": "/mnt/host-claude-credentials.json",
            "mode": "ro",
        }

    # CLAUDE.md -- global instructions
    if config.claude.claude_md is False:
        pass  # explicitly disabled
    elif config.claude.claude_md is not None:
        claude_md_path = Path(config.claude.claude_md).expanduser().resolve()
        if claude_md_path.exists():
            volumes[str(claude_md_path)] = {"bind": "/home/scad/CLAUDE.md", "mode": "ro"}
    else:
        claude_md_path = home_dir / "CLAUDE.md"
        if claude_md_path.exists():
            volumes[str(claude_md_path)] = {"bind": "/home/scad/CLAUDE.md", "mode": "ro"}

    # /etc/localtime -- container inherits host timezone
    localtime = Path("/etc/localtime")
    if localtime.exists():
        volumes[str(localtime.resolve())] = {"bind": "/etc/localtime", "mode": "ro"}

    return volumes
