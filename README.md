# scoped-agent-dispatch

Config-driven CLI for launching Claude Code sessions in isolated Docker containers.

## The problem

When you ask Claude Code to execute an implementation plan, it runs in your current working tree — touching your files, your branch, your environment. If you want to run multiple plans in parallel, or keep your workspace clean while an agent works, you're stuck setting up Docker containers, entrypoint scripts, git branches, and volume mounts by hand.

## What this does

Define your project setup once in YAML — repos, data directories, Python deps, GPU access — then dispatch agents with one command:

```bash
scad run myproject --branch plan-22 --prompt "Execute plan 22"
```

Each agent gets:
- **Its own container** with a baked-in Python environment
- **Its own git branch** cloned from your local repos
- **Shared data mounts** for experiment I/O
- **Full `--dangerously-skip-permissions`** since it's isolated in a container

When done, the agent pushes its branch back to your local repos. You review and merge.

## Intended usage

```bash
# Launch agents on different plans in parallel
scad run myproject --branch plan-22 --prompt "Execute plan 22"
scad run myproject --branch plan-23 --prompt "Execute plan 23"

# Check what's running
scad status

# Tail logs
scad logs plan-22

# Stop an agent
scad stop plan-22
```

## Status

**Early development.** Design spec is written, implementation hasn't started yet. See the [design doc](docs/design.md) once available.

## License

MIT
