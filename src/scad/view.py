"""Render the session index to a self-contained HTML page.

Answers two questions and nothing else: who is waiting on me, and how do I get
back to that one. Read-only — the only file written is the page itself.

No server: a rendered page needs no process to remember to start, no port, and
survives being copied off a remote box.
"""

import html as _html
import json
import shlex
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scad.live import (
    ClaudeSession,
    TmuxPane,
    agent_panes,
    claude_live_sessions,
    is_agent_command,
)

_RESUME = {"claude": "claude --resume {id}", "codex": "codex resume {id}"}


@dataclass(frozen=True)
class Reentry:
    kind: str        # tmux | container | resume | none
    command: str
    note: str = ""


def _goto(target: str) -> str:
    """Shell command that lands the cursor on `target` (session:window.pane).

    `tmux select-window -t main:3.0` looks like it selects pane 0 and does not:
    select-window takes a target-WINDOW and discards the pane component, leaving
    you on whichever pane was last active there. Verified on the real machine —
    it landed on a zsh sitting beside the agent. So select the window, then the
    pane. The `\\;` is escaped for the shell, which would otherwise eat the
    separator before tmux sees it.
    """
    window, _, pane = target.rpartition(".")
    if not window or not pane.isdigit():
        return f"tmux select-window -t {target}"
    return f"tmux select-window -t {window} \\; select-pane -t {target}"


def live_pane_rows(conn, panes: list[TmuxPane]) -> list[dict]:
    """One row per live agent pane — the panes themselves, not sessions.

    This is the section that answers "what have I got open right now". It is
    pane-first because that is what exists: `list-panes -a` spans every tmux
    session, and a single window routinely holds several agents (three in
    `main:3` here). Sessions cannot be matched one-to-one onto them, so the
    newest session in the pane's directory is offered as a hint and labelled as
    one — never as fact.
    """
    rows = []
    for pane in panes:
        if not is_agent_command(pane.command):
            continue
        agent = "codex" if pane.command == "codex" else "claude"
        # Match the pane's own agent: a codex pane must not be offered a claude
        # session as its likely occupant, which the unfiltered query did.
        guess = conn.execute(
            "SELECT id, project, name, title, outcome, ended FROM sessions "
            "WHERE cwd = ? AND kind = 'main' AND agent = ? ORDER BY ended DESC LIMIT 1",
            (pane.path, agent),
        ).fetchone()
        rows.append({
            "target": pane.target,
            "tmux_session": pane.session,
            "window": pane.window,
            "cwd": pane.path,
            "agent": agent,
            "version": pane.command,
            "project": (guess["project"] if guess else None),
            "likely_id": (guess["id"] if guess else None),
            "likely_name": (guess["name"] if guess else None),
            "likely_title": (guess["title"] if guess else None),
            "likely_outcome": (guess["outcome"] if guess else None),
            "last_activity": (guess["ended"] if guess else None),
            "goto": _goto(pane.target),
        })
    return rows


def group_panes(rows: list[dict]) -> list[dict]:
    """Nest live panes the way tmux holds them: session -> window -> panes.

    This mirrors how the work is actually laid out — tmuxinator opens a window
    per project — so the page reads like the screen rather than like a table dump.

    Everything is ordered by **last message time**, most recent first: panes
    within a window, windows within a session, sessions against each other. What
    you touched last is what you are most likely coming back to; tmux's own index
    order says nothing about that. Panes whose directory has no indexed session
    sort last — unknown is not recent.
    """
    sessions: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        windows = sessions.setdefault(row["tmux_session"], {})
        key = f'{row["target"].split(".")[0]}|{row.get("window") or ""}'
        windows.setdefault(key, []).append(row)
    def recency(rows):
        return max((r.get("last_activity") or 0) for r in rows)

    grouped = []
    for name, windows in sessions.items():
        win_rows = [
            {"label": key.split("|", 1)[1], "target": key.split("|", 1)[0],
             "panes": sorted(panes, key=lambda r: r.get("last_activity") or 0, reverse=True),
             "last_activity": recency(panes)}
            for key, panes in windows.items()
        ]
        win_rows.sort(key=lambda w: w["last_activity"], reverse=True)
        grouped.append({"session": name, "windows": win_rows,
                        "last_activity": max(w["last_activity"] for w in win_rows)})
    grouped.sort(key=lambda g: g["last_activity"], reverse=True)
    return grouped


def split_waiting(waiting: list[dict], cwds: set[str]) -> tuple[list[dict], list[dict]]:
    """Split the waiting list into "there is a pane open for it" and "gone".

    118 undifferentiated rows is a dump, not a view. The distinction that matters
    is whether you can walk to it — an open pane in that directory means switch
    windows; nothing open means the conversation is closed and needs resuming.
    """
    at_hand = [r for r in waiting if r.get("cwd") and r["cwd"] in cwds]
    closed = [r for r in waiting if not (r.get("cwd") and r["cwd"] in cwds)]
    return at_hand, closed


def group_notes(notes: list[dict]) -> list[dict]:
    """Notes grouped by the session that wrote them, newest session first.

    A note only means something beside its siblings — the `relation` edges
    (continue / shift / branch / return) describe a session's shape, and a lone
    note out of order says nothing about it.
    """
    sessions: dict[str, list[dict]] = {}
    for note in notes:
        sessions.setdefault(note["session_id"], []).append(note)
    groups = [
        {"session_id": sid,
         "label": rows[0].get("name") or sid[:12],
         "project": rows[0].get("project"),
         "agent": rows[0].get("agent"),
         "cwd": rows[0].get("cwd"),
         "rows": sorted(rows, key=lambda r: r.get("idx") or 0),
         "last_activity": max((r.get("ts") or 0) for r in rows)}
        for sid, rows in sessions.items()
    ]
    groups.sort(key=lambda g: g["last_activity"], reverse=True)
    return groups


def group_by_project(rows: list[dict]) -> list[dict]:
    """Group rows by project, most recently active project first.

    Ordered by last message time rather than by group size: a project you touched
    an hour ago belongs above one with more rows you last saw in March.
    """
    projects: dict[str, list[dict]] = {}
    for row in rows:
        projects.setdefault(row.get("project") or "unfiled", []).append(row)
    groups = [
        {"project": name,
         "rows": sorted(rs, key=lambda r: r.get("ended") or 0, reverse=True),
         "last_activity": max((r.get("ended") or 0) for r in rs)}
        for name, rs in projects.items()
    ]
    groups.sort(key=lambda g: g["last_activity"], reverse=True)
    return groups


# Directories that belong to an agent's own machinery, not to your work. A
# session can record one as its cwd — codex's ChatGPT-project sessions do — and
# `cd`-ing there is never what you want: you would land inside the tool's state
# rather than in a repo. Resume works fine without the cd, so drop it.
_AGENT_STATE = (".claude", ".codex", ".scad")


def _is_agent_state_dir(cwd: str) -> bool:
    try:
        parts = Path(cwd).parts
    except (TypeError, ValueError):
        return False
    return any(part in _AGENT_STATE for part in parts)


def _resume_command(row: dict) -> str:
    """The resume command, regardless of whether the session is open.

    Always available and always unambiguous — unlike a tmux target, which cannot
    be resolved to a session when the same project opens in the same window
    every time. This is what you actually paste.
    """
    if row.get("kind") not in (None, "main") or not row.get("id"):
        return ""
    template = _RESUME.get(row.get("agent") or "claude", _RESUME["claude"])
    resume = template.format(id=row["id"])
    cwd = row.get("cwd")
    if not cwd or _is_agent_state_dir(cwd):
        return resume
    return f"cd {shlex.quote(cwd)} && {resume}"


def _one_per_place(rows) -> list[dict]:
    """Collapse live rows to one per pane or container — the newest in each.

    Panes are matched by cwd, so *every* session that ever ran in a live pane's
    directory matches it: on this machine 36 rows resolved to 8 actual panes.
    "Live now" then reads as far busier than reality, and the older rows are not
    live at all — they merely share a directory with something that is.

    Only the most recent session per place can plausibly be the one running
    there, so that is the one kept. The rest remain findable in "All sessions".
    """
    newest: dict[str, dict] = {}
    for row in rows:                       # all_rows is already ordered ended DESC
        newest.setdefault(row["reentry"]["command"], row)
    return list(newest.values())


STATUS_OPEN = "open"
STATUS_MAYBE = "maybe-open"
STATUS_CLOSED = "closed"


def status_for(row: dict, live_ids: set[str], cwds: set[str], running: set[str]) -> str:
    """Is this session still open?

    Three answers, and the middle one is the honest part. `claude` does not hold
    its transcript open, so a running agent cannot be traced back to the session
    inside it. Where the daemon roster names a live pid we can say "open" for
    certain; where an agent is merely running in the same directory we can only
    say "maybe" — and with the same project opened in the same window every time,
    that ambiguity is the norm rather than the exception.

    Claiming certainty there would be worse than admitting the limit: you would
    stop trusting the column the first time it was wrong.
    """
    if row.get("id") in live_ids:
        return STATUS_OPEN
    run_id = row.get("scad_run_id")
    if run_id and run_id in running:
        return STATUS_OPEN
    if row.get("cwd") and row["cwd"] in cwds:
        return STATUS_MAYBE
    return STATUS_CLOSED


def reentry_for(row: dict, panes: list[TmuxPane], running: set[str]) -> Reentry:
    """How to get back into this session.

    Precedence is live-first: a running pane or container is a place you can go
    now, while a resume command rebuilds a conversation from disk.

    Matching a pane by cwd is approximate — several panes can share a directory —
    so every candidate is reported rather than one being guessed at.
    """
    if row.get("kind") not in (None, "main"):
        # Subagents and workflow agents have no independent session to re-enter.
        return Reentry("none", "")

    cwd = row.get("cwd")

    if cwd:
        matches = [p for p in panes if p.path == cwd and is_agent_command(p.command)]
        if matches:
            note = ""
            if len(matches) > 1:
                others = ", ".join(p.target for p in matches[1:])
                note = f"ambiguous — same cwd also in {others}"
            return Reentry("tmux", _goto(matches[0].target), note)

    run_id = row.get("scad_run_id")
    if run_id and run_id in running:
        return Reentry("container", f"scad run attach {run_id}")

    template = _RESUME.get(row.get("agent") or "claude", _RESUME["claude"])
    resume = template.format(id=row.get("id"))
    # shlex.quote leaves ordinary paths alone and quotes the 84 real cwds that
    # contain spaces ("Saptarishi Apartments"), which `cd` would otherwise split.
    # An agent-state directory gets no cd at all — see _is_agent_state_dir.
    if not cwd or _is_agent_state_dir(cwd):
        return Reentry("resume", resume)
    return Reentry("resume", f"cd {shlex.quote(cwd)} && {resume}")


_WAITING = ("awaiting-question", "awaiting-user")
_SNIPPET = 400

_COLUMNS = ("id, name, kind, agent, project, cwd, title, outcome, harness_state, "
            "needs, n_turns, started, ended, scad_run_id, grade")


def _as_rows(cursor_rows, panes, running, live_ids=None, cwds=None) -> list[dict]:
    live_ids = live_ids or set()
    cwds = cwds if cwds is not None else set()
    out = []
    for r in cursor_rows:
        row = dict(r)
        re_ = reentry_for(row, panes, running)
        row["reentry"] = {"kind": re_.kind, "command": re_.command, "note": re_.note}
        row["status"] = status_for(row, live_ids, cwds, running)
        out.append(row)
    return out


def _live_sessions(injected) -> list[ClaudeSession]:
    """The claude process registry, or nothing at all.

    Same contract as `tmux_panes()` and `running_run_ids()`: a viewer that
    raised because a machine has no `~/.claude/sessions` would be useless, so
    every failure degrades to "nothing is provably open". `claude_live_sessions`
    already swallows its own errors; this covers the import-time and
    monkeypatched cases too.
    """
    if injected is not None:
        return list(injected)
    try:
        return list(claude_live_sessions())
    except Exception:
        return []


def project_tabs(rows: list[dict], waiting: list[dict]) -> list[dict]:
    """One tab per project, most recently active first.

    Recency, not the alphabet: the project you were just in is the one you are
    coming back to, and an alphabetical strip buries it wherever its name falls.

    Each tab carries both counts because they answer different questions — how
    much is here, and how much of it wants you. A project with nothing waiting
    has to look different from one with five, which is the whole reason the
    number is on the tab rather than only inside the section.

    Rows with no project at all get no tab: there is nothing to name it, and an
    empty label would collide with the "All" tab's own empty value. They stay
    visible under All.
    """
    waits: dict[str, int] = {}
    for row in waiting:
        key = row.get("project") or ""
        waits[key] = waits.get(key, 0) + 1

    tabs: dict[str, dict] = {}
    for row in rows:
        key = row.get("project") or ""
        if not key:
            continue
        tab = tabs.setdefault(key, {"project": key, "sessions": 0, "waiting": 0,
                                    "notes": 0, "last_activity": 0})
        tab["sessions"] += 1
        # Notes per project, so the strip answers a third question: where has
        # anything been *written down*. The authored tier is the only one that
        # cannot be re-derived, and it is invisible from a session count — a
        # project with 200 sessions and no notes looks identical to one with
        # 200 sessions and twenty, which is exactly the difference worth seeing.
        tab["notes"] += row.get("n_notes") or 0
        tab["last_activity"] = max(tab["last_activity"], row.get("ended") or 0)
    for key, tab in tabs.items():
        tab["waiting"] = waits.get(key, 0)
    return sorted(tabs.values(), key=lambda t: (t["last_activity"], t["project"]),
                  reverse=True)


def open_now_rows(sessions: list[ClaudeSession], indexed: list[dict],
                  panes: list[TmuxPane], running: set[str]) -> list[dict]:
    """One row per Claude session that is running right now.

    Exact, not inferred. The registry names the session, so this is the one
    section on the page that can say "open" without hedging — which is also why
    it holds claude only: there is no equivalent registry for codex or kimi, and
    correlating a pane by cwd or timing would put a guess in a section whose
    entire value is that it does not guess.

    A session the index has never seen — started minutes ago, not yet archived —
    still gets a row, built from the registry's own `cwd` and `name`. Losing a
    running session from "open now" is the worst failure this section has.

    Ordered by last activity, newest first. `ended` is that; `started_at` is
    when the *process* began and says nothing about which session you were just
    in. An unindexed session has no `ended` at all, so its registry
    `started_at` stands in for the sort — it is the only timestamp that exists
    for it, and it keeps a brand-new session near the top where it belongs.
    """
    by_id = {row["id"]: row for row in indexed}
    rows = []
    for session in sessions:
        known = by_id.get(session.session_id)
        base = known or {}
        row = {
            "id": session.session_id,
            "pid": session.pid,
            "agent": base.get("agent") or "claude",
            "kind": "main",
            # From the registry: what the human called it, and what it is doing.
            "name": session.name or base.get("name") or "",
            "status": session.status or "",
            "waiting_for": session.waiting_for or "",
            "started_at": session.started_at,
            # From the index: where it lives and how far it got.
            "cwd": base.get("cwd") or session.cwd or "",
            "project": base.get("project"),
            "n_turns": base.get("n_turns") or 0,
            "ended": base.get("ended"),
            "title": base.get("title"),
            "outcome": base.get("outcome"),
            "scad_run_id": base.get("scad_run_id"),
            "indexed": known is not None,
        }
        re_ = reentry_for(row, panes, running)
        row["reentry"] = {"kind": re_.kind, "command": re_.command, "note": re_.note}
        rows.append(row)
    rows.sort(key=lambda r: r["ended"] or r["started_at"] or 0, reverse=True)
    return rows


def _last_text(conn, session_id: str) -> str:
    row = conn.execute(
        "SELECT text FROM turns WHERE session_id = ? ORDER BY idx DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return (row["text"] or "")[:_SNIPPET] if row else ""


def gather(conn, panes: list[TmuxPane], running: set[str], days: int = 14,
           live_ids: set[str] | None = None, live_sessions: list | None = None) -> dict:
    """Everything the page needs: what waits, what is live, and the full list.

    `live_sessions` is the claude process registry. It is read here rather than
    passed by the caller so the page cannot silently lose it — but it stays
    injectable, exactly like `panes` and `running`, because a test must never
    depend on what the developer happens to have open.

    Nothing is filtered by project here. The page embeds every row already and
    scopes itself in the browser, so a second filter in SQL would be a parallel
    mechanism that could disagree with the tabs about the same project.
    """
    cutoff = int((time.time() - days * 86400) * 1000)
    sessions = _live_sessions(live_sessions)
    # The registry names sessions exactly, which is what finally lets status_for
    # answer `open` rather than `maybe-open` for a claude session.
    live_ids = set(live_ids or ()) | {s.session_id for s in sessions}
    # A cwd with an agent in it means "something is running here", not "this
    # session is". That distinction is what status_for reports honestly.
    cwds = {p.path for p in panes if is_agent_command(p.command) and p.path}

    waiting_rows = conn.execute(
        f"SELECT {_COLUMNS} FROM sessions "
        f"WHERE kind = 'main' AND outcome IN (?, ?) AND ended >= ? "
        # Questions first — one that actually asked you something outranks one
        # merely idle — then newest first inside each group. The page is read
        # top-down and current work is what you want at hand. Oldest-first was
        # the earlier rule, to keep old rows from rotting unseen at the bottom;
        # they are still listed, just below the live ones rather than above them.
        f"ORDER BY CASE outcome WHEN 'awaiting-question' THEN 0 ELSE 1 END, ended DESC",
        (*_WAITING, cutoff),
    ).fetchall()
    waiting = _as_rows(waiting_rows, panes, running, live_ids, cwds)
    for row in waiting:
        row["last_text"] = _last_text(conn, row["id"])

    # Only human-started sessions are listed. A subagent is triggered BY an agent,
    # has no independent existence and cannot be resumed — listing it beside the
    # session that spawned it makes 1309 of 1462 rows things you never started.
    # They stay in the index (searchable, readable); they are just not peers here.
    all_rows = _as_rows(
        conn.execute(
            f"SELECT {_COLUMNS}, "
            f"(SELECT count(*) FROM sessions c WHERE c.parent_session_id = sessions.id) "
            f"AS n_agents "
            f"FROM sessions WHERE kind = 'main' ORDER BY ended DESC"
        ).fetchall(),
        panes, running, live_ids, cwds,
    )
    live = _one_per_place(r for r in all_rows if r["reentry"]["kind"] in ("tmux", "container"))

    # Notes are the authored tier — the only thing here that can never be
    # re-derived — and until now they were write-only from the page's side.
    notes = [dict(r) for r in conn.execute(
        "SELECT n.session_id, n.idx, n.ts, n.topic, n.relation, n.parent, n.title, "
        "       n.tags, n.entities, n.note_path, s.project, s.name, s.agent, s.cwd "
        "FROM notes n LEFT JOIN sessions s ON s.id = n.session_id "
        "ORDER BY n.ts DESC"
    ).fetchall()]

    # Notes belong ON the row, not only in their own section. A session with
    # three notes rendered identically to one with none, so the only way to find
    # a note was to scroll elsewhere and match session ids by eye. Notes are the
    # authored tier — the one thing here that can never be re-derived — and a
    # tier you cannot see from the main view is one you stop writing to.
    # Counted from the notes already loaded above rather than re-queried: same
    # numbers by construction, so the row and the section cannot disagree.
    note_counts: dict[str, int] = {}
    for note in notes:
        sid = note.get("session_id")
        if sid:
            note_counts[sid] = note_counts.get(sid, 0) + 1
    # Built here rather than inline in the return so it can be counted too — a
    # running session is the likeliest one to have just been written about.
    open_now = open_now_rows(sessions, all_rows, panes, running)
    for collection in (waiting, all_rows, open_now):
        for row in collection:
            # 0, never None: the renderer should be able to test a number, and
            # "no notes" is a fact worth stating rather than absent data.
            row["n_notes"] = note_counts.get(row.get("id"), 0)

    pane_rows = live_pane_rows(conn, panes)
    at_hand, closed_waiting = split_waiting(waiting, cwds)

    return {
        "open_now": open_now,
        "tabs": project_tabs(all_rows, waiting),
        "waiting": waiting,
        "waiting_at_hand": at_hand,
        "waiting_closed": closed_waiting,
        "notes": notes,
        "grouped_notes": group_notes(notes),
        "grouped_panes": group_panes(pane_rows),
        "grouped_closed": group_by_project(closed_waiting),
        "live": live,
        "panes": pane_rows,
        "all": all_rows,
        "generated": int(time.time() * 1000),
    }


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>scad — {page_name}</title>
<style>
 :root {{
   --bg:#f6f7f9; --card:#fff; --ink:#1c1f24; --dim:#6b7280; --faint:#9ca3af;
   --line:#e5e7eb; --chip:#f3f4f6; --chip-h:#e5e7eb;
   --open:#059669; --open-bg:#d1fae5; --maybe:#b45309; --maybe-bg:#fef3c7;
   --shut:#6b7280; --shut-bg:#f3f4f6; --ask:#b45309; --claude:#4f46e5; --codex:#0891b2;
 }}
 @media (prefers-color-scheme: dark) {{
   :root {{
     --bg:#0f1115; --card:#181b21; --ink:#e5e7eb; --dim:#9199a6; --faint:#6b7280;
     --line:#262b33; --chip:#22262e; --chip-h:#2c313a;
     --open:#34d399; --open-bg:#064e3b; --maybe:#fbbf24; --maybe-bg:#4a3208;
     --shut:#9199a6; --shut-bg:#22262e; --ask:#fbbf24; --claude:#818cf8; --codex:#22d3ee;
   }}
 }}
 * {{ box-sizing: border-box; }}
 body {{ font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
        margin: 0; padding: 2.5rem 1.5rem 5rem; background: var(--bg); color: var(--ink); }}
 .wrap {{ max-width: 68rem; margin: 0 auto; }}
 header {{ margin-bottom: 2rem; }}
 h1 {{ font-size: 1.05rem; font-weight: 650; margin: 0; letter-spacing: -.01em; }}
 .sub {{ color: var(--dim); font-size: .82rem; margin-top: .3rem; }}
 h2 {{ font-size: .74rem; font-weight: 650; text-transform: uppercase; letter-spacing: .07em;
       color: var(--dim); margin: 2.4rem 0 .9rem; }}
 h2 .n {{ color: var(--faint); font-weight: 500; }}
 .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          margin-bottom: .8rem; overflow: hidden; }}
 .card > .head {{ display: flex; align-items: baseline; gap: .6rem; padding: .7rem .9rem;
                  border-bottom: 1px solid var(--line); }}
 .card > .head b {{ font-weight: 620; font-size: .93rem; }}
 .card > .head .meta {{ color: var(--faint); font-size: .78rem; margin-left: auto; }}
 .row {{ display: grid; grid-template-columns: 1fr auto; gap: 1rem; align-items: start;
         padding: .65rem .9rem; border-top: 1px solid var(--line); }}
 .row:first-of-type {{ border-top: 0; }}
 .row .who {{ min-width: 0; }}
 .row .t {{ font-weight: 550; overflow-wrap: anywhere; }}
 .row .m {{ color: var(--faint); font-size: .78rem; margin-top: .15rem; }}
 .go {{ display: flex; flex-direction: column; gap: .3rem; align-items: flex-end; }}
 code {{ font: 11.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
         background: var(--chip); border: 1px solid var(--line); color: var(--ink);
         padding: .22rem .45rem; border-radius: 5px; cursor: pointer; white-space: nowrap;
         max-width: 30rem; overflow: hidden; text-overflow: ellipsis; display: inline-block; }}
 code:hover {{ background: var(--chip-h); }}
 code.ghost {{ background: transparent; border-color: transparent; color: var(--faint); }}
 .pill {{ font-size: .68rem; font-weight: 600; padding: .12rem .4rem; border-radius: 4px;
          text-transform: uppercase; letter-spacing: .04em; }}
 .open {{ color: var(--open); background: var(--open-bg); }}
 .maybe-open {{ color: var(--maybe); background: var(--maybe-bg); }}
 .closed {{ color: var(--shut); background: var(--shut-bg); }}
 /* The registry's own status for a session proven to be running. */
 .busy {{ color: var(--open); background: var(--open-bg); }}
 .waiting {{ color: var(--maybe); background: var(--maybe-bg); }}
 .nn {{ margin-left: .3rem; padding: 0 .3rem; border-radius: 6px;
        background: var(--note-bg, #e8e2ff); color: var(--note, #5b46b8);
        font-size: .7rem; font-weight: 600; }}
 .idle {{ color: var(--shut); background: var(--shut-bg); }}
 .ag {{ font-size: .72rem; font-weight: 600; }}
 .ag.claude {{ color: var(--claude); }}
 .ag.codex {{ color: var(--codex); }}
 .q {{ box-shadow: inset 3px 0 0 var(--ask); }}
 .needs {{ color: var(--ask); font-size: .82rem; margin-top: .2rem; }}
 .snip {{ color: var(--dim); font-size: .82rem; margin-top: .25rem;
          display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
 .empty {{ color: var(--faint); font-size: .85rem; padding: .9rem; background: var(--card);
           border: 1px dashed var(--line); border-radius: 10px; }}
 input {{ font: inherit; padding: .5rem .7rem; width: 100%; max-width: 24rem; margin-bottom: .8rem;
          border: 1px solid var(--line); border-radius: 7px; background: var(--card); color: var(--ink); }}
 input:focus {{ outline: 2px solid var(--claude); outline-offset: -1px; }}
 .tags {{ margin-top: .3rem; display: flex; flex-wrap: wrap; gap: .25rem; }}
 .tag {{ font-size: .7rem; padding: .08rem .35rem; border-radius: 4px;
         background: var(--chip); color: var(--dim); border: 1px solid var(--line); }}
 .clip {{ cursor: zoom-in; border-bottom: 1px dotted var(--faint); }}
 .clip.open {{ cursor: auto; border-bottom: 0; user-select: text;
               -webkit-line-clamp: unset; overflow: visible; }}
 /* .row is display:grid, which outranks the UA sheet's [hidden] rule, so a
    hidden row would otherwise still be drawn. Same for .card and .body. */
 [hidden] {{ display: none !important; }}
 .tabs {{ display: flex; flex-wrap: wrap; gap: .35rem; margin: 0 0 .4rem;
          padding-bottom: .8rem; border-bottom: 1px solid var(--line); }}
 .tab {{ font: inherit; font-size: .8rem; display: inline-flex; align-items: center;
         gap: .35rem; padding: .28rem .6rem; border-radius: 999px; cursor: pointer;
         background: var(--card); color: var(--dim);
         border: 1px solid var(--line); }}
 .tab:hover {{ background: var(--chip-h); }}
 .tab.on {{ background: var(--ink); color: var(--bg); border-color: var(--ink); }}
 .tab .n {{ font-size: .7rem; font-weight: 600; color: var(--faint); }}
 .tab.on .n {{ color: var(--bg); opacity: .75; }}
 /* Anything waiting on you is the reason to click a tab at all. */
 .tab.hot .n {{ color: var(--ask); }}
 .tab.on.hot .n {{ color: var(--bg); opacity: 1; }}
 .row.top {{ border-top: 0; }}
 #gen {{ font-weight: 600; }}
 #gen.stale {{ color: var(--ask); }}
</style>
<div class="wrap">
<header>
 <h1>scad sessions</h1>
 <!-- There is no refresh button and there cannot be one: this is a file://
      document with no server, so it cannot run an index pass. A button that
      re-read the same file would look like refreshing while changing nothing.
      What the page CAN do honestly is say how old it is, and keep saying it. -->
 <div class="sub">generated {generated} · <b id="gen">just now</b> ·
   regenerate with <code onclick="copy(this)">scad view</code></div>
 <div class="sub">{n_open_now} open now · {n_panes} panes open ·
   {n_at_hand} waiting at hand · {n_closed} waiting closed · {n_all} sessions ·
   click any command to copy</div>
</header>

<div class="tabs" id="tabs">{tabs}</div>

<section data-sec="open-now">
<h2>Open now <span class="n" data-count>{n_open_now}</span></h2>
<div class="body">{open_now}</div>
<div class="empty scoped" hidden></div>
</section>

<section data-sec="panes">
<h2>Agent panes <span class="n" data-count>{n_panes}</span></h2>
<div class="body">{grouped_panes}</div>
<div class="empty scoped" hidden></div>
</section>

<section data-sec="at-hand">
<h2>Waiting — a pane is open for it <span class="n" data-count>{n_at_hand}</span></h2>
<div class="body">{waiting}</div>
<div class="empty scoped" hidden></div>
</section>

<section data-sec="closed">
<h2>Waiting — closed <span class="n" data-count>{n_closed}</span></h2>
<div class="body">{grouped_closed}</div>
<div class="empty scoped" hidden></div>
</section>

<section data-sec="notes">
<h2>Notes <span class="n" data-count>{n_notes}</span></h2>
<div class="body">{notes}</div>
<div class="empty scoped" hidden></div>
</section>

<section data-sec="all">
<h2>All sessions <span class="n" data-count>{n_all}</span></h2>
<input id="f" placeholder="filter by name, project, cwd, title…" autocomplete="off">
<div id="all"></div>
</section>
</div>

<script>
const DATA = {data};
// The quote key is single-quoted on purpose. Written as an escaped double
// quote it collapsed here into three bare quote characters, a syntax error
// that took the ENTIRE script down with it: no All-sessions list, no copy, no
// expand, no tabs, and a page that still looked plausible. Single quotes need
// no escape on either side, so the hazard cannot come back.
const esc = s => (s ?? "").toString().replace(/[&<>"]/g, c =>
  ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);
const when = ms => ms ? new Date(ms).toLocaleString() : "";
// Same rule as the server-rendered rows: the name a human chose, else the id.
// A derived title never stands in for one — it rides along in the meta line.
const label = r => r.name || r.id.slice(0, 12);

function rows(list) {{
  if (!list.length) return '<div class="empty">' +
    (SCOPE ? 'No sessions in ' + esc(SCOPE) + '.' : 'Nothing here.') + '</div>';
  return '<div class="card">' + list.map(r =>
    '<div class="row"><div class="who"><div class="t" title="' + esc(r.cwd ?? "") + '">' + esc(label(r)) +
    '</div><div class="m"><span class="ag ' + esc(r.agent) + '">' + esc(r.agent) + '</span> · ' +
    esc(r.project ?? "") + ' · ' + r.n_turns + ' turns' +
    (r.n_agents ? ' · ' + r.n_agents + ' sub-agents' : '') + ' · ' + esc(when(r.ended)) +
    (r.title ? ' · ' + esc(r.title.slice(0, 70)) : '') +
    '</div></div><div class="go"><span class="pill ' + esc(r.status) + '">' + esc(r.status) +
    '</span>' + (r.reentry.command ? '<code title="' + esc(r.reentry.command) + '" onclick="copy(this)">' + esc(r.reentry.command) +
    '</code>' : '') + '</div></div>').join('') + '</div>';
}}

function expand(ev, el) {{
  // A title tooltip cannot be selected, so hover alone makes text readable but
  // not copyable. Expanding turns it into ordinary selectable content.
  ev.stopPropagation();
  if (el.classList.contains("open")) return;
  el.textContent = el.dataset.full;
  el.classList.add("open");
  el.removeAttribute("title");
}}

function copy(el) {{
  navigator.clipboard.writeText(el.textContent);
  const was = el.textContent; el.textContent = "copied ✓";
  setTimeout(() => el.textContent = was, 800);
}}

const f = document.getElementById("f");
const draw = () => {{
  const q = f.value.toLowerCase();
  const list = DATA.all.filter(r =>
    (!SCOPE || (r.project || "") === SCOPE) &&
    (!q || [r.name, r.project, r.cwd, r.title, r.id]
             .some(v => (v ?? "").toLowerCase().includes(q))));
  document.getElementById("all").innerHTML = rows(list);
  const n = document.querySelector('section[data-sec="all"] [data-count]');
  if (n) n.textContent = list.length;
}};
f.addEventListener("input", draw);

// Which project the page is scoped to. "" is All — the page as it has always
// looked. Everything is already in the document, so a tab only hides rows:
// no re-render, no second file, nothing to re-run.
let SCOPE = "";

function applyScope() {{
  document.querySelectorAll(".row[data-project]").forEach(r => {{
    r.hidden = SCOPE !== "" && (r.dataset.project || "") !== SCOPE;
  }});
  document.querySelectorAll(".card").forEach(card => {{
    const rs = [...card.querySelectorAll(".row")];
    const vis = rs.filter(r => !r.hidden);
    card.hidden = rs.length > 0 && vis.length === 0;
    // The first row draws no top border. Which row is first changes with
    // the scope, so the rule cannot be :first-of-type alone.
    rs.forEach(r => r.classList.remove("top"));
    if (vis.length) vis[0].classList.add("top");
  }});
  document.querySelectorAll("section[data-sec]").forEach(sec => {{
    if (sec.dataset.sec === "all") return;      // drawn from DATA, see draw()
    const body = sec.querySelector(".body");
    const rs = [...body.querySelectorAll(".row")];
    const vis = rs.filter(r => !r.hidden).length;
    const note = sec.querySelector(".scoped");
    // A section that empties out under a scope says so. A blank region reads
    // as a broken page, not as an answer.
    const blank = SCOPE !== "" && rs.length > 0 && vis === 0;
    body.hidden = blank;
    note.hidden = !blank;
    if (blank) note.textContent = "Nothing here for " + SCOPE + ".";
    const n = sec.querySelector("[data-count]");
    if (n) n.textContent = SCOPE === "" ? rs.length : vis;
  }});
  draw();
}}

// The page is opened once and left open, so its age is the one fact it can
// still learn without a server. STALE_AFTER is 15 minutes — long enough not to
// nag, short enough that a morning-old page is unmistakable.
const STALE_AFTER = 15 * 60 * 1000;
function since(ms) {{
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return Math.round(s) + "s ago";
  const m = Math.round(s / 60);
  if (m < 90) return m + (m === 1 ? " minute ago" : " minutes ago");
  const h = Math.round(m / 60);
  if (h < 36) return h + (h === 1 ? " hour ago" : " hours ago");
  return Math.round(h / 24) + " days ago";
}}
const genEl = document.getElementById("gen");
const age = () => {{
  genEl.textContent = since(DATA.generated);
  genEl.classList.toggle("stale", Date.now() - DATA.generated > STALE_AFTER);
}};
age();
setInterval(age, 15000);

document.getElementById("tabs").addEventListener("click", ev => {{
  const tab = ev.target.closest(".tab");
  if (!tab) return;
  SCOPE = tab.dataset.tab;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("on", t === tab));
  applyScope();
}});

applyScope();
</script>
</html>
"""


def _embed(data: dict) -> str:
    """JSON safe to sit inside a <script> element.

    A session title containing "</script>" would otherwise close the block early
    and dump the rest of the payload into the document as markup — the corpus has
    titles quoting HTML. Escaping the three characters as \\u sequences keeps the
    text byte-identical after JSON.parse while making the terminator unspellable.
    """
    return (json.dumps(data, default=str)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _ago(ms) -> str:
    if not ms:
        return ""
    mins = max(0, int((time.time() * 1000 - ms) / 60000))
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def _chip(text: str, ghost: bool = False) -> str:
    """A click-to-copy command chip, with the full text on hover.

    CSS ellipsis only hides the overflow visually — the whole string stays in the
    DOM, so copy still yields the full command. The title is for reading it
    without copying, which matters most for the long paths inside `cd …`.
    """
    if not text:
        return ""
    cls = ' class="ghost"' if ghost else ""
    safe = _html.escape(text)
    return f'<code{cls} title="{safe}" onclick="copy(this)">{safe}</code>'


def _clip(text: str, n: int) -> str:
    """Truncate for display, expandable to the full text on click.

    A `title` tooltip alone is not enough: browser tooltips cannot be selected,
    so anything only reachable by hover cannot be copied — and a path you can
    read but not copy is half a feature. Clicking expands the element in place,
    after which the text is ordinary selectable content.

    The tooltip stays for a quick peek that needs no click.
    """
    text = str(text or "")
    safe = _html.escape(text)
    if len(text) <= n:
        return safe
    return (f'<span class="clip" title="{safe}" data-full="{safe}" '
            f'onclick="expand(event, this)">{_html.escape(text[:n])}…</span>')


def _row(title: str, meta: str, chips: list[str], *, pill: str = "",
         extra: str = "", flag: bool = False, project: str | None = None) -> str:
    """One row, identical in every section.

    Every section previously built its own `<table>`, so each computed column
    widths independently and nothing lined up down the page. One grid row shared
    everywhere fixes that by construction — the columns cannot drift apart
    because there is only one definition of them.

    `data-project` is what the tab strip scopes on. Every row carries it, empty
    string included, so the project filter is one selector rather than a rule
    per section that some future section would forget to join.
    """
    pill_html = f'<span class="pill {pill}">{_html.escape(pill)}</span>' if pill else ""
    scope = _html.escape(project or "")
    return (
        f'<div class="row{" q" if flag else ""}" data-project="{scope}">'
        f'<div class="who"><div class="t">{title}</div>'
        f'<div class="m">{meta}</div>{extra}</div>'
        f'<div class="go">{pill_html}{"".join(chips)}</div>'
        f'</div>'
    )


def _label(row: dict) -> str:
    """What a row is called: the human's name, or the id when there is none.

    Never the title. `title` is derived — an agent's summary of the work, or on
    a skeleton row the first message verbatim — and standing it in for a name is
    what put the literal string "/rename writing-wm-evals-research" at the top of
    a row as though the user had titled it that. An id prefix says less and
    claims nothing, and the name is right there whenever someone chose one.
    """
    return _clip(row.get("name") or (row.get("id") or "")[:12], 70)


def _title_meta(row: dict) -> str:
    """The derived title, demoted to the meta line where it cannot pose as a name."""
    title = row.get("title")
    return f' · {_clip(title, 70)}' if title else ""


def _notes_meta(row: dict) -> str:
    """`· 2 notes` when a session has any, nothing when it has none.

    Silent at zero on purpose: most sessions have no notes, and "0 notes" on
    every row would be noise that trains the eye to skip the very place the
    count matters. A note is the authored tier — worth marking where it exists,
    not worth announcing where it does not.
    """
    n = row.get("n_notes") or 0
    return f' · {n} note{"" if n == 1 else "s"}' if n else ""


def _card(head: str, meta: str, rows: list[str]) -> str:
    return (f'<div class="card"><div class="head"><b>{head}</b>'
            f'<span class="meta">{meta}</span></div>{"".join(rows)}</div>')


def _empty(msg: str) -> str:
    return f'<div class="empty">{_html.escape(msg)}</div>'


def _agent_tag(agent: str) -> str:
    a = _html.escape(agent or "")
    return f'<span class="ag {a}">{a}</span>'


def _tabs_html(tabs: list[dict], n_all: int, n_waiting: int) -> str:
    """The project strip. "All" first and selected, then recency order.

    The number on each tab is what is waiting on you there, because that is the
    reason to click one. The totals ride along in the tooltip.
    """
    e = _html.escape
    n_notes_all = sum(t.get("notes") or 0 for t in tabs)
    out = [f'<button class="tab on{" hot" if n_waiting else ""}" data-tab="" '
           f'title="{n_all} sessions · {n_waiting} waiting · {n_notes_all} notes">All'
           f'<span class="n">{n_waiting}</span>'
           f'{_note_badge(n_notes_all)}</button>']
    for tab in tabs:
        name = e(tab["project"])
        out.append(
            f'<button class="tab{" hot" if tab["waiting"] else ""}" data-tab="{name}" '
            f'title="{tab["sessions"]} sessions · {tab["waiting"]} waiting · '
            f'{tab.get("notes") or 0} notes · {_ago(tab["last_activity"])}">{name}'
            f'<span class="n">{tab["waiting"]}</span>'
            f'{_note_badge(tab.get("notes") or 0)}</button>')
    return "".join(out)


def _note_badge(n: int) -> str:
    """A second, quieter number on a tab: how much has been written down here.

    Separate from the waiting count rather than folded into it, because they
    pull in opposite directions — waiting is work arriving, notes are work
    understood. Silent at zero, which is the common case and would otherwise
    put a `0` on every tab and teach the eye to ignore the position entirely.
    """
    return f'<span class="nn" title="{n} note{"" if n == 1 else "s"}">{n}</span>' if n else ""


def _open_now_html(rows: list[dict]) -> str:
    """Sessions the registry proves are running, newest activity first.

    The status pill is the point of the section: `waiting` is the one asking
    for you, `busy` is working without you, `idle` is open and quiet.
    """
    if not rows:
        return _empty("No Claude sessions are running — nothing is provably open. "
                      "(codex and kimi publish no registry, so they never appear here.)")
    e = _html.escape
    out = []
    for r in rows:
        bits = [_agent_tag(r.get("agent") or "claude")]
        if r.get("indexed"):
            bits += [e(r.get("project") or ""), f'{r.get("n_turns") or 0} turns',
                     _ago(r.get("ended"))]
        else:
            # Started too recently to have been archived. Say so rather than
            # showing zeros that look like a session which did nothing.
            bits += [_clip(r.get("cwd") or "", 60), "not indexed yet"]
        meta = " · ".join(b for b in bits if b) + _notes_meta(r) + _title_meta(r)

        extra = ""
        if r.get("waiting_for"):
            extra = f'<div class="needs">waiting on {e(str(r["waiting_for"]))}</div>'

        chips = [_chip(_resume_command(r), ghost=True)]
        if r["reentry"]["kind"] in ("tmux", "container"):
            chips.insert(0, _chip(r["reentry"]["command"]))
        out.append(_row(_label(r), meta, chips, pill=r.get("status") or "open",
                        extra=extra, flag=r.get("status") == "waiting",
                        project=r.get("project")))
    return f'<div class="card">{"".join(out)}</div>'


def _grouped_panes_html(groups: list[dict]) -> str:
    """tmux session -> window card -> one row per agent pane."""
    if not groups:
        return _empty("No agent panes open.")
    e = _html.escape
    out = []
    for g in groups:
        for w in g["windows"]:
            rows = []
            for r in w["panes"]:
                if r.get("likely_id"):
                    title = _label({"name": r.get("likely_name"), "id": r["likely_id"]})
                    hint = f'~{e(r["likely_id"][:8])} · best guess'
                    if r.get("likely_title"):
                        hint += f' · {_clip(r["likely_title"], 70)}'
                else:
                    title = '<span class="m">no indexed session here</span>'
                    hint = "unmatched"
                resume = _resume_command({"id": r.get("likely_id"), "cwd": r.get("cwd"),
                                          "agent": r.get("agent"), "kind": "main"}) \
                    if r.get("likely_id") else ""
                rows.append(_row(
                    title,
                    f'{_agent_tag(r["agent"])} {e(r.get("version") or "")} · '
                    f'{e(r["target"])} · {hint} · {_ago(r.get("last_activity"))}',
                    [_chip(r["goto"]), _chip(resume, ghost=True)],
                    project=r.get("project"),
                ))
            cwds = ", ".join(sorted({r.get("cwd") for r in w["panes"] if r.get("cwd")}))
            head = (f'<span class="clip" title="{e(cwds)}" data-full="{e(cwds)}" '
                    f'onclick="expand(event, this)">{e(w["label"] or w["target"])}</span>'
                    if cwds else e(w["label"] or w["target"]))
            out.append(_card(
                head,
                f'{e(g["session"])} · {e(w["target"])} · {len(w["panes"])} agent'
                f'{"s" if len(w["panes"]) != 1 else ""} · {_ago(w["last_activity"])}',
                rows,
            ))
    return "".join(out)


def _waiting_rows_html(rows: list[dict]) -> str:
    """Sessions awaiting you that still have a pane open — go to the pane."""
    if not rows:
        return _empty("Nothing waiting with a pane open.")
    e = _html.escape
    out = []
    for r in rows:
        extra = ""
        if r.get("needs"):
            extra += f'<div class="needs">{e(str(r["needs"]))}</div>'
        if r.get("last_text"):
            flat = " ".join(str(r["last_text"]).split())
            extra += (f'<div class="snip clip" title="{e(flat)}" data-full="{e(flat)}" '
                      f'onclick="expand(event, this)">{e(flat[:240])}</div>')
        chips = [_chip(_resume_command(r), ghost=True)]
        if r["reentry"]["kind"] == "tmux":
            chips.insert(0, _chip(r["reentry"]["command"]))
        out.append(_row(
            _label(r),
            f'{_agent_tag(r.get("agent") or "")} · {e(r.get("project") or "")} · '
            f'{r.get("n_turns") or 0} turns · {_ago(r.get("ended"))}{_title_meta(r)}',
            chips, pill=r.get("status", ""), extra=extra,
            flag=r.get("outcome") == "awaiting-question",
            project=r.get("project"),
        ))
    return f'<div class="card">{"".join(out)}</div>'


def _grouped_closed_html(groups: list[dict]) -> str:
    """Closed sessions still awaiting you, one card per project."""
    if not groups:
        return _empty("Nothing closed and waiting.")
    e = _html.escape
    out = []
    for g in groups:
        rows = []
        for r in g["rows"]:
            extra = f'<div class="needs">{e(str(r["needs"]))}</div>' if r.get("needs") else ""
            rows.append(_row(
                _label(r),
                f'{_agent_tag(r.get("agent") or "")} · {r.get("n_turns") or 0} turns · '
                f'{_ago(r.get("ended"))}{_title_meta(r)}',
                [_chip(_resume_command(r))], extra=extra,
                flag=r.get("outcome") == "awaiting-question",
                project=r.get("project") or g["project"],
            ))
        out.append(_card(e(g["project"]),
                         f'{len(g["rows"])} waiting · {_ago(g["last_activity"])}', rows))
    return "".join(out)


def _notes_html(groups: list[dict]) -> str:
    """One card per session, its notes in the order they were written."""
    if not groups:
        return _empty("No notes yet — write one with /remember or `scad session note`.")
    e = _html.escape
    out = []
    for g in groups:
        rows = []
        for n in g["rows"]:
            try:
                tags = json.loads(n.get("tags") or "[]")
            except (TypeError, ValueError):
                tags = []
            tag_html = "".join(f'<span class="tag">{e(str(t))}</span>' for t in tags[:12])
            rel = n.get("relation") or ""
            parent = f' ← {e(str(n["parent"]))}' if n.get("parent") else ""
            out_extra = f'<div class="tags">{tag_html}</div>' if tag_html else ""
            rows.append(_row(
                _clip(n.get("title") or n.get("topic") or "(untitled)", 90),
                f'<b>{e(str(n.get("topic") or ""))}</b> · {e(rel)}{parent} · {_ago(n.get("ts"))}',
                [], extra=out_extra, project=n.get("project"),
            ))
        out.append(_card(
            _clip(g["label"], 60),
            f'{e(g.get("project") or "")} · {len(g["rows"])} note'
            f'{"s" if len(g["rows"]) != 1 else ""} · {_ago(g["last_activity"])}',
            rows))
    return "".join(out)


def render(data: dict) -> str:
    """One self-contained page. No network, no external assets.

    Every row is in the document already, so the project tabs scope the page in
    the browser. That keeps one artifact that still survives `scp` — which is
    the whole reason this is a static file — and one filtering mechanism, so
    nothing can disagree with anything else about a project.
    """
    return _PAGE.format(
        page_name="sessions",
        tabs=_tabs_html(data.get("tabs") or [], len(data["all"]), len(data["waiting"])),
        generated=datetime.fromtimestamp(data["generated"] / 1000).strftime("%Y-%m-%d %H:%M"),
        n_all=len(data["all"]), n_waiting=len(data["waiting"]), n_live=len(data["live"]),
        n_panes=len(data.get("panes") or []),
        n_open_now=len(data.get("open_now") or []),
        open_now=_open_now_html(data.get("open_now") or []),
        n_at_hand=len(data.get("waiting_at_hand") or []),
        n_closed=len(data.get("waiting_closed") or []),
        grouped_panes=_grouped_panes_html(data.get("grouped_panes") or []),
        grouped_closed=_grouped_closed_html(data.get("grouped_closed") or []),
        n_notes=len(data.get("notes") or []),
        notes=_notes_html(data.get("grouped_notes") or []),
        waiting=_waiting_rows_html(data.get("waiting_at_hand") or []),
        data=_embed(data),
    )


def write_view(path: Path, html: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path
