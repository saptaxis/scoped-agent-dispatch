# Changelog

## [Unreleased]

## [0.10.0] - 2026-10-07

### Added

- `scad session snapshot` records the open agent sessions: every Claude session, and the codex and kimi sessions started with `scad session launch`. Snapshots are written to `~/.scad/snapshots/`, each listing its sessions by project; the newest 50 are kept.
- `scad session restore [FILE]` brings a snapshot's sessions back. It shows the plan, grouped by project, and asks once (`-y` to skip; `--skip` and `--only` take id prefixes; `--list` shows recent snapshots). Each session resumes in its own directory, as a split of the window it came from, or in a new window of that name. Sessions already open are not started again.
- `scad session handoff ID ["ANGLE"]` asks an open session that scad launched or restored to write its handoff memo, and waits for it. It refuses a session whose context is nearly full.
- `scad session launch --from ID` starts a fresh session that picks up ID's work: it reads ID's newest handoff memo and every turn after it, or ID's last 200 text turns when there is no handoff, writes a handoff memo, then follows `--prompt`. The directory defaults to ID's.
- `scad view` marks a session whose context is nearly full: 80% of its window, or 160k tokens when the window is unknown.
- `scad session read --last N` reads a session's last N turns, and `--since TIME` the turns at or after a time (ISO 8601 or epoch milliseconds).
- `/memo-recall` reads the turns written after the memo it starts from.
- `scad session launch` notes when `--prompt` contains a phrase that can set off one of scad's skills in the new session ("write a memo", "hand this off", "catch me up" and others), and launches anyway.

### Changed

- `install.sh` no longer edits `~/.zshrc` or `~/.bashrc`. It writes the completion scripts to `$SCAD_HOME/completion/` and prints the line that sources them. Opening a new shell no longer runs scad (it took 0.21 to 0.33 s).

### Fixed

- `scad view` showed some open Claude sessions without their tmux pane: those whose pane tmux reports as the shell rather than Claude. A session's pane now comes from the pane id Claude records in its own registry, then from the process tree.

### Upgrading

1. Re-run `install.sh`. It writes the completion scripts and updates the installed skills, which are copies: the `scad` and `memo-recall` skills changed.
2. In `~/.zshrc` (or `~/.bashrc`), replace `eval "$(_SCAD_COMPLETE=zsh_source scad)"` with the line `install.sh` prints.

## [0.9.0] - 2026-10-07

### Changed

- Notes are renamed to memos everywhere. The old commands and skills are removed, with no aliases.

| Before | 0.9.0 |
|---|---|
| `scad notes ls`, `scad notes read` | `scad memos ls`, `scad memos read` |
| `scad session note`, `scad session notes` | `scad session memo`, `scad session memos` |
| `scad search --notes` | `scad search --memos` |
| `/remember`, `/recall` | `/memo-write`, `/memo-recall` |
| `~/.scad/notes/` | `~/.scad/memos/` |
| JSON `note_path`; view rows `notes`, `n_notes` | `memo_path`; `memos`, `n_memos` |

- `scad view` counts a session as waiting on you only when it asked you a question (`awaiting-question`). Sessions that ended with the agent speaking last (`awaiting-user`) are no longer counted. Questions stay listed however old they are. `scad view --days` is still accepted and has no effect.

### Added

- `/memo-handoff` writes a handoff memo after checking `git status` and `git log` in each repository the work touched. Its argument sets the scope: none for the whole session, `brief` for the latest phase, any other text for a focus.
- `scad search --memos` matches memo bodies as well as topic, title, tags, entities and project.
- `scad view`, `scad session ls` and `scad session show` show how much of each session's context is used: a percentage when the window is known, otherwise the token count. `session ls --json` adds `context_tokens`, `context_window` and `context_pct`. codex and kimi record their window; for Claude it is known once a turn exceeds 200k tokens, which means the 1M window. Sessions not parsed since upgrading show nothing until their next turn or a `scad reindex --rebuild`.
- `scad session ls` and `scad project show` show each session's sub-agent count. `session ls --json` adds `n_subagents`.
- `scad index status [--json]` prints when the last reindex finished and how many sessions and memos the index holds, without reindexing.
- Tab completion for session ids, memo ids and project names. Each candidate shows the session's name or title and its project.
- `scad session launch --split WINDOW` opens the agent in a pane of another tmux window, given by name or as a target such as `main:4`. A name shared by two windows is refused. Bare `--split` still splits the current pane.

### Fixed

- Two reindexes running at once could index the same turns twice. A reindex now holds a lock, and a second one waits for it. Writing a memo takes the same lock.
- Opening the index while another process was creating it could fail with "database is locked".
- The test suite no longer reads or writes the real `~/.scad`, and no test can run `colima` or reach the Docker daemon. Tests that need the real VM are marked `vm` and run only with `pytest --run-vm -m vm`.

### Upgrading

On each machine, before any other scad command:

1. `mv ~/.scad/notes ~/.scad/memos`. Until this is done, every memo command exits with this instruction.
2. `scad reindex`.
3. Optionally, remove the old index data: `sqlite3 ~/.scad/index.sqlite "DROP TABLE notes; ALTER TABLE sessions DROP COLUMN notes_offset; ALTER TABLE sessions DROP COLUMN notes_mtime; UPDATE sessions SET source = 'scad-memo' WHERE source = 'scad-note';"`
4. Remove the `remember` and `recall` skills from `~/.agents/skills` and `~/.claude/skills`, then re-run `install.sh` to install `memo-write`, `memo-handoff` and `memo-recall`.

### Known limitations

- kimi exports no session id, so the memo skills need `--session <id>` under kimi.

## [0.8.0] - 2026-10-01

**The release where a directory can move twice.** 0.7.0's aliases carried the docs repository from `~/Dropbox/traitful-code/traitful-docs` to `~/Dropbox/inwit`. The next step is a restructure inside it: about 30 unit folders move from `inwit/docs/projects/X` to `inwit/{personal,traitful}/projects/X`. The 777 sessions recorded before the first move are translated by its rule into `inwit/docs/projects/X`, which the restructure removes, and in 0.7.0 nothing took them further: every rule would have had to be written twice, once per earlier spelling. scad's own label is unaffected (every such session resolves to `inwit`); orglens's attribution by path is affected: 7 of the 48 inwit sessions it sees on 2026-10-01.

### Changed

- Path aliases chain. A rule that leads to a path which is itself gone hands it to the next matching rule, until the path exists, so each move needs only its own rules: a repository that moved and then had folders moved inside it is one rule per move, written in the current spelling, rather than one per earlier spelling. No rule is used twice in a chain. A chain that runs into a missing directory keeps its last hop whose new side exists, so a wrong later rule no longer costs the earlier translation. `scad where` shows every hop, and `scad project aliases` counts a rule whose new side moved on as `ok` when its chain arrives.

### Fixed

- A config repo whose `path` is a folder inside a repository (orglens renders a unit's home that way, e.g. `inwit/docs/projects/orglens`) failed at clone time: `git clone --local` refuses a subfolder. The repository containing the folder is now cloned, and `code sync` and `harvest` read from it too. A `worktree: false` repo is still mounted as the folder itself.

### Not done

- A chain still needs its first rule to reach somewhere. A rule written in an old spelling whose new side is mistyped translates nothing, and a rebuild then walks up from the old path, which may find no marker and file the session `unfiled`. Writing each rule in the current spelling avoids it.

## [0.7.0] - 2026-09-30

**The release where a directory can move without taking its sessions with it.** `project` is derived from each session's recorded cwd, resolved against the filesystem as it is now, and a transcript records its cwd forever. So after a directory moves, `reindex --rebuild` re-derives every row, finds nothing at the old path, and files the sessions under `unfiled`, or under the name of any marked directory above it. Measured on a copy before the fix: the rebuild is the only step that loses attribution, and ordinary reindexes never recompute it. The move that forced this one affected 775 of the 1,921 sessions indexed on the machine it was measured on.

### Added

- Path aliases: `~/.scad/aliases` holds hand-written `old path -> new path` rules. A rule is used only when a session's recorded directory no longer exists, so it can never redirect one that is still there. The longest matching rule wins, and rules do not chain. Nothing recorded is rewritten: not the transcripts, not the archive, not the index's `cwd`.
- Both sides of a rule, and every recorded path, are resolved through symlinks before they are compared. Of the 775 sessions above, 774 recorded the `~/Library/CloudStorage/Dropbox` spelling and one recorded `~/Dropbox`; a rule in either spelling matches both.
- `scad project aliases` lists the rules as `ok`, `stale` (the old path still exists) or `broken` (the new path is missing). `scad where` shows a `via alias:` line when a rule answered, and for a directory that no longer exists it points at the alias file rather than suggesting a marker.

### Changed

- `session ls --json` serves `cwd` as where the directory is now, through symlinks and the alias file, and adds `cwd_recorded`, the path exactly as the transcript recorded it. This changes an existing field's meaning: a row whose recorded path went through a symlink now serves the resolved path even when nothing moved (`/tmp/x` becomes `/private/tmp/x` on macOS). `session show` and the viewer show the same.
- `session resume` resumes where a rule says a moved directory went, and says so. For a directory that is gone with no rule it still resumes, since `claude --resume` does not need the directory, and warns that the agent will start in the current directory; `--print` and the viewer drop the `cd`, which used to fail and stop the command. A rule whose new path does not exist is refused.

### Not done

- Nothing warns before a rebuild. A directory that moved without a rule still goes to `unfiled`, silently, exactly as before; the rule has to be written first.
- Rules are per machine and edited by hand. There is no command that writes one.
- A named `.scad-project` marker, which would stop a rename from orphaning future sessions, is still open.

## [0.6.0] — 2026-09-27

**The release where a launched session lands where you are looking.** A launch opened its own detached tmux session, so an agent ran in a window nobody watched, and the way back in was `claude --resume` typed by hand. That habit is what produced three separate bug reports: a second process on one session id, a `/rename` landing in the wrong registry file, and forked transcripts. `--window` and `--split` put the agent in the tmux session and the pane you are already in, and the launch record now names its pane by an id that survives being moved, so nothing downstream has to guess where a session went.

### Added

- `session launch --window [NAME]`: land the agent as a named window in the caller's tmux session instead of a detached `scad-cl-HHMM` sibling. `NAME` defaults to the cwd basename. Outside tmux, unchanged. The launch record's target becomes e.g. `main:7.0`.
- `session launch --split`: land the agent in a pane beside the one the command was typed in, in that window, rather than a new window or a detached session. The pane comes from `$TMUX_PANE`, so it is exact. Outside tmux, unchanged.
- `session launch --name NAME`: the session's display name, passed to `claude -n` and written into the index row at launch, so a listing can tell several sessions apart before any of them has taken a turn. Recorded for codex and kimi too, which have no flag of their own.

### Changed

- Spacing in the viewer's stylesheet uses the `--s1`…`--s5` scale wherever a value was already exactly on it — seven declarations, byte-identical output. Nineteen off-scale values remain and are left alone on purpose: snapping `.3rem` to `.25rem` changes how the page looks, CSS has no test that would catch it, and the list belongs in front of someone who can see the page.
- `scad view` puts a session's notes on its row, as a third block of the context fold beside what it opened with and what it last said, and the separate Notes section is gone. A note was previously findable only by scrolling to that section and matching session ids by eye, which is a poor fate for the one tier that cannot be re-derived. Up to four per row, newest first, with `+N more` beyond that. **Tag chips went with the section** — twelve per note across four notes would have dominated every row; tags stay searchable through `notes ls --about` and `search --notes`. The `/remember` hint the section used to carry moved to the page header, so removing the section did not make the tier harder to discover.
- `scad view` groups the all-sessions list by recency: Today, Yesterday, This week, This month, Older, newest first, with a count per heading. This answers the standing question of whether `--days` should default to something finite — grouping needs no threshold and hides nothing, where a default would have had to be guessed and would cut rows off. Headings are built from the filtered list, so one never outlives its rows.
- `scad view` has one **Live** section where it had `Open now` and `Agent panes`. They were the same rows sourced two ways — the registry names the session and not the place, tmux names the place and guessed the occupant by directory — and the duplication was more visible under a filter, not less. A live session now carries its own pane, resolved by process tree or by launch record, and the only rows left are panes running an agent that nothing can name, which say so. On this machine all 12 agent panes resolved, so the guess is gone rather than relabelled.

### Fixed

- A launch record now holds the pane's **id** (`%45`) as well as its index path, and every reader resolves through the id: `session resume`'s attach, `session show`, `session send`, and the viewer's pane-to-session proof. An index path is a snapshot — a pane joined into another window keeps its id and goes from `main:11.0` to `main:0.1` — and the resume path is where a stale target opens a second process on one session id. Records without an id behave as before.
- `scad view` showed a session once per process holding it, so five sessions appeared twice in "Open now" (19 rows for 14 sessions, measured 2026-09-24). One row per session now, with the other holders named on it.
- A launch-seeded index row carries the launch time. `session ls` orders by `started DESC` and every one of these rows had it NULL, so the session you started ten seconds ago sorted to the bottom of the listing.
- `session ls --json` `live` gains `also_held_by`: the other live processes on that session id, each with the pane it sits in. Six ids were doubly held on this machine and nothing said so.

## [0.5.0] — 2026-09-18

**The release where scad's index became an interface rather than a file.** 0.4.0 added the read tier; the first consumer of it, orglens, then reached past the CLI and queried the sqlite file directly, which made the schema a contract nobody had written down. This release writes it down: `session ls --json` carries what a consumer was fetching, `notes ls --about` answers the question a project join could not, and the index is in WAL mode so a reader is never stuck behind a reindex. It also closes the one bug 0.4.0 shipped with: the resume command a launch printed was the wrong command for the moment it was printed.

### Added

- `session ls --json` is now the export a consumer reads instead of the index file. Rows carry `cwd`, `ended`, `needs` and `parent_session_id`, plus `last_turn` (the newest turn with text, clipped to 240 characters) and `live` (pid, name, status from Claude's process registry, or null). Filed by orglens as the five query shapes it ran against `~/.scad/index.sqlite`.
- `session ls --parent <id>`: a session's subagents and workflow agents.
- `session send <id> TEXT | --file PATH`: a later turn into an open session scad launched. The text goes into the session's pane as one bracketed paste (`tmux load-buffer` then `paste-buffer -p`), the echo is waited for, then it is submitted. Measured 2026-09-18: raw `tmux send-keys` of a 1,442-character turn lost its first ~200 characters in the Claude Code TUI; the paste delivered 7,806 bytes over 62 lines verbatim. A closed session is refused with the resume command; a pane at a dialog is refused unanswered. `session launch --prompt` now uses the same transport. Codex and kimi panes are untested with it.
- `session launch --add-dir PATH`, repeatable. Claude-only, and refused rather than dropped for codex and kimi. Recorded in the launch record as `add_dirs`.
- `session notes --current`, resolved the same way `session note --current` writes.
- `notes ls --about NAME`: notes naming NAME in `tags` or `entities`, as the topic, or as the project. By project alone, three of eight notes about orglens were found; this finds all.
- An index on `turns(session_id, ts)`, for "the last thing said" per session.
- `notes ls --json` rows carry `entities`, and `--about` is repeatable; with several names each JSON row carries `about`, the names it matched, so one call serves a consumer that joins per name. Asked by orglens after adopting the export.

### Changed

- `scad session send` is the host-session turn. It was a hidden alias of `scad run send`, the container turn, from the v2.1 rename; `run send` is unchanged.
- The index opens in WAL mode. A reader in another process is no longer blocked for the whole of a reindex; one external view had stalled 600s behind one.

### Fixed

- A source file the archive had forked (rewritten at the source, so a `<name>.<mtime>.jsonl` copy sits beside the original) was parsed from zero and appended on every pass, alternating between the two copies. Codex rewrote 133 rollouts in place on 2026-09-15; each `scad view` then added 6,320 duplicate turns and took ten seconds. Both copies now resolve to one row by name; the older is skipped unopened and a newer fork replaces that session's turns once, reported as "re-read from a rewritten source". Later passes re-read nothing.
- `session ls --json` `live` is the newest registry entry for a session, by `updatedAt`. A reattach leaves the first process's `<pid>.json` in place with both pids alive, and `/rename` writes into the newer file; the older name was being reported.
- A note line edited after it was indexed now reaches the index. The unknown-project warning on `session note` invites exactly that edit, and the pass never re-read an existing line, so a corrected note stayed unfindable by project until a rebuild. A changed note file is read whole and its rows replaced when they no longer match its lines; a pure append is still an append.
- `session launch` no longer prints `cd <cwd> && claude --resume <id>` at the one moment the session is certainly open. A second `claude --resume` on an open session is a second process on one transcript: it appends its own entries, the chain forks, and every later resume follows the fork until the original process exits. It prints `scad session resume <id>` instead, which attaches while the pane is open and resumes once it has closed. The launch record still carries the raw command for then.
- `session resume --print` warns on stderr when the session is open in a recorded pane or in Claude's process registry. stdout is unchanged; it is what the viewer copies.
- `scad view` no longer shows one session twice when two agent panes share a directory. A claude pane is matched to its session through the process tree, from the pane's shell pid to the pid Claude's registry names, and a scad-launched pane through its launch record; both are exact and for any pane. Only a pane neither can name falls back to the newest session in its directory, and the card now says "best guess by directory" when it does.

## [0.4.0] — 2026-09-09

**The release where scad stopped being only a container dispatcher.** 0.3.0 could put a Claude agent in a container and get a branch back. This one adds a second tier that reads rather than runs: every agent trace on the machine is archived before it can be pruned, indexed into sqlite, and made searchable and browsable — for claude, codex and kimi alike, including sessions scad never launched. It also adds the one tier nothing can re-derive: durable notes written by `/remember`.

The two tiers share a project key and little else, and they are deliberately asymmetric: the read tier is multi-agent, the container tier remains Claude-only.

**Breaking:** the container verbs moved off `session` (`session start` → `run start`, `scad status` → `scad run ls`); the old paths survive as hidden aliases. scad is no longer distributed as a Claude Code plugin. The `/remember` record shape changed — `span` and an authored `relation` are gone.

### Added — the read tier

- `scad archive` — an append-only copy of every agent trace, taken *before* an agent prunes its own history. Mirrors the source layout under `~/.scad/archive/` and carries a `DO-NOT-DELETE.md` saying why. Reads claude, codex and kimi; a family that is absent from the machine is normal, not an error
- `scad reindex` — sqlite over `sessions` and `turns`, with FTS5 on turn text. Incremental by size and mtime, resuming from `parsed_offset`, so an ordinary pass costs about a second. `--rebuild` re-derives every table from the archive and refuses when any session's raw is missing — the guard that stops a rebuild silently emptying the corpus
- `scad session ls|show|read` — the indexed traces of every session on the machine, whether scad started it or merely observed it. Filters for project, agent, kind, machine, grade, outcome and date; `--json` throughout
- `scad search <query>` — full-text search across every indexed turn. `--notes` searches the authored tier instead, matching metadata rather than bodies
- `scad view` — a static HTML browser over the index, answering the two questions worth asking: who is waiting on you, and how do you get back in. A `file://` page with no server by design
- `scad resolve`, `scad where`, `scad project ls|show` — a domain-free resolver by fixed precedence, the project key computed from it, and an explanation of how any directory resolved. `scad where` reports which rung answered, so a wrong answer is checkable rather than mysterious
- **Durable notes.** `scad session note` appends one `/remember` capture to `~/.scad/notes/<agent>/<session>.jsonl` — session-keyed, project-free, append-only, and the only tier that cannot be re-derived from anything. `scad notes ls|read` browse them; the store is indexed but the file is truth, readable before anything is indexed and after a `--rebuild`
- Notes are classified by `kind` — `info`, `handoff`, `bug`, `request`, `verification` — with `scad notes ls --kind handoff` as the "where did this leave off" query. An authored `project` files a note against a *different* project than the session it was written in, so a bug noticed while working elsewhere reaches the people looking for it
- `scad session launch --agent claude|codex|kimi` — start an interactive agent at a directory and learn which session it became, with the exact resume command handed back. tmux is required and not as a convenience: a non-TTY stdout alone makes Claude stamp a session `sdk-cli`, which its own `/resume` picker then hides permanently
- `scad session resume <id>` — attach if the session is open, resume it if not
- `scad session launch --json` — the launch record on stdout and nothing else, so a caller reads the session id from a contract rather than by scraping human-facing lines. Human output moves to stderr under this flag; a launch that resolves no id still exits non-zero

### Added

- Skills install into **every** agent, not just Claude — `install.sh` now calls `npx skills add -g -a '*'`, which routes to `~/.agents/skills` (Codex, Kimi, and the shared convention) and `~/.claude/skills` (Claude, which does not read the shared one). Falls back to symlinking those two directories when node is absent. `--no-skills` opts out; `--no-plugin` still works, undocumented
- `/remember` is a skill (`skills/remember/SKILL.md`), not a Claude Code command. The frontmatter `name` maps to the invocation, so `/remember` is unchanged — and it now works in Codex (`$remember`) and anything else following the convention
- `scad view --refresh` — archive and index new traces before rendering. Opt-in, so plain `scad view` stays the read-only renderer its spec promises
- `scad run start|stop|clean|attach|info|inject|jobs|logs|send|refresh` — the container verbs, renamed off `session`. A run hosts many jobs; each job produces one agent session, so the two can never share a noun
- `scad run ls` — the fleet view, renamed from `scad status`. `scad session` now means agent sessions only (`ls`, `show`, `read`); the old container paths and `scad status` remain as **hidden aliases** — working, absent from `--help`
- Job state can create a session row — a job whose transcript and `history.jsonl` line are both gone is now indexed as `grade='skeleton'`, `source='claude-jobstate'`, carrying its human name
- `tool-result-last` outcome — a tool returned and the model never spoke again; 944 of 1458 rows on a real index, previously NULL. Distinct from `in-flight`, which is a call awaiting its result
- macOS support — scad runs on macOS via a dedicated `scad` Colima VM it owns, isolated from any other Docker. `get_docker_client()` resolves the daemon per-OS (Linux: native; macOS: `~/.colima/scad/docker.sock`); Linux behaviour is unchanged
- `scad vm start|stop|status|info|delete` — manage the macOS VM; `build`, `session start`, `dispatch`, and `batch` all start it lazily
- Auto mount-translation on macOS — non-`$HOME` repo and `mounts:` paths are added to the VM's mount list (writable) and the VM restarted, only when the required set changed
- `~/.scad/settings.yml` — user-level settings; `colima.cpu` (2), `colima.memory` (4 GiB), `colima.disk` (60 GiB), `colima.vm_type` (vz), `colima.mount_type` (virtiofs)
- `install.sh` platform branch — Linux verifies a reachable dockerd; macOS installs Colima via Homebrew and creates the `scad` profile (`--no-vm` to skip)
- GPU passthrough — `gpu: true` config option adds an NVIDIA `DeviceRequest` (all GPUs) to the container; requires nvidia-container-toolkit on host
- Submodule support — `create_clones` runs `git submodule update --init --recursive`; `code fetch` walks submodules and fetches their non-default branches back to the host
- `pip_install` per-repo config — `pip install --no-deps -e /workspace/<key>` at startup
- SSH key mount — `~/.ssh` mounted read-only at `/home/scad/.ssh` for SSH git operations (submodules, private repos)
- `scad build --no-cache` — bust Docker layer cache
- `harvest --merge` / `finish --merge` — fast-forward-only merge of fetched branches per repo

### Changed
- **BREAKING — the container verbs moved off `session`.** `session start|stop|clean|attach|inject| jobs|logs|send|refresh` are now `run …`, and `scad status` is `scad run ls`. A run is a container and a session is a trace; one noun could not keep meaning both. The old paths remain as **hidden aliases** — working, absent from `--help`
- **BREAKING — the `/remember` record changed shape.** `span` is gone (it was written on every record and read by no machine), and `relation` is no longer authored: it is derived at read time from `parent` and the topics already in the thread. `kind` and an optional `project` take their place. Older records keep working — `kind` reads through a default and `relation` is computed per query — but a writer must stop emitting the two removed fields
- **scad is no longer a Claude Code plugin.** `plugin.json`, `marketplace.json` and `register_claude_plugin()` are gone; skills reach every agent instead of one. The plugin never installed the binary — `install.sh` always did that — so nothing moves but distribution. Install *deregisters* any existing plugin first: plugin skills and directory skills **stack** rather than override, so a machine carrying both offered every skill twice under two names
- `scad session note --current` resolves the session from the id the agent exports (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`) rather than by scanning directories and comparing mtimes — exact instead of inferred, and it settles the case of several sessions sharing one cwd by removing the ambiguity rather than arbitrating it. Selected by `--agent`; the cwd scan remains a fallback
- `gpu: true` now errors on macOS — no GPU passthrough into a Lima VM

### Fixed
- **Launching into an untrusted directory killed the session.** Claude Code's folder-trust dialog is an arrow menu, so the numbered-option matcher found nothing and the gate marker missed too — the pane read as ready, the priming turn was typed into the dialog, and the Enter that submits a prompt answered its highlighted default, `No, exit`. Claude quit, and because no transcript was written the session was absent from `/resume` as well. Compounding it, only the codex leg consulted the pane state at all; claude and kimi discarded it and sent regardless. All three legs now stop, and `session launch` exits non-zero naming the dialog, the live pane, and the fact that nothing was sent and no key was pressed
- **A session's project label wandered even after cwd was pinned.** The earlier fix stopped `cwd` moving and left `project = excluded.project` one line below — a plain assignment from the cwd of whatever record a pass happened to parse. One row could therefore contradict itself: cwd in one repository, project in another. Because `project` is the retrieval join key, a session's notes fell out of their own project's listing. Both columns are now pinned the same way
- **A session scad had just launched was invisible until the next `reindex`.** Launch wrote its record and told the index nothing, so `session show` denied a session started minutes earlier and checking your own run meant leaving scad for `tmux capture-pane`. Launch now seeds a skeleton row; the archive pass upserts onto the same row and upgrades it
- **A note was unfindable until someone reindexed.** `session note` wrote the file and left indexing to the next pass — worst for a note cross-filed against another project, whose whole purpose is that someone working elsewhere picks it up. Notes now index as they are written, advancing the offset so the next pass does not re-append them
- **`notes read` denied notes it had just listed.** It defaulted to the claude shard, so a codex or kimi note answered "No notes for <id>" one line after appearing in `notes ls`. It now falls back to whichever shard holds the session, and the listing shows the agent
- **The `codex` skill told Codex to consult Codex.** Its *When NOT to Use* never said "you are Codex", so a Codex session launched as an independent reviewer matched the skill and spawned `codex exec` against itself — the same weights answering their own question. Surfaced by skills shipping to every agent rather than to Claude alone
- **`session launch --json` emitted a preamble before the record.** Human-facing `[scad]` lines shared stdout with the JSON, so the output was not parseable as a whole
- `session show` now reports when a session started, ended and how long it ran. The columns are epoch **milliseconds**, and `datetime(ended,'unixepoch')` returns NULL rather than erroring on them, so the conversion lives in one named place
- Removed a dead `session_notes` in `index.py` that a second definition had been shadowing — the two returned different types, so deleting the wrong one would have quietly changed every caller's rows to dicts
- `--current` no longer files a note against another agent's session. Environment variables are inherited, so codex or kimi launched from a Claude session sees `CLAUDE_CODE_SESSION_ID`; resolution now refuses when the requested agent's own variable is absent instead of silently using the parent's id — wrong attribution in the one tier that can never be re-derived
- Cumulative counters (`n_interrupts`, `n_tool_denials`, `n_errors`) survive an incremental reindex. They were overwritten with the tail's counts, so a session that was interrupted and then grew quietly reported zero; the values were recoverable only while raw survived
- The test suite no longer depends on the developer's ambient git config — 17 tests failed on any machine with no global `user.email`
- `install.sh --uninstall` on macOS — shell-config cleanup used GNU-only `sed -i "/x/,+2d"`, which BSD sed silently ignored; rewritten with portable awk and now cleans `~/.bashrc` too
- macOS container mounts — `/etc/localtime` is no longer bind-mounted (it resolves outside `$HOME` and is invisible in the VM; `TZ` already covers it), and `~/.ssh` is staged through `/mnt/host-ssh` so the entrypoint can restore the 0600 modes OpenSSH requires
- Submodule fetch — create `scad-*` branch inside submodules, fetch detached submodule HEAD, fetch host submodule objects into container clones, surface fetch errors
- tmux inject race — poll `tmux has-session` before `new-window`, with container crash detection
- Dockerfile build speed — create user before pip install, drop `--no-cache-dir`

## [0.3.0] — 2026-03-03

Composite workflows, Claude Code plugin, small features. Stable release.

### Added
- `dispatch --plan <path>` — auto-generate execution prompt from plan file
- `session logs --job` — human-readable tool activity view (was result-only)
- `harvest --diff` — opt-in full diff (default changed to git log --oneline)

### Changed
- `harvest` default output is now `git log --oneline` (readable) instead of full unified diff

### Fixed
- Interactive inject `tmux new-window` silent failure — removed `detach=True`, now checks exit code
- Plugin skill (`skills/scad/SKILL.md`) updated to match current CLI (removed stale `--interactive` flag, added missing commands)
- Documented `.yml`-only config convention in README
- Clarified PyPI package name vs CLI command in pyproject.toml

### Added (Plan 13)
- `session inject --wait` — blocking inject with exit code propagation and elapsed timer
- `session inject --wait --tail` — real-time streaming of Claude activity during wait
- `scad dispatch` — composite: build-if-needed → start → inject (headless+wait by default)
- `scad harvest` — composite: code fetch + code diff summary, `--merge` for fast-forward
- `scad finish` — composite: fetch-first safety + diff + session clean
- Claude Code plugin — `.claude-plugin/plugin.json` + `skills/scad/SKILL.md` + `skills/scad-plan-adapt/SKILL.md`
- Crash detection — `session status` shows recently-crashed containers, `session start` checks startup health
- `python.editable` config option — `pip install -e .` at runtime for pyproject.toml projects
- Configurable `SCAD_HOME` — env var override for `~/.scad/`, enables test isolation
- `session send` — type into a running interactive Claude via tmux send-keys
- `scad batch` — parallel headless jobs from `---`-delimited prompt file with `--parallel N` and `--fail-fast`
- `code branch <run-id> <name>` — create/switch branch in all session clones
- `install.sh` — bootstrap installer with auto-detection (venv, symlink, shell completions, Claude Code plugin registration, uninstall)
- Top-level `scad status` — no arg lists sessions, with config arg shows project overview

### Changed
- `dispatch` defaults to interactive mode — `--headless` is now the opt-in flag (was `--interactive`)
- `code refresh` moved to `session refresh` (credential push is session maintenance)
- `project` CLI group removed — functionality merged into top-level `scad status`

### Fixed
- Added missing unit tests for `get_image_info()` and `get_recently_crashed()`

## [0.2.0] — 2026-03-03

Session injection architecture — separates container lifecycle from Claude execution.

### Added
- `session inject` command — inject Claude processes into running sessions via docker exec
- `session jobs` command — list injected jobs with status, mode, and branch
- `code add` / `code remove` — modify session workspace at runtime (symlink or clone)
- `code diff` — show differences between session clones and source repos
- Branch-per-job support — `--branch` flag on inject, multi-branch fetch
- Job tracking — per-job metadata in `~/.scad/runs/<id>/jobs/`, stream logs per job

### Changed
- Entrypoint simplified to setup-only (~50 lines, was ~140). No Claude launch in entrypoint.
- All Claude launches now happen via `docker exec` injection from the host
- Single `workspace/` bind mount replaces per-repo Docker volumes
- Non-worktree repos and data mounts are symlinked into workspace
- `session start --prompt` is now sugar for start + immediate inject
- `--headless` is a property of the injection, not the session
- `code fetch` discovers and fetches all branches (was single branch only)
- Workspace directory: `runs/<id>/workspace/` (was `runs/<id>/worktrees/`)

## [0.1.0] — 2026-03-01

Initial pre-release.

- Config-driven Docker sessions for Claude Code
- Hierarchical CLI: `scad session` (start/stop/attach/clean/status/info/logs), `scad code` (fetch/sync/refresh), `scad config` (list/view/edit/add/remove/new), `scad project` (status), `scad build`, `scad gc`
- Interactive (tmux) and headless (stream-json) session modes
- Host-side local clones with auto-branching (`scad-{config}-{tag}-MonDD-HHMM`)
- `--prompt` for interactive session with prompt pre-entered, `--headless` for fire-and-forget
- `code sync` with fast-forward, `--checkout`, `--no-update-main`
- `config info` — structured environment summary for tooling
- Bulk operations: `session stop/clean --all`, `--config`, `--yes`
- Garbage collection: orphaned containers, dead run dirs, unused images
- Consolidated session state: `~/.scad/runs/<run-id>/` with auto-migration
- Safety: deny rules, PreToolUse hooks, bypass permissions, telemetry controls
- Visibility: session info with token usage, project status, statusline, credential expiry warnings
- Container timezone, co-authored-by suppression, plugin pre-seeding
- Git-delta for diffs, credential refresh, Docker image auto-prune on build
- Bootstrap plugins (superpowers, commit-commands, pyright-lsp)
- Run-ID validation, tab completion across all commands
