# Changelog

## [Unreleased]

Post-0.3.0 features (Mar–Apr 2026): GPU passthrough, submodule support, per-repo pip install.

### Added
- GPU passthrough — `gpu: true` config option adds an NVIDIA `DeviceRequest` (all GPUs) to the container; requires nvidia-container-toolkit on host
- Submodule support — `create_clones` runs `git submodule update --init --recursive`; `code fetch` walks submodules and fetches their non-default branches back to the host
- `pip_install` per-repo config — `pip install --no-deps -e /workspace/<key>` at startup
- SSH key mount — `~/.ssh` mounted read-only at `/home/scad/.ssh` for SSH git operations (submodules, private repos)
- `scad build --no-cache` — bust Docker layer cache
- `harvest --merge` / `finish --merge` — fast-forward-only merge of fetched branches per repo

### Fixed
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
