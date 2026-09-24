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

### Reading the index from another program

`scad session ls --json` is the contract. Reading `~/.scad/index.sqlite` directly
ties a consumer to a schema nothing checks. The export carries, per row: `id`, `kind`, `parent_session_id`, `agent`, `cwd`, `project`,
`name`, `title`, `started`, `ended`, `n_turns`, `outcome`, `needs`, `grade`,
`harness_state`; `last_turn` as `{ts, role, text}` with the text clipped to 240
characters and empty turns skipped; and `live` as `{pid, name, status,
waiting_for}` from Claude's process registry, or `null`. `live` is Claude-only,
since the registry is Claude's; `name` there is fresher than the index's, which
learns it on reindex.

```bash
scad session ls --json --kind main --limit 1000     # every main session, one call
scad session ls --parent <id>                       # a session's subagents
scad notes ls --about orglens --about scad --json   # notes about things, wherever written
```

`--about` matches the name in `tags` or `entities`, as the `topic`, or as the
project, and can be given several times; each JSON row then carries `about`,
the names it matched. A note about X is often written in Y's session and
cross-tagged; by project alone, three of eight such notes were found. Rows also
carry `tags` and `entities` themselves, so a consumer can do the match from one
plain export instead.

`live` is the newest registry entry for the session, by `updatedAt`; a reattach
can leave two entries for one id, and a `/rename` lands in the newer. The others
are listed in `live.also_held_by`, each with the pane it sits in, because a
stale holder keeps the context it had when it was left and typing into it is
what makes a transcript diverge.

The index is in WAL mode, so a reader is not blocked while a reindex writes.

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
scad view                 # refresh, then: waiting list, live sessions, everything, then open
scad view --days 30       # widen the waiting window
scad view --no-open       # just write ~/.scad/view.html
scad view --no-refresh    # render the index as it is; the pure reader
```

It answers who is waiting on you and how to get back to them. Each row carries a
command: `tmux select-window ... \; select-pane ...` for a live pane, `scad run
attach` for a container, or `cd <cwd> && claude --resume <id>` for a session that
has closed. The page is read-only; reply in the session itself.

Live panes and containers are discovered at render time and are current. The
index is refreshed first by default, an incremental pass of about a second;
`--no-refresh` keeps the pure reader. A refresh that fails warns and renders the
existing index.

The archive keeps every version of a source file: one that was rewritten rather
than appended to is stored as a fork beside the original. A refresh reads the
newest fork and replaces that session's turns from it, once, and reports it as
"re-read from a rewritten source". Codex did this to 133 rollouts at once in
September 2026 when it changed its on-disk format.

A claude pane is matched to its session through the process tree, from the
pane's shell to the pid Claude's registry names, and a scad-launched pane through
its launch record. A pane neither can name gets the newest session in its
working directory, labelled as the guess it is. Only panes running an agent
count. tmux and docker are queried at render time and degrade to empty if either
is unavailable.

## Interactive launch

```bash
scad session launch --agent codex --cwd ~/code/thing --prompt "port the parser"
scad session launch --agent claude --cwd . --json    # the launch record, for scripts
scad session launch --agent claude --cwd . --add-dir ../docs   # more directories it may work in
scad session launch --agent claude --window triage --name "triage loop"   # a window here, named
scad session resume <id>                             # attach if open, resume if closed
scad session send <id> "next turn"                  # into the open pane; --file for a long one
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

By default a launch opens its own detached tmux session, `scad-cl-HHMM`. `--window
[NAME]` puts it in the tmux session you are already in, as a named window, and
the launch record's target becomes e.g. `main:7.0`; with no NAME the window is
named after the directory. That is worth preferring where it applies: a launch
you cannot see is one you go back into by hand, and the hand route — `claude`
then `/resume` — starts a second process on one session id. All five
doubly-held sessions on this machine were launched panes re-entered that way.

`--name NAME` sets the session's display name. It goes into the index row at
launch, so a listing can tell several sessions apart before any of them has
taken a turn, and for claude it is also passed to `claude -n`, which shows it in
the prompt box, the `/resume` picker and the terminal title. codex and kimi have
no equivalent flag, so there the name is scad's label alone.

Launching goes through tmux for all three. tmux supplies the pty that keeps a
Claude session stamped `entrypoint: cli` rather than `sdk-cli`, which is what
keeps it in Claude's own `/resume` picker. A non-pty launch produces a session the
picker hides, so a missing tmux refuses rather than degrading.

A later turn goes in with `session send`, delivered as one bracketed paste and
then submitted, the same transport as the first turn. `tmux send-keys` was
measured to lose the head of a 1,400-character turn. `send` refuses a session
that has closed, naming the resume command, and a pane sitting at a dialog.

`session resume` attaches when the session is still open and only runs the agent's
own resume when it has closed. A second `claude --resume <id>` against an open
session is a second process on one transcript: it
appends its own entries, the chain forks, and until the original process exits every
later resume follows the fork and hides the original's turns. Go through `session
resume`, or attach to the pane, while a session is open.

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
scad session notes --current       # this session's
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
