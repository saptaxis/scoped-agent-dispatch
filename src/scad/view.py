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

from scad.live import TmuxPane, is_agent_command

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
    return Reentry("resume", f"cd {shlex.quote(cwd)} && {resume}" if cwd else resume)


_WAITING = ("awaiting-question", "awaiting-user")
_SNIPPET = 400

_COLUMNS = ("id, name, kind, agent, project, cwd, title, outcome, harness_state, "
            "needs, n_turns, started, ended, scad_run_id, grade")


def _as_rows(cursor_rows, panes, running) -> list[dict]:
    out = []
    for r in cursor_rows:
        row = dict(r)
        re_ = reentry_for(row, panes, running)
        row["reentry"] = {"kind": re_.kind, "command": re_.command, "note": re_.note}
        out.append(row)
    return out


def _last_text(conn, session_id: str) -> str:
    row = conn.execute(
        "SELECT text FROM turns WHERE session_id = ? ORDER BY idx DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return (row["text"] or "")[:_SNIPPET] if row else ""


def gather(conn, panes: list[TmuxPane], running: set[str], days: int = 14) -> dict:
    """Everything the page needs: what waits, what is live, and the full list."""
    cutoff = int((time.time() - days * 86400) * 1000)

    waiting_rows = conn.execute(
        f"SELECT {_COLUMNS} FROM sessions "
        f"WHERE kind = 'main' AND outcome IN (?, ?) AND ended >= ? "
        # Questions first, then oldest first inside each group so nothing rots.
        f"ORDER BY CASE outcome WHEN 'awaiting-question' THEN 0 ELSE 1 END, ended ASC",
        (*_WAITING, cutoff),
    ).fetchall()
    waiting = _as_rows(waiting_rows, panes, running)
    for row in waiting:
        row["last_text"] = _last_text(conn, row["id"])

    all_rows = _as_rows(
        conn.execute(f"SELECT {_COLUMNS} FROM sessions ORDER BY ended DESC").fetchall(),
        panes, running,
    )
    live = _one_per_place(r for r in all_rows if r["reentry"]["kind"] in ("tmux", "container"))

    return {
        "waiting": waiting,
        "live": live,
        "all": all_rows,
        "generated": int(time.time() * 1000),
    }


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>scad — sessions</title>
<style>
 body {{ font: 14px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 78rem;
        padding: 0 1rem; color: #1a1a1a; background: #fff; }}
 h1 {{ font-size: 1.3rem; margin: 0 0 .25rem; }}
 h2 {{ font-size: 1rem; margin: 2rem 0 .5rem; padding-bottom: .25rem; border-bottom: 1px solid #e3e3e3; }}
 .sub {{ color: #666; font-size: .85rem; margin-bottom: 1.5rem; }}
 table {{ border-collapse: collapse; width: 100%; }}
 td, th {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #eee; vertical-align: top; }}
 th {{ font-weight: 600; font-size: .8rem; color: #555; text-transform: uppercase; letter-spacing: .03em; }}
 code {{ font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; background: #f5f5f5;
         padding: .15rem .35rem; border-radius: 3px; cursor: pointer; }}
 code:hover {{ background: #e8e8e8; }}
 .q {{ border-left: 3px solid #b45309; }}
 .snippet {{ color: #555; font-size: .85rem; max-width: 34rem; }}
 .needs {{ color: #b45309; font-size: .85rem; }}
 .muted {{ color: #999; }}
 input {{ font: inherit; padding: .35rem .5rem; width: 22rem; margin-bottom: .75rem;
          border: 1px solid #ccc; border-radius: 4px; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background: #16181c; color: #e6e6e6; }}
   td, th {{ border-color: #2a2d33; }} h2 {{ border-color: #2a2d33; }}
   code {{ background: #24272e; }} code:hover {{ background: #2e323a; }}
   .snippet, .sub, th {{ color: #9aa0aa; }} input {{ background: #1d2026; color: #e6e6e6; border-color: #343841; }}
 }}
</style>
<h1>scad sessions</h1>
<div class="sub">generated {generated} · {n_all} sessions · click any command to copy</div>

<h2>Waiting on you ({n_waiting})</h2>
{waiting}

<h2>Live now ({n_live})</h2>
{live}

<h2>All sessions</h2>
<input id="f" placeholder="filter by name, project, cwd, title…" autocomplete="off">
<div id="all"></div>

<script>
const DATA = {data};
const esc = s => (s ?? "").toString().replace(/[&<>"]/g, c =>
  ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}})[c]);
const when = ms => ms ? new Date(ms).toLocaleString() : "";
const label = r => r.name || r.title || r.id.slice(0, 12);

function table(rows) {{
  if (!rows.length) return '<p class="muted">None.</p>';
  return '<table><tr><th>session</th><th>project</th><th>last</th><th>go</th></tr>' +
    rows.map(r => '<tr><td>' + esc(label(r)) + '<br><span class="muted">' + esc(r.agent) +
      ' · ' + r.n_turns + ' turns</span></td><td>' + esc(r.project) + '</td><td>' +
      esc(when(r.ended)) + '</td><td>' + (r.reentry.command
        ? '<code onclick="copy(this)">' + esc(r.reentry.command) + '</code>' : '') +
      '</td></tr>').join('') + '</table>';
}}

function copy(el) {{
  navigator.clipboard.writeText(el.textContent);
  const was = el.textContent; el.textContent = "copied"; setTimeout(() => el.textContent = was, 700);
}}

const f = document.getElementById("f");
const render = () => {{
  const q = f.value.toLowerCase();
  document.getElementById("all").innerHTML = table(DATA.all.filter(r => !q ||
    [r.name, r.project, r.cwd, r.title, r.id].some(v => (v ?? "").toLowerCase().includes(q))));
}};
f.addEventListener("input", render);
render();
</script>
</html>
"""


def _rows_html(rows: list[dict], waiting: bool = False) -> str:
    """Server-render the two priority sections so they are readable with JS off."""
    if not rows:
        return '<p class="muted">Nothing waiting.</p>' if waiting else '<p class="muted">None.</p>'
    out = ['<table><tr><th>session</th><th>project</th><th>waiting since</th><th>go</th></tr>']
    for r in rows:
        e = _html.escape
        cls = ' class="q"' if r.get("outcome") == "awaiting-question" else ""
        name = e(str(r.get("name") or r.get("title") or (r.get("id") or "")[:12]))
        since = datetime.fromtimestamp(r["ended"] / 1000).strftime("%Y-%m-%d %H:%M") if r.get("ended") else ""
        cmd = r["reentry"]["command"]
        note = f'<br><span class="muted">{e(r["reentry"]["note"])}</span>' if r["reentry"]["note"] else ""
        extra = ""
        if r.get("needs"):
            extra += f'<br><span class="needs">needs: {e(str(r["needs"]))}</span>'
        if waiting and r.get("last_text"):
            extra += f'<br><span class="snippet">{e(" ".join(str(r["last_text"]).split())[:220])}</span>'
        # Built outside the f-string so the attribute keeps its quotes.
        cmd_html = f'<code onclick="copy(this)">{e(cmd)}</code>{note}' if cmd else ""
        out.append(
            f'<tr{cls}><td>{name}<br><span class="muted">{e(str(r.get("agent") or ""))} · '
            f'{r.get("n_turns") or 0} turns</span>{extra}</td>'
            f'<td>{e(str(r.get("project") or ""))}<br><span class="muted">{e(str(r.get("cwd") or ""))}</span></td>'
            f'<td>{since}</td>'
            f'<td>{cmd_html}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def _embed(data: dict) -> str:
    """JSON safe to sit inside a <script> element.

    A session title containing "</script>" would otherwise close the block early
    and dump the rest of the payload into the document as markup — the corpus has
    titles quoting HTML. Escaping the three characters as \\u sequences keeps the
    text byte-identical after JSON.parse while making the terminator unspellable.
    """
    return (json.dumps(data, default=str)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render(data: dict) -> str:
    """One self-contained page. No network, no external assets."""
    return _PAGE.format(
        generated=datetime.fromtimestamp(data["generated"] / 1000).strftime("%Y-%m-%d %H:%M"),
        n_all=len(data["all"]), n_waiting=len(data["waiting"]), n_live=len(data["live"]),
        waiting=_rows_html(data["waiting"], waiting=True),
        live=_rows_html(data["live"]),
        data=_embed(data),
    )


def write_view(path: Path, html: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path
