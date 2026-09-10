# Session state

scad reads the logs agents already write, copies them somewhere nothing deletes
them, and indexes the copy. Four stores are involved: the archive and the notes
are durable, the index and the run dirs are rebuildable.

## Trace archive

`scad archive` copies agent traces (Claude, codex, kimi, and every scad run) into
an append-only archive at `~/.scad/archive/`, overridable with `SCAD_ARCHIVE`.

Agents prune their own transcripts, and `scad run clean` destroys a run's traces
with its container. The archive is the copy that survives both.

```bash
scad archive                 # sweep every root
scad archive --run <run-id>  # one run only
scad archive --json          # machine-readable counts
```

Safe to run repeatedly: unchanged files are skipped, growing files have only
their new lines appended, and nothing is overwritten or shortened. A copy stops
at the last complete newline, so a transcript being written mid-copy contributes
no partial record and the split line arrives whole on the next run. A source that
is rewritten rather than appended to is written beside the existing archive as
`<name>.<mtime>.jsonl`.

`scad run clean` archives a run's traces before removing it. A run that lived an
hour is gone before any scheduled sweep would fire.

## Session index

`scad reindex` turns the archive into a queryable index at `~/.scad/index.sqlite`.

```bash
scad archive          # nothing unarchived is ever indexed
scad reindex          # incremental; --rebuild to start over
scad session ls --project scad --kind main
scad session show <id>
scad project ls
```

The index covers Claude main sessions, their subagents and workflow agents, codex
rollouts, kimi sessions, and container sessions from scad runs. Sessions known
only to `history.jsonl`, whose transcripts were pruned, appear as
`grade=skeleton` with no turns.

Re-running is incremental. A file whose size already matches what was parsed is
skipped without being opened, so a second pass over 1454 files takes under a
second.

`reindex` never deletes. `--rebuild` drops rows and refuses when any session's
raw file is no longer in the archive, because those turns are then the only
surviving copy. `--force` overrides that and should be treated as destructive.

### Reading and searching

```bash
scad session read <id> --kind text            # turns in order, minus tool noise
scad search "resolver engine"                 # full-text across every indexed turn
scad search retry --kind thinking             # search reasoning only
scad session ls --outcome awaiting-question   # sessions that asked you something
```

`--outcome` is derived from structure: whether the model called
`AskUserQuestion`, whether a tool call ever got its result, who spoke last. Never
from reading the prose. A session whose ending cannot be classified is left
unlabelled.

### Project attribution

`scad where` reports which tier matched (`scad.yml`, then `.scad-project`, then
git root), what was tried before it, and the root it landed on. When nothing
matches, the answer is `unfiled`, which is a shared bucket rather than a result.

A marker files future sessions. `project` is a computed column and the
incremental pass is mtime-based, so re-filing what is already indexed needs
`scad reindex --rebuild`. Redefining what a project means is an edit to
`project.py` plus a reindex; no files move.

## `scad view`

Renders the index to a self-contained HTML page and opens it.

```bash
scad view                 # waiting list, live sessions, everything, then open
scad view --days 30       # widen the waiting window
scad view --no-open       # just write ~/.scad/view.html
scad view --refresh       # archive and index new traces first
```

It answers who is waiting on you and how to get back to them. Each row carries a
command: `tmux select-window ... \; select-pane ...` for a live pane, `scad run
attach` for a container, or `cd <cwd> && claude --resume <id>` for a session that
has closed. The page is read-only; reply in the session itself.

Live panes and containers are discovered at render time and are current.
Everything else reflects the last `scad reindex`. `--refresh` runs an incremental
pass (about a second) before rendering, and is opt-in so that plain `scad view`
stays a reader. A refresh that fails warns and renders the existing index.

Live panes are matched by working directory, which is approximate, since several
panes can share one. Only panes running an agent count, and where more than one
matches, every candidate is listed. tmux and docker are queried at render time
and degrade to empty if either is unavailable.

## Interactive launch

```bash
scad session launch --agent codex --cwd ~/code/thing --prompt "port the parser"
scad session launch --agent claude --cwd . --json    # the launch record, for scripts
scad session resume <id>                             # attach if open, resume if closed
scad session resume <id> --print                     # just the command
```

An interactive session's only output channel is its trace, so reading it back and
going back into it both reduce to knowing its session id. The three families
expose that differently:

| Agent | Where the id comes from |
|---|---|
| claude | minted by scad and passed in with `--session-id` |
| kimi | its own index line at TUI start, confirmed against the working directory |
| codex | the rollout its first turn creates, so a turn is sent to get one |

Launching goes through tmux for all three. tmux supplies the pty that keeps a
Claude session stamped `entrypoint: cli` rather than `sdk-cli`, which is what
keeps it in Claude's own `/resume` picker. A non-pty launch produces a session the
picker hides, so a missing tmux refuses rather than degrading.

Gates shown in the pane are answered by matching the option label, never by
pressing Enter, whose default on codex's update gate runs `curl ... | sh`. Gates
with no safe answer, such as Claude Code's folder-trust dialog, stop the launch:
nothing is sent, and the command exits non-zero naming the dialog and the pane.

Every launch writes `~/.scad/launches/<session-id>.json` with the agent, cwd,
pane, resume command, and how the session was born. A file rather than a row,
since `reindex --rebuild` would drop it. `scad session resume` reads it when it
exists and falls back to the index when it does not, so resume works for every
session on the machine.

A launched session is indexed immediately as a skeleton row. Its turns appear
after the next index pass, where headless output is immediate.

## Notes

Traces are evidence: derived, rebuildable, and pruned by the agents themselves.
Notes are self-report: what an agent decided was worth keeping. They cannot be
re-derived, so they are stored as plain files and the database only indexes them.

```bash
/remember                        # from inside any agent session ($remember in Codex)
/remember focus on the tradeoff  # optional angle
```

The command writes the record in-session, where the context already is, and pipes
it to `scad session note --current`, which resolves the session whose trace is
being written in this cwd. Notes land at
`~/.scad/notes/<agent>/<session-uuid>.jsonl`, one appending file per session, and
are indexed as they are written.

```bash
scad session notes <id>            # read them back, from the file
scad notes ls --kind handoff       # what a session left for whoever comes next
scad notes read <id> --last
scad search "resolver" --notes     # topic, title, tags, entities, project
```

Notes are session-keyed, never project-keyed. A project is derived and can be
redefined, and a path containing one would orphan every file the moment it
changed. Each record carries `cwd_at_write`, so the project stays derivable from
the note alone.

Each record is one JSON line:

| Field | |
|---|---|
| `kind` | `info`, `handoff`, `bug`, `request` or `verification` |
| `topic` | the subject, as a short label |
| `parent` | the earlier topic this one hangs off, when it does |
| `project` | files the note against a project other than the session's |
| `title`, `text` | one line, then the body |
| `tags`, `entities` | the search index |

`relation` (`continue`, `shift`, `branch`) is derived when the note is read, from
`parent` and the topics already in the thread, rather than authored.
