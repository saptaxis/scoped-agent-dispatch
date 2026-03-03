---
name: scad
description: >
  Use when dispatching work to Docker containers, running Claude sessions
  in isolation, managing headless jobs, or when the user mentions scad,
  session inject, container execution, or isolated agent dispatch.
---

# Scad — Isolated Agent Dispatch

**Announce at start:** "I'm using the scad skill to manage container sessions."

## Model

**Session = environment.** A running Docker container with repos, venv, credentials, and plugins set up. The entrypoint does setup and waits. Nothing runs until work is injected.

**Injection = work.** Claude processes sent into a running session via `docker exec`. Can be interactive (tmux window) or headless (`claude -p`). One at a time or N in parallel.

**Workspace = unified mount.** Single bind mount at `/workspace/`. Git repos cloned into it (managed). Everything else symlinked (unmanaged).

## When NOT to Use

- Local-only tasks that don't need container isolation
- Quick edits to a single file
- When the user explicitly says they don't want containers

## Common Workflows

| Workflow | Commands | When |
|----------|----------|------|
| Interactive dispatch | `scad dispatch <config> --tag t --prompt "task"` | "Do this task interactively" |
| Interactive + attach | `scad dispatch <config> --tag t --attach --prompt "task"` | "I want to work with Claude" |
| Execute a plan | `scad dispatch <config> --tag t --plan plan.md` | "Run this implementation plan" |
| Headless dispatch | `scad dispatch <config> --tag t --headless --prompt "task"` | "Do this, give me results" |
| Fire and forget | `scad dispatch <config> --tag t --headless --no-wait --prompt "task"` | "Start this, I'll check later" |
| Parallel batch | `scad batch <config> --tag t --prompt-file prompts.txt` | "Run these N tasks in parallel" |
| Get results back | `scad harvest <run-id>` | "What did Claude produce?" |
| Done with session | `scad finish <run-id>` | "Save work, tear down" |
| Add more work | `scad session inject <run-id> --prompt "more work"` | "Do this too in the same session" |
| Send to running Claude | `scad session send <run-id> "message"` | "Tell Claude something mid-conversation" |
| Monitor | `scad session jobs <run-id>` then `scad session logs <run-id> --job <id>` | "What's happening?" |

## Quick Reference

### Composites (start here)

```bash
scad dispatch <config> --tag <tag> --prompt "..."            # interactive (default)
scad dispatch <config> --tag <tag> --prompt "..." --attach   # interactive + attach to tmux
scad dispatch <config> --tag <tag> --plan plan.md            # execute plan file
scad dispatch <config> --tag <tag> --prompt "..." --headless # headless + wait
scad dispatch <config> --tag <tag> --prompt "..." --fetch    # headless + wait + auto-fetch
scad batch <config> --tag <tag> --prompt-file prompts.txt    # parallel headless jobs
scad harvest <run-id>                                        # fetch + git log summary
scad harvest <run-id> --diff                                 # fetch + full diff
scad finish <run-id>                                         # fetch + clean
```

### Session Lifecycle

```bash
scad session start <config> --tag <tag>            # start container (no work)
scad session start <config> --tag <tag> --prompt "..." # start + inject
scad session inject <run-id> --prompt "..."         # inject interactive work
scad session inject <run-id> --prompt "..." --headless  # inject headless
scad session inject <run-id> --prompt "..." --wait  # block until done
scad session send <run-id> "message"               # send to running Claude
scad session attach <run-id>                        # attach to tmux
scad session jobs <run-id>                          # list jobs
scad session logs <run-id> --job <id>               # job result
scad session logs <run-id> --job <id> --stream      # raw stream.jsonl
scad session stop <run-id>                          # stop container
scad session clean <run-id>                         # destroy everything
scad session status                                 # running sessions
scad session status --all                           # full history
```

### Code Management

```bash
scad code fetch <run-id>        # fetch branches to host repos
scad code sync <run-id>         # push host changes into clones
scad code diff <run-id>         # diff clones vs source repos
scad code branch <run-id> <name> # create/switch branch in all clones
scad code add <run-id> --path ~/data --name data    # add to workspace
scad code remove <run-id> --name data               # remove from workspace
scad session refresh <run-id>   # push fresh credentials
```

### Infrastructure

```bash
scad build <config>             # build Docker image
scad config list                # list configs
scad config info <config>       # environment summary
scad config new <name>          # scaffold config
scad status                     # running sessions
scad status <config> --cost     # project overview with cost
scad gc                         # find orphans (dry-run)
scad gc --force                 # clean orphans
```

## Environment (inside container)

| Item | Value |
|------|-------|
| User | `scad` (non-root) |
| Working dir | `/workspace/<workdir-key>` |
| Python venv | `/opt/venv` (auto-activated) |
| Repos | `/workspace/<key>` |
| Git branch | `scad-{config}-{tag}-{MonDD}-{HHMM}` |
| Tmux session | `scad` (one session, windows per interactive job) |
| Credentials | Copied from host at startup |
| Timezone | Matches host |

## Gotchas

- **`--wait` is headless only.** Interactive jobs can't block — they're in tmux. Use `--headless --wait` or just `--wait` (implies headless).
- **`--fetch` implies `--wait`.** Can't fetch results from unfinished work.
- **Detaching tmux returns to host shell.** This is expected — the container keeps running. Reattach with `scad session attach`.
- **`session clean` is destructive.** No undo. Fetch branches first (`scad harvest`) or use `scad finish` which fetches automatically.
- **Credentials expire ~8h.** Use `scad session refresh <run-id>` to push fresh creds. `session status` warns when <2h remaining.

<HARD-GATE>
NEVER construct Docker commands manually when scad has a command for it.
ALWAYS use `scad session inject` to send work — not raw `docker exec`.
ALWAYS use `scad code fetch` to get branches — not manual git commands.
If a scad command fails, report the error — do not bypass with Docker/git.
</HARD-GATE>
