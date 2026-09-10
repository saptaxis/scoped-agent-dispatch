# Containers

A run is a long-lived container for a project. Start it once, then inject as many
jobs as you need. Each job is an agent process, interactive or headless, that can
target its own branch, and each produces one session.

```bash
scad config new myproject --edit          # scaffold and edit a config
scad build myproject                      # build the image
scad run start myproject --tag feat1      # start the run, environment only
scad run inject myproject-feat1-Mar02-1400 --prompt "implement X"
scad run inject myproject-feat1-Mar02-1400 --prompt "fix tests"
scad code add myproject-feat1-Mar02-1400 --path ~/data --name data
scad run ls
scad run logs myproject-feat1-Mar02-1400 --job job-001
scad code fetch myproject-feat1-Mar02-1400
scad run clean myproject-feat1-Mar02-1400
```

Composites combine those for the common paths:

```bash
scad dispatch myproject --tag feat1 --prompt "implement X"  # build, start, inject
scad dispatch myproject --tag feat1 --plan plan.md          # same, from a plan file
scad harvest myproject-feat1-Mar02-1400                     # fetch, show the log
scad batch myproject --tag exp --prompt-file prompts.txt    # parallel headless jobs
scad finish myproject-feat1-Mar02-1400                      # fetch, then clean
```

Each run gets its own container with a baked Python environment, isolated
`git clone --local` copies of your repos, shared data mounts for host and
container I/O, `--dangerously-skip-permissions` since it is isolated, Claude
session data that survives stop and restart, and any plugins named in the config
active from the first prompt.

Detach and reattach, exit the agent and drop to bash, restart the container: the
run survives until `scad run clean`.

`scad run ls` shows runs and their jobs, `scad run info` shows token usage and
session history, and `scad gc` cleans orphaned containers, run dirs and images.

## What a run does

1. **Build.** Renders a Dockerfile from the config (Python venv, deps, Claude
   Code, non-root user) and builds the image. Cached after the first build.
2. **Clone.** `git clone --local` of each repo at
   `~/.scad/runs/<run-id>/workspace/`. Non-worktree repos and data mounts are
   symlinked.
3. **Branch.** Generates `scad-{config}-{tag}-MonDD-HHMM` and checks it out in
   each clone.
4. **Configure.** `claude_config.py` writes `settings.json` (permissions,
   attribution, `enabledPlugins`) and `.claude.json`, bind-mounted from the run
   dir, and passes the host timezone through.
5. **Run.** Starts the container detached. The entrypoint does setup only, git
   config and tmux init, and launches no agent.
6. **Inject.** `scad run inject` runs the agent inside the container via
   `docker exec`. Each injection is a tracked job with its own mode, optional
   branch, and log stream.
7. **Session.** Agent session data persists at `~/.scad/runs/<run-id>/claude/`
   and job metadata at `~/.scad/runs/<run-id>/jobs/`.
8. **Fetch.** `scad code fetch` finds every branch across the clones and
   snapshots them back to the host repos.
9. **GC.** `scad gc` finds orphaned containers, dead run dirs and unused images.

This tier runs Claude. Codex and kimi are supported by the session index and by
`scad session launch`, not by the container.

## Config reference

Configs live in `~/.scad/configs/` and must use the `.yml` extension.

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Project name, used for the Docker image tag |
| `repos` | yes | Map of repo keys to config. Exactly one must have `workdir: true` |
| `repos.<key>.path` | yes | Host path to a git repo, `~` expanded |
| `repos.<key>.workdir` | no | Container working directory, exactly one required |
| `repos.<key>.add_dir` | no | Passed to `claude --add-dir` for multi-repo context |
| `repos.<key>.focus` | no | Subdirectory to highlight in the context prompt |
| `mounts` | no | List of `{host, container}` read-write mounts. Concurrent jobs share them, so avoid conflicting writes |
| `python.version` | no | Python version, default `3.11` |
| `python.requirements` | no | Path to requirements.txt, relative to the workdir repo |
| `python.editable` | no | `pip install -e .` at startup |
| `pip_install` | no | Per-repo editable install |
| `apt_packages` | no | System packages to install via apt |
| `gpu` | no | NVIDIA passthrough. Linux only |
| `claude.dangerously_skip_permissions` | no | Skip permission prompts, default `false` |
| `claude.plugins` | no | Plugins to bootstrap at startup |
| `claude.claude_md` | no | Host path to a CLAUDE.md to mount in |
