# scad — scoped agent dispatch

Thin, config-driven CLI that wraps Docker, git, and Claude Code into a repeatable workflow for isolated AI coding sessions. Define your project once in YAML — scad handles environment setup, session lifecycle, code flow, and operational visibility.

## The problem

Running Claude Code on your working tree means it touches your files, your branch, your environment. If you want isolated agents — or parallel plans on separate branches — you're stuck setting up Docker, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

`scad` manages the full lifecycle: **config** your project, **build** a Docker image, start a **session**, inject **jobs**, manage **code** flow between host and container, and **clean up** when done.

```bash
scad config new myproject --edit          # scaffold and edit a config
scad build myproject                      # build Docker image
scad session start myproject --tag feat1  # start session (environment only)
scad session inject myproject-feat1-Mar02-1400 --prompt "implement X"  # inject a job
scad session inject myproject-feat1-Mar02-1400 --prompt "fix tests" --branch hotfix  # another job, different branch
scad code add myproject-feat1-Mar02-1400 --path ~/data --name data  # update workspace anytime
scad status                                     # see sessions and their jobs
scad session logs myproject-feat1-Mar02-1400 --job job-001  # what did a job do?
scad code fetch myproject-feat1-Mar02-1400      # fetch branches back to host
scad session clean myproject-feat1-Mar02-1400   # tear down
```

Composites combine primitives for common workflows:

```bash
scad dispatch myproject --tag feat1 --prompt "implement X"  # build + start + inject
scad dispatch myproject --tag feat1 --plan plan.md          # same, but from a plan file
scad harvest myproject-feat1-Mar02-1400                     # fetch + show git log
scad batch myproject --tag exp --prompt-file prompts.txt     # parallel headless jobs
scad finish myproject-feat1-Mar02-1400                      # fetch + clean
```

A **session** is a long-lived container for a project. Start it once, then inject as many **jobs** as you need — each job is a Claude process (interactive or headless) that can target its own branch. Update the workspace, add repos or data mounts, push fresh credentials — all while the session runs.

Each session gets:
- **Its own container** with a baked Python environment
- **Isolated git clones** from your local repos — the host repo is never touched
- **Shared data mounts** for bidirectional host/container I/O
- **Full `--dangerously-skip-permissions`** since it's isolated
- **Persistent Claude session data** across stop/restart
- **Pre-configured plugins** active from the first prompt
Detach and reattach, exit Claude and drop to bash, restart the container — the session survives until you `scad session clean` it.

Operational visibility: `scad status` shows running sessions and their jobs, `scad session info` shows token usage and Claude session history, `scad status <config>` aggregates across sessions, and `scad gc` cleans orphaned state.

## CLI

```bash
# Top-level composites + status
scad dispatch <config> --tag <tag> --prompt "..."  # start session + inject work (interactive default)
scad dispatch <config> --tag <tag> --plan plan.md  # start session + inject from plan file
scad batch <config> --tag <tag> --prompt-file prompts.txt  # parallel headless jobs from file
scad harvest <run-id>                              # fetch branches + show summary
scad harvest <run-id> --diff                       # fetch + show full diff
scad finish <run-id>                               # fetch + clean (safe teardown)
scad status                                        # list running sessions
scad status --all                                  # full session history
scad status <config>                               # cross-session project overview
scad status <config> --cost                        # include cost data (slow)

# Session — container + Claude lifecycle
scad session start <config> --tag <tag>            # launch session (setup only, no Claude)
scad session start <config> --tag <tag> --prompt "..."  # start + immediate inject (sugar)
scad session inject <run-id> --prompt "..."         # inject new Claude process (interactive default)
scad session inject <run-id> --prompt "..." --headless  # inject headless (fire-and-forget)
scad session inject <run-id> --prompt "..." --wait  # inject headless + block until done
scad session inject <run-id> --prompt "..." --wait --tail  # block + stream activity
scad session send <run-id> "text"                  # type into running interactive Claude
scad session jobs <run-id>                         # list injected jobs with status
scad session stop <run-id>                         # stop container (preserves state)
scad session stop --all [--yes]                    # stop all running sessions
scad session attach <run-id>                       # attach to tmux session
scad session clean <run-id>                        # remove container + clones (destructive)
scad session clean --all [--yes] [--force]         # clean all sessions
scad session logs <run-id>                         # read agent output
scad session info <run-id>                         # session dashboard
scad session refresh <run-id>                      # push fresh credentials into container

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
scad gc [--force]                                  # garbage collection
```

## Prerequisites

- **Docker** — installed and running ([install](https://docs.docker.com/engine/install/))
- **Python >= 3.11**
- **Git**
- **Claude Code subscription or API key**

## Install

Requires Python 3.11+ and Docker.

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch.git
cd scoped-agent-dispatch
./install.sh
```

The installer creates a Python venv, symlinks `scad` to `~/.local/bin/`, sets up
shell completions (zsh/bash auto-detected), and registers the Claude Code plugin
(if installed). Everything is auto-detected — skips gracefully when optional deps
are missing.

Options:

```bash
./install.sh --home ~/my-scad     # custom SCAD_HOME (default: ~/.scad)
./install.sh --dry-run            # preview without making changes
./install.sh --no-plugin          # skip Claude Code plugin registration
./install.sh --no-completions     # skip shell completion setup
./install.sh --uninstall          # remove scad (preserves your configs + data)
```

Development (editable install — same script, detects repo checkout):

```bash
pip install -e ".[dev]"           # if you prefer manual venv management
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
scad session start my-project --tag initial # creates clones, starts container
scad session attach my-project-initial-Mar02-1400  # drops into tmux with Claude
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
scad session clean my-project-initial-Mar02-1400  # removes container, clones, session data
```

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

## How it works

1. **Build** — Renders a Dockerfile from your config (Python venv, deps, Claude Code, non-root user) and builds the image. Cached after first build.
2. **Clone** — Creates `git clone --local` of each repo on the host at `~/.scad/runs/<run-id>/workspace/`. Non-worktree repos and data mounts are symlinked.
3. **Branch** — Auto-generates branch name (`scad-{config}-{tag}-MonDD-HHMM`) and checks it out in each clone.
4. **Configure** — `claude_config.py` centralizes all Claude Code configuration: `settings.json` (permissions, `attribution`, `enabledPlugins`), `.claude.json` (persisted across sessions via bind-mount from the run dir), host timezone inheritance (IANA `TZ` env var + `/etc/localtime` mount).
5. **Run** — Starts container detached. Entrypoint performs setup only (git config, tmux init) — no Claude launch.
6. **Inject** — `scad session inject` runs Claude inside the container via `docker exec`. Each injection is a tracked job with its own mode (interactive/headless), optional branch, and log stream.
7. **Session** — Claude session data persists at `~/.scad/runs/<run-id>/claude/`. Job metadata lives in `~/.scad/runs/<run-id>/jobs/`. Survives stop/restart.
8. **Fetch** — `scad code fetch` discovers all branches across clones and snapshots them back to host repos.
9. **GC** — `scad gc` finds orphaned containers, dead run dirs, and unused images.

## Architecture

| Module | Responsibility |
|--------|---------------|
| `container.py` | Docker container lifecycle (create, start, stop, clean) |
| `claude_config.py` | Claude Code configuration — settings.json, mounts, timezone |
| `config.py` | YAML config loading and validation |
| `cli.py` | Click CLI commands and argument handling |

## Data layout

```
~/.scad/
  configs/                          # project YAML configs
    my-project.yml
  runs/<run-id>/                    # one directory = one session
    workspace/                      # single bind mount into container
      my-project-code/              # git clone (workdir repo)
      my-project-docs/              # git clone or symlink (add_dir repo)
    jobs/                           # per-job metadata (one file per inject)
      <job-id>.json
    claude/                         # mounted as container ~/.claude/
    claude.json                     # mounted as container ~/.claude.json
    events.log                      # append-only event history
```

## License

MIT
