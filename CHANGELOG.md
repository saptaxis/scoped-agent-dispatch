# Changelog

## [Unreleased]

## [0.4.0] — 2026-09-09

**The release where scad stopped being only a container dispatcher.** 0.3.0 could put a Claude
agent in a container and get a branch back. This one adds a second tier that reads rather than
runs: every agent trace on the machine is archived before it can be pruned, indexed into sqlite,
and made searchable and browsable — for claude, codex and kimi alike, including sessions scad
never launched. It also adds the one tier nothing can re-derive: durable notes written by
`/remember`.

The two tiers share a project key and little else, and they are deliberately asymmetric: the read
tier is multi-agent, the container tier remains Claude-only.

**Breaking:** the container verbs moved off `session` (`session start` → `run start`, `scad status`
→ `scad run ls`); the old paths survive as hidden aliases. scad is no longer distributed as a
Claude Code plugin. The `/remember` record shape changed — `span` and an authored `relation` are
gone.

### Added — the read tier

- `scad archive` — an append-only copy of every agent trace, taken *before* an agent prunes its
  own history. Mirrors the source layout under `~/.scad/archive/` and carries a `DO-NOT-DELETE.md`
  saying why. Reads claude, codex and kimi; a family that is absent from the machine is normal,
  not an error
- `scad reindex` — sqlite over `sessions` and `turns`, with FTS5 on turn text. Incremental by size
  and mtime, resuming from `parsed_offset`, so an ordinary pass costs about a second.
  `--rebuild` re-derives every table from the archive and refuses when any session's raw is
  missing — the guard that stops a rebuild silently emptying the corpus
- `scad session ls|show|read` — the indexed traces of every session on the machine, whether scad
  started it or merely observed it. Filters for project, agent, kind, machine, grade, outcome and
  date; `--json` throughout
- `scad search <query>` — full-text search across every indexed turn. `--notes` searches the
  authored tier instead, matching metadata rather than bodies
- `scad view` — a static HTML browser over the index, answering the two questions worth asking:
  who is waiting on you, and how do you get back in. A `file://` page with no server by design
- `scad resolve`, `scad where`, `scad project ls|show` — a domain-free resolver by fixed
  precedence, the project key computed from it, and an explanation of how any directory resolved.
  `scad where` reports which rung answered, so a wrong answer is checkable rather than mysterious
- **Durable notes.** `scad session note` appends one `/remember` capture to
  `~/.scad/notes/<agent>/<session>.jsonl` — session-keyed, project-free, append-only, and the only
  tier that cannot be re-derived from anything. `scad notes ls|read` browse them; the store is
  indexed but the file is truth, readable before anything is indexed and after a `--rebuild`
- Notes are classified by `kind` — `info`, `handoff`, `bug`, `request`, `verification` — with
  `scad notes ls --kind handoff` as the "where did this leave off" query. An authored `project`
  files a note against a *different* project than the session it was written in, so a bug noticed
  while working elsewhere reaches the people looking for it
- `scad session launch --agent claude|codex|kimi` — start an interactive agent at a directory and
  learn which session it became, with the exact resume command handed back. tmux is required and
  not as a convenience: a non-TTY stdout alone makes Claude stamp a session `sdk-cli`, which its
  own `/resume` picker then hides permanently
- `scad session resume <id>` — attach if the session is open, resume it if not
- `scad session launch --json` — the launch record on stdout and nothing else, so a caller reads
  the session id from a contract rather than by scraping human-facing lines. Human output moves to
  stderr under this flag; a launch that resolves no id still exits non-zero

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
- **BREAKING — the container verbs moved off `session`.** `session start|stop|clean|attach|inject|
  jobs|logs|send|refresh` are now `run …`, and `scad status` is `scad run ls`. A run is a
  container and a session is a trace; one noun could not keep meaning both. The old paths remain
  as **hidden aliases** — working, absent from `--help`
- **BREAKING — the `/remember` record changed shape.** `span` is gone (it was written on every
  record and read by no machine), and `relation` is no longer authored: it is derived at read time
  from `parent` and the topics already in the thread. `kind` and an optional `project` take their
  place. Older records keep working — `kind` reads through a default and `relation` is computed
  per query — but a writer must stop emitting the two removed fields
- **scad is no longer a Claude Code plugin.** `plugin.json`, `marketplace.json` and `register_claude_plugin()` are gone; skills reach every agent instead of one. The plugin never installed the binary — `install.sh` always did that — so nothing moves but distribution. Install *deregisters* any existing plugin first: plugin skills and directory skills **stack** rather than override, so a machine carrying both offered every skill twice under two names
- `scad session note --current` resolves the session from the id the agent exports (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`) rather than by scanning directories and comparing mtimes — exact instead of inferred, and it settles the case of several sessions sharing one cwd by removing the ambiguity rather than arbitrating it. Selected by `--agent`; the cwd scan remains a fallback
- `gpu: true` now errors on macOS — no GPU passthrough into a Lima VM

### Fixed
- **Launching into an untrusted directory killed the session.** Claude Code's folder-trust dialog
  is an arrow menu, so the numbered-option matcher found nothing and the gate marker missed too —
  the pane read as ready, the priming turn was typed into the dialog, and the Enter that submits a
  prompt answered its highlighted default, `No, exit`. Claude quit, and because no transcript was
  written the session was absent from `/resume` as well. Compounding it, only the codex leg
  consulted the pane state at all; claude and kimi discarded it and sent regardless. All three legs
  now stop, and `session launch` exits non-zero naming the dialog, the live pane, and the fact that
  nothing was sent and no key was pressed
- **A session's project label wandered even after cwd was pinned.** The earlier fix stopped `cwd`
  moving and left `project = excluded.project` one line below — a plain assignment from the cwd of
  whatever record a pass happened to parse. One row could therefore contradict itself: cwd in one
  repository, project in another. Because `project` is the retrieval join key, a session's notes
  fell out of their own project's listing. Both columns are now pinned the same way
- **A session scad had just launched was invisible until the next `reindex`.** Launch wrote its
  record and told the index nothing, so `session show` denied a session started minutes earlier
  and checking your own run meant leaving scad for `tmux capture-pane`. Launch now seeds a
  skeleton row; the archive pass upserts onto the same row and upgrades it
- **A note was unfindable until someone reindexed.** `session note` wrote the file and left
  indexing to the next pass — worst for a note cross-filed against another project, whose whole
  purpose is that someone working elsewhere picks it up. Notes now index as they are written,
  advancing the offset so the next pass does not re-append them
- **`notes read` denied notes it had just listed.** It defaulted to the claude shard, so a codex or
  kimi note answered "No notes for <id>" one line after appearing in `notes ls`. It now falls back
  to whichever shard holds the session, and the listing shows the agent
- **The `codex` skill told Codex to consult Codex.** Its *When NOT to Use* never said "you are
  Codex", so a Codex session launched as an independent reviewer matched the skill and spawned
  `codex exec` against itself — the same weights answering their own question. Surfaced by skills
  shipping to every agent rather than to Claude alone
- **`session launch --json` emitted a preamble before the record.** Human-facing `[scad]` lines
  shared stdout with the JSON, so the output was not parseable as a whole
- `session show` now reports when a session started, ended and how long it ran. The columns are
  epoch **milliseconds**, and `datetime(ended,'unixepoch')` returns NULL rather than erroring on
  them, so the conversion lives in one named place
- Removed a dead `session_notes` in `index.py` that a second definition had been shadowing — the
  two returned different types, so deleting the wrong one would have quietly changed every
  caller's rows to dicts
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
- Hierarchical CLI: `scad session` (start/stop/attach/clean/status/info/logs),
  `scad code` (fetch/sync/refresh), `scad config` (list/view/edit/add/remove/new),
  `scad project` (status), `scad build`, `scad gc`
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
