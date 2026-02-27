# scad — scoped agent dispatch

Config-driven CLI for launching Claude Code sessions in isolated Docker containers.

## The problem

Running Claude Code on your working tree means it touches your files, your branch, your environment. If you want isolated agents — or parallel plans on separate branches — you're stuck setting up Docker, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

Define your project once in YAML — repos, data mounts, Python deps — then launch isolated sessions:

```bash
# Interactive — drop into a long-lived Claude session
scad run myproject
scad attach myproject-Feb28-1400

# Headless — fire-and-forget with streaming logs
scad run myproject --prompt "Execute plan 22"
scad logs myproject-Feb28-1400 -sf
```

Each session gets:
- **Its own container** with a baked Python environment
- **Its own git branches** cloned from your local repos
- **Shared data mounts** for experiment I/O
- **Full `--dangerously-skip-permissions`** since it's isolated
- **Persistent Claude session data** across stop/restart

Sessions are long-lived. Work on multiple plans, detach and reattach, exit Claude and drop to bash, restart the container — the session survives until you `scad clean` it.

## CLI

```bash
scad run <config>              # launch session (interactive or --prompt for headless)
scad attach <run-id>           # attach to tmux session inside container
scad status                    # list running/stopped containers
scad logs <run-id>             # read agent output
scad stop <run-id>             # stop container (preserves state)
scad clean <run-id>            # remove container + clones + session data (destructive)
scad fetch <run-id>            # snapshot clone branches back to host repos
scad sync <run-id>             # pull host repo updates into clones
scad build <config>            # build/rebuild Docker image
scad configs                   # list available configs
scad config view <name>        # print config YAML
scad config edit <name>        # open config in $EDITOR
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
scad build my-project          # builds Docker image (cached after first run)
scad run my-project            # creates clones, starts container
scad attach my-project-Feb28   # drops into tmux with Claude running
```

### 3. Work

Inside the container, Claude has access to all repos and mounts. Detach with `Ctrl+b d` — container keeps running.

### 4. Get code back

```bash
scad fetch my-project-Feb28    # fetches clone branches into your host repos
```

Then review and merge on the host:

```bash
git log main..scad-Feb28-1400 --oneline
git merge scad-Feb28-1400
```

### 5. Clean up

```bash
scad clean my-project-Feb28    # removes container, clones, session data
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
3. **Branch** — Auto-generates branch name (`scad-MonDD-HHMM`) and checks it out in each clone.
4. **Run** — Starts container detached. Entrypoint launches tmux with Claude (interactive) or streams JSON output (headless).
5. **Session** — Claude session data persists at `~/.scad/runs/<run-id>/claude/`. Survives stop/restart.
6. **Fetch** — `scad fetch` snapshots clone branches back to host source repos.

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
    fetches.log                     # append-only fetch history
```

## License

MIT
