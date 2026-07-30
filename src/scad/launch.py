"""The launch record — what scad knows about a session it started itself.

Path: `~/.scad/launches/<session-id>.json`

A file, never the index. `reindex --rebuild` drops every row and recomputes it
from the archive; a launch record is an authored fact about an event and there
is nothing to recompute it from, so it belongs beside notes rather than inside
the archive. Same reasoning, same tier.

Nothing here is a precondition for anything. `scad session resume` works off
the index for every session on the machine — the record only makes it better,
by naming the pane the session is actually sitting in and by remembering how
the session was born. So every read degrades to None rather than raising.
"""

import json
from pathlib import Path

from scad.config import get_scad_home

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


def write_record(record: dict) -> Path:
    """Write one launch record, keyed by the session id inside it.

    Overwrites: a session id is unique to one launch, so a second write for the
    same id is a correction (an `unresolved` record gaining its id) rather than
    a new event.
    """
    session_id = _component(record.get("session_id"))
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
