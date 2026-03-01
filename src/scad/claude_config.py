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
