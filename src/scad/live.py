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

TMUX_FORMAT = ("#{session_name}:#{window_index}.#{pane_index}|#{window_name}|"
               "#{pane_current_path}|#{pane_current_command}")
_TIMEOUT = 5

# Claude Code shows up in tmux as its version string (e.g. "2.1.219"), not as
# "claude" — the binary re-execs. codex uses its own name.
_VERSION = re.compile(r"^\d+\.\d+\.\d+")
_AGENT_NAMES = {"codex", "claude"}


@dataclass(frozen=True)
class TmuxPane:
    target: str      # session:window.pane
    path: str
    command: str
    window: str = ""   # tmuxinator's window name, e.g. "scad" — the human label

    @property
    def session(self) -> str:
        """The tmux session name: `main` from `main:3.1`. There may be several."""
        return self.target.split(":", 1)[0]


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
        # Split from both ends: a path may contain "|", the others may not.
        head, _, rest = line.partition("|")
        window, _, rest = rest.partition("|")
        path, _, command = rest.rpartition("|")
        if not head or not command:
            continue
        panes.append(TmuxPane(target=head, path=path, command=command, window=window))
    return panes


def agent_panes() -> list[TmuxPane]:
    """Every pane currently sitting in an agent REPL, across all tmux sessions.

    `list-panes -a` spans every session on the server, so `main`, `main2` and any
    others are all covered without enumerating them. Panes are the unit here, not
    sessions: one window regularly holds several agents — this machine has three
    in `main:3` alone (two claude, one codex) sharing a directory — which is
    precisely why a pane cannot be resolved back to a session id.
    """
    return [p for p in tmux_panes() if is_agent_command(p.command)]


def _docker_env() -> dict | None:
    """Environment that points the docker CLI at the daemon scad actually uses.

    REQUIRED on macOS. scad runs a dedicated colima profile, so the bare `docker`
    CLI talks to /var/run/docker.sock and finds nothing — verified on this
    machine: `docker info` fails with "no such file or directory" while
    `scad vm status` reports the VM running. Without this the container column
    would silently be empty forever, which looks identical to "nothing running".

    Imported lazily so a missing/broken scad.vm degrades like everything else here.
    """
    try:
        from scad.vm import docker_cli_env

        return docker_cli_env()
    except Exception:
        return None


def running_run_ids() -> set[str]:
    """Run ids with a live container."""
    # Broad except by design: this module's contract is that discovery never
    # raises, and the failure modes are open-ended (a docker CLI that is really
    # a shell wrapper, a vm module that blows up importing). See module docstring.
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=scad-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=_TIMEOUT, env=_docker_env(),
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    return {
        name[len("scad-"):]
        for name in (n.strip() for n in result.stdout.splitlines())
        if name.startswith("scad-")
    }


def live_session_ids() -> set[str]:
    """Session ids we can prove are running, from the daemon roster.

    Exact, not inferred: the roster maps sessionId -> pid, and a signal-0 kill
    says whether that process is alive. Only covers daemon-managed sessions —
    a foreground agent in a tmux pane is not in here.
    """
    import json
    import os
    from pathlib import Path

    roster = Path.home() / ".claude" / "daemon" / "roster.json"
    try:
        workers = json.loads(roster.read_text()).get("workers") or {}
    except Exception:
        return set()

    alive = set()
    for worker in workers.values() if isinstance(workers, dict) else workers:
        sid, pid = worker.get("sessionId"), worker.get("pid")
        if not sid or not pid:
            continue
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError, TypeError):
            continue
        alive.add(sid)
    return alive


def agent_cwds(panes: list[TmuxPane] | None = None) -> set[str]:
    """Directories that currently have an agent running in them.

    This is deliberately weaker than an id: `claude` does not hold its transcript
    open, so there is no way to map a pane back to the session inside it. All we
    can honestly say is "something is running here" — which, with tmuxinator
    opening the same project in the same window every time, is exactly as far as
    the evidence goes.
    """
    panes = tmux_panes() if panes is None else panes
    return {p.path for p in panes if is_agent_command(p.command) and p.path}
