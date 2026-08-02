# Dispatch — containers, runs, and jobs

Read this when the work needs isolation: a container with the repos cloned,
credentials staged, and one or more agents running inside it.

## Model

**Run = environment.** A running Docker container with repos, venv, credentials, skills and Claude plugins set up, identified by a run id. The entrypoint does setup and waits. Nothing runs until work is injected.

**Injection = work.** Claude processes sent into a running run via `docker exec`. Can be interactive (tmux window) or headless (`claude -p`). One at a time or N in parallel. Each one is a **job**, and each job produces one agent **session** — its trace, which `scad session ls|show|read` reads. A run hosts many jobs, so `run` and `session` are never interchangeable.

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
| Done with a run | `scad finish <run-id>` | "Save work, tear down" |
| Add more work | `scad run inject <run-id> --prompt "more work"` | "Do this too in the same run" |
| Send to running Claude | `scad run send <run-id> "message"` | "Tell Claude something mid-conversation" |
| Monitor | `scad run jobs <run-id>` then `scad run logs <run-id> --job <id>` | "What's happening?" |

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

### Run Lifecycle

```bash
scad run start <config> --tag <tag>            # start container (no work)
scad run start <config> --tag <tag> --prompt "..." # start + inject
scad run inject <run-id> --prompt "..."         # inject interactive work
scad run inject <run-id> --prompt "..." --headless  # inject headless
scad run inject <run-id> --prompt "..." --wait  # block until done
scad run send <run-id> "message"               # send to running Claude
scad run attach <run-id>                        # attach to tmux
scad run jobs <run-id>                          # list jobs
scad run logs <run-id> --job <id>               # job result
scad run logs <run-id> --job <id> --stream      # raw stream.jsonl
scad run stop <run-id>                          # stop container
scad run clean <run-id>                         # destroy everything
scad run ls                                     # running runs
scad run ls --all                               # full history
```

### Code Management

```bash
scad code fetch <run-id>        # fetch branches to host repos
scad code sync <run-id>         # push host changes into clones
scad code diff <run-id>         # diff clones vs source repos
scad code branch <run-id> <name> # create/switch branch in all clones
scad code add <run-id> --path ~/data --name data    # add to workspace
scad code remove <run-id> --name data               # remove from workspace
scad run refresh <run-id>   # push fresh credentials
```

### Infrastructure

```bash
scad build <config>             # build Docker image
scad config list                # list configs
scad config info <config>       # environment summary
scad config new <name>          # scaffold config
scad run ls                     # running runs
scad run ls <config> --cost     # project overview with cost
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
- **Detaching tmux returns to host shell.** This is expected — the container keeps running. Reattach with `scad run attach`.
- **`scad run clean` is destructive.** No undo. Fetch branches first (`scad harvest`) or use `scad finish` which fetches automatically.
- **Credentials expire ~8h.** Use `scad run refresh <run-id>` to push fresh creds. `scad run ls` warns when <2h remaining.

<HARD-GATE>
NEVER construct Docker commands manually when scad has a command for it.
ALWAYS use `scad run inject` to send work — not raw `docker exec`.
ALWAYS use `scad code fetch` to get branches — not manual git commands.
If a scad command fails, report the error — do not bypass with Docker/git.
</HARD-GATE>
