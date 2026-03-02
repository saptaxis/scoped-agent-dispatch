# Changelog

## [Unreleased]

### Added
- `--prompt` flag now starts interactive session with prompt pre-entered (Claude starts working immediately)
- `--headless` flag for fire-and-forget mode (requires `--prompt`)
- `code sync` fast-forwards clone's main branch by default
- `code sync --checkout <branch>` to switch branch after sync
- `code sync --no-update-main` for fetch-only behavior
- `scad config info <name>` — structured environment summary for tooling
- Plan adaptation skill for rewriting plans for container execution
- Claude-level events in entrypoint (start/finish with timestamps)
- Subagent count in `session info`
- Cache token display in `session info` (creation + read)

### Fixed
- ccusage JSON parsing — tokens no longer show 0 in `session info`
- `session info` no longer counts subagent sessions as top-level Claude sessions
- 11 CLI tests fixed (missing `validate_run_id` mock)

### Changed
- `--prompt` without `--headless` is now interactive (was headless). Add `--headless` for old behavior.

### Previously Added (Plan 09)
- `.claude` management module — centralized Claude Code configuration
- Container timezone support (IANA timezone via `TZ` + `/etc/localtime`)
- Co-authored-by suppression via `attribution` setting
- `.claude.json` bind-mount for persistent onboarding/trust state
- Plugin pre-seeding (`enabledPlugins` in settings.json)
- CONTRIBUTING guide, dev dependencies

## [0.1.0] — 2026-03-01

Initial pre-release.

- Config-driven Docker sessions for Claude Code
- Hierarchical CLI: `scad session` (start/stop/attach/clean/status/info/logs),
  `scad code` (fetch/sync/refresh), `scad config` (list/view/edit/add/remove/new),
  `scad project` (status), `scad build`, `scad gc`
- Interactive (tmux) and headless (stream-json) session modes
- Host-side local clones with auto-branching (`scad-{config}-{tag}-MonDD-HHMM`)
- Bulk operations: `session stop/clean --all`, `--config`, `--yes`
- Garbage collection: orphaned containers, dead run dirs, unused images
- Consolidated session state: `~/.scad/runs/<run-id>/` with auto-migration
- Safety: deny rules, PreToolUse hooks, bypass permissions, telemetry controls
- Visibility: session info with token usage, project status, statusline, credential expiry warnings
- Symlink-based config registration, config scaffolding (`scad config new`)
- Git-delta for diffs, credential refresh, Docker image auto-prune on build
- Bootstrap plugins (superpowers, commit-commands, pyright-lsp)
- Run-ID validation, tab completion across all commands
