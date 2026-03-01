# scad — scoped agent dispatch

Config-driven CLI for launching Claude Code sessions in isolated Docker containers.

## The problem

Running Claude Code on your working tree means it touches your files, your branch, your environment. If you want isolated agents — or parallel plans on separate branches — you're stuck setting up Docker, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

Define your project once in YAML — repos, data mounts, Python deps — then launch isolated sessions:

```bash
# Interactive — drop into a long-lived Claude session
scad session start myproject --tag plan22
scad session attach myproject-plan22-Feb28-1400

# Headless — fire-and-forget with streaming logs
scad session start myproject --tag plan22 --prompt "Execute plan 22"
scad session logs myproject-plan22-Feb28-1400 -sf
```

Each session gets:
- **Its own container** with a baked Python environment
- **Its own git branches** cloned from your local repos
- **Shared data mounts** for experiment I/O
- **Full `--dangerously-skip-permissions`** since it's isolated
- **Persistent Claude session data** across stop/restart

Sessions are long-lived. Work on multiple plans, detach and reattach, exit Claude and drop to bash, restart the container — the session survives until you `scad session clean` it.

## CLI

```bash
# Session — container + Claude lifecycle
scad session start <config> --tag <tag>  # launch session (--tag required, e.g., plan07, bugfix)
scad session stop <run-id>               # stop container (preserves state)
scad session attach <run-id>             # attach to tmux session inside container
scad session clean <run-id>              # remove container + clones + session data (destructive)
scad session status [--all]              # list sessions (running by default, --all for history)
scad session logs <run-id>               # read agent output
scad session info <run-id>               # session dashboard (includes cost if available)

# Code — git state between host and clones
scad code fetch <run-id>                 # snapshot clone branches back to host repos
scad code sync <run-id>                  # pull host repo updates into clones
scad code refresh <run-id>               # push fresh credentials into running container

# Project
scad project status <config>             # cross-session project view with cost

# Infrastructure
scad build <config>                      # build/rebuild Docker image
scad config list                         # list available configs
scad config new <name> [--edit]          # scaffold a new config from template
scad config view <name>                  # print config YAML
scad config edit <name>                  # open config in $EDITOR
scad config add <path>                   # register external config (symlink)
scad config remove <name>               # unregister config
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

Configs live in `~/.scad/configs/<name>.yml`:

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
  plugins:
    - superpowers
```

### 2. Build and run

```bash
scad build my-project              # builds Docker image (cached after first run)
scad session start my-project --tag initial  # creates clones, starts container
scad session attach my-project-initial-Feb28-1400  # drops into tmux with Claude running
```

### 3. Work

Inside the container, Claude has access to all repos and mounts. Detach with `Ctrl+b d` — container keeps running.

### 4. Get code back

```bash
scad code fetch my-project-initial-Feb28-1400   # fetches clone branches into your host repos
```

Then review and merge on the host:

```bash
git log main..scad-initial-Feb28-1400 --oneline
git merge scad-initial-Feb28-1400
```

### 5. Clean up

```bash
scad session clean my-project-initial-Feb28-1400  # removes container, clones, session data
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
2. **Clone** — Creates `git clone --local` of each repo on the host at `~/.scad/worktrees/<run-id>/`. Mounts into container.
3. **Branch** — Auto-generates branch name (`scad-{tag}-MonDD-HHMM`) and checks it out in each clone.
4. **Run** — Starts container detached. Entrypoint launches tmux with Claude (interactive) or streams JSON output (headless).
5. **Session** — Claude session data persists at `~/.scad/runs/<run-id>/claude/`. Survives stop/restart.
6. **Fetch** — `scad code fetch` snapshots clone branches back to host source repos.

## Data layout

```
~/.scad/
  configs/                          # project YAML configs
    my-project.yml
  worktrees/<run-id>/               # git clones (one per repo)
    my-project-code/
    my-project-docs/
  runs/<run-id>/                    # persistent session data
    claude/                         # mounted as container ~/.claude/
    events.log                      # append-only event history
```

## License

MIT
