"""The notes store — `/remember` capture, on disk.

The one tier that is never rederivable. Turns can be re-extracted while raw
survives and sessions can be recomputed from turns, but a note is self-report:
if the file is lost, nothing anywhere can reconstruct it. So the file is truth
and the `notes` table is only an index over it, appending is the only write,
and nothing in here ever deletes, truncates or rewrites.

Path: `~/.scad/notes/<agent>/<session-uuid>.jsonl`

Session-keyed and project-free on purpose. A project is a *computed* property
of a cwd — redefining what counts as a project root is a config edit, and any
durable path containing `<project>` would orphan its files the moment that
happened. Sharding by agent instead keeps the one property of a note that
cannot be recomputed.
"""

import json
import os
import unicodedata
from datetime import datetime
from pathlib import Path

from scad.config import get_scad_home

# The record, from capture-format.md §Record — plus `cwd_at_write`, which that
# spec does not have because it stored the project in the path. We do not, so
# the location has to live in the record or the project stops being rederivable
# once the trace is pruned. Order is the on-disk key order: these files are read
# by humans as often as by code.
NOTE_FIELDS = (
    "ts",            # ISO8601, when the capture was made
    "span",          # what it covers — "since-last" or a short description
    "topic",         # semantic subject, kebab
    "relation",      # continue | shift | branch | return
    "parent",        # the earlier topic, on branch / return
    "title",         # one-line label
    "text",          # adaptive narrative, markdown
    "tags",          # dense keywords — the search index
    "entities",      # canonical named things
    "sessions",      # refs into the raw archive, e.g. claude:<id>
    "invalidation",  # what would revise this capture
    "cwd_at_write",  # where the session was working; keeps `project` derivable
)

RELATIONS = ("continue", "shift", "branch", "return")

_LIST_FIELDS = ("tags", "entities", "sessions")

# Claude Code truncates an encoded project directory at 200 characters and
# appends a hash of the full path. Both values are read from the shipped binary,
# not guessed: `iRt=200`, and `o0h(e) = Math.abs(art(e)).toString(36)`.
PROJECT_DIR_CAP = 200

_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _js_string_hash(text: str) -> int:
    """Claude Code's `art(e)`: the classic Java 31-multiplier hash, 32-bit signed.

        function art(e){let t=0;for(let r=0;r<e.length;r++)
                        t=(t<<5)-t+e.charCodeAt(r)|0;return t}

    `|0` truncates to a signed 32-bit int on every iteration, so overflow wraps
    rather than growing — Python ints do not, hence the explicit mask.
    """
    h = 0
    for ch in text:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = _B36[rem] + out
    return out


def encode_cwd(cwd: str) -> str:
    """Encode a working directory the way Claude Code names `projects/<dir>`.

    Every non-alphanumeric character becomes a dash — not only `/`. Verified
    two ways: the encoder in the 2.1.220 binary is
    `e.replace(/[^a-zA-Z0-9]/g,"-")`, and a real session started in
    `/private/tmp/scad enc.test_dir-1` landed in
    `~/.claude/projects/-private-tmp-scad-enc-test-dir-1`.

    The mapping is therefore lossy and ONE-WAY: `a.b`, `a_b` and `a-b` all
    encode to `a-b`. Never try to decode a directory name back to a path — read
    the `cwd` field out of a record instead.
    """
    text = unicodedata.normalize("NFC", cwd)
    encoded = "".join(c if c.isascii() and c.isalnum() else "-" for c in text)
    if len(encoded) <= PROJECT_DIR_CAP:
        return encoded
    return f"{encoded[:PROJECT_DIR_CAP]}-{_base36(abs(_js_string_hash(text)))}"


def notes_root() -> Path:
    """`~/.scad/notes` — durable, never pruned, not part of the archive."""
    return get_scad_home() / "notes"


def note_path(session_id: str, agent: str = "claude") -> Path:
    return notes_root() / agent / f"{session_id}.jsonl"


def _checked_id(value: str, label: str) -> str:
    """A session id and an agent both name a path component. Refuse anything else.

    Not paranoia about a hostile caller — the caller is an LLM composing a
    record, and a plausible-looking id with a slash in it would silently write
    outside the store or, worse, into a directory the store then cannot find.
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"{label} is required")
    if value in (".", "..") or set(value) & set("/\\\0"):
        raise ValueError(f"{label} {value!r} is not a valid path component")
    return value


def normalize_note(record: dict, *, cwd: str | None = None) -> dict:
    """Fill the defaults a caller may omit; leave everything else alone.

    Unknown keys are carried through untouched. The record shape is owned by
    capture-format.md, and a store that dropped fields it did not recognize
    would make every future addition to that spec a code change here.
    """
    if not isinstance(record, dict):
        raise ValueError("a note must be a JSON object")

    out = {field: record.get(field) for field in NOTE_FIELDS}
    out.update({k: v for k, v in record.items() if k not in NOTE_FIELDS})

    out["ts"] = record.get("ts") or datetime.now().astimezone().isoformat(timespec="seconds")
    out["span"] = record.get("span") or "since-last"
    out["cwd_at_write"] = record.get("cwd_at_write") or cwd or os.getcwd()
    for field in _LIST_FIELDS:
        if out.get(field) is None:
            out[field] = []
    return out


def append_note(
    record: dict, *, session_id: str, agent: str = "claude", cwd: str | None = None
) -> Path:
    """Append one capture to a session's note file. The only write there is.

    Opened "a" so the append is a single positioned write: concurrent `/remember`
    calls from a session and one of its subagents interleave as whole lines
    rather than corrupting each other, and no existing byte is ever revisited.
    """
    _checked_id(session_id, "session id")
    _checked_id(agent, "agent")
    line = json.dumps(normalize_note(record, cwd=cwd), ensure_ascii=False, default=str)

    path = note_path(session_id, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


# --- --current: the session whose trace is being written in this cwd right now ---

# Two transcripts in one cwd touched within this many seconds of each other are
# both taken to be live. A live session appends on every turn, so two minutes is
# generous for "currently being written"; anything much longer starts sweeping in
# this morning's finished work and turns an ordinary busy project into a
# permanent ambiguity. This is deliberately NOT "how many files are in the
# directory" — a project with a year of history has dozens and no live session.
LIVE_WINDOW_S = 120


# The variable each harness exports naming the session it is currently running.
# Both are verified to equal the id in that agent's own transcript path:
# `~/.claude/projects/<encoded-cwd>/<CLAUDE_CODE_SESSION_ID>.jsonl` and
# `~/.codex/sessions/<Y>/<M>/<D>/rollout-<ts>-<CODEX_THREAD_ID>.jsonl`.
#
# Kimi is deliberately absent: it exports nothing, and an entry here is a claim
# that the variable exists. Adding an agent is one line — and MUST be the
# agent's own variable, never a near-enough one (see `current_session_id`).
SESSION_ID_ENV = {
    "claude": "CLAUDE_CODE_SESSION_ID",
    "codex": "CODEX_THREAD_ID",
}


class NoteTargetError(Exception):
    """`--current` could not name exactly one session."""


class NoSessionFound(NoteTargetError):
    pass


class AmbiguousSession(NoteTargetError):
    pass


def claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _transcripts_in(directory: Path) -> list[Path]:
    """Top-level `*.jsonl` only — never `<parent>/subagents/agent-*.jsonl`.

    A subagent file repeats its PARENT's sessionId and is named by its agentId,
    so treating one as the current session would key the note onto a row that is
    not the session the human is talking to.
    """
    if not directory.is_dir():
        return []
    return [p for p in directory.glob("*.jsonl") if p.is_file()]


def _first_cwd(path: Path) -> str | None:
    """The `cwd` off the first record that carries one, without parsing the file."""
    try:
        with path.open("rb") as fh:
            for i, raw in enumerate(fh):
                if i > 40:
                    break
                try:
                    rec = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(rec, dict) and rec.get("cwd"):
                    return rec["cwd"]
    except OSError:
        return None
    return None


def _scan_for_cwd(projects_root: Path, cwd: str) -> list[Path]:
    """Fallback: find transcripts whose records say they ran in `cwd`.

    The encoded directory name is a Claude Code implementation detail and could
    change under us; the `cwd` field inside the records is the agent's own
    statement of where it ran, so it is the honest backstop. Costs a read of the
    head of every top-level transcript, which is why it is not the first move.
    """
    if not projects_root.is_dir():
        return []
    return [
        p
        for d in projects_root.iterdir() if d.is_dir()
        for p in _transcripts_in(d)
        if _first_cwd(p) == cwd
    ]


def current_session_id(
    cwd: str | None = None, *, agent: str = "claude", projects_root: Path | None = None,
    window_s: float = LIVE_WINDOW_S,
) -> str:
    """Resolve the session `agent` is running right now.

    The agent already knows its own id and exports it, so ask before searching:
    `SESSION_ID_ENV[agent]` is authoritative and needs no filesystem at all.

    It must be that agent's OWN variable, and nothing else. These are ordinary
    environment variables, so they are inherited: a codex or a kimi launched
    from inside a Claude session carries `CLAUDE_CODE_SESSION_ID` with it, and
    reading it would file the note into the codex shard under a Claude
    session's id — a wrong answer that looks exactly like a right one. So a
    missing variable is an error, never a reason to consult a different one or
    to go looking through another agent's transcripts.

    Only claude has a fallback, and only for its own directory: encoded
    directory first, `cwd`-field scan next, newest mtime wins. Where several
    are genuinely live that raises rather than picking one, for the same reason
    — a note filed against the wrong session is worse than a note not filed,
    because nothing downstream can detect the mistake.
    """
    env_var = SESSION_ID_ENV.get(agent)
    if env_var:
        exported = (os.environ.get(env_var) or "").strip()
        if exported:
            try:
                return _checked_id(exported, f"${env_var}")
            except ValueError as exc:
                # Set but unusable. Refuse loudly instead of falling through to
                # the scan: something is wrong with the environment, and a
                # silently different answer would hide it.
                raise NoSessionFound(
                    f"{exc}. Unset {env_var} or pass --session <id> explicitly."
                ) from exc

    if agent != "claude":
        detail = (
            f"{env_var} is not set" if env_var
            else f"{agent} exports no session variable"
        )
        raise NoSessionFound(
            f"Cannot resolve the current {agent} session: {detail}. "
            "Pass --session <id> explicitly."
        )

    root = projects_root or claude_projects_root()
    here = os.path.realpath(cwd or os.getcwd())

    candidates = _transcripts_in(root / encode_cwd(here)) or _scan_for_cwd(root, here)
    if not candidates:
        raise NoSessionFound(
            f"No Claude transcript for {here}. Pass --session <id> explicitly."
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest = candidates[0].stat().st_mtime
    live = [p for p in candidates if newest - p.stat().st_mtime <= window_s]

    if len(live) > 1:
        names = ", ".join(p.stem for p in live)
        raise AmbiguousSession(
            f"{len(live)} sessions are live in {here} ({names}). "
            "Pass --session <id> to say which."
        )
    return candidates[0].stem


def read_note_file(path: Path, start_offset: int = 0) -> list[dict]:
    """Read a note file's records in append order — oldest first, newest last.

    Tolerant like the trace readers: a malformed line is skipped rather than
    fatal. A note file is the one artifact with no second copy, so refusing to
    read the whole of it because one line is bad would be the wrong trade.
    """
    if not path.exists():
        return []
    out = []
    with path.open("rb") as fh:
        fh.seek(start_offset)
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out
