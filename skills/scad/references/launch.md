# Launch — starting an interactive agent, on the host

Hand work to an agent — possibly a different family from the one you are — at a
directory of your choosing, and be able to find that conversation again.

No container. This is independent of `scad dispatch`, which is the same idea
with isolation and a config.

```bash
scad session launch --agent claude|codex|kimi [--cwd DIR] [--prompt TEXT] [--attach]
scad session resume <session-id> [--print]
```

`launch` is detached by default: it starts the agent in tmux, prints the
resume command and the pane, and exits. `--attach` opts into attaching.

`resume` attaches if the session is open and execs the agent if it is closed —
never a second process against one live session id. It works for **every**
indexed session, not only launched ones: the index already holds the agent, the
cwd and the id, which is all a resume command needs.

## Why tmux is not incidental

It supplies the pty. Claude's `/resume` picker drops sessions whose entrypoint
is `sdk-cli`, and entrypoint is decided at launch by `-p || --print ||
--init-only || --sdk-url* || !process.stdout.isTTY` — **a non-TTY stdout alone
flips it**, with no `-p` required, and the picker reads it from the session's
*head*, so later interactive turns never rehabilitate it.

So a launch without a pty produces a session that works but is invisible in the
agent's own UI. `scad session launch` refuses rather than falling back — the
one place this codebase raises instead of degrading, because the degraded
result looks fine and is wrong.

Diagnostic: `claude --debug` prints
`Session <id> filtered from /resume: entrypoint=sdk-cli`.

## How each family yields its id

Measured on a real machine, not read from documentation.

| Agent | Route | Known |
|---|---|---|
| **claude** | `--session-id <uuid>` — scad mints it | before launch |
| **kimi** | a new line in `~/.kimi-code/session_index.jsonl`, confirmed by its own `workDir` | at launch, before any turn |
| **codex** | the rollout its first turn creates; the uuid is in the filename | after the first turn |

Codex writes **nothing** until a turn happens, which is why a first turn is
sent. `codex exec` would give the id earlier and is not used: an exec-born
thread is stamped `source: exec` at the head, permanently, and codex's default
picker hides it. Resuming it interactively does not re-stamp it.

kimi's id carries a `session_` prefix that the index strips, so a resume
command has to add it back — `kimi -S <bare-uuid>` answers `Session not found`.

## The launch record

`~/.scad/launches/<session-id>.json`, written as soon as the id exists. A file,
never the index: `reindex --rebuild` drops every row, and this is an authored
fact about an event with nothing to recompute it from.

It is load-bearing rather than a convenience — a launched session may be one
the agent's own picker never lists, so if scad does not record the id, the
session is unreachable.

## Traps, all paid for once already

- **`tmux send-keys` without `-l` drops the text silently.** Always `-l`, then
  a separate `Enter`.
- **A cleared composer is not proof the turn ran.** kimi will take the text out
  of its composer and render it as sent while never dispatching it. Only the
  family's own progress signal is real.
- **Never send Enter at a gate.** Codex's update prompt highlights
  `1. Update now`, which runs `curl … | sh`. Gates are answered by matching the
  option's *label*, and an unrecognised screen gets no keystroke at all.
- **Being a git repo does not skip codex's trust prompt.** Trust is per
  directory; expect the gate on any first launch into a new tree.
- **Anything you put in `--prompt` runs where scad's own skills are loaded.**
  `scad`, `remember`, `recall` and `codex` are live trigger words in every
  family. A prompt saying "remember the phrase…" made kimi invoke the
  `remember` skill and try to write a note.

## Verifying by hand

`docs/interactive-launch-verification.md` in the repo is the manual walk — it
costs a model call per family, so it is deliberately not in the test suite.
