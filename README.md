# scad — scoped agent dispatch

Config-driven CLI for launching Claude Code agents in isolated Docker containers. Define your project once in YAML, then build, dispatch, monitor, and fetch code back — all from the host.

## The problem

Running Claude Code on your working tree means it touches your files, your branch, your environment. If you want isolated agents — or parallel plans on separate branches — you're stuck setting up Docker, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

`scad` manages the full lifecycle: **config** your project, **build** a Docker image, start a **session** (interactive or headless), manage **code** flow between host and container, and **clean up** when done.

```bash
scad config new myproject --edit          # scaffold and edit a config
scad build myproject                      # build Docker image
scad session start myproject --tag feat1  # launch isolated Claude session
scad session attach myproject-feat1-Mar02-1400  # drop into tmux
# ... work, detach (Ctrl+b d), reattach anytime ...
scad code fetch myproject-feat1-Mar02-1400      # fetch branches back to host
scad session clean myproject-feat1-Mar02-1400   # tear down when done
```

Each session gets:
- **Its own container** with a baked Python environment
- **Its own git branches** cloned from your local repos
- **Shared data mounts** for experiment I/O
- **Full `--dangerously-skip-permissions`** since it's isolated
- **Persistent Claude session data** across stop/restart
- **Pre-configured plugins** active from the first prompt
- **Host timezone** inherited (git commits match your clock)
- **Three session modes** — interactive, interactive-with-prompt (Claude starts working on attach), headless (fire-and-forget)

Sessions are long-lived. Detach and reattach, exit Claude and drop to bash, restart the container — the session survives until you `scad session clean` it.

Operational visibility: `scad session status` shows running sessions with credential expiry warnings, `scad session info` shows token usage and Claude session history, `scad project status` aggregates across sessions, and `scad gc` cleans orphaned state.

## CLI

```bash
# Session — container + Claude lifecycle
scad session start <config> --tag <tag>  # launch interactive session
scad session start <config> --tag <tag> --prompt "..."  # interactive with prompt (Claude starts working)
scad session start <config> --tag <tag> --prompt "..." --headless  # fire-and-forget (uses claude -p)
scad session stop <run-id>               # stop container (preserves state)
scad session stop --all [--yes]          # stop all running sessions
scad session stop --config <name> [--yes]  # stop all sessions for a config
scad session attach <run-id>             # attach to tmux session inside container
scad session clean <run-id>              # remove container + clones + session data (destructive)
scad session clean --all [--yes] [--force]  # clean all sessions (--force includes running)
scad session clean --config <name> [--yes]  # clean all sessions for a config
scad session status [--all]              # list sessions (running by default, --all for history)
scad session logs <run-id>               # read agent output
scad session info <run-id>               # session dashboard (tokens/turns, cost if available)

# Code — git state between host and clones
scad code fetch <run-id>                 # snapshot clone branches back to host repos
scad code sync <run-id>                  # sync host refs + fast-forward main
scad code sync <run-id> --checkout main  # sync and switch to updated main
scad code sync <run-id> --no-update-main  # fetch only (skip fast-forward)
scad code refresh <run-id>               # push fresh credentials into running container

# Project
scad project status <config>             # cross-session project view (tokens/turns)
scad project status <config> --cost      # include cost data (slow — runs ccusage)

# Infrastructure
scad build <config>                      # build/rebuild Docker image (auto-prunes old images)
scad gc [--force]                        # garbage collection for orphaned state (dry-run by default)
scad config list                         # list available configs
scad config new <name> [--edit]          # scaffold a new config from template
scad config view <name>                  # print config YAML
scad config edit <name>                  # open config in $EDITOR
scad config add <path>                   # register external config (symlink)
scad config remove <name>               # unregister config
scad config info <name>                # structured environment summary
```

## Prerequisites

- **Docker** — installed and running ([install](https://docs.docker.com/engine/install/))
- **Python >= 3.11**
- **Git**
- **Claude Code subscription or API key**

## Install

```bash
pip install git+https://github.com/saptaxis/scoped-agent-dispatch.git
```

Development:

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch.git
cd scoped-agent-dispatch
pip install -e ".[dev]"
```

Shell completion (zsh):

```bash
echo 'eval "$(_SCAD_COMPLETE=zsh_source scad)"' >> ~/.zshrc
source ~/.zshrc
```

Bash:

```bash
echo 'eval "$(_SCAD_COMPLETE=bash_source scad)"' >> ~/.bashrc
```

Completes commands, subcommands, run IDs, and config names.

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
| `mounts` | no | List of `{host, container}` read-write data mounts |
| `python.version` | no | Python version (default: `3.11`) |
| `python.requirements` | no | Path to requirements.txt relative to workdir repo |
| `apt_packages` | no | System packages to install via apt |
| `claude.dangerously_skip_permissions` | no | Skip permission prompts (default: `false`) |
| `claude.plugins` | no | Claude Code plugins to bootstrap at startup |
| `claude.claude_md` | no | Host path to CLAUDE.md to mount into container |

## How it works

1. **Build** — Renders a Dockerfile from your config (Python venv, deps, Claude Code, non-root user) and builds the image. Cached after first build.
2. **Clone** — Creates `git clone --local` of each repo on the host at `~/.scad/runs/<run-id>/worktrees/`. Mounts into container.
3. **Branch** — Auto-generates branch name (`scad-{config}-{tag}-MonDD-HHMM`) and checks it out in each clone.
4. **Configure** — `claude_config.py` centralizes all Claude Code configuration: `settings.json` (permissions, `attribution`, `enabledPlugins`), `.claude.json` (persisted across sessions via bind-mount from the run dir), host timezone inheritance (IANA `TZ` env var + `/etc/localtime` mount).
5. **Run** — Starts container detached. Entrypoint launches tmux with Claude (interactive) or streams JSON output (headless).
6. **Session** — Claude session data persists at `~/.scad/runs/<run-id>/claude/`. Survives stop/restart.
7. **Fetch** — `scad code fetch` snapshots clone branches back to host source repos.
8. **GC** — `scad gc` finds orphaned containers, dead run dirs, and unused images.

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
    worktrees/                      # git clones (one per repo)
      my-project-code/
      my-project-docs/
    claude/                         # mounted as container ~/.claude/
    claude.json                     # mounted as container ~/.claude.json
    events.log                      # append-only event history
```

## License

MIT
