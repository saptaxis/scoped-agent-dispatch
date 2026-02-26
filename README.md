# scad — scoped agent dispatch

Config-driven CLI for launching Claude Code sessions in isolated Docker containers.

## The problem

When you ask Claude Code to execute an implementation plan, it runs in your current working tree — touching your files, your branch, your environment. If you want to run multiple plans in parallel, or keep your workspace clean while an agent works, you're stuck setting up Docker containers, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

Define your project setup once in YAML — repos, data directories, Python deps — then dispatch agents with one command:

```bash
scad run myproject --branch plan-22 --prompt "Execute plan 22"
```

Each agent gets:
- **Its own container** with a baked-in Python environment
- **Its own git branch** cloned from your local repos (read-only mounts)
- **Shared data mounts** for experiment I/O
- **Full `--dangerously-skip-permissions`** since it's isolated

When done, the agent's work comes back as git bundles — fetched into your host repos as branches you can review and merge. Your working tree is never touched.

## How it works

1. **Build** — First run renders a Dockerfile from your config (Python venv, deps, Claude Code, non-root user) and builds the image. Cached after that.
2. **Clone** — Entrypoint clones repos from read-only mounts into `/workspace/`, creates the agent's branch.
3. **Run** — Claude Code runs with your prompt inside the container.
4. **Bundle** — On exit, the entrypoint creates git bundles for each repo with new commits.
5. **Fetch** — The CLI fetches bundles into your host repos as new branches.

Run IDs follow the format `<branch>-<MonDD>-<HHMM>` (e.g., `plan-22-Feb26-1430`) and are used for container names, log files, and bundle files.

## Prerequisites

- **Docker** — must be installed and running. scad uses the Docker SDK to build images and manage containers. Install: https://docs.docker.com/engine/install/
- **Python >= 3.11**
- **Git** — for repo cloning and bundle operations
- **Claude Code subscription or API key** — for auth inside the container

## Install

```bash
pip install git+https://github.com/saptaxis/scoped-agent-dispatch.git
```

Or for development:

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch.git
cd scoped-agent-dispatch
pip install -e .
```

## Setup

### 1. Create a project config

Configs live in `~/.scad/templates/<name>.yml`. Copy an example from `examples/`:

```bash
mkdir -p ~/.scad/templates
cp examples/minimal.yml ~/.scad/templates/myproject.yml
# Edit paths to match your repos
```

Minimal config:

```yaml
name: my-project

repos:
  code:
    path: ~/code/my-project
    workdir: true
    branch_from: main

python:
  version: "3.11"

claude:
  dangerously_skip_permissions: true
```

See `examples/multi-repo.yml` for a config with multiple repos, data mounts, and apt packages.

### 2. Auth

scad mounts your Claude auth into the container. Either:

- **Subscription auth** (default): Have an active Claude subscription. `~/.claude/` and `~/.claude.json` are mounted automatically.
- **API key**: Set `ANTHROPIC_API_KEY` in your environment — it's passed through to the container.

### 3. Dispatch

```bash
# Headless — dispatches and returns immediately
scad run myproject --branch feature-x --prompt "Add user authentication"

# Interactive — attaches to the container session
scad run myproject --branch explore --prompt ""
```

### 4. Monitor (pre-CLI — using Docker directly)

```bash
# List running scad containers
docker ps --filter "label=scad.managed=true"

# Tail logs from a running agent
docker logs -f scad-<run-id>

# View the last 50 lines
docker logs --tail 50 scad-<run-id>

# Stop an agent
docker stop scad-<run-id> && docker rm scad-<run-id>

# Check exit status after completion
cat ~/.scad/logs/<run-id>.status.json | python3 -m json.tool

# Fetch bundles manually (if the background watcher didn't)
git bundle verify ~/.scad/logs/<run-id>-code.bundle
git fetch ~/.scad/logs/<run-id>-code.bundle <branch>:<branch>
```

Container names follow the pattern `scad-<run-id>` where run ID is `<branch>-<MonDD>-<HHMM>`.

### 5. Demo

`demo.sh` walks through the full lifecycle — dispatch, watch logs, check artifacts, fetch the bundle:

```bash
./demo.sh my-branch "Add a hello_world function and test it"
```

## Config reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Project name (used for Docker image tag) |
| `repos` | yes | Map of repo labels to config. At least one must have `workdir: true` |
| `repos.<key>.path` | yes | Host path to the git repo (`~` expanded) |
| `repos.<key>.workdir` | no | Set as container working directory (exactly one required) |
| `repos.<key>.branch_from` | no | Base branch for the agent's work (default: `main`) |
| `repos.<key>.add_dir` | no | Pass to `claude --add-dir` for multi-repo context |
| `mounts` | no | List of `{host, container}` read-write data mounts |
| `python.version` | no | Python version for the container venv (default: `3.11`) |
| `python.requirements` | no | Path to requirements.txt relative to workdir repo root |
| `apt_packages` | no | System packages to install via apt |
| `claude.dangerously_skip_permissions` | no | Pass `--dangerously-skip-permissions` (default: `false`) |
| `claude.additional_flags` | no | Extra CLI flags passed to claude |

## Artifacts

All artifacts live in `~/.scad/logs/`:

| File | Description |
|------|-------------|
| `<run-id>.log` | Claude's stdout/stderr |
| `<run-id>.status.json` | Structured exit status (exit code, duration, bundle results) |
| `<run-id>-<repo>.bundle` | Git bundle per repo with new commits |

## Status

**Working:** `scad run` with full container lifecycle — build, dispatch, bundle fetch.

**In progress:** Remaining CLI commands (`status`, `logs`, `stop`, `build`, `configs`).

## License

MIT
