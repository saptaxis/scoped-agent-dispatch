# scad — scoped agent dispatch

A CLI with state that knows every agent session on your machine — and can run them in isolated containers when you want that.

Two halves that share one substrate:

- **Session state.** Every Claude and codex session on the machine, archived before it is pruned, indexed, searchable, and answerable: *what is waiting on me, and how do I get back to it?*
- **Isolated runs.** Define a project once in YAML; scad handles Docker, git branches, mounts and session lifecycle so agents never touch your working tree.

## The problem

Two problems, and they turn out to be the same one.

**Agents forget, and then the evidence disappears.** Claude Code deletes transcripts after 30 days by default; `scad run clean` used to destroy a container's traces outright. Run five agents in parallel and you lose track of which conversation is waiting on you — and once a session's terminal closes, it is effectively unreachable even though the data survives.

**Running Claude Code on your working tree** means it touches your files, your branch, your environment. Isolated or parallel agents mean Docker, entrypoints, branches and mounts by hand.

## What this does

**Knows what ran.** Reads the agents' own JSONL — it instruments nothing — and archives it somewhere nothing deletes it. `scad view` renders one self-contained page: open agent panes grouped by tmux window, sessions waiting on you, and a copy-pasteable command to re-enter any of them (`tmux select-pane`, `scad run attach`, or `cd <cwd> && claude --resume <id>` for one that has been closed).

**Remembers what mattered.** `/remember` from any agent appends a durable note keyed to that session — the one thing here that cannot be re-derived from anything else.

**Runs agents in isolation.** **config** your project, **build** an image, start a **run**, inject **jobs**, manage **code** flow between host and container, **clean up** when done.

```bash
scad config new myproject --edit          # scaffold and edit a config
scad build myproject                      # build Docker image
scad run start myproject --tag feat1      # start the run (environment only)
scad run inject myproject-feat1-Mar02-1400 --prompt "implement X"  # inject a job
scad run inject myproject-feat1-Mar02-1400 --prompt "fix tests"    # another job, same run
scad code add myproject-feat1-Mar02-1400 --path ~/data --name data # update workspace anytime
scad run ls                               # see runs and their jobs
scad run logs myproject-feat1-Mar02-1400 --job job-001  # what did a job do?
scad code fetch myproject-feat1-Mar02-1400  # fetch branches back to host
scad run clean myproject-feat1-Mar02-1400   # tear down
```

Composites combine primitives for common workflows:

```bash
scad dispatch myproject --tag feat1 --prompt "implement X"  # build + start + inject
scad dispatch myproject --tag feat1 --plan plan.md          # same, but from a plan file
scad harvest myproject-feat1-Mar02-1400                     # fetch + show git log
scad batch myproject --tag exp --prompt-file prompts.txt     # parallel headless jobs
scad finish myproject-feat1-Mar02-1400                      # fetch + clean
```

A **run** is a long-lived container for a project. Start it once, then inject as many **jobs** as you need — each job is a Claude process (interactive or headless) that can target its own branch, and each produces one agent **session** (its trace). Update the workspace, add repos or data mounts, push fresh credentials — all while the run continues.

Each run gets:
- **Its own container** with a baked Python environment
- **Isolated git clones** from your local repos — the host repo is never touched
- **Shared data mounts** for bidirectional host/container I/O
- **Full `--dangerously-skip-permissions`** since it's isolated
- **Persistent Claude session data** across stop/restart
- **Pre-configured plugins** active from the first prompt
Detach and reattach, exit Claude and drop to bash, restart the container — the run survives until you `scad run clean` it.

Operational visibility: `scad run ls` shows running runs and their jobs, `scad run info` shows token usage and Claude session history, `scad run ls <config>` aggregates across runs, and `scad gc` cleans orphaned state.

## Install

Requires Python 3.11+ and Git.

On first install scad offers to raise Claude Code's transcript retention
(`cleanupPeriodDays`, 30 days by default). The archive can only keep what still exists, so
on a machine that has been running a while the default has already destroyed history before
scad first runs. It **asks** — `~/.claude/settings.json` is Claude Code's file, not scad's —
and it leaves any value you have already set alone, in either direction. `--no-retention`
never asks; `--yes` accepts without prompting, for scripted installs.

Install also registers scad as a Claude Code plugin via its own marketplace, so `/remember`
is available immediately and survives the official-plugin updates that rewrite
`installed_plugins.json`.

**Linux** — needs a running Docker daemon. `install.sh` verifies it is reachable and errors with setup guidance if not.

**macOS** — there is no native Docker, so scad runs containers in a dedicated [Colima](https://github.com/abiosoft/colima) VM it owns, under the profile name `scad`, isolated from any other Docker you use. `install.sh` installs Colima via Homebrew if needed and creates the profile; pass `--no-vm` to skip and wire it up yourself. Manage the VM with `scad vm`:

```bash
scad vm start      # start (creates it on first run)
scad vm status     # is scad's Docker daemon reachable?
scad vm info       # sizing, socket, extra mounts
scad vm stop       # stop; containers are preserved
scad vm delete     # destroy the VM and everything in it
```

`build`, `session start`, `dispatch`, and `batch` all start the VM automatically if it is down, so `scad vm start` is rarely needed by hand.

Size the VM in `~/.scad/settings.yml` (defaults shown):

```yaml
colima:
  cpu: 2
  memory: 4          # GiB
  disk: 60           # GiB
  vm_type: vz        # "qemu" on macOS 12 or older
  mount_type: virtiofs   # "sshfs" on macOS 12 or older
```

Sizing applies at VM creation. To resize: `scad vm delete && scad vm start`.

**macOS caveats**

- Host paths **outside `$HOME`** (external drives, `/Volumes/…`, `/data`) are not visible to the VM by default. scad reconciles this for you at `session start` — it adds any such `mounts:` or repo paths to the VM and restarts it, but only when the set actually changed.
- `scad code add` of a non-`$HOME` path **cannot** hot-add: a VM mount is only addable at restart. scad warns and offers to restart (`--restart-vm` to skip the prompt); the restart stops running sessions, and scad restarts the target session afterwards.
- `gpu: true` is **unsupported** on macOS — there is no NVIDIA runtime in a Lima VM. It errors clearly. GPU passthrough stays Linux-only.
- Docker Desktop and Podman are not supported targets. Colima is *the* macOS provider.

```bash
curl -fsSL https://raw.githubusercontent.com/saptaxis/scoped-agent-dispatch/main/install-remote.sh | bash
```

Or clone manually:

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch.git
cd scoped-agent-dispatch
./install.sh
```

Options:

```bash
# Remote installer
curl -fsSL ... | bash -s -- --prefix ~/my-scad-src  # custom clone location (default: ~/.scad/src)
curl -fsSL ... | bash -s -- --no-plugin              # skip Claude Code plugin

# Local installer (from repo checkout)
./install.sh --home ~/my-scad     # custom SCAD_HOME (default: ~/.scad)
./install.sh --dry-run            # preview without making changes
./install.sh --no-plugin          # skip Claude Code plugin registration
./install.sh --no-completions     # skip shell completion setup
./install.sh --uninstall          # remove scad (preserves your configs + data)
```


## CLI

```bash
# Top-level composites + the fleet view
scad dispatch <config> --tag <tag> --prompt "..."  # start a run + inject work (interactive default)
scad dispatch <config> --tag <tag> --plan plan.md  # start a run + inject from plan file
scad batch <config> --tag <tag> --prompt-file prompts.txt  # parallel headless jobs from file
scad harvest <run-id>                              # fetch branches + show summary
scad harvest <run-id> --diff                       # fetch + show full diff
scad finish <run-id>                               # fetch + clean (safe teardown)
scad run ls                                        # list running runs
scad run ls --all                                  # full run history
scad run ls <config>                               # cross-run project overview
scad run ls <config> --cost                        # include cost data (slow)

# Run — the container lifecycle (a run hosts many jobs)
scad run start <config> --tag <tag>                # launch the run (setup only, no Claude)
scad run start <config> --tag <tag> --prompt "..." # start + immediate inject (sugar)
scad run inject <run-id> --prompt "..."            # inject new Claude process (interactive default)
scad run inject <run-id> --prompt "..." --headless # inject headless (fire-and-forget)
scad run inject <run-id> --prompt "..." --wait     # inject headless + block until done
scad run inject <run-id> --prompt "..." --wait --tail  # block + stream activity
scad run send <run-id> "text"                      # type into running interactive Claude
scad run jobs <run-id>                             # list injected jobs with status
scad run stop <run-id>                             # stop container (preserves state)
scad run stop --all [--yes]                        # stop all running runs
scad run attach <run-id>                           # attach to tmux session
scad run clean <run-id>                            # remove container + clones (destructive)
scad run clean --all [--yes] [--force]             # clean all runs
scad run logs <run-id>                             # read agent output
scad run info <run-id>                             # run dashboard
scad run refresh <run-id>                          # push fresh credentials into container

# Code — git state between host and clones
scad code fetch <run-id>                           # fetch branches back to host
scad code sync <run-id>                            # sync host changes into clones
scad code diff <run-id>                            # show diff between clones and source
scad code branch <run-id> <name>                   # create/switch branch in all clones
scad code add <run-id> --path <dir> --name <name>  # add directory to workspace
scad code remove <run-id> --name <name>            # remove directory from workspace

# Config
scad config list                                   # list available configs
scad config new <name> [--edit]                    # scaffold new config
scad config view <name>                            # print config YAML
scad config edit <name>                            # open in $EDITOR
scad config info <name>                            # structured environment summary
scad config add <path>                             # register external config
scad config remove <name>                          # unregister config

# Infrastructure
scad build <config>                                # build/rebuild Docker image
scad vm start|stop|status|info|delete              # macOS: manage scad's Docker VM
scad gc [--force]                                  # garbage collection
scad archive                                       # copy agent traces to the archive
scad archive --run <run-id>                        # archive one run only
scad archive --json                                # machine-readable counts

# Session index
scad reindex [--rebuild] [--force]                 # archive new traces, then index them
scad reindex --no-archive                          # index only what is already archived
scad session ls [--project X] [--kind K] ...       # list indexed sessions
scad session ls --outcome awaiting-question        # what is explicitly asking you something
scad session show <id>                             # one session's metadata + turn breakdown
scad session read <id> [--kind text]               # print a session's turns
scad search <query> [--kind thinking]              # full-text search across every turn
scad project ls | scad project show <name>         # sessions grouped by resolved project
scad where                                         # which project scad resolves for a directory
scad view [--days N] [--no-open] [--refresh] [--output PATH]   # render the index to a page and open it

# Notes — the authored tier
scad session note --current                        # append a record (JSON on stdin); /remember calls this
scad session notes <id>                            # read a session's notes back
scad search <query> --notes                        # search topic, title, tags, entities
```

## Quick start

### 1. Create a config

```bash
scad config new my-project --edit   # scaffolds ~/.scad/configs/my-project.yml and opens in $EDITOR
```

Or write one directly. Configs can also live in your project repo and be registered with `scad config add <path>`:

```yaml
name: my-project

repos:
  code:
    path: ~/code/my-project
    workdir: true
  docs:
    path: ~/code/my-project-docs
    add_dir: true

mounts:
  - host: /data/experiments

python:
  version: "3.11"
  requirements: requirements.txt

claude:
  dangerously_skip_permissions: true
```

### 2. Build and run

```bash
scad build my-project                       # builds Docker image (cached after first run)
scad run start my-project --tag initial     # creates clones, starts container
scad run attach my-project-initial-Mar02-1400   # drops into tmux with Claude
```

Or dispatch from a plan file:

```bash
scad dispatch my-project --tag implement --plan docs/plans/feature.md
```

### 3. Work

Inside the container, Claude has access to all repos and mounts. Detach with `Ctrl+b d` — container keeps running.

### 4. Get code back

```bash
scad code fetch my-project-initial-Mar02-1400   # fetches clone branches into your host repos
```

Then review and merge on the host:

```bash
git log main..scad-my-project-initial-Mar02-1400 --oneline
git merge scad-my-project-initial-Mar02-1400
```

### 5. Clean up

```bash
scad run clean my-project-initial-Mar02-1400  # removes container, clones, session data
```

## How it works

1. **Build** — Renders a Dockerfile from your config (Python venv, deps, Claude Code, non-root user) and builds the image. Cached after first build.
2. **Clone** — Creates `git clone --local` of each repo on the host at `~/.scad/runs/<run-id>/workspace/`. Non-worktree repos and data mounts are symlinked.
3. **Branch** — Auto-generates branch name (`scad-{config}-{tag}-MonDD-HHMM`) and checks it out in each clone.
4. **Configure** — `claude_config.py` centralizes all Claude Code configuration: `settings.json` (permissions, `attribution`, `enabledPlugins`), `.claude.json` (persisted across sessions via bind-mount from the run dir), host timezone inheritance (IANA `TZ` env var + `/etc/localtime` mount).
5. **Run** — Starts container detached. Entrypoint performs setup only (git config, tmux init) — no Claude launch.
6. **Inject** — `scad run inject` runs Claude inside the container via `docker exec`. Each injection is a tracked job with its own mode (interactive/headless), optional branch, and log stream.
7. **Session** — Claude session data persists at `~/.scad/runs/<run-id>/claude/`. Job metadata lives in `~/.scad/runs/<run-id>/jobs/`. Survives stop/restart.
8. **Fetch** — `scad code fetch` discovers all branches across clones and snapshots them back to host repos.
9. **GC** — `scad gc` finds orphaned containers, dead run dirs, and unused images.

## Trace archive

`scad archive` copies agent traces (Claude, codex, and every scad run) into an
append-only archive at `~/.scad/archive/`, overridable with `SCAD_ARCHIVE`.

Agents prune their own transcripts — Claude Code keeps 30 days by default — and
`scad run clean` destroys a run's traces along with its container. This copies
them somewhere nothing deletes them. Safe to run repeatedly: unchanged files are
skipped, growing files have only their new lines appended, and nothing is ever
overwritten or shortened.

```bash
scad archive                 # sweep every root
scad archive --run <run-id>  # one run only
scad archive --json          # machine-readable counts
```

A copy always stops at the last complete newline, so a transcript being written
mid-copy contributes no partial record — the split line arrives whole on the next
run. If a source is ever rewritten or rotated rather than appended to, the existing
archive is kept untouched and the new content is written beside it as
`<name>.<mtime>.jsonl`.

`scad run clean` now archives a run's traces automatically before removing it —
that is the one loss no schedule can catch, since a run that lived an hour is gone
before any cron fires.

## Session index

`scad reindex` turns the archive into a queryable index at `~/.scad/index.sqlite`.

```bash
scad archive          # preserve traces first (nothing unarchived is ever indexed)
scad reindex          # incremental; --rebuild to start over
scad session ls --project scad --kind main
scad session show <id>
scad project ls
scad where            # which project scad resolves for this directory
```

Sessions include Claude main sessions, their subagents and workflow agents, codex
rollouts, and container sessions from scad runs. Sessions known only to
`history.jsonl` — those whose transcripts were pruned — appear as `grade=skeleton`
with no turns, which on this machine reaches five months further back than the
oldest surviving transcript.

The index reads the archive, never the live trace directories, so nothing can enter
it that is not preserved first. `project` is a computed column, not identity:
redefining what a project means is an edit to `project.py` plus a reindex, and no
files move. Re-running is incremental — a file whose size already matches what was
parsed is skipped unopened, so a second pass over 1454 files takes well under a
second.

Reading and searching the corpus:

```bash
scad session read <id> --kind text     # turns in order, minus the tool noise
scad search "resolver engine"          # full-text across every indexed turn
scad search retry --kind thinking      # search reasoning only
scad session ls --outcome awaiting-question   # sessions that asked you something
```

`--outcome` is derived from structure alone — whether the model called
`AskUserQuestion`, whether a tool call ever got its result, who spoke last — never
from reading the prose. Sessions whose ending it cannot classify honestly are left
unlabelled rather than guessed at.

**`reindex` never deletes.** Only `--rebuild` drops rows, and it refuses outright
when any session's raw is no longer in the archive, because those turns are then the
only surviving copy. `--force` overrides it, and should be treated as destructive.

### `scad view`

Render the session index to a self-contained HTML page and open it.

```bash
scad view                 # waiting list, live sessions, everything, then open
scad view --days 30       # widen the waiting window
scad view --no-open       # just write ~/.scad/view.html
scad view --refresh       # archive and index new traces first
```

Answers two questions: who is waiting on you, and how to get back to them. Each row
carries a command — `tmux select-window … \; select-pane …` for a live pane,
`scad run attach` for a container, or `cd <cwd> && claude --resume <id>` for a session
that has been closed. Read-only; reply in the session itself.

Live panes and containers are discovered at render time, so they are always current —
but everything else reflects the last `scad reindex`, which makes an unrefreshed page
*half* fresh. `--refresh` closes that gap with an incremental pass (~1s) before
rendering; it is opt-in so that plain `scad view` stays a pure reader, and a refresh
that fails warns and renders the existing index rather than withholding the page.

Live panes are matched by working directory, which is approximate — several panes can
share one. Only panes actually running an agent count, and where more than one matches
every candidate is listed rather than one being guessed at. tmux and docker are queried
at render time and degrade to empty if either is unavailable, so the page still renders.

## Notes — what the agent chose to record

Traces are *evidence*: what happened, derived, rebuildable, and pruned by the agents
themselves. Notes are *self-report*: what an agent decided was worth keeping. That makes
them the one tier here that can never be re-derived from anything else, so they are stored
as plain files and the database only indexes them.

```bash
/remember                       # from inside any Claude session
/remember focus on the tradeoff # optional angle — you supply it, the session has the material
```

The command produces the record in-session — where the context already is, so it costs one
generation and no re-reading — and pipes it to `scad session note --current`, which resolves
"the session whose trace is being written in this cwd right now". Notes land at
`~/.scad/notes/<agent>/<session-uuid>.jsonl`, one appending file per session.

```bash
scad session notes <id>         # read them back, from the file
scad search "resolver" --notes  # search topic, title, tags, entities
```

**Session-keyed, never project-keyed.** A project is derived and can be redefined; a path
containing one would orphan every file the moment it changed. Each record carries
`cwd_at_write`, so the project stays rederivable from the note alone even after every trace
is gone.

Each record is one JSON line: `topic`, `relation` (`continue` / `shift` / `branch` /
`return`), `parent`, `title`, `text`, `tags`, `entities`. The relation edges form the
session's semantic tree, so the notes viewer shows them in write order — a note out of
sequence says nothing about the shape of the work.

## Config reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Project name (used for Docker image tag) |
| `repos` | yes | Map of repo keys to config. Exactly one must have `workdir: true` |
| `repos.<key>.path` | yes | Host path to git repo (`~` expanded) |
| `repos.<key>.workdir` | no | Container working directory (exactly one required) |
| `repos.<key>.add_dir` | no | Pass to `claude --add-dir` for multi-repo context |
| `repos.<key>.focus` | no | Subdirectory to highlight in Claude's context prompt |
| `mounts` | no | List of `{host, container}` read-write data mounts. Concurrent jobs share these mounts — avoid conflicting writes. |
| `python.version` | no | Python version (default: `3.11`) |
| `python.requirements` | no | Path to requirements.txt relative to workdir repo |
| `apt_packages` | no | System packages to install via apt |
| `claude.dangerously_skip_permissions` | no | Skip permission prompts (default: `false`) |
| `claude.plugins` | no | Claude Code plugins to bootstrap at startup |
| `claude.claude_md` | no | Host path to CLAUDE.md to mount into container |

Config files must use the `.yml` extension (not `.yaml`).

## License

MIT
