"""What is running right now — tmux panes and scad containers.

The index describes what happened; this describes what is live. Neither can
answer the other's question, which is why this is computed at render time and
never stored.

Every function here degrades to an empty list on any failure — no tmux server,
tmux not installed, docker unreachable, a timeout, malformed output. A viewer
that raises because tmux is not running would be useless.
"""

import re
import subprocess
from dataclasses import dataclass

TMUX_FORMAT = "#{session_name}:#{window_index}.#{pane_index}|#{pane_current_path}|#{pane_current_command}"
_TIMEOUT = 5

# Claude Code shows up in tmux as its version string (e.g. "2.1.219"), not as
# "claude" — the binary re-execs. codex uses its own name.
_VERSION = re.compile(r"^\d+\.\d+\.\d+")
_AGENT_NAMES = {"codex", "claude"}


@dataclass(frozen=True)
class TmuxPane:
    target: str      # session:window.pane — paste into `tmux select-window -t`
    path: str
    command: str


def is_agent_command(cmd: str) -> bool:
    """Is this pane running an agent?"""
    cmd = (cmd or "").strip()
    return cmd in _AGENT_NAMES or bool(_VERSION.match(cmd))


def tmux_panes() -> list[TmuxPane]:
    """Every pane tmux knows about. Empty if tmux is absent or has no server."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", TMUX_FORMAT],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    panes = []
    for line in result.stdout.splitlines():
        # Split from both ends: a path may contain "|", target and command may not.
        head, _, rest = line.partition("|")
        path, _, command = rest.rpartition("|")
        if not head or not command:
            continue
        panes.append(TmuxPane(target=head, path=path, command=command))
    return panes
