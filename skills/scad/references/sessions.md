# Sessions — finding what ran, and reading it back

Every agent session on the machine is indexed, whether scad started it or merely observed it. scad sweeps `~/.claude`, `~/.codex/sessions` and `~/.kimi-code/sessions` on every pass, so a session you started by hand in a terminal is in here beside one a container produced.

## The two questions this tier answers

**Which one was that?** — search and list. **What happened in it?** — read the turns, or the memos if any were written.

## Finding a session

```bash
scad session ls                      # every indexed session, newest first, with context fill and sub-agent counts
scad session ls --project <name>     # scoped to one project
scad search "phrase"                 # full-text across every indexed turn
scad search "phrase" --memos         # memos: body, topic, title, tags, entities
scad project ls                      # projects with session counts
scad project show <name>             # one project's sessions
```

`search --memos` matches a memo's body as well as its labels, and prints which memo matched, never the body: use it to *locate* a memo, then read it.

## Reading one

```bash
scad session show <id>               # metadata and a turn breakdown
scad session read <id>               # the turns, in order
scad session memos <id>              # this session's memos, from the FILE
```

`session memos` reads the memo file rather than the index deliberately: the file is the truth, and it stays readable before anything is indexed and after a `--rebuild` has dropped every row.

## The browsable page

```bash
scad view                            # render and open ~/.scad/view.html
scad view --no-open                  # render only
scad view --no-refresh               # skip the index pass
```

Refreshes the index by default, because nothing else does — there is no timer, so an opt-in refresh meant a stale page every time it was forgotten. It is a `file://` document with no server, so it cannot refresh itself.

## Keeping the corpus current

```bash
scad archive                         # copy new traces into the append-only archive
scad reindex                         # archive, then index what is new
scad reindex --rebuild               # drop and re-derive every row
scad index status                    # when a reindex last finished; runs none
```

Nothing reindexes on a timer: `scad view` refreshes before it renders, and every other command reads the index as it is. Before trusting an empty or quiet answer, check `scad index status` (`--json` gives `indexed_at` and `age_s`) and reindex if it is old. Only one reindex runs at a time; a second waits, then finds nothing new.

**Write the alias rule before any rebuild after a directory moves**, or its sessions go to `unfiled`.

**`--rebuild` is for derivation-rule changes, not for new data.** The incremental pass handles new sessions and growth. Reach for `--rebuild` when a computed column's *rule* changed — `project` is computed from `cwd`, so dropping a `.scad-project` marker only affects sessions the incremental pass re-reads, and the mtime-based scan never re-reads unchanged ones.

## Why a session might be filed as `unfiled`

`project` is a computed column and the retrieval join key, so a wrong one is worse than a missing one.

```bash
scad where                           # what project resolves here, and how
scad resolve                         # the path, for scripting
```

`where` prints `matched by` and everything it tried. Resolution is: the markers `scad.yml` / `.scad-project`, else the git root. A directory with none of those is `unfiled` — fix it by dropping a marker, and see the `attribution` skill, which walks it.

**A directory that moved** is the other cause. The recorded cwd points at nothing, so a rebuild files those sessions as `unfiled`. The fix is a rule in `~/.scad/aliases`, `old path -> new path`, used only when the old path is gone. Rules chain, so each move needs only its own rules, written in the current spelling. `scad project aliases` checks the rules (`ok`, `stale`, `broken`), and `scad where --start <old path>` shows a `via alias:` line per rule that answered. `session ls --json` then serves the new path as `cwd` and the old one as `cwd_recorded`, and `session resume` resumes in the new place. With no rule, a gone directory still resumes, with a warning that the agent will start in the current directory.

## Handing work on

```bash
scad session handoff <id> "ANGLE"              # ask a scad-launched session for its handoff memo
scad session launch --agent claude --from <id> --prompt "where next"
scad session read <id> --kind text --last 200  # the tail; --since TIME for turns after a memo
```

`--from` works on a session too full to write its own handoff: the new session reads the source's handoff and the turns after it, or its last turns, and writes the handoff itself. `scad view` marks nearly full sessions (80% of the window, or 160k tokens when unknown).

## After a restart

```bash
scad session snapshot                # before: record the open sessions
scad session restore                 # after: shows the plan, asks once, resumes each in its window
```

Includes every open Claude session and the codex and kimi sessions `scad session launch` started. A session already open is never started twice. Snapshots are only taken by hand.

## Memos

The authored tier — the only thing in the corpus that cannot be re-derived. Until 0.9.0 these were called notes; the store is `~/.scad/memos` now, and the old commands are gone.

```bash
scad memos ls                        # every memo, newest first
scad memos ls --project <name>
scad memos read <session-id> --last  # the newest memo in a session
scad memos read <session-id> --idx N
```

Writing them is the `memo-write` skill, or `memo-handoff` for a handoff; catching up from them is `memo-recall`. Do not hand-roll them: `memo-recall` knows when to stop reading, and `memo-handoff` checks the repo first.
