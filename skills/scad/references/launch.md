# Launch — starting an interactive agent, on the host

Hand work to an agent — possibly a different family from the one you are — at a
directory of your choosing, and be able to find that conversation again.

No container. This is independent of `scad dispatch`, which is the same idea
with isolation and a config.

```bash
scad session launch --agent claude|codex|kimi [--cwd DIR] [--prompt TEXT] [--add-dir DIR]...
                   [--window [NAME]] [--split] [--name NAME] [--attach]
scad session resume <session-id> [--print]
scad session send <session-id> TEXT | --file PATH
```

`launch` opens its own detached tmux session by default. **`--window [NAME]`
puts it in the caller's tmux session as a named window instead**, and the
recorded target becomes e.g. `main:7.0`; bare `--window` names it after the
directory. Prefer it: a launch the human cannot see is one they re-enter by
hand with `claude` + `/resume`, which is a second process on one session id.
`--split` opens it in a pane beside the caller's own, from `$TMUX_PANE`.
`--name NAME` sets the display name — in the index row at launch, and for
claude also `claude -n`, which shows it in the prompt box and the `/resume`
picker. It starts the agent in tmux, prints the
pane and `scad session resume <id>`, and exits. `--attach` opts into
attaching. `--add-dir` is Claude-only and repeatable; it is refused, not
dropped, for the other two.

`send` delivers a later turn into the session's open pane and submits it.
Use it instead of `tmux send-keys`: a 1,400-character turn sent that way was
measured to arrive with its first ~200 characters missing, because the
Claude Code TUI took the burst as a paste and collapsed it. `send` pastes
through a tmux buffer with bracketed paste, waits for the echo, then presses
Enter. It refuses a session that has closed (use `resume`) and a pane sitting
at a dialog. Only sessions scad launched can be sent to; the pane comes from
the launch record.

`resume` attaches if the session is open and execs the agent if it is closed —
never a second process against one live session id. It works for **every**
indexed session, not only launched ones: the index already holds the agent, the
cwd and the id, which is all a resume command needs. If the directory moved
and a rule in `~/.scad/aliases` covers it, the session resumes in the new place;
if the directory is gone with no rule, it resumes anyway and warns that the
agent will start in the current directory.

## Why tmux is required

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

Measured on a real machine.

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

`~/.scad/launches/<session-id>.json`, written as soon as the id exists. It
holds the pane as an id (`pane_id: %45`) and as an index path (`tmux:
main:11.0`); the id is the one to trust, since a moved window changes the path
and not the id. A file,
never the index: `reindex --rebuild` drops every row, and this is an authored
fact about an event with nothing to recompute it from.

A launched session may be one the agent's own picker never lists; without the
record it is unreachable.

## Traps

- **`tmux send-keys` is for gate answers, not turns.** Without `-l` it drops the
  text silently; with `-l` a long turn loses its head. A turn goes through
  `paste-buffer -p`. Enter is always a separate call.
- **A cleared composer is not proof the turn ran.** kimi will take the text out
  of its composer and render it as sent while never dispatching it. Only the
  family's own progress signal is real.
- **Never send Enter at a gate.** Codex's update prompt highlights
  `1. Update now`, which runs `curl … | sh`. Gates are answered by matching the
  option's *label*, and an unrecognised screen gets no keystroke at all.
- **Being a git repo does not skip codex's trust prompt.** Trust is per
  directory; expect the gate on any first launch into a new tree.
- **Anything you put in `--prompt` runs where scad's own skills are loaded.**
  `scad`, `memo-write`, `memo-handoff`, `memo-recall` and `codex` are live
  trigger words in every family. Before 0.9.0 the writer was called
  `remember`, and a prompt saying "remember the phrase…" made kimi invoke it
  and try to write a memo; "write a memo" or "hand this off" would do the same
  now.

## Verifying by hand

`docs/interactive-launch-verification.md` in the repo is the manual walk — it
costs a model call per family, so it is deliberately not in the test suite.
