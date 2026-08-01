"""Render the session index to a self-contained HTML page.

Answers two questions and nothing else: who is waiting on me, and how do I get
back to that one. Read-only — the only file written is the page itself.

No server: a rendered page needs no process to remember to start, no port, and
survives being copied off a remote box.
"""

import html as _html
import json
import re
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

_RESUME = {"claude": "claude --resume {id}",
           "codex": "codex resume {id}",
           "kimi": "kimi --session {id}"}

# kimi keys a session directory `session_<uuid>` and `kimi_identity_from_path`
# strips that prefix to key the row — but kimi's own CLI wants it back. Measured
# on 2026-07-30: `kimi -S <bare-uuid>` answers `Session "<uuid>" not found.`
# while the prefixed form resolves and prints the session's own resume line.
_KIMI_PREFIX = "session_"


@dataclass(frozen=True)
class Reentry:
    """How to get back into a session, and where it is open right now.

    Two questions, and conflating them is what hid the resume command from the
    rows most likely to be clicked. `command` answers "what do I paste" and is
    always the resume command; `target` and `goto` answer "where is it open",
    which is state the resume path consults and the row reports as metadata.
    """
    kind: str        # tmux | container | resume | none
    command: str     # the resume command — always, when the row has one
    note: str = ""
    target: str = ""  # the live place: a tmux pane target, or a run id
    goto: str = ""    # how to walk to that place now


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
        # The pane names its own agent, except Claude, which re-execs to its
        # version string. Anything-else-is-claude was wrong the moment a third
        # family existed: a kimi pane was labelled claude, and then matched
        # against claude sessions for its occupant.
        agent = pane.command if pane.command in ("codex", "kimi") else "claude"
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


def resume_argv(row: dict) -> list[str]:
    """The resume command as argv, for `os.execvp`. `[]` when there is none.

    argv rather than a string is the primitive because `scad session resume`
    execs it directly, with no shell in between. The displayed command is built
    from this, so the two can never name different flags.
    """
    if row.get("kind") not in (None, "main") or not row.get("id"):
        return []
    agent = row.get("agent") or "claude"
    session_id = str(row["id"])
    if agent == "kimi" and not session_id.startswith(_KIMI_PREFIX):
        session_id = _KIMI_PREFIX + session_id
    template = _RESUME.get(agent, _RESUME["claude"])
    return shlex.split(template.format(id=session_id))


def resume_command(row: dict) -> str:
    """The resume command, regardless of whether the session is open.

    Always available and always unambiguous — unlike a tmux target, which cannot
    be resolved to a session when the same project opens in the same window
    every time. This is what you actually paste.
    """
    argv = resume_argv(row)
    if not argv:
        return ""
    resume = shlex.join(argv)
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

    Keyed on `reentry.target` — the place itself. It used to key on the command,
    which worked only because a live row's command WAS the tmux target; now that
    the command is the resume command, one per session, keying on it would put
    every session that ever ran in the directory back on the page.
    """
    newest: dict[str, dict] = {}
    for row in rows:                       # all_rows is already ordered ended DESC
        newest.setdefault(row["reentry"]["target"], row)
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
    """How to get back into this session, and where it is open right now.

    `kind` is still live-first, because that is what `scad session resume` acts
    on: attach to a place that exists rather than start a second process against
    a live session id. What changed is that liveness no longer displaces the
    command — a pane closes, the resume command does not.

    Matching a pane by cwd is approximate — several panes can share a directory —
    so every candidate is reported rather than one being guessed at.
    """
    if row.get("kind") not in (None, "main"):
        # Subagents and workflow agents have no independent session to re-enter.
        return Reentry("none", "")

    # shlex.quote leaves ordinary paths alone and quotes the 84 real cwds that
    # contain spaces ("Saptarishi Apartments"), which `cd` would otherwise split.
    # An agent-state directory gets no cd at all — see _is_agent_state_dir.
    resume = resume_command(row)
    cwd = row.get("cwd")

    if cwd:
        matches = [p for p in panes if p.path == cwd and is_agent_command(p.command)]
        if matches:
            note = ""
            if len(matches) > 1:
                others = ", ".join(p.target for p in matches[1:])
                note = f"ambiguous — same cwd also in {others}"
            return Reentry("tmux", resume, note,
                           target=matches[0].target, goto=_goto(matches[0].target))

    run_id = row.get("scad_run_id")
    if run_id and run_id in running:
        return Reentry("container", resume, target=run_id,
                       goto=f"scad run attach {run_id}")

    return Reentry("resume", resume)


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
        row["reentry"] = {"kind": re_.kind, "command": re_.command, "note": re_.note,
                          "target": re_.target, "goto": re_.goto}
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
            # What it is about and where it got to. Carried explicitly because
            # this row is built field by field from `base` rather than copied,
            # so anything not named here is silently dropped — which is how
            # Open now ended up the one live section with no context at all.
            "first_text": base.get("first_text") or "",
            "last_text": base.get("last_text") or "",
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
        row["reentry"] = {"kind": re_.kind, "command": re_.command, "note": re_.note,
                          "target": re_.target, "goto": re_.goto}
        rows.append(row)
    rows.sort(key=lambda r: r["ended"] or r["started_at"] or 0, reverse=True)
    return rows


# Openers the agent writes for itself, not the human's ask. Measured over 400
# sessions: 3% of first user turns are one of these, and they are useless as
# "what is this session about" — the real question is the turn after them.
_MACHINERY = re.compile(
    r"\s*<(system-reminder|environment_context|recommended_plugins|user_instructions|"
    r"command-name|command-message|local-command|ide_[a-z_]+)\b", re.I)

# A slash-command pastes its whole skill body in as the first user turn. The
# human's actual ask is elsewhere, so the body answers "which skill ran" and
# never "what is this session about".
_SKILL_PREAMBLE = re.compile(r"\s*Base directory for this skill:", re.I)


def _first_text(conn, session_id: str) -> str:
    """The human's opening ask — what this session is *about*.

    Skips the wrappers an agent injects ahead of the real prompt, and reads a
    few turns rather than one so a session that opens with two of them still
    answers the question.
    """
    for row in conn.execute(
        "SELECT text FROM turns WHERE session_id = ? AND role = 'user' "
        "AND kind = 'text' AND text <> '' ORDER BY idx LIMIT 6",
        (session_id,),
    ):
        text = (row["text"] or "").strip()
        if text and not _MACHINERY.match(text) and not _SKILL_PREAMBLE.match(text):
            return text[:_SNIPPET]
    return ""


def _last_text(conn, session_id: str) -> str:
    """Where the session got to — the last thing that was *said*.

    Deliberately `kind = 'text'`. **61% of sessions end on a `tool_result`**
    (945 of 1537 measured), so taking the literal last turn showed tool output
    to most rows — which answers "what did a tool return" and never "where did
    this conversation stop". That a session ended mid-tool-loop is already said
    by `outcome = tool-result-last`; it does not also need to fill the snippet.
    """
    row = conn.execute(
        "SELECT text FROM turns WHERE session_id = ? AND kind = 'text' "
        "AND text <> '' ORDER BY idx DESC LIMIT 1",
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
    # What each session opened with and where it got to. Every row, not just the
    # waiting ones: "which of these is the thing I was doing" is the question
    # the full list exists to answer, and a uuid and a turn count never answer
    # it. Two indexed lookups per row; the page is rendered rarely and read a
    # lot, so the cost belongs here rather than in the reader's head.
    by_id = {}
    for row in all_rows:
        by_id.setdefault(row["id"], []).append(row)
    for row in waiting:
        by_id.setdefault(row["id"], []).append(row)
    for session_id, rows_for_id in by_id.items():
        first, last = _first_text(conn, session_id), _last_text(conn, session_id)
        for row in rows_for_id:
            row["first_text"], row["last_text"] = first, last

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
    # A pane row is a pane, not a session — but it names the session it most
    # likely holds, and that is the row a reader is looking at when they ask
    # "what is this one". Same context, fetched once per distinct session.
    for row in pane_rows:
        if row.get("likely_id"):
            row["first_text"] = _first_text(conn, row["likely_id"])
            row["last_text"] = _last_text(conn, row["likely_id"])
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
   --surface:#f7f8fa; --surface-line:#e8eaee; --shadow:0 1px 2px rgba(16,20,28,.06);
   /* One spacing scale, used everywhere. Every gap was an ad-hoc .3/.4/.5rem
      before, so controls that belong together landed at different rhythms —
      most visibly between the filter rows and the search box. */
   --s1:.25rem; --s2:.5rem; --s3:.75rem; --s4:1.25rem; --s5:2rem;
   --label:.66rem;
   --open:#059669; --open-bg:#d1fae5; --maybe:#b45309; --maybe-bg:#fef3c7;
   --shut:#6b7280; --shut-bg:#f3f4f6; --ask:#b45309; --claude:#4f46e5; --codex:#0891b2; --kimi:#7c3aed;
 }}
 @media (prefers-color-scheme: dark) {{
   :root {{
     --bg:#0f1115; --card:#181b21; --ink:#e5e7eb; --dim:#9199a6; --faint:#6b7280;
     --line:#262b33; --chip:#22262e; --chip-h:#2c313a;
     /* Dark UI reads depth as elevation, so the recessed panel is LIGHTER
        than the card rather than darker — the same trick inverted. */
     --surface:#1e222a; --surface-line:#2b3038; --shadow:0 1px 2px rgba(0,0,0,.35);
     --open:#34d399; --open-bg:#064e3b; --maybe:#fbbf24; --maybe-bg:#4a3208;
     --shut:#9199a6; --shut-bg:#22262e; --ask:#fbbf24; --claude:#818cf8; --codex:#22d3ee; --kimi:#c084fc;
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
 /* The container is a stack, not a surface. A hairline between rows was fine
    when a row was one line; a row is now two columns several lines tall, and a
    1px rule between two of them reads as one continuous block of text. So the
    row is the card and the gap does the separating. */
 .card {{ display: flex; flex-direction: column; gap: var(--s2);
          margin-bottom: var(--s4); }}
 .card > .head {{ display: flex; align-items: baseline; gap: .6rem;
                  padding: 0 .2rem var(--s1); }}
 .card > .head b {{ font-weight: 620; font-size: .93rem; }}
 .card > .head .meta {{ color: var(--faint); font-size: .78rem; margin-left: auto; }}
 /* ONE rule for the row. There were three, spread across the stylesheet, and
    twice a later one silently cancelled an earlier one -- the same collision
    that made the preview a single line for a day. */
 .row {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.25fr);
         gap: var(--s4); align-items: stretch;
         padding: var(--s3) .9rem; background: var(--card);
         border: 1px solid var(--line); border-left-width: 3px;
         border-radius: 10px; box-shadow: var(--shadow); }}
 .row .who {{ min-width: 0; }}
 /* The recessed panel. Two columns of plain text on one background read as
    one run-on paragraph; giving the quoted text its own surface says "this is
    the session talking, the rest is us describing it". */
 .ctx-col {{ min-width: 0; background: var(--surface); border-radius: 8px;
             border: 1px solid var(--surface-line); padding: var(--s2) .6rem;
             overflow: hidden; }}
 .row.nocontext .ctx-col {{ display: none; }}

 /* THE one accent. Agent is the strongest categorical fact on the page and the
    palette already names each family, so a rail turns the list into something
    you can scan by colour without reading — the same axis the agent filter
    works on, made visible. 3px, and nothing else on the page competes. */
 .row[data-agent="claude"] {{ border-left-color: var(--claude); }}
 .row[data-agent="codex"]  {{ border-left-color: var(--codex); }}
 .row[data-agent="kimi"]   {{ border-left-color: var(--kimi); }}
 .row:hover {{ background: color-mix(in srgb, var(--chip) 55%, transparent); }}

 /* Inside an open fold, the two ends of a conversation are separate facts. */
 .fold-part {{ margin: 0; }}
 .fold-part + .fold-part {{ border-top: 1px solid var(--surface-line);
                            padding-top: var(--s2); margin-top: var(--s2); }}
 .fold-tag {{ color: var(--faint); }}
 /* Labelled pairs. The label column is fixed so values line up down the page. */
 .facts {{ display: grid; grid-template-columns: 3.2rem minmax(0,1fr);
           gap: 0 var(--s2); margin: var(--s1) 0 0; font-size: .76rem; }}
 .ident {{ font-size: .8rem; color: var(--dim); margin-top: var(--s1); }}
 .dimmer {{ color: var(--faint); }}
 .tiny {{ font-size: .74rem; margin-left: auto; white-space: nowrap; }}
 .age {{ color: var(--dim); font-variant-numeric: tabular-nums; }}
 .turns {{ font-size: var(--label); color: var(--faint); }}
 .notes-badge {{ color: var(--ask); }}
 /* Every dot on the page. Faded so the facts carry the weight, and spaced --
    without this the run read as one word: "0m ago·645 turns". */
 .sep {{ color: var(--faint); opacity: .55; margin: 0 .45rem; }}
 .facts dt {{ font-size: var(--label); letter-spacing: .08em; text-transform: uppercase;
              color: var(--faint); line-height: 1.7; }}
 .facts dd {{ margin: 0; color: var(--dim); min-width: 0; overflow-wrap: anywhere; }}
 .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: .72rem; color: var(--ink); }}
 .row.nocontext {{ grid-template-columns: minmax(0,1fr); }}
 .row.nocontext .ctx-col {{ display: none; }}
 @media (max-width: 820px) {{
   .row {{ grid-template-columns: minmax(0,1fr); }}
 }}
 /* Actions sit under the metadata they act on, not in a column of their own. */
 .acts {{ display: flex; gap: .3rem; margin-top: .4rem; flex-wrap: wrap; }}
 .acts:empty {{ display: none; }}
 .t {{ display: flex; align-items: baseline; gap: .45rem; flex-wrap: wrap; }}
 /* Two lines of the opener before the fold, rather than one clipped line —
    the column is wide and a single truncated line wastes it. */
 .ctx[open] > summary {{ color: var(--ink); }}
 /* A clipped preview should say so. The fade sits over the last line only when
    closed, so a short session that fits shows no fade and needs no explaining. */

 .ctx > summary:hover {{ color: var(--ink); }}
 .cmd {{ font: inherit; font-size: .72rem; font-family: ui-monospace, monospace;
         padding: .1rem .45rem; border-radius: 5px; cursor: pointer;
         border: 1px solid var(--line); background: var(--chip); color: var(--dim);
         white-space: nowrap; }}
 .cmd:hover {{ border-color: currentColor; color: var(--ink); }}
 .cmd.ghost {{ background: transparent; opacity: .65; }}
 .row .t {{ font-weight: 550; overflow-wrap: anywhere; }}
 .row .m {{ color: var(--faint); font-size: .78rem; margin-top: .15rem; }}
 code {{ font: 11.5px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
         background: var(--chip); border: 1px solid var(--line); color: var(--ink);
         padding: .22rem .45rem; border-radius: 5px; cursor: pointer; white-space: nowrap;
         max-width: 30rem; overflow: hidden; text-overflow: ellipsis; display: inline-block;
         /* `overflow` other than visible moves an inline-block's baseline to its
            bottom margin edge, so this sat visibly high against the text beside
            it. Aligning on the middle rather than a baseline that no longer
            means what it says. */
         vertical-align: middle; }}
 code:hover {{ background: var(--chip-h); }}
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
 .ag.kimi {{ color: var(--kimi); }}
 /* All three axes in one place, and they stay reachable: 200 rows means the
    filters scroll away exactly when you realise you want them. */
 /* A facet rail, not three widgets. Each facet is one row of a shared grid --
    label gutter, then controls -- so a new facet (outcome, when, has-notes,
    full-text) is another row rather than a re-layout. That matters because
    this is where Littlebird's query UI grows. */
 .filters {{ position: sticky; top: 0; z-index: 20; background: var(--bg);
             padding: var(--s3) 0; margin-bottom: var(--s3);
             border-bottom: 1px solid var(--line); }}
 .facet {{ display: grid; grid-template-columns: 4.5rem minmax(0,1fr);
           align-items: baseline; gap: var(--s2); }}
 .facet + .facet {{ margin-top: var(--s3); }}
 .flabel {{ font-size: var(--label); letter-spacing: .09em; text-transform: uppercase;
            color: var(--faint); }}
 .facet input {{ width: 100%; margin: 0; }}
 .tabs, .agents {{ display: flex; flex-wrap: wrap; gap: var(--s1); margin: 0; }}
 /* Only shown once something is filtered: the seam to a server-run query. */
 .summary {{ margin-top: var(--s3); font-size: .74rem; color: var(--dim);
             display: flex; gap: var(--s2); align-items: baseline; }}
 .summary b {{ color: var(--ink); font-weight: 600; }}
 .clearf {{ font: inherit; font-size: .72rem; background: none; cursor: pointer;
            border: 1px solid var(--line); border-radius: 999px;
            padding: 0 .5rem; color: var(--dim); }}
 @media (max-width: 640px) {{
   .facet {{ grid-template-columns: 1fr; gap: var(--s1); }}
 }}
 .achip {{ font: inherit; font-size: .76rem; padding: .12rem .5rem; cursor: pointer;
           border: 1px solid var(--line); border-radius: 999px;
           background: transparent; color: var(--dim); }}
 .achip.claude {{ color: var(--claude); }}
 .achip.codex {{ color: var(--codex); }}
 .achip.kimi {{ color: var(--kimi); }}
 .achip.on {{ background: var(--chip); border-color: currentColor; }}
 .q {{ box-shadow: inset 3px 0 0 var(--ask); }}
 .needs {{ color: var(--ask); font-size: .82rem; margin-top: .2rem; }}
 .ctx {{ margin-top: .25rem; font-size: .82rem; }}
 /* ONE rule for the closed summary. There were two, and the later one set
    white-space:nowrap, so the line-clamp above it never applied and the
    preview was a single clipped line inside a box sized for six. */
 /* The summary holds both ends. The clamp is per block, not on the summary,
    so each gets its own opening lines instead of the first swallowing the
    space and the second falling off the bottom. */
 .ctx > summary {{ cursor: pointer; list-style: none; color: var(--dim); }}
 .ctx:not([open]) .fold-part {{ display: -webkit-box; -webkit-box-orient: vertical;
                                -webkit-line-clamp: 3; overflow: hidden; }}
 .ctx[open] .fold-part {{ display: block; }}
 .ctx > summary::-webkit-details-marker {{ display: none; }}
 .ctx > summary::after {{ content: "▸ more"; display: block; margin-top: var(--s1);
                          font-size: var(--label); letter-spacing: .06em;
                          text-transform: uppercase; color: var(--faint); }}
 .ctx[open] > summary::after {{ content: "▾ less"; }}
 .ctx[open] > summary {{ color: var(--ink); }}
 .fold-part {{ margin: .35rem 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
 .fold-tag {{ display: inline-block; min-width: 3.2rem; color: var(--faint);
              text-transform: uppercase; font-size: var(--label);
              letter-spacing: .08em; }}
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

<div class="filters">
  <div class="facet"><span class="flabel">project</span>
    <div class="tabs" id="tabs">{tabs}</div></div>
  <div class="facet"><span class="flabel">agent</span>
    <div class="agents" id="ag">
      <button class="achip on" data-agent="">all</button>
      <button class="achip claude" data-agent="claude">claude</button>
      <button class="achip codex" data-agent="codex">codex</button>
      <button class="achip kimi" data-agent="kimi">kimi</button>
    </div></div>
  <div class="facet"><span class="flabel">find</span>
    <input id="f" placeholder="name, path, or anything said in the session…"
           autocomplete="off"></div>
  <div class="summary" id="sum" hidden></div>
</div>

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

// Mirror of _context_fold: the opener says what a session is, the last word
// says where it stopped, and a native <details> keeps 200 rows scannable.
function ctxFold(r) {{
  const first = (r.first_text || "").trim(), last = (r.last_text || "").trim();
  if (!first && !last) return '';
  let blocks = '';
  if (first) blocks += '<p class="fold-part"><span class="fold-tag">opened</span>' + esc(first) + '</p>';
  if (last && last !== first) blocks += '<p class="fold-part"><span class="fold-tag">last</span>' + esc(last) + '</p>';
  return '<details class="ctx"><summary>' + blocks + '</summary></details>';
}}

function rows(list) {{
  if (!list.length) return '<div class="empty">' +
    (scopeLabel() ? 'No sessions for ' + esc(scopeLabel()) + '.' : 'Nothing here.') + '</div>';
  return '<div class="card">' + list.map(r =>
    '<div class="row' + (ctxFold(r) ? '' : ' nocontext') + '">' +
    '<div class="who"><div class="t" title="' + esc(r.cwd ?? "") + '">' + esc(label(r)) +
    '<span class="pill ' + esc(r.status) + '">' + esc(r.status) + '</span></div>' +
    '<div class="m"><span class="ag ' + esc(r.agent) + '">' + esc(r.agent) + '</span> · ' +
    esc(r.project ?? "") + ' · ' + r.n_turns + ' turns' +
    (r.n_agents ? ' · ' + r.n_agents + ' sub-agents' : '') + ' · ' + esc(when(r.ended)) +
    (r.title ? ' · ' + esc(r.title.slice(0, 70)) : '') + '</div>' +
    '<div class="acts">' + (r.reentry.command ? '<button class="cmd" data-cmd="' +
    esc(r.reentry.command) + '" title="' + esc(r.reentry.command) +
    '" onclick="copy(this)">\u29c9 resume</button>' : '') + '</div></div>' +
    '<div class="ctx-col">' + ctxFold(r) + '</div></div>').join('') + '</div>';
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
  // The command lives in data-cmd, not in the text: the button shows a label.
  navigator.clipboard.writeText(el.dataset.cmd || el.textContent);
  const was = el.textContent; el.textContent = "copied ✓";
  setTimeout(() => el.textContent = was, 800);
}}

const f = document.getElementById("f");
const draw = () => {{
  const q = f.value.toLowerCase();
  const list = DATA.all.filter(r =>
    (!SCOPE || (r.project || "") === SCOPE) &&
    (!AGENT || (r.agent || "") === AGENT) &&
    (!q || [r.name, r.project, r.cwd, r.title, r.id]
             .some(v => (v ?? "").toLowerCase().includes(q))));
  document.getElementById("all").innerHTML = rows(list);
  const n = document.querySelector('section[data-sec="all"] [data-count]');
  if (n) n.textContent = list.length;
  renderSummary(list.length, DATA.all.length);
}};
f.addEventListener("input", applyScope);

// Which project the page is scoped to. "" is All — the page as it has always
// looked. Everything is already in the document, so a tab only hides rows:
// no re-render, no second file, nothing to re-run.
let SCOPE = "";
// A second axis, ANDed with the project. Kept as its own variable rather than
// folded into SCOPE because the two answer different questions -- "whose work
// is this" and "which tool ran it" -- and a session belongs to exactly one of
// each, so composing them narrows rather than conflicts.
let AGENT = "";

function matches(el) {{
  if (SCOPE !== "" && (el.dataset.project || "") !== SCOPE) return false;
  if (AGENT !== "" && (el.dataset.agent || "") !== AGENT) return false;
  const q = f.value.trim().toLowerCase();
  // Searched against the row's own text, which is everything the row shows:
  // name, id, project, path, pane, and what the session actually said. The
  // box promises "anything said in the session" and the fold puts that text
  // in the row, so the simplest reading is also the honest one.
  return !q || (el.textContent || "").toLowerCase().includes(q);
}}

function scopeLabel() {{
  if (SCOPE && AGENT) return AGENT + " in " + SCOPE;
  return SCOPE || AGENT || "";
}}

function applyScope() {{
  document.querySelectorAll(".row[data-project]").forEach(r => {{
    r.hidden = !matches(r);
  }});
  document.querySelectorAll(".card").forEach(card => {{
    const rs = [...card.querySelectorAll(".row")];
    const vis = rs.filter(r => !r.hidden);
    card.hidden = rs.length > 0 && vis.length === 0;
    // Nothing to patch up per-row any more: rows are separated by a gap, not
    // by a border the first one has to suppress, so which row is first stopped
    // mattering when the filter changes it.
  }});
  document.querySelectorAll("section[data-sec]").forEach(sec => {{
    if (sec.dataset.sec === "all") return;      // drawn from DATA, see draw()
    const body = sec.querySelector(".body");
    const rs = [...body.querySelectorAll(".row")];
    const vis = rs.filter(r => !r.hidden).length;
    const note = sec.querySelector(".scoped");
    // A section that empties out under a scope says so. A blank region reads
    // as a broken page, not as an answer.
    const scoped = SCOPE !== "" || AGENT !== "";
    const blank = scoped && rs.length > 0 && vis === 0;
    body.hidden = blank;
    note.hidden = !blank;
    if (blank) note.textContent = "Nothing here for " + scopeLabel() + ".";
    const n = sec.querySelector("[data-count]");
    if (n) n.textContent = scoped ? vis : rs.length;
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

document.getElementById("ag").addEventListener("click", ev => {{
  const chip = ev.target.closest(".achip");
  if (!chip) return;
  // Clicking the active agent clears it, so the filter is escapable without
  // hunting for an "all" button.
  AGENT = (chip.dataset.agent === AGENT) ? "" : chip.dataset.agent;
  document.querySelectorAll(".achip").forEach(
    c => c.classList.toggle("on", c.dataset.agent === AGENT));
  applyScope();
}});

function renderSummary(shown, total) {{
  const sum = document.getElementById("sum");
  const parts = [];
  if (AGENT) parts.push(AGENT);
  if (SCOPE) parts.push("in " + SCOPE);
  if (f.value.trim()) parts.push('matching "' + f.value.trim() + '"');
  if (!parts.length) {{ sum.hidden = true; return; }}
  sum.hidden = false;
  sum.innerHTML = '<span><b>' + esc(parts.join(" ")) + '</b> \u00b7 ' +
    shown + ' of ' + total + '</span>' +
    '<button class="clearf" onclick="clearFilters()">clear</button>';
}}

function clearFilters() {{
  SCOPE = ""; AGENT = ""; f.value = "";
  document.querySelectorAll(".tab").forEach(
    t => t.classList.toggle("on", t.dataset.tab === ""));
  document.querySelectorAll(".achip").forEach(
    c => c.classList.toggle("on", c.dataset.agent === ""));
  applyScope();
}}

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


def _chip(text: str, ghost: bool = False, label: str = "resume") -> str:
    """A click-to-copy button that shows its label, not its command.

    The command used to be the visible text, so every row spent two lines on
    `cd /long/path && claude --resume <uuid>` and a tmux incantation. Across
    200 rows that is most of the page, and none of it is information — nobody
    reads a resume command, they paste it.

    The command still travels in `data-cmd`, so a copy yields the whole thing,
    and `title` shows it for anyone who wants to read before pasting.
    """
    if not text:
        return ""
    cls = "cmd ghost" if ghost else "cmd"
    safe = _html.escape(text)
    return (f'<button class="{cls}" data-cmd="{safe}" title="{safe}" '
            f'onclick="copy(this)">⧉ {_html.escape(label)}</button>')


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
         extra: str = "", flag: bool = False, project: str | None = None,
         agent: str | None = None, context: str = "", tiny: str = "") -> str:
    """One row, identical in every section.

    Every section previously built its own `<table>`, so each computed column
    widths independently and nothing lined up down the page. One grid row shared
    everywhere fixes that by construction — the columns cannot drift apart
    because there is only one definition of them.

    `data-project` and `data-agent` are what the filters scope on. Every row
    carries both, empty string included, so each filter is one selector rather
    than a rule per section that some future section would forget to join —
    and the two compose by AND without either knowing about the other.
    """
    pill_html = f'<span class="pill {pill}">{_html.escape(pill)}</span>' if pill else ""
    scope = _html.escape(project or "")
    who = _html.escape(agent or "")
    ctx = context or ""
    # Two columns, not three. Everything that describes the session — name,
    # status, counts, and the buttons that act on it — is metadata and belongs
    # together on the left. The right column is only what was *said*, so it is
    # free to run to several lines without pushing anything else around.
    #
    # A row with nothing to quote drops the second column rather than reserving
    # an empty one: 35 of 220 sessions have no human turn, and a blank gutter
    # reads as missing data rather than as absent data.
    blank = "" if ctx else " nocontext"
    return (
        f'<div class="row{" q" if flag else ""}{blank}" data-project="{scope}" '
        f'data-agent="{who}">'
        f'<div class="who">'
        f'<div class="t">{title}{pill_html}{tiny}</div>'
        f'<div class="m">{meta}</div>{extra}'
        f'<div class="acts">{"".join(chips)}</div>'
        f'</div>'
        f'<div class="ctx-col">{ctx}</div>'
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



def _card(head: str, meta: str, rows: list[str]) -> str:
    return (f'<div class="card"><div class="head"><b>{head}</b>'
            f'<span class="meta">{meta}</span></div>{"".join(rows)}</div>')


def _empty(msg: str) -> str:
    return f'<div class="empty">{_html.escape(msg)}</div>'



_SEP = '<span class="sep">·</span>'


def _facts(row: dict) -> str:
    """The metadata, in four lines rather than nine.

    A label per fact was the right instinct and the wrong dose: nine labelled
    rows made a short session taller than the thing it described. Only two
    facts actually need naming — the id, because it is an opaque string, and
    where, because a path and a pane target look alike. Everything else says
    what it is by how it reads: an agent is a coloured name, a count is a
    count.

    So: counts and age ride beside the heading as a quiet indicator, identity
    is one inline run, and the two labelled rows carry what is left.
    """
    e = _html.escape
    out = []

    ident = []
    if row.get("agent"):
        ident.append(_agent_tag(str(row["agent"])))
    if row.get("project"):
        ident.append(e(str(row["project"])))
    if row.get("n_notes"):
        ident.append(f'<span class="notes-badge" title="{row["n_notes"]} notes">'
                     f'◆ {row["n_notes"]}</span>')
    title = (row.get("title") or "").strip()
    label = (row.get("name") or "").strip()
    # A session named by /rename gets a title recording that rename, so this
    # was printing the heading back verbatim on every renamed row.
    if title and not (label and label.lower() in title.lower()):
        ident.append(f'<span class="dimmer">{_clip(title, 60)}</span>')
    if ident:
        out.append(f'<div class="ident">{_SEP.join(ident)}</div>')

    pairs = []
    if row.get("id"):
        pairs.append(("id", f'<span class="mono">{e(str(row["id"]))}</span>'))
    where = []
    if row.get("cwd"):
        where.append(f'<span class="mono">{_clip(str(row["cwd"]), 52)}</span>')
    target = (row.get("reentry") or {}).get("target") or row.get("target") or ""
    if target:
        where.append(f'<span class="mono">{e(str(target))}</span>')
    if where:
        pairs.append(("where", _SEP.join(where)))
    if pairs:
        cells = "".join(f'<dt>{e(k)}</dt><dd>{v}</dd>' for k, v in pairs)
        out.append(f'<dl class="facts">{cells}</dl>')
    return "".join(out)


def _tiny(row: dict) -> str:
    """Counts and age, beside the heading. Small enough to ignore, present
    enough to answer "is this a big session and was it recent"."""
    bits = []
    when = _ago(row.get("ended"))
    if when:
        # Age first and brighter. How long ago something moved is what decides
        # whether you look at it; how many turns it took is trivia by comparison.
        bits.append(f'<span class="age">{_html.escape(when)}</span>')
    if row.get("n_turns"):
        bits.append(f'<span class="turns">{row["n_turns"]} turns</span>')
    return f'<span class="tiny">{_SEP.join(bits)}</span>' if bits else ""


def session_row(row: dict, *, extra: str = "") -> str:
    """THE row renderer. Every section calls this and nothing builds its own.

    Five sections each assembled a row their own way, and every inconsistency
    on this page came from that: two sections never got the context fold, the
    pane action was labelled "resume" in one place and "go to pane" in another,
    and the metadata was a different subset in each. Fixing any of them meant
    finding all five, and twice it meant missing two.

    So the shape of a row is decided once. A section chooses which rows to
    show and in what order; it does not get an opinion about what a row is.

    `extra` is the only per-section slot, for a fact that belongs to one
    context only — what a live session is blocked on, say.
    """
    return _row(
        _label(row),
        _facts(row),
        _actions(row),
        pill=_pill_for(row),
        tiny=_tiny(row),
        extra=extra,
        context=_context_fold(row),
        flag=_wants_you(row),
        project=row.get("project"),
        agent=row.get("agent"),
    )


def _pill_for(row: dict) -> str:
    """One status vocabulary. `status` when the registry reported one,
    otherwise the derived open/maybe/closed the index computes."""
    return row.get("status") or row.get("state") or ""


def _wants_you(row: dict) -> bool:
    """Whether the row is asking for a human, however that was established."""
    return (row.get("status") == "waiting"
            or row.get("outcome") == "awaiting-question")


def _actions(row: dict) -> list[str]:
    """The two things you can do with a session, named the same way everywhere.

    `resume` rebuilds the conversation from disk and always works. `pane` walks
    to where it is open now, and only exists while that is true — so it is the
    demoted one. They were previously labelled inconsistently across sections,
    which made the same button look like two different features.
    """
    reentry = row.get("reentry") or {}
    command = reentry.get("command") or resume_command(row)
    actions = [_chip(command, label="resume")] if command else []
    goto = reentry.get("goto") or row.get("goto")
    if goto:
        actions.append(_chip(goto, ghost=True, label="pane"))
    return actions



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
        extra = ""
        if r.get("waiting_for"):
            extra = f'<div class="needs">waiting on {e(str(r["waiting_for"]))}</div>'
        if not r.get("indexed"):
            # Started too recently to have been archived. Say so rather than
            # letting the fact grid show zeros that look like a session which
            # did nothing.
            extra += '<div class="m">not indexed yet</div>'
        out.append(session_row(r, extra=extra))
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
                # A pane is not a session, but the row a human reads is the
                # same object: give it the session shape and let the one
                # renderer decide what a row looks like.
                rows.append(session_row({
                    "id": r.get("likely_id"), "name": r.get("likely_name"),
                    "title": r.get("likely_title"), "outcome": r.get("likely_outcome"),
                    "cwd": r.get("cwd"), "agent": r.get("agent"), "kind": "main",
                    "project": r.get("project"), "ended": r.get("last_activity"),
                    "first_text": r.get("first_text"), "last_text": r.get("last_text"),
                    "goto": r.get("goto"), "target": r.get("target"),
                }, extra=f'<div class="m">{hint}</div>' if not r.get("likely_id") else ""))
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


def _context_fold(r: dict) -> str:
    """Collapsed one-liner that opens into the opening ask and the last word.

    A native `<details>` rather than the click-to-expand used for single long
    lines: this is two paragraphs, and a disclosure keeps a 1500-row list
    scannable while costing no JS — which a `file://` page has little room to
    spend anyway.

    The summary shows whichever end exists, because "what is this session" is
    answered by the opener and "where did it stop" by the last word, and a row
    with only one of them should still say something.
    """
    e = _html.escape
    first = " ".join(str(r.get("first_text") or "").split())
    last = " ".join(str(r.get("last_text") or "").split())
    if not first and not last:
        return ""
    # The summary IS the opener, carried in full — `<details>` keeps it on
    # screen when open, so repeating it in the body printed the same paragraph
    # twice the moment anyone expanded a row. The body therefore holds only
    # what the summary does not: the last word.
    #
    # No truncation: the box is as tall as the row already is and CSS clamps
    # the preview to the lines that fit. Cutting the string first meant the
    # clamp had nothing to clamp and the stretched box sat empty.
    parts = []
    if first:
        parts.append(("opened", first))
    if last and last != first:
        parts.append(("last", last))
    blocks = "".join(
        f'<p class="fold-part"><span class="fold-tag">{tag}</span>{e(text)}</p>'
        for tag, text in parts)
    # Both ends live in the summary so both are visible without opening
    # anything: "what did I start this for" and "where did it get to" are two
    # questions, and answering only the first until you click made the second
    # invisible on a page you scan. CSS clamps each block; opening lifts both.
    return f'<details class="ctx"><summary>{blocks}</summary></details>' 


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
        out.append(session_row(r, extra=extra))
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
            rows.append(session_row({**r, "project": r.get("project") or g["project"]},
                                    extra=extra))
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
