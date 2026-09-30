# Sessions — finding what ran, and reading it back

Every agent session on the machine is indexed, whether scad started it or
merely observed it. scad sweeps `~/.claude`, `~/.codex/sessions` and
`~/.kimi-code/sessions` on every pass, so a session you started by hand in a
terminal is in here beside one a container produced.

## The two questions this tier answers

**Which one was that?** — search and list.
**What happened in it?** — read the turns, or the notes if any were written.

## Finding a session

```bash
scad session ls                      # every indexed session, newest first
scad session ls --project <name>     # scoped to one project
scad search "phrase"                 # full-text across every indexed turn
scad search "phrase" --notes         # topic/title/tags/entities only, NOT bodies
scad project ls                      # projects with session counts
scad project show <name>             # one project's sessions
```

`search --notes` matches metadata only. Searching `"cwd drift"` will miss a
note that the tag `cwd-drift` finds — use it to *locate* a note, then read it.

## Reading one

```bash
scad session show <id>               # metadata and a turn breakdown
scad session read <id>               # the turns, in order
scad session notes <id>              # this session's notes, from the FILE
```

`session notes` reads the note file rather than the index deliberately: the
file is the truth, and it stays readable before anything is indexed and after
a `--rebuild` has dropped every row.

## The browsable page

```bash
scad view                            # render and open ~/.scad/view.html
scad view --no-open                  # render only
scad view --no-refresh               # skip the index pass
scad view --days N                   # how far back the waiting list looks
```

Refreshes the index by default, because nothing else does — there is no timer,
so an opt-in refresh meant a stale page every time it was forgotten. It is a
`file://` document with no server, so it cannot refresh itself.

## Keeping the corpus current

```bash
scad archive                         # copy new traces into the append-only archive
scad reindex                         # archive, then index what is new
scad reindex --rebuild               # drop and re-derive every row
```

**Write the alias rule before any rebuild after a directory moves**, or its
sessions go to `unfiled`.

**`--rebuild` is for derivation-rule changes, not for new data.** The
incremental pass handles new sessions and growth. Reach for `--rebuild` when a
computed column's *rule* changed — `project` is computed from `cwd`, so
dropping a `.scad-project` marker only affects sessions the incremental pass
re-reads, and the mtime-based scan never re-reads unchanged ones.

## Why a session might be filed as `unfiled`

`project` is a computed column and the retrieval join key, so a wrong one is
worse than a missing one.

```bash
scad where                           # what project resolves here, and how
scad resolve                         # the path, for scripting
```

`where` prints `matched by` and everything it tried. Resolution is: the markers
`scad.yml` / `.scad-project`, else the git root. A directory with none of those
is `unfiled` — fix it by dropping a marker, and see the `attribution` skill,
which walks it.

**A directory that moved** is the other cause. The recorded cwd points at
nothing, so a rebuild files those sessions as `unfiled`. The fix is a rule in
`~/.scad/aliases`, `old path -> new path`, used only when the old path is gone.
Rules chain, so each move needs only its own rules, written in the current
spelling. `scad project aliases` checks the rules (`ok`, `stale`, `broken`), and
`scad where --start <old path>` shows a `via alias:` line per rule that answered.
`session ls --json` then serves the new path as `cwd` and the old one as
`cwd_recorded`, and `session resume` resumes in the new place. With no rule, a
gone directory still resumes, with a warning that the agent will start in the
current directory.

## Notes

The authored tier — the only thing in the corpus that cannot be re-derived.

```bash
scad notes ls                        # every note, newest first
scad notes ls --project <name>
scad notes read <session-id> --last  # the newest note in a session
scad notes read <session-id> --idx N
```

Writing them is the `remember` skill; catching up from them is `recall`. Do not
hand-roll either — both exist, and `recall` knows when to stop reading.
