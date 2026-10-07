"""Snapshots of the open agent sessions, for bringing them back after a restart.

`scad session snapshot` writes one; `scad session restore` reads one. A
snapshot is taken by hand only. Taken automatically (on `view` or reindex), the
first one after a restart would record the near-empty state and become the
newest, so a plain restore would bring back nothing.

Which sessions: the same ones the Live section of `scad view` lists
(`view.live_rows`), so the page and a snapshot never disagree. Every open Claude
session, from Claude's registry, however it was started; codex and kimi only
when `scad session launch` started them, from its launch records, because
neither publishes a registry. Any other agent pane is recorded as not
restorable.

Where each one is: exact or nothing. A Claude session's pane is the one whose
shell its process descends from (`live.pid_panes`); a launched session's pane
is its launch record's. Matching a pane by directory is the guess 0.6.0
removed, because two panes in one directory got the same session.

The file is for people: indented, sessions sorted by project then name, each
session's fields in reading order. `restore` reads each session's own fields
and never depends on the order.

Path: `~/.scad/snapshots/open-<YYYYMMDD-HHMMSS>.json`; the newest `KEEP` stay.
"""

import json
import platform
from collections import Counter
from datetime import datetime
from pathlib import Path

from scad.config import get_scad_home

FORMAT = 1
KEEP = 50
PREFIX = "open-"


def snapshots_root() -> Path:
    return get_scad_home() / "snapshots"


def snapshot_paths() -> list[Path]:
    """Every snapshot, newest first. The stamp in the name sorts by time."""
    root = snapshots_root()
    if not root.is_dir():
        return []
    return sorted(root.glob(f"{PREFIX}*.json"), reverse=True)


def latest() -> Path | None:
    paths = snapshot_paths()
    return paths[0] if paths else None


def read(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def prune(keep: int = KEEP) -> None:
    for old in snapshot_paths()[keep:]:
        old.unlink(missing_ok=True)


def _indexed(conn, ids) -> dict[str, dict]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, name, agent, project, cwd, title, n_turns, ended, outcome, "
        f"scad_run_id, context_tokens, context_window FROM sessions WHERE id IN ({marks})",
        ids).fetchall()
    return {r["id"]: dict(r) for r in rows}


def gather(conn, sessions: list, panes: list, records: list) -> dict:
    """The snapshot as a dict: what is open now, and where."""
    from scad.aliases import current_cwd
    from scad.live import newest_by_session, pid_panes
    from scad.view import live_rows

    indexed = _indexed(conn, [s.session_id for s in sessions] +
                       [r.get("session_id") for r in records])
    rows = live_rows(sessions, panes, list(indexed.values()), set(), records)
    by_target = {p.target: p for p in panes}
    live = newest_by_session(sessions)

    out, not_restorable = [], []
    for row in rows:
        if row.get("is_pane"):
            pane = by_target.get(row["target"])
            not_restorable.append({"pane": row["target"],
                                   "window": pane.window if pane else row.get("window"),
                                   "command": pane.command if pane else row.get("version"),
                                   "cwd": row.get("cwd")})
            continue
        sid = row["id"]
        claude = live.get(sid)
        if claude is not None:
            # The registry's own pane id first; then the process tree, over every
            # pane, since a pane tmux reports as the shell holds agents too.
            by_id = next((p for p in panes if claude.tmux_pane and p.pane_id == claude.tmux_pane),
                         None)
            target = (by_id.target if by_id
                      else pid_panes(panes, [claude.pid]).get(claude.pid))
            found_by = "registry"
        else:
            target = row.get("target")
            found_by = "launch-record"
        pane = by_target.get(target) if target else None
        base = indexed.get(sid, {})
        out.append({
            "name": row.get("name") or base.get("name") or "",
            "project": row.get("project") or base.get("project"),
            "agent": row.get("agent") or base.get("agent") or "claude",
            "id": sid,
            "cwd": row.get("cwd") or "",
            "cwd_recorded": base.get("cwd") or (claude.cwd if claude else None),
            "context_tokens": base.get("context_tokens"),
            "context_window": base.get("context_window"),
            "last_active": base.get("ended") or (claude.updated_at if claude else None),
            "tmux_session": pane.session if pane else None,
            "window": pane.window if pane else None,
            "pane": target if pane else None,
            "found_by": found_by,
        })
        if out[-1]["cwd"]:
            out[-1]["cwd"] = current_cwd(out[-1]["cwd"]) or out[-1]["cwd"]

    out.sort(key=lambda s: ((s["project"] or "").lower(), (s["name"] or "").lower(), s["id"]))
    not_restorable.sort(key=lambda p: p["pane"] or "")
    return {
        "format": FORMAT,
        "taken": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": platform.node(),
        "sessions": out,
        "not_restorable": not_restorable,
    }


def summary(data: dict) -> str:
    """`8 sessions: 7 claude, 1 codex; 1 pane not restorable`."""
    sessions = data.get("sessions") or []
    agents = Counter(s.get("agent") for s in sessions)
    head = f"{len(sessions)} session{'' if len(sessions) == 1 else 's'}"
    if agents:
        head += ": " + ", ".join(f"{n} {a}" for a, n in sorted(agents.items(),
                                                               key=lambda kv: -kv[1]))
    lost = len(data.get("not_restorable") or [])
    if lost:
        head += f"; {lost} pane{'' if lost == 1 else 's'} not restorable"
    return head


def take(conn=None, *, sessions=None, panes=None, records=None) -> tuple[Path, str]:
    """Write a snapshot of what is open now. Returns (path, summary).

    The sources are read here unless given, so tests can inject every one.
    """
    if conn is None:
        from scad.index import connect
        conn = connect()
    if sessions is None:
        from scad.live import claude_live_sessions
        sessions = claude_live_sessions()
    if panes is None:
        from scad.live import tmux_panes
        panes = tmux_panes()
    if records is None:
        from scad.launch import launch_records
        records = launch_records()
    data = gather(conn, sessions, panes, records)
    root = snapshots_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root / f"{PREFIX}{stamp}.json"
    n = 1
    while path.exists():                 # two snapshots in one second
        path = root / f"{PREFIX}{stamp}-{n}.json"
        n += 1
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    prune()
    return path, summary(data)
