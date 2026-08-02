"""What is running right now — tmux panes and scad containers.

The index describes what happened; this describes what is live. Neither can
answer the other's question, which is why this is computed at render time and
never stored.

Every function here degrades to an empty list on any failure — no tmux server,
tmux not installed, docker unreachable, a timeout, malformed output. A viewer
that raises because tmux is not running would be useless.
"""

import calendar
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TMUX_FORMAT = ("#{session_name}:#{window_index}.#{pane_index}|#{window_name}|"
               "#{pane_current_path}|#{pane_current_command}")
_TIMEOUT = 5

# Slack allowed between the registry's `procStart` and the kernel's start time.
# Measured difference is 0.0s across every live entry; one second only absorbs a
# platform that rounds where another truncates. It is far too tight for a
# recycled pid to slip through.
_START_SKEW = 1.0

# Claude Code shows up in tmux as its version string (e.g. "2.1.219"), not as
# "claude" — the binary re-execs. codex uses its own name.
_VERSION = re.compile(r"^\d+\.\d+\.\d+")
# Measured on this machine: `codex` and `kimi` show under their own names,
# Claude as its version string. kimi was missing here for as long as it had a
# reader — so its panes never appeared, and because the "a pane is open for it"
# cwd set is built from recognised panes only, a kimi session sharing a
# directory with Claude was filed against Claude's pane and told to go there.
_AGENT_NAMES = {"codex", "claude", "kimi"}


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


def find_pane(target: str, panes: list[TmuxPane] | None = None) -> TmuxPane | None:
    """The agent pane sitting at `target` right now, or None.

    Both halves matter. A recorded target outlives the pane it named — tmux
    hands out the same session:window.pane again as windows come and go — so
    "the target exists" is not "the session is still there". Requiring an agent
    in it is the cheapest check that keeps `scad session resume` from attaching
    somebody to their own shell.
    """
    panes = tmux_panes() if panes is None else panes
    for pane in panes:
        if pane.target == target and is_agent_command(pane.command):
            return pane
    return None


def attach_argv(target: str, *, inside: bool | None = None) -> list[str]:
    """tmux argv that lands the caller on `target`, for `os.execvp`.

    Two forms, because a client already inside tmux cannot attach again —
    tmux refuses to nest — and one already outside has nothing to switch. The
    bare `;` is its own argv element: with no shell in between there is nothing
    to escape it from, unlike `_goto`'s pasteable string.

    The pane is always selected explicitly. `select-window -t main:3.1`
    discards the pane component and leaves you wherever that window was last.
    """
    inside = bool(os.environ.get("TMUX")) if inside is None else inside
    session = target.split(":", 1)[0]
    window, _, pane = target.rpartition(".")
    if not window or not pane.isdigit():
        window, pane = target, ""

    argv = ["tmux", "switch-client" if inside else "select-window", "-t", window]
    if pane:
        argv += [";", "select-pane", "-t", target]
    if not inside:
        argv += [";", "attach-session", "-t", session]
    return argv


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


@dataclass(frozen=True)
class ClaudeSession:
    """A Claude Code process running right now, named by session id.

    Every field comes straight out of the registry file — nothing here is
    derived, correlated or guessed. `waiting_for` is empty unless `status` is
    `waiting`, in which case it names what the session is blocked on (e.g.
    "permission prompt").
    """
    session_id: str
    pid: int
    cwd: str = ""
    # Set only for a session inside a scad container. Its `cwd` is a
    # `/workspace/...` path that does not exist on the host, so the run id is
    # the only thing that can resolve a project for it.
    run_id: str = ""
    name: str = ""
    status: str = ""       # idle | busy | waiting
    waiting_for: str = ""
    started_at: int = 0    # epoch ms, when the *session* began
    kind: str = ""         # interactive | ...
    entrypoint: str = ""   # cli | ...
    version: str = ""


def _sessions_dir() -> Path:
    """Claude Code's process->session registry: one `<pid>.json` per process."""
    return Path.home() / ".claude" / "sessions"


def _process_start_times(pids: list[int]) -> dict[int, float]:
    """Kernel start time, in epoch seconds, for each pid `ps` can still see.

    One `ps` call for the whole batch. `returncode` is deliberately ignored:
    when some of the requested pids have exited `ps` exits non-zero while still
    printing the survivors on stdout, and those survivors are the answer.

    `ps` renders `lstart` in *local* time; the registry renders `procStart` in
    UTC. Both are normalised to epoch seconds here so the comparison never has
    to reason about a timezone. The one place that leaks is the ambiguous hour
    of a DST fall-back, where `mktime` may guess an hour wrong — that costs a
    live session its `open` status for an hour a year, and never invents one.
    """
    if not pids:
        return {}
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except Exception:
        return {}

    starts: dict[int, float] = {}
    for line in result.stdout.splitlines():
        pid, _, lstart = line.strip().partition(" ")
        try:
            starts[int(pid)] = time.mktime(time.strptime(lstart.strip()))
        except (ValueError, OverflowError):
            continue
    return starts


def _is_alive(pid: int) -> bool:
    """Does this pid belong to a process that exists?

    `ProcessLookupError` is the only answer that means dead. `PermissionError`
    means the opposite of what it looks like: the process is there, we simply
    are not allowed to signal it because someone else owns it.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return True


def _utc_ctime(stamp) -> float | None:
    """`procStart` -> epoch seconds. It is ctime format written in UTC."""
    if not isinstance(stamp, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(stamp.strip())))
    except (ValueError, OverflowError):
        return None


def _read_entry(path: Path) -> tuple[ClaudeSession, float] | None:
    """One registry file -> (session, the process start time it claims).

    None for anything we cannot vouch for: unreadable, not JSON, not an object,
    no session id, a pid that is not an int, a filename that disagrees with the
    pid inside, or no `procStart` to check the pid against. The descriptive
    fields are allowed to be missing and default to empty — losing a `name` is
    a cosmetic loss, whereas losing the identity fields means the record cannot
    be trusted at all.
    """
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None

    session_id = record.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        pid = int(record.get("pid"))
        if pid != int(path.stem):
            return None
    except (TypeError, ValueError):
        return None

    proc_start = _utc_ctime(record.get("procStart"))
    if proc_start is None:
        return None

    try:
        started_at = int(record.get("startedAt") or 0)
    except (TypeError, ValueError):
        started_at = 0

    def text(key: str) -> str:
        value = record.get(key)
        return value if isinstance(value, str) else ""

    return ClaudeSession(
        session_id=session_id,
        pid=pid,
        cwd=text("cwd"),
        name=text("name"),
        status=text("status"),
        waiting_for=text("waitingFor"),
        started_at=started_at,
        kind=text("kind"),
        entrypoint=text("entrypoint"),
        version=text("version"),
    ), proc_start


def _runs_root() -> Path:
    from scad.config import get_scad_home

    return get_scad_home() / "runs"


def _read_container_entry(path: Path, run_id: str) -> ClaudeSession | None:
    """One registry file from inside a container -> a session, or None.

    Deliberately not `_read_entry`. That one proves two things about the host:
    the pid is alive, and it is the same process the file was written for. Both
    are meaningless here -- the pid belongs to the container's namespace, so
    `os.kill` would ask about an unrelated host process, and `procStart` is the
    container's clock and does not parse as a UTC ctime at all. Pointing the
    host reader at these paths returns nothing, silently.

    What replaces those checks is stronger: the caller only passes run ids
    whose container docker says is up.
    """
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    session_id = record.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0

    def text(key: str) -> str:
        value = record.get(key)
        return value if isinstance(value, str) else ""

    try:
        started_at = int(record.get("startedAt") or 0)
    except (TypeError, ValueError):
        started_at = 0

    return ClaudeSession(
        session_id=session_id, pid=pid, cwd=text("cwd"), name=text("name"),
        status=text("status"), waiting_for=text("waitingFor"),
        started_at=started_at, kind=text("kind"), entrypoint=text("entrypoint"),
        version=text("version"), run_id=run_id,
    )


def container_live_sessions(run_ids, runs_root: Path | None = None) -> list[ClaudeSession]:
    """Agents running inside scad's own containers, right now.

    The same registry Claude writes on the host, distributed: a run bind-mounts
    `~/.scad/runs/<run-id>/claude` to the container's `~/.claude`, so the
    container writes its `<pid>.json` there. scad only ever read the host's,
    which made an agent in a scad container -- the thing scad itself started --
    the one kind of session the live view could not see.

    `run_ids` are the runs whose containers are up. A registry file outlives
    its container, so liveness is the container, not the file.

    Empty on every failure, per the module contract.
    """
    root = Path(runs_root) if runs_root is not None else _runs_root()
    found = []
    for run_id in sorted(run_ids or ()):
        directory = root / run_id / "claude" / "sessions"
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            continue
        for path in paths:
            session = _read_container_entry(path, run_id)
            if session is not None:
                found.append(session)
    found.sort(key=lambda s: s.started_at, reverse=True)
    return found


def claude_live_sessions(registry: Path | str | None = None) -> list[ClaudeSession]:
    """Claude sessions that are open right now, by name, for certain.

    Claude Code keeps an exact process->session registry at
    `~/.claude/sessions/<pid>.json`, and this reads it. That makes it the one
    source here that can name a session rather than a directory: `agent_cwds`
    can only say "something is running in this folder", because a tmux pane
    cannot be resolved back to a session id. This can, so nothing in here is
    correlated by time or path — if the registry does not name a session, that
    session is absent from the result rather than guessed at.

    Two independent checks stand between a file and a record, because a
    `<pid>.json` proves neither that the process lives nor that it is the same
    process:

    1. Liveness — `os.kill(pid, 0)`. Claude does not remove these files
       reliably; a stale one looks exactly like a live one.
    2. Identity — the running process must have started at the instant the file
       records in `procStart`, which defeats pid reuse. Measured against all 12
       live entries on this machine, `procStart` read as UTC equals the kernel's
       start time to the second, every time, so this can be an equality test
       rather than a window. `startedAt` cannot serve: it is when the *session*
       began, and it trailed the process start by up to 838s in the same sample.
       A recycled pid would have to have been spawned in the same second as the
       process that died holding the file for this to pass.

    Sorted most recently started first. Empty on every failure, per the module
    contract: no registry directory, an unreadable file, malformed JSON, a pid
    that is not an int, no `ps` to check start times with. A bad file costs its
    own entry and no others.

    `registry` overrides the directory, for tests.
    """
    directory = Path(registry) if registry is not None else _sessions_dir()
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []

    entries = [entry for entry in (_read_entry(p) for p in paths) if entry is not None]
    live = [(session, start) for session, start in entries if _is_alive(session.pid)]

    starts = _process_start_times([session.pid for session, _ in live])
    sessions = [
        session for session, claimed in live
        if session.pid in starts and abs(starts[session.pid] - claimed) <= _START_SKEW
    ]
    return sorted(sessions, key=lambda s: (-s.started_at, s.session_id))


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
