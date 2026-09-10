# scad

Session state and isolated execution for coding agents.

scad indexes every agent session on this machine by reading the logs the agents
already write, and it runs agents in containers that never touch your working
tree. The two halves share a project key and are otherwise independent: the
index covers claude, codex and kimi, including sessions scad never started, and
the container side runs Claude.

## What it assumes

- **Traces are read, never instrumented.** scad parses the JSONL the agents
  already write. Nothing is asked of them, so a family that changes its format
  degrades that family alone.
- **Copy before anything prunes.** Claude Code deletes transcripts after 30 days
  by default and `scad run clean` removes a run's traces with its container. The
  archive is append-only and is taken first.
- **Nothing enters the index unarchived.** The archive is what makes a row
  rebuildable, so `reindex` reads it rather than the live directories.
- **Derived is disposable, authored is not.** Sessions and turns can be dropped
  and rebuilt. Notes written by `/remember` cannot, so they are plain files and
  the database only indexes them.
- **A wrong label is worse than no label.** `project` is the retrieval key, so
  `unfiled` is a valid answer and a guess is not.
- **A run is an environment, a job is work.** One container, many jobs, each with
  its own branch and its own session.
- **Failure degrades to empty.** No index, no codex on this machine, no tmux for
  a listing: all ordinary.

## Install

Requires Python 3.11+, Git, and Docker (Linux) or Colima (macOS, installed for
you).

```bash
curl -fsSL https://raw.githubusercontent.com/saptaxis/scoped-agent-dispatch/main/install-remote.sh | bash
```

From a checkout:

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch.git
cd scoped-agent-dispatch && ./install.sh
```

Install does two things worth knowing about. It offers to raise Claude Code's
`cleanupPeriodDays`, because the archive can only keep what still exists; it asks
rather than sets, since that file is Claude Code's. And it installs scad's skills
for every agent on the machine through [`npx skills`](https://github.com/vercel-labs/skills),
falling back to symlinks when node is absent.

`--no-retention` skips the first, `--no-skills` the second, `--yes` accepts both.

macOS runs containers in a Colima VM that scad owns. See
[`docs/macos.md`](docs/macos.md) for sizing, mounts and caveats.

## Quick start

```bash
scad config new myproject --edit                      # scaffold a config
scad dispatch myproject --tag feat1 --prompt "implement X"
scad harvest myproject-feat1-Mar02-1400               # fetch branches, show the log
scad finish  myproject-feat1-Mar02-1400               # fetch and tear down
```

A config declares the repos, mounts and Python environment for a project:

```yaml
name: myproject

repos:
  code:
    path: ~/code/myproject
    workdir: true            # container working directory
  docs:
    path: ~/code/docs
    add_dir: true            # passed to claude --add-dir

mounts:
  - host: ~/data/experiments

python:
  version: "3.11"
  requirements: requirements.txt

claude:
  dangerously_skip_permissions: true
```

Configs live in `~/.scad/configs/` and must use `.yml`.

## CLI

```bash
# Containers
scad dispatch <config> --tag <tag> --prompt "..."   # build, start, inject
scad batch <config> --tag <tag> --prompt-file p.md  # parallel headless jobs
scad run start|inject|send|attach|logs|jobs|clean   # the lifecycle, one step at a time
scad code fetch|sync|diff|branch <run-id>           # git state between host and clones
scad harvest|finish <run-id>                        # fetch, then review or tear down

# Sessions
scad session launch --agent claude|codex|kimi --cwd <dir>
scad session resume <id>                            # attach if open, resume if closed
scad session ls|show|read <id>
scad session note --current                         # append a /remember capture

# Corpus
scad archive                                        # copy traces in, append-only
scad reindex                                        # archive, then index
scad search <query> [--notes]
scad view                                           # render the index and open it
scad notes ls [--kind handoff]
scad project ls|show <name>
scad where                                          # how this directory resolves
```

Every command and flag: [`docs/command-reference.md`](docs/command-reference.md).

## What is where

| | |
|---|---|
| `~/.scad/archive/` | agent traces, append-only, never pruned |
| `~/.scad/notes/` | `/remember` captures, one file per session |
| `~/.scad/index.sqlite` | derived from the archive, rebuildable |
| `~/.scad/runs/` | per-run workspace, clones and job metadata |
| `~/.scad/configs/` | project configs |

## Documentation

- [Session state](docs/session-state.md): the archive, the index, `scad view`,
  interactive launch, and how notes are stored.
- [Containers](docs/containers.md): what a run does, step by step, and the config
  reference.
- [macOS](docs/macos.md): the Colima VM, sizing, and mount caveats.
- [The resolver](docs/resolver.md): how a directory becomes a project key.
- [Command reference](docs/command-reference.md).

## Development

```bash
pip install -e ".[dev]"
pytest                 # 1420 tests
```

The interactive launch routes are verified by hand, since every run costs a model
call: [`docs/interactive-launch-verification.md`](docs/interactive-launch-verification.md).

## License

MIT
