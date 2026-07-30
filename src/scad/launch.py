"""Launching an interactive agent, and remembering which session it became.

An interactive session's only output channel is its trace, so "read what it
said" and "go back into it" both reduce to knowing its session id. This module
obtains that id honestly for three agents that each expose it differently:

- **claude** — the caller mints it (`--session-id`), so it is known before launch.
- **kimi** — the TUI writes a line to `session_index.jsonl` at start, carrying
  its own `workDir`, so the id is *confirmed* against a path we chose.
- **codex** — nothing is on disk until a turn happens, so a first turn is sent
  and the id read off the new rollout's filename.

Every family launches into tmux, and that is not incidental: tmux supplies the
pty that keeps Claude's entrypoint at `cli` rather than `sdk-cli`, which is what
keeps the session in its own `/resume` picker. A non-pty launch looks fine and
is wrong, so a missing tmux refuses rather than degrades — the one deliberate
exception to this codebase's degrade-never-raise rule.

The launch record lives at `~/.scad/launches/<session-id>.json`. A file, never
the index: `reindex --rebuild` drops every row and recomputes it from the
archive, and a launch record is an authored fact about an event with nothing to
recompute it from. Same reasoning, same tier as notes.

Nothing here is a precondition for anything. `scad session resume` works off
the index for every session on the machine — the record only makes it better,
by naming the pane the session is sitting in and by remembering how the session
was born. So every read degrades to None rather than raising.
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scad.config import get_scad_home
from scad.view import resume_command

# How the session was born, which is exactly what predicts whether the agent's
# own resume picker will show it (see the spec's §1.3). Recorded rather than
# re-derived: a human reading the file should not have to know the rule.
MINTED = "minted"            # scad chose the id and passed it in (claude)
TUI_NATIVE = "tui-native"    # the TUI made its own id and scad read it back
EXEC_PRIMED = "exec-primed"  # born headless — reachable, but hidden from the picker
UNRESOLVED = "unresolved"    # the session is real; its id is not known


def launches_root() -> Path:
    """`~/.scad/launches` — durable, never pruned, not part of the archive."""
    return get_scad_home() / "launches"


def _component(value) -> str | None:
    """A session id names a file, so refuse anything that is not a filename.

    The id reaches here from the command line and from an agent's own output,
    and a plausible-looking one carrying a slash would read or write outside
    the store instead of failing.
    """
    if not value or not isinstance(value, str):
        return None
    if value in (".", "..") or set(value) & set("/\\\0"):
        return None
    return value


def record_path(session_id: str) -> Path:
    return launches_root() / f"{session_id}.json"


def write_record(record: dict, *, key: str | None = None) -> Path:
    """Write one launch record, keyed by the session id inside it.

    Overwrites: a session id is unique to one launch, so a second write for the
    same id is a correction (an `unresolved` record gaining its id) rather than
    a new event.

    `key` names the file when there is no session id to name it — the launch
    happened and its record has to exist, so an unresolved one is filed under
    its tmux session instead. Nothing joins to it; it is there to be read.
    """
    session_id = _component(key if key is not None else record.get("session_id"))
    if session_id is None:
        raise ValueError(
            f"session_id {record.get('session_id')!r} is not a valid path component")
    path = record_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, default=str) + "\n")
    return path


def read_record(session_id: str) -> dict | None:
    """The launch record for a session, or None — unreadable counts as absent."""
    if _component(session_id) is None:
        return None
    try:
        record = json.loads(record_path(session_id).read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


# --- what gets launched -------------------------------------------------------

AGENTS = ("claude", "codex", "kimi")

# Two letters, so a tmux session name stays short enough to read in a status bar.
_CODE = {"claude": "cl", "codex": "cx", "kimi": "km"}

# The binary and the flags that make the session findable afterwards. Only
# claude takes an id; the other two are launched bare and read back.
_BINARY = {"claude": "claude", "codex": "codex", "kimi": "kimi"}
_FLAGS = {"claude": "--session-id {id}", "codex": "", "kimi": ""}

# The first turn sent to codex when the caller supplied no prompt. A constant,
# not a per-session string: it is assertable in a test, and turn 1 tells any
# later reader that the session was launched rather than started by hand.
#
# Every clause is load-bearing. Naming scad gives the session the context to
# reach for scad's own skills later; `Take no action and read nothing` is what
# makes that safe, because skills install globally into every family, so `scad`,
# `remember` and `recall` are live trigger words inside the session being
# primed — and a fired `remember` writes junk into the authored notes tier, the
# one tier nothing can re-derive.
PRIMING_PROMPT = ("This is a scad-launched session. Take no action and read "
                  "nothing. Reply with exactly: ready.")

# Bounded waits, all of them. Nothing here outlives the command.
POLL_INTERVAL = 0.2
# kimi's index line is written at TUI start, and TUI start is not instant — the
# observed case appeared inside 15s. The polling target is a few hundred bytes,
# so a generous deadline costs nothing; revise it if a slow start is ever seen
# rather than tuning it in advance.
KIMI_DEADLINE = 15.0
START_DEADLINE = 20.0     # TUI up and past any gate
ROLLOUT_DEADLINE = 30.0   # codex writing the rollout its first turn produces
# Delivering the first turn. The text is waited for on screen, the TUI is given
# a per-family settle (below), and the Enter is repeated as a backstop. An extra
# press is an empty submit, which every TUI ignores; a missed one costs the turn.
ECHO_DEADLINE = 5.0
SUBMIT_TRIES = 3
SUBMIT_RETRY_INTERVAL = 0.4
_ECHO_PROBE = 40          # enough of the text to identify it on screen

# How long to let a TUI settle between the paste and the Enter, per family.
# Measured 2026-07-30 by isolating the paste from the submit: kimi took the
# text out of its composer and *displayed it as sent* while never dispatching
# it, for every wait under a second. A single Enter 8s after the paste
# dispatched immediately — so the composer clearing is NOT proof the turn ran,
# and kimi needs a settle an order of magnitude longer than the others.
SUBMIT_SETTLE = {"kimi": 8.0}
SUBMIT_SETTLE_DEFAULT = 0.5

# Proof that a turn actually ran, per family — NOT that the composer cleared,
# which kimi does while discarding the turn. Only kimi is checked: claude and
# codex were verified dispatching reliably on 2026-07-30, and kimi was not.
DISPATCH_PROOF = {"kimi": re.compile(r"context:\s*[1-9]")}
DISPATCH_DEADLINE = 25.0


class LaunchError(Exception):
    """The launch did not happen. Distinct from "it happened and the id is unknown"."""


# --- tmux --------------------------------------------------------------------

# Which tmux server scad talks to. Unset means tmux's own default, which is what
# a human runs. The suite sets it so a test can never create a session on — or
# kill — the server holding somebody's actual work.
TMUX_SOCKET_ENV = "SCAD_TMUX_SOCKET"

_TMUX_TIMEOUT = 5

# A detached session would otherwise default to 80x24, and every gate screen
# here is read by matching lines. Wider means fewer wrapped labels to reason
# about; the pane resizes to the client the moment a human attaches.
_WIDTH, _HEIGHT = 200, 50


def _tmux(args: list[str]) -> subprocess.CompletedProcess:
    """Run one tmux command. Never raises — the caller reads returncode."""
    socket = os.environ.get(TMUX_SOCKET_ENV)
    prefix = ["tmux", "-L", socket] if socket else ["tmux"]
    try:
        return subprocess.run(prefix + args, capture_output=True, text=True,
                              timeout=_TMUX_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(args=prefix + args, returncode=127,
                                           stdout="", stderr=str(exc))


def tmux_available() -> bool:
    """Is there a tmux we can drive?"""
    if shutil.which("tmux") is None:
        return False
    return _tmux(["-V"]).returncode == 0


def session_names() -> set[str]:
    """Every tmux session name on the server. Empty when there is no server."""
    result = _tmux(["list-sessions", "-F", "#{session_name}"])
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def tmux_session_name(agent: str, taken: set[str] | None = None, now=None) -> str:
    """`scad-cx-1430` — agent and launch minute, uniquified.

    Two launches inside one minute are ordinary and tmux refuses a duplicate
    session name outright, so the collision has to be resolved here.
    """
    stamp = time.strftime("%H%M", now or time.localtime())
    base = f"scad-{_CODE.get(agent, agent[:2])}-{stamp}"
    taken = taken or set()
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def new_session(name: str, cwd: Path, command: str) -> str:
    """Start a detached tmux session running `command`, and return its target.

    `; exec bash` follows the container precedent at `container.py:196` and keeps
    the pane alive after the agent exits, so a failed launch is diagnosable
    instead of vanishing along with whatever it printed.
    """
    result = _tmux(["new-session", "-d", "-s", name, "-c", str(cwd),
                    "-x", str(_WIDTH), "-y", str(_HEIGHT),
                    f"{command}; exec bash"])
    if result.returncode != 0:
        raise LaunchError(f"tmux refused to start a session: "
                          f"{result.stderr.strip() or result.stdout.strip()}")
    return f"{name}:0.0"


def capture_pane(target: str) -> str:
    """What the pane is showing right now. Empty on any failure."""
    result = _tmux(["capture-pane", "-p", "-t", target])
    return result.stdout if result.returncode == 0 else ""


def send_text(target: str, text: str) -> None:
    """Type `text` into the pane, submitting nothing.

    `-l` is not optional. Plain `send-keys` treats its argument as key names and
    was observed to drop a prompt silently, leaving an empty composer and no
    transcript — an hour to diagnose, because the pane looked idle rather than
    broken.
    """
    _tmux(["send-keys", "-l", "-t", target, text])


def submit(target: str) -> None:
    """Press Enter. Always a separate call from the text it submits."""
    _tmux(["send-keys", "-t", target, "Enter"])


# --- reading the pane --------------------------------------------------------

STARTING = "starting"
GATE = "gate"
READY = "ready"

# Both measured gates end with this line and present numbered options; no ready
# screen does either. That pair is the discriminator, in preference to the
# prose, which drifts with every version.
_GATE_MARKER = "Press enter to continue"

# `› 1. Yes, continue` — the highlighted option carries a marker and is still an
# option. A wrapped label continues on a line with no number and is ignored,
# which is what keeps the update gate's `curl | sh` label from splitting in two.
_OPTION = re.compile(r"^[\s›>*]*(\d+)\.\s+(.*?)\s*$")

# Options we are willing to choose, by label. A whitelist, because the cost of
# the two answers is not symmetric: the update gate's default runs
# `curl -fsSL … | sh`, so an unrecognised screen must be reported rather than
# answered, and Enter must never be sent at a gate at all.
_SAFE_OPTION = re.compile(r"^(skip|yes)\b", re.I)
_NEVER = re.compile(r"update now", re.I)


def gate_options(text: str) -> list[tuple[str, str]]:
    """The numbered options a gate is offering, in the order it lists them."""
    out = []
    for line in text.splitlines():
        match = _OPTION.match(line)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def gate_choice(text: str) -> str | None:
    """The number to send to clear this gate, or None if we do not recognise it.

    Chosen by label, never by position: the safe answer is `Skip` on the update
    gate and `Yes, continue` on the trust gate, and either could be renumbered.
    """
    for number, label in gate_options(text):
        if _NEVER.search(label):
            continue
        if _SAFE_OPTION.match(label):
            return number
    return None


def pane_state(text: str) -> str:
    """`starting`, `gate` or `ready` — what the pane is doing."""
    if not text.strip():
        return STARTING
    if _GATE_MARKER in text and gate_options(text):
        return GATE
    return READY


def _wait(predicate, deadline: float, interval: float = POLL_INTERVAL):
    """Poll `predicate` until it returns something truthy, or give up."""
    end = time.monotonic() + deadline
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= end:
            return None
        time.sleep(interval)


def settle_pane(target: str, deadline: float = START_DEADLINE,
                clear_gates: bool = False) -> tuple[str, str]:
    """Wait for the TUI to be ready, clearing gates we recognise on the way.

    Returns `(state, pane text)`. A gate we cannot read stops the wait
    immediately — there is nothing to learn by watching it, and guessing at it
    is the one thing that must not happen.

    Each distinct gate screen is answered exactly once. A TUI mid-redraw shows
    the same screen for several polls, and re-sending would type the number
    into whatever came next.
    """
    end = time.monotonic() + deadline
    answered: set[str] = set()
    text = ""
    while True:
        text = capture_pane(target)
        state = pane_state(text)
        if state == READY:
            return READY, text
        if state == GATE:
            if not clear_gates:
                return GATE, text
            choice = gate_choice(text)
            if choice is None:
                return GATE, text
            if text not in answered:
                send_text(target, choice)
                answered.add(text)
        if time.monotonic() >= end:
            return pane_state(text), text
        time.sleep(POLL_INTERVAL)


# --- kimi: the id is read back and confirmed ---------------------------------

def kimi_index_path() -> Path:
    """The index kimi appends to when a TUI starts."""
    return Path.home() / ".kimi-code" / "session_index.jsonl"


def read_kimi_index(path: Path | None = None) -> list[dict]:
    """Every readable line of kimi's session index. A bad line costs only itself."""
    path = kimi_index_path() if path is None else Path(path)
    try:
        raw = path.read_text()
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _realpath(value) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except (OSError, TypeError, ValueError):
        return str(value)


def kimi_candidates(entries: list[dict], since: int, cwd) -> list[str]:
    """New index lines that declare `cwd` as their working directory.

    The strongest read-back of the three: the record names the directory itself,
    so the answer is confirmed against a path we chose rather than correlated by
    timestamp. Comparison is on realpaths — kimi records one, and `~/Dropbox` is
    a CloudStorage symlink on this machine, so an unresolved compare never
    matches.

    Ids come back the way the index stores them: `kimi_identity_from_path` keys
    a row on the bare uuid, so a record carrying kimi's `session_` prefix would
    join to nothing.
    """
    want = _realpath(cwd)
    out = []
    for entry in entries[since:]:
        if _realpath(entry.get("workDir")) != want:
            continue
        sid = entry.get("sessionId")
        if not isinstance(sid, str) or not sid:
            continue
        out.append(sid[len("session_"):] if sid.startswith("session_") else sid)
    return out


# --- codex: the id is read off the rollout the first turn creates ------------

def codex_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


# rollout-<timestamp>-<uuid>.jsonl, where the timestamp has dashes of its own —
# so the uuid is anchored by its shape, not by counting separators.
_ROLLOUT = re.compile(
    r"^rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.I)


def rollout_id_from_name(name: str) -> str | None:
    match = _ROLLOUT.match(name)
    return match.group(1) if match else None


def rollout_ids(root: Path | None = None) -> set[str]:
    """Every codex thread id with a rollout on disk, across the dated tree."""
    root = codex_sessions_root() if root is None else Path(root)
    out = set()
    try:
        for path in root.rglob("rollout-*.jsonl"):
            found = rollout_id_from_name(path.name)
            if found:
                out.add(found)
    except OSError:
        return out
    return out


# --- the launch ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_turn(target: str, text: str, agent: str = "") -> None:
    """Deliver one turn: type it, let the TUI settle, then submit it.

    The settle is the whole point, and it is per-family. kimi ingests a pasted
    prompt asynchronously and, until it has finished, an Enter does not submit
    — it takes the text out of the composer and **renders it as sent while
    never dispatching it**. Observed 2026-07-30: `context: 0% (0/256k)` for
    minutes with the turn displayed above the composer, and further Enters
    changing nothing. Isolating paste from submit showed a single Enter 8s
    later dispatches immediately.

    Two consequences worth stating because both cost time to learn:

    - **A cleared composer is not proof the turn ran.** The obvious check is
      the wrong one; only the family's own progress signal is real.
    - **The launch still reports success**, since kimi's id comes from its
      index line and does not depend on the turn. A dropped prompt is
      therefore silent, which is what makes the settle worth paying for.

    Runs only after READY, never at a gate — a stray Enter there would answer
    the update prompt's `curl | sh` default.
    """
    send_text(target, text)
    # The echo proves the paste was ingested; it does not prove the TUI is
    # ready to act on Enter. Both waits are needed, for different reasons.
    probe = text.strip()[:_ECHO_PROBE]
    _wait(lambda: probe and probe in capture_pane(target), ECHO_DEADLINE)
    time.sleep(SUBMIT_SETTLE.get(agent, SUBMIT_SETTLE_DEFAULT))

    for attempt in range(SUBMIT_TRIES):
        submit(target)
        if attempt + 1 == SUBMIT_TRIES:
            return
        time.sleep(SUBMIT_RETRY_INTERVAL)
        if probe and probe not in capture_pane(target):
            return          # composer cleared — weak, but worth exiting on


def _turn_undispatched(target: str, agent: str) -> str | None:
    """None if the turn demonstrably ran; a description of the problem if not.

    Exists because kimi will accept a pasted prompt, clear its composer, render
    the text as a sent turn, and then **not run it** — leaving a session that
    looks correct in every way scad can otherwise see. Measured 2026-07-30: the
    same keystrokes dispatched when issued by hand into a pane and did not when
    issued by the launcher, and that difference is still unexplained.

    Until it is understood, a launch that cannot prove the turn ran says so.
    A wrong success is worse than an honest warning: the session is real and
    resumable either way, and the only thing at stake is whether the human
    knows their first instruction is still sitting there unrun.
    """
    proof = DISPATCH_PROOF.get(agent)
    if proof is None:
        return None
    if _wait(lambda: bool(proof.search(capture_pane(target))), DISPATCH_DEADLINE):
        return None
    return (f"{agent} accepted the prompt but showed no sign of running it "
            f"within {DISPATCH_DEADLINE:.0f}s. The session is real and "
            f"resumable; the first turn may need submitting by hand — attach "
            f"to the pane and press Enter.")


def _resolve_kimi(target, cwd, since, prompt, say) -> tuple:
    """Wait for kimi's index line, and confirm it is ours by its own workDir."""
    say("waiting for kimi's index line")
    found = _wait(lambda: kimi_candidates(read_kimi_index(), since, cwd),
                  KIMI_DEADLINE)
    candidates = found or []

    if len(candidates) == 1:
        problem = None
        if prompt:
            settle_pane(target)
            _first_turn(target, prompt, "kimi")
            problem = _turn_undispatched(target, "kimi")
            if problem:
                say(problem)
        return candidates[0], TUI_NATIVE, problem, {}

    if not candidates:
        return None, UNRESOLVED, (
            f"kimi wrote no new index line for {cwd} within "
            f"{KIMI_DEADLINE:.0f}s. The pane is live and the session is real — "
            f"find its id in ~/.kimi-code/session_index.jsonl and resume with "
            f"kimi --session <id>."), {}

    # Two lines means two sessions started in the window we were watching, and
    # nothing on disk says which one is ours. Refuse rather than pick.
    return None, UNRESOLVED, (
        f"kimi wrote {len(candidates)} new index lines for {cwd}; scad cannot "
        f"tell which is this launch."), {"candidates": candidates}


def _resolve_codex(target, before, prompt, say) -> tuple:
    """Clear any gate, send the first turn, and read the id off the new rollout.

    Codex writes nothing until a turn happens, so the turn is not optional —
    it is how the session acquires an identity at all.
    """
    say("waiting for the codex TUI")
    state, text = settle_pane(target, clear_gates=True)
    if state != READY:
        return None, UNRESOLVED, (
            f"the codex TUI did not come up clean — no turn was sent and no "
            f"key was pressed. The pane is showing:\n{text.strip()}"), {}

    turn = prompt or PRIMING_PROMPT
    _first_turn(target, turn, "codex")
    say("waiting for the rollout the first turn creates")
    new = _wait(lambda: rollout_ids() - before, ROLLOUT_DEADLINE)
    new = sorted(new or ())

    if len(new) == 1:
        return new[0], TUI_NATIVE, None, {}

    if not new:
        return None, UNRESOLVED, (
            f"codex produced no rollout within {ROLLOUT_DEADLINE:.0f}s of the "
            f"first turn. The pane is live; most likely the turn never "
            f"submitted — check it before assuming worse."), {}

    return None, UNRESOLVED, (
        f"{len(new)} codex rollouts appeared at once; scad cannot tell which "
        f"is this launch."), {"candidates": new}


def launch(agent: str, cwd, *, prompt: str | None = None,
           binary: str | None = None, say=None) -> dict:
    """Start an interactive agent in tmux and record which session it became.

    Detached: the pane is left running and the caller keeps its terminal.
    Returns the launch record, which is on disk before this returns — a caller
    must never observe a launch with no record, including the launches whose id
    could not be resolved.

    `binary` overrides what gets executed. It is how every test here drives a
    stub instead of a real agent, which is not a convenience: one real launch
    costs a model call and leaves a junk session in the corpus.
    """
    say = say or (lambda _message: None)

    if agent not in AGENTS:
        raise LaunchError(f"Unknown agent {agent!r}. One of: {', '.join(AGENTS)}.")

    cwd = Path(cwd).expanduser()
    if not cwd.is_dir():
        raise LaunchError(f"{cwd} is not a directory.")

    # The one place this codebase refuses instead of degrading. Without a pty
    # Claude stamps the session `entrypoint: sdk-cli` and its own picker drops
    # it — a result that looks fine and is wrong.
    if not tmux_available():
        raise LaunchError(
            "tmux is required to launch an interactive agent, and it is not "
            "usable here. scad will not fall back to a non-pty launch: that "
            "silently produces a session the agent's own resume picker hides.")

    name = tmux_session_name(agent, taken=session_names())
    binary = binary or _BINARY[agent]
    session_id = str(uuid.uuid4()) if agent == "claude" else None

    # Both read-back families need a before-picture, taken before the TUI can
    # possibly have written anything.
    before_kimi = len(read_kimi_index()) if agent == "kimi" else 0
    before_rollouts = rollout_ids() if agent == "codex" else set()

    command = f"{binary} {_FLAGS[agent].format(id=session_id)}".strip()
    target = new_session(name, cwd, command)
    say(f"launched {agent} in {target}")

    extra: dict = {}
    problem = None
    if agent == "claude":
        provenance = MINTED
        if prompt:
            settle_pane(target)
            _first_turn(target, prompt)
    elif agent == "kimi":
        session_id, provenance, problem, extra = _resolve_kimi(
            target, cwd, before_kimi, prompt, say)
    else:
        session_id, provenance, problem, extra = _resolve_codex(
            target, before_rollouts, prompt, say)

    record = {
        "agent": agent,
        "session_id": session_id,
        "cwd": str(cwd),
        "tmux": target,
        "started": _now_iso(),
        "resume": resume_command({"id": session_id, "agent": agent,
                                  "cwd": str(cwd), "kind": "main"}),
        "provenance": provenance,
    }
    record.update(extra)
    if problem:
        record["problem"] = problem

    write_record(record, key=session_id or f"unresolved-{name}")
    return record
