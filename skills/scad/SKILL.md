---
name: scad
description: >
  Run and find agent sessions. Use when dispatching work to isolated Docker
  containers (scad dispatch, run inject, batch, harvest); when launching an
  interactive claude, codex or kimi session at a directory and resuming it
  later; or when asking which sessions exist, what is running, what is waiting
  on you, what a past session was about, or where work happened. Also for the
  session index, full-text search across turns, the browsable view page, and
  project attribution. Triggers on scad, container execution, isolated agent
  dispatch, session launch, resume that session, which sessions, what am I
  running, and what did I work on.
compatibility: Requires tmux. Container features additionally require Docker.
---

# scad

**Announce at start:** "I'm using the scad skill."

scad does two things that share one vocabulary: it **runs** agents, and it
**finds** them afterwards. Most confusion comes from mixing the two, so the
model below is worth reading before any command.

## Model

**Run = environment.** A Docker container with repos, venv, credentials and
skills set up, identified by a run id. Nothing happens in it until work is
injected.

**Job = work.** One agent process inside a run. A run hosts many jobs, so `run`
and `session` are never interchangeable.

**Session = the trace.** What an agent actually did, identified by that agent's
own session id. Every session on the machine is indexed — container or host,
claude or codex or kimi, whether scad started it or merely observed it.

The asymmetry that matters: **scad launches only what you ask it to, but sees
everything.** A session you started by hand in a terminal is in the index
beside one a container produced.

## Which page do you need

| You want to | Read |
|---|---|
| Run work in an isolated container | [references/dispatch.md](references/dispatch.md) |
| Start an interactive agent here, or resume one | [references/launch.md](references/launch.md) |
| Find a session, read it, or browse them | [references/sessions.md](references/sessions.md) |

Load one. They do not depend on each other.

## The commands, at a glance

Enough to recognise what exists; the reference pages carry the flags.

```bash
# run work in a container
scad dispatch <config> --tag t --prompt "..."   # build, start, inject, wait
scad batch <config> --tag t --prompt-file f     # N jobs in parallel
scad harvest <run-id>                           # fetch branches + summary
scad finish <run-id>                            # fetch + tear down

# run an agent here, on the host
scad session launch --agent codex --cwd .       # interactive, in tmux
scad session resume <id>                        # attach if open, resume if not

# find what ran
scad session ls                                 # every indexed session
scad search "phrase"                            # full text across turns
scad view                                       # the browsable page
scad notes ls                                   # the authored tier
scad where                                      # what project resolves here
```

## When NOT to use scad

- A local edit that needs no isolation and no record.
- Writing or catching up on notes — that is the `remember` and `recall` skills.
- Reaching another model family for a one-off opinion — that is the `codex`
  skill, which needs no scad at all.

## Rules that hold everywhere

- **Never construct Docker commands by hand** when scad has one. Use
  `scad run inject`, not `docker exec`; `scad code fetch`, not manual git. If a
  scad command fails, report the error rather than bypassing it.
- **`scad run clean` is destructive** and has no undo. Fetch first, or use
  `scad finish`, which fetches for you.
- **`reindex --rebuild` is for derivation-rule changes, never for new data.**
  The incremental pass handles new sessions and growth.
- **Measure, never cite.** Trace and skill locations have contradicted their
  own documentation repeatedly. Check the machine before trusting a path.
