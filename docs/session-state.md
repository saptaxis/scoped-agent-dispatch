# Session state

scad reads the logs agents already write, copies them somewhere nothing deletes them, and indexes the copy. Four stores are involved: the archive and the memos are durable, the index and the run dirs are rebuildable.

## Trace archive

`scad archive` copies agent traces (Claude, codex, kimi, and every scad run) into an append-only archive at `~/.scad/archive/`, overridable with `SCAD_ARCHIVE`.

Agents prune their own transcripts, and `scad run clean` destroys a run's traces with its container. The archive is the copy that survives both.

```bash
scad archive                 # sweep every root
scad archive --run <run-id>  # one run only
scad archive --json          # machine-readable counts
```

Safe to run repeatedly: unchanged files are skipped, growing files have only their new lines appended, and nothing is overwritten or shortened. A copy stops at the last complete newline, so a transcript being written mid-copy contributes no partial record and the split line arrives whole on the next run. A source that is rewritten rather than appended to is written beside the existing archive as `<name>.<mtime>.jsonl`.

`scad run clean` archives a run's traces before removing it. A run that lived an hour is gone before any scheduled sweep would fire.

## Session index

`scad reindex` turns the archive into a queryable index at `~/.scad/index.sqlite`.

```bash
scad archive          # nothing unarchived is ever indexed
scad reindex          # incremental; --rebuild to start over
scad session ls --project scad --kind main
scad session show <id>
scad project ls
```

The index covers Claude main sessions, their subagents and workflow agents, codex rollouts, kimi sessions, and container sessions from scad runs. Sessions known only to `history.jsonl`, whose transcripts were pruned, appear as `grade=skeleton` with no turns.

Re-running is incremental. A file whose size already matches what was parsed is skipped without being opened, so a second pass over 1454 files takes under a second.

`reindex` never deletes. `--rebuild` drops rows and refuses when any session's raw file is no longer in the archive, because those turns are then the only surviving copy. `--force` overrides that and should be treated as destructive.

Nothing reindexes on a timer. `scad view` runs a pass before it renders; every other command reads the index as it is. `scad index status` says when a pass last finished, without running one:

```bash
scad index status          # last indexed 2026-10-06 21:14 (7m ago); 1712 sessions, 94 memos
scad index status --json   # {"indexed_at": "...", "age_s": 412, "sessions": ..., "memos": ..., "schema_version": 3}
```

One reindex runs at a time. A second waits on a lock beside the index (`index.sqlite.lock`) and then finds nothing new, so two pages refreshing at once do not index the same turns twice.

### Reading and searching

```bash
scad session read <id> --kind text            # turns in order, minus tool noise
scad search "resolver engine"                 # full-text across every indexed turn
scad search retry --kind thinking             # search reasoning only
scad session ls --outcome awaiting-question   # sessions that asked you something
```

`--outcome` is derived from structure: whether the model called `AskUserQuestion`, whether a tool call ever got its result, who spoke last. Never from reading the prose. A session whose ending cannot be classified is left unlabelled.

### Reading the index from another program

`scad session ls --json` is the contract. Reading `~/.scad/index.sqlite` directly ties a consumer to a schema nothing checks. The export carries, per row: `id`, `kind`, `parent_session_id`, `agent`, `cwd`, `cwd_recorded`, `project`, `name`, `title`, `started`, `ended`, `n_turns`, `outcome`, `needs`, `grade`, `harness_state`; `last_turn` as `{ts, role, text}` with the text clipped to 240 characters and empty turns skipped; and `live` as `{pid, name, status, waiting_for}` from Claude's process registry, or `null`. `live` is Claude-only, since the registry is Claude's; `name` there is fresher than the index's, which learns it on reindex.

`cwd` is where the session's directory is now: resolved through symlinks, and through the alias file when the recorded directory has moved (see below). `cwd_recorded` is exactly what the transcript said. The two are equal unless the path went through a symlink or a move.

```bash
scad session ls --json --kind main --limit 1000     # every main session, one call
scad session ls --parent <id>                       # a session's subagents
scad memos ls --about orglens --about scad --json   # memos about things, wherever written
```

`--about` matches the name in `tags` or `entities`, as the `topic`, or as the project, and can be given several times; each JSON row then carries `about`, the names it matched. A memo about X is often written in Y's session and cross-tagged; by project alone, three of eight such memos were found. Rows also carry `tags` and `entities` themselves, so a consumer can do the match from one plain export instead.

`live` is the newest registry entry for the session, by `updatedAt`; a reattach can leave two entries for one id, and a `/rename` lands in the newer. The others are listed in `live.also_held_by`, each with the pane it sits in, because a stale holder keeps the context it had when it was left and typing into it is what makes a transcript diverge.

The index is in WAL mode, so a reader is not blocked while a reindex writes.

### Project attribution

`scad where` reports which tier matched (`scad.yml`, then `.scad-project`, then git root), what was tried before it, and the root it landed on. When nothing matches, the answer is `unfiled`, which is a shared bucket rather than a result.

A marker files future sessions. `project` is a computed column and the incremental pass is mtime-based, so re-filing what is already indexed needs `scad reindex --rebuild`. Redefining what a project means is an edit to `project.py` plus a reindex; no files move.

#### A directory that moved

`project` is derived from the recorded cwd, and a transcript records its cwd forever. After a directory moves, the recorded path points at nothing, and `scad reindex --rebuild` re-derives every row against the filesystem as it is now, so those sessions go to `unfiled` (or to the name of a marked ancestor). An ordinary reindex never recomputes `project`, so nothing is lost until a rebuild. **Write the rule before any rebuild.**

The rules live in `~/.scad/aliases` (under `SCAD_HOME`), one per line:

```
# a directory that moved, and when
/Users/me/Dropbox/old/place/proj -> /Users/me/code/proj   # 2026-10
```

- The separator is ` -> `, with the spaces. `#` after whitespace starts a comment. Both sides must be absolute; `~` is expanded.
- A rule is used only when the recorded directory no longer exists, so it can never redirect a session whose directory is still there.
- The longest matching old path wins, with no fallback to a shorter rule.
- Rules chain, so each move needs only its own rules. If a rule leads to a path that is itself gone, the next matching rule is applied, until the path exists; `a -> b` then `b -> c` takes a path recorded under `a` to `c`, and a later move of a folder inside a moved repository is one rule written in the repository's new spelling. No rule is used twice in one chain. A chain that runs into a missing directory keeps its last hop whose new side exists.
- Both sides, and the recorded path, are resolved through symlinks before they are compared, so either spelling of a symlinked path matches the other.
- A bad line is skipped with a warning on stderr; the rest still load.

`scad project aliases` lists the rules: `ok` (the old path is gone and the new one exists), `stale` (the old path still exists, so the rule does nothing), or `broken` (the new path is missing, and no later rule takes it anywhere that exists). `scad where --start <old path>` shows a `via alias:` line for each rule that answered, in order. Nothing recorded is rewritten: not the transcripts, not the archive, not the index's `cwd`.

## `scad view`

Renders the index to a self-contained HTML page and opens it.

```bash
scad view                 # refresh, then: waiting list, live sessions, everything, then open
scad view --no-open       # just write ~/.scad/view.html
scad view --no-refresh    # render the index as it is; the pure reader
```

One **Live** section covers everything running: a session per row with the pane it is in, and a row for any agent pane that cannot be resolved to a session, saying so. It replaced a pair of sections that showed mostly the same rows from the registry and from tmux.

It answers who is waiting on you and how to get back to them. A session is waiting when it asked you something (`outcome = awaiting-question`), however long ago; one that merely ended with the agent speaking last is not, which is how nearly every finished session ends. Each row carries a command: `tmux select-window ... \; select-pane ...` for a live pane, `scad run attach` for a container, or `cd <cwd> && claude --resume <id>` for a session that has closed. The page is read-only; reply in the session itself.

A session's memos ride on its row, in the same fold as its opening ask and its last word, so the authored tier is visible where the session is rather than in a list of its own. Tags are not on the row; `scad memos ls --about NAME` and `scad search --memos` are how a tag is searched.

All sessions are grouped by when they last ran — Today, Yesterday, This week, This month, Older — so the months-old rows fall to the bottom under a heading rather than being cut off by a day threshold. The grouping happens in the browser, over whatever the facets and the search box have left.

Live panes and containers are discovered at render time and are current. The index is refreshed first by default, an incremental pass of about a second; `--no-refresh` keeps the pure reader. A refresh that fails warns and renders the existing index.

The archive keeps every version of a source file: one that was rewritten rather than appended to is stored as a fork beside the original. A refresh reads the newest fork and replaces that session's turns from it, once, and reports it as "re-read from a rewritten source". Codex did this to 133 rollouts at once in September 2026 when it changed its on-disk format.

A claude pane is matched to its session through the process tree, from the pane's shell to the pid Claude's registry names, and a scad-launched pane through its launch record. A pane neither can name gets the newest session in its working directory, labelled as the guess it is. Only panes running an agent count. tmux and docker are queried at render time and degrade to empty if either is unavailable.

## Interactive launch

```bash
scad session launch --agent codex --cwd ~/code/thing --prompt "port the parser"
scad session launch --agent claude --cwd . --json    # the launch record, for scripts
scad session launch --agent claude --cwd . --add-dir ../docs   # more directories it may work in
scad session launch --agent claude --window triage --name "triage loop"   # a window here, named
scad session launch --agent claude --split                     # a pane beside this one
scad session launch --agent claude --split review              # a pane in the window named review
scad session resume <id>                             # attach if open, resume if closed
scad session send <id> "next turn"                  # into the open pane; --file for a long one
scad session resume <id> --print                     # just the command
```

An interactive session's only output channel is its trace, so reading it back and going back into it both reduce to knowing its session id. The three families expose that differently:

| Agent | Where the id comes from |
|---|---|
| claude | minted by scad and passed in with `--session-id` |
| kimi | its own index line at TUI start, confirmed against the working directory |
| codex | the rollout its first turn creates, so a turn is sent to get one |

By default a launch opens its own detached tmux session, `scad-cl-HHMM`. `--window [NAME]` puts it in the tmux session you are already in, as a named window, and the launch record's target becomes e.g. `main:7.0`; with no NAME the window is named after the directory. That is worth preferring where it applies: a launch you cannot see is one you go back into by hand, and the hand route — `claude` then `/resume` — starts a second process on one session id. All five doubly-held sessions on this machine were launched panes re-entered that way.

`--split` goes one step further and opens the agent in a pane beside the one you typed in, in the window you already have arranged. The pane comes from `$TMUX_PANE`, which tmux exports into every pane, so it is exact rather than matched. Taking the caller's pane over instead was considered and not built: the process in it is the shell running scad, so scad would be killing its own parent. `--split NAME` splits another window's active pane instead: a window name, matched across every tmux session, or a target such as `main:4`. Two windows with that name are refused, naming both.

`--name NAME` sets the session's display name. It goes into the index row at launch, so a listing can tell several sessions apart before any of them has taken a turn, and for claude it is also passed to `claude -n`, which shows it in the prompt box, the `/resume` picker and the terminal title. codex and kimi have no equivalent flag, so there the name is scad's label alone.

Launching goes through tmux for all three. tmux supplies the pty that keeps a Claude session stamped `entrypoint: cli` rather than `sdk-cli`, which is what keeps it in Claude's own `/resume` picker. A non-pty launch produces a session the picker hides, so a missing tmux refuses rather than degrading.

A later turn goes in with `session send`, delivered as one bracketed paste and then submitted, the same transport as the first turn. `tmux send-keys` was measured to lose the head of a 1,400-character turn. `send` refuses a session that has closed, naming the resume command, and a pane sitting at a dialog.

`session resume` attaches when the session is still open and only runs the agent's own resume when it has closed. A second `claude --resume <id>` against an open session is a second process on one transcript: it appends its own entries, the chain forks, and until the original process exits every later resume follows the fork and hides the original's turns. Go through `session resume`, or attach to the pane, while a session is open.

When a session's directory has moved and a rule in `~/.scad/aliases` says where, `session resume` resumes there and says so. When it is gone with no rule, it still resumes, since `claude --resume` does not need the directory, but warns on stderr that the agent will start in the current directory and names the rule that would fix it; `--print` drops the `cd`, which would fail, and `scad view` marks the row `directory gone`. A rule whose new path does not exist is refused; `scad project aliases` marks it `broken`.

Gates shown in the pane are answered by matching the option label, never by pressing Enter, whose default on codex's update gate runs `curl ... | sh`. Gates with no safe answer, such as Claude Code's folder-trust dialog, stop the launch: nothing is sent, and the command exits non-zero naming the dialog and the pane.

Every launch writes `~/.scad/launches/<session-id>.json` with the agent, cwd, pane, resume command, and how the session was born. The pane is recorded twice: `pane_id` (`%45`) is authoritative and `tmux` (`main:11.0`) is a snapshot for reading, because an index path stops naming the pane the moment a window is moved or renumbered while the id survives every rearrangement. Everything that needs the pane resolves the id; a record written before the id existed falls back to the path. A file rather than a row, since `reindex --rebuild` would drop it. `scad session resume` reads it when it exists and falls back to the index when it does not, so resume works for every session on the machine.

A launched session is indexed immediately as a skeleton row. Its turns appear after the next index pass, where headless output is immediate.

## Handoffs

```bash
scad session handoff <id> "frame it for the release"   # ask a session for its handoff
scad session launch --agent claude --from <id>          # a fresh session picks up its work
scad session launch --agent claude --from <id> --prompt "prepare the docs instead"
scad session read <id> --kind text --last 200           # a session's last turns
scad session read <id> --kind text --since 2026-10-07T12:00:00+05:30
```

`session handoff` types `/memo-handoff ANGLE` into the session's pane and waits for the handoff memo (up to `--timeout`, 600 seconds by default). It works for sessions scad launched or restored, since those have a pane scad can type into, and it refuses a session whose context is nearly full.

A session is nearly full at 80% of its context window, or at 160k tokens when the window is unknown. `scad view` marks it, so a handoff can be written while there is still room.

`session launch --from` starts a fresh session in the source session's directory and gives it its first turn: read the source's newest handoff memo and every turn written after it, or its last 200 text turns when it has no handoff; write a handoff memo; then follow `--prompt`, or continue the work. The source does not have to write anything, so this works on a session too full to write its own handoff.

## Snapshot and restore

```bash
scad session snapshot              # record the open sessions
scad session restore               # bring back the newest snapshot's sessions
scad session restore --list        # recent snapshots
scad session restore FILE --skip 3f --only 9a -y
```

A snapshot records every open Claude session, from Claude's own registry, and every codex and kimi session started with `scad session launch`, from its launch record. Other agent panes are listed as not restorable, since nothing names their session. Snapshots are taken only by hand, into `~/.scad/snapshots/open-<stamp>.json`; the newest 50 are kept. The file lists sessions by project, with each session's name, agent, id, directory, context fill, tmux session, window and pane.

`restore` shows its plan, grouped by project, and asks once. A session that is open now is marked and never started a second time. Each session is resumed in its own directory, in the window it came from: split into that window if it exists, otherwise in a new window of that name, in its tmux session (created if missing). Windows `restore` creates are tiled. A session whose directory is gone resumes without changing directory, with a warning. Each restored pane gets a launch record, so `scad view` and `session resume` know where it is. One failure does not stop the others; the exit status is non-zero if any failed.

## Memos

Traces are evidence: derived, rebuildable, and pruned by the agents themselves. Memos are self-report: what an agent decided was worth keeping. They cannot be re-derived, so they are stored as plain files and the database only indexes them. Until 0.9.0 they were called notes.

```bash
/memo-write                        # from inside any agent session ($memo-write in Codex)
/memo-write focus on the tradeoff  # optional angle
/memo-handoff                      # a handoff memo, checked against the repo first
/memo-handoff brief                # the latest phase only
/memo-recall                       # catch up on a project: newest memo, then the repo
```

A skill writes the record in-session, where the context already is, and pipes it to `scad session memo --current`, which resolves the session whose trace is being written in this cwd. Memos land at `~/.scad/memos/<agent>/<session-uuid>.jsonl`, one appending file per session, and are indexed as they are written.

```bash
scad session memos <id>            # read them back, from the file
scad session memos --current       # this session's
scad memos ls --kind handoff       # what a session left for whoever comes next
scad memos read <id> --last
scad search "resolver" --memos     # body, topic, title, tags, entities, project
```

Memos are session-keyed, never project-keyed. A project is derived and can be redefined, and a path containing one would orphan every file the moment it changed. Each record carries `cwd_at_write`, so the project stays derivable from the memo alone.

Each record is one JSON line:

| Field | |
|---|---|
| `kind` | `info`, `handoff`, `bug`, `request` or `verification` |
| `topic` | the subject, as a short label |
| `parent` | the earlier topic this one hangs off, when it does |
| `project` | files the memo against a project other than the session's |
| `title`, `text` | one line, then the body |
| `tags`, `entities` | the search index |

`relation` (`continue`, `shift`, `branch`) is derived when the memo is read, from `parent` and the topics already in the thread, rather than authored.

A machine that still has the pre-0.9.0 store (`notes` in `~/.scad`) and no `memos` is refused by every command that reads or writes memos, with the `mv` that fixes it.
