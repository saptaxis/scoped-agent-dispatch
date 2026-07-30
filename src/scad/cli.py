"""CLI entry point."""

import copy
import json
import os
import subprocess
import subprocess as _subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import click
import docker
import yaml

from scad.config import load_config, list_configs, CONFIG_DIR, SCAD_DIR, ScadConfig
from scad.prompts import parse_prompt_file
from scad.vm import (
    SCAD_PROFILE,
    VMUnsupported,
    colima_socket_path,
    docker_cli_env,
    ensure_gpu_supported,
    ensure_vm_running,
    get_docker_client,
    is_macos,
    mount_root,
    path_visible_in_vm,
    read_vm_mounts,
    reconcile_vm_mounts,
    vm_delete,
    vm_info,
    vm_start,
    vm_state,
    vm_stop,
)
from scad.container import (
    build_image,
    check_claude_auth,
    clean_run,
    cleanup_clones,
    config_name_for_run,
    create_branch,
    create_clones,
    diff_from_source,
    fetch_to_host,
    log_from_source,
    merge_fetched_branches,
    gc,
    generate_run_id,
    get_all_sessions,
    get_image_info,
    get_recently_crashed,
    get_project_status,
    get_session_usage,
    get_session_info,
    image_exists,
    inject_job,
    list_jobs,
    send_to_job,
    list_scad_containers,
    log_event,
    prune_old_images,
    refresh_credentials,
    resolve_branch,
    run_container,
    stop_container,
    sync_from_host,
    validate_run_id,
    workspace_add,
    workspace_name_taken,
    workspace_remove,
)
from scad.resolve import ResolveConfig, announce, require, resolve as resolve_target
from scad.archive import archive_all, archive_root, archive_run, summarize
from scad.project import resolve_project
from scad.index import (
    connect as index_connect,
    reindex as run_reindex,
    search_notes,
    search_turns,
    session_notes as index_session_notes,
    session_notes as index_session_notes,
    session_row,
    session_turns,
)
from scad.launch import read_record
from scad.live import (
    attach_argv,
    claude_live_sessions,
    find_pane,
    running_run_ids,
    tmux_panes,
)
from scad.notes import (
    NoteTargetError,
    append_note,
    current_session_id,
    note_path,
    read_note_file,
)
from scad.view import gather, render, resume_argv, resume_command, write_view


def _relative_time(iso_str: str) -> str:
    """Format an ISO timestamp as relative time."""
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{seconds // 60} min ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except (ValueError, TypeError):
        return iso_str or "?"


def _day_ms(day: str) -> int:
    """YYYY-MM-DD -> epoch ms at local midnight."""
    try:
        return int(datetime.strptime(day, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        raise click.ClickException(f"Invalid date {day!r}; expected YYYY-MM-DD.")


def _complete_run_ids(ctx, param, incomplete):
    """Shell completion for run IDs from SCAD_HOME/runs/."""
    runs_dir = SCAD_DIR / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        d.name for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(incomplete)
    )


def _complete_running_sessions(ctx, param, incomplete):
    """Shell completion for running sessions only (attach, inject, send, etc.)."""
    try:
        running = list_scad_containers()
    except Exception:
        return _complete_run_ids(ctx, param, incomplete)
    return sorted(
        s["run_id"] for s in running
        if s["run_id"].startswith(incomplete)
    )


def _complete_sessions_with_clones(ctx, param, incomplete):
    """Shell completion for sessions that have clones (code fetch, harvest, etc.)."""
    runs_dir = SCAD_DIR / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        d.name for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(incomplete)
        and ((d / "workspace").exists() or (d / "worktrees").exists())
    )


def _complete_cleanable_sessions(ctx, param, incomplete):
    """Shell completion for sessions that can be cleaned (not already cleaned)."""
    runs_dir = SCAD_DIR / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        d.name for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(incomplete)
        and any(d.iterdir())  # not empty = not fully cleaned
    )


def _complete_config_names(ctx, param, incomplete):
    """Shell completion for config names."""
    return sorted(n for n in list_configs() if n.startswith(incomplete))


def _format_tool_line(record: dict) -> list[str]:
    """Extract condensed tool activity lines from a stream record."""
    lines = []
    rtype = record.get("type", "")
    if rtype == "assistant":
        msg = record.get("message", {})
        for block in msg.get("content", []):
            if block.get("type") == "tool_use":
                tool = block.get("name", "?")
                inp = block.get("input", {})
                if tool == "Read":
                    lines.append(f"[scad] Reading {inp.get('file_path', '?')}...")
                elif tool == "Edit":
                    lines.append(f"[scad] Editing {inp.get('file_path', '?')}...")
                elif tool == "Write":
                    lines.append(f"[scad] Writing {inp.get('file_path', '?')}...")
                elif tool == "Bash":
                    cmd = inp.get("command", "?")[:60]
                    lines.append(f"[scad] Running: {cmd}...")
                else:
                    lines.append(f"[scad] {tool}...")
    return lines


def _tail_stream(stream_path: Path, stop_event: threading.Event):
    """Tail a stream.jsonl file, printing condensed Claude activity.

    Waits for the file to appear, then reads lines as they are written.
    After stop_event is set, drains any remaining lines before returning.
    """
    while not stream_path.exists():
        if stop_event.wait(0.5):
            # Job finished before stream file appeared — try one last time
            if not stream_path.exists():
                return
            break
    with open(stream_path) as f:
        while True:
            line = f.readline()
            if not line:
                if stop_event.is_set():
                    # Drain: read any remaining lines that appeared
                    remaining = f.read()
                    if remaining:
                        for rem_line in remaining.splitlines():
                            try:
                                record = json.loads(rem_line.strip())
                            except json.JSONDecodeError:
                                continue
                            for msg in _format_tool_line(record):
                                click.echo(msg)
                    break
                stop_event.wait(0.3)
                continue
            try:
                record = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            for msg in _format_tool_line(record):
                click.echo(msg)


@click.group()
def main():
    """scad — dispatch Claude Code agents in isolated Docker containers."""
    pass


@main.command()
@click.argument("root", required=False, default=None, type=click.Path())
@click.option("--marker", "markers", multiple=True,
              help="Walk-up sentinel filename. Repeatable; order breaks ties within a directory.")
@click.option("--git-root", is_flag=True, help="Walk up for .git (worktrees resolve to the repo).")
@click.option("--ask", is_flag=True, help="Prompt when nothing is found (needs a tty).")
@click.option("--start", default=None, type=click.Path(), help="Where to start the walk (default: cwd).")
@click.option("--label", default="target", help="Name used in the stderr announce line.")
@click.option("--json", "as_json", is_flag=True, help="Emit {path, matched_by, tried} on stdout.")
def resolve(root, markers, git_root, ask, start, label, as_json):
    """Resolve a target directory by fixed precedence.

    Precedence: explicit ROOT -> markers (walk-up) -> git-root -> ask -> unresolved.

    The resolved path goes to stdout; the announce line goes to stderr, so the
    output stays pipeable.

    \b
    matched_by vocabulary (a stable public contract):
      explicit       the caller passed ROOT
      marker:<file>  walk-up found <file>
      ask            answered interactively
      unresolved     nothing matched

    \b
    Exit codes:
      0  resolved
      1  unresolved
      2  usage error
    """
    cfg = ResolveConfig(markers=tuple(markers), use_git_root=git_root, allow_ask=ask)
    res = resolve_target(
        cfg,
        start=Path(start) if start else None,
        explicit=Path(root) if root else None,
    )

    if as_json:
        click.echo(json.dumps({
            "path": str(res.path) if res.path else None,
            "matched_by": res.matched_by,
            "tried": list(res.tried),
        }))
        raise SystemExit(0 if res.path else 1)

    path = require(res)
    announce(path, res, label)
    click.echo(str(path))


@main.command()
@click.option("--start", default=None, type=click.Path(), help="Resolve from this directory instead of cwd.")
def where(start):
    """Announce the project scad resolves for a directory."""
    from pathlib import Path as _Path

    target = _Path(start) if start else _Path.cwd()
    project = resolve_project(target)
    click.echo(f"[scad] project: {project}  ({target})")


@main.group()
def run():
    """Container runs — start, inspect, inject into, tear down.

    Three levels, each with the name the code already uses: a RUN is the
    container, a JOB is one launched agent process inside it, and a SESSION is
    that agent's trace. A run hosts many jobs, so `run` and `session` are a
    genuine one-to-many and cannot share a noun.
    """
    pass


@main.group()
def session():
    """Agent sessions — the indexed traces of what actually ran.

    Every session on the machine, whether scad launched it or merely observed
    it. For the container lifecycle see `scad run`.
    """
    pass


@main.group()
def code():
    """Git state between host and clones."""
    pass


@run.command("ls")
@click.argument("config_name", required=False, default=None, shell_complete=_complete_config_names)
@click.option("--all", "show_all", is_flag=True, help="Show full run history.")
@click.option("--cost", is_flag=True, help="Include cost data (slow — runs ccusage).")
def status(config_name: str, show_all: bool, cost: bool):
    """List runs, or show project overview for a config.

    Without arguments: list running containers.
    With a config name: show cross-run project overview.
    """
    if config_name:
        # Project overview mode
        status_data = get_project_status(config_name, include_cost=cost)

        if status_data["total_sessions"] == 0:
            click.echo(f"[scad] No runs found for config: {config_name}")
            return

        parts = []
        if status_data["running"]:
            parts.append(f"{status_data['running']} running")
        if status_data["stopped"]:
            parts.append(f"{status_data['stopped']} stopped")
        if status_data["cleaned"]:
            parts.append(f"{status_data['cleaned']} cleaned")
        status_str = ", ".join(parts) if parts else "none"

        click.echo(f"Project:     {status_data['config']}")
        click.echo(f"Sessions:    {status_data['total_sessions']} ({status_str})")
        click.echo(f"Last active: {_relative_time(status_data['last_active'])}")
        if cost and status_data["total_cost"] > 0:
            click.echo(f"Total cost:  ${status_data['total_cost']:.2f}")

        click.echo()
        if cost:
            click.echo(
                f"  {'RUN ID':<35} {'BRANCH':<30} {'STARTED':<12} {'STATUS':<10} {'COST'}"
            )
            for s in status_data["sessions"]:
                started = _relative_time(s["started"])
                cost_str = f"${s['cost']:.2f}" if s["cost"] > 0 else "-"
                click.echo(
                    f"  {s['run_id']:<35} {s['branch']:<30} {started:<12} {s['container']:<10} {cost_str}"
                )
        else:
            click.echo(
                f"  {'RUN ID':<35} {'BRANCH':<30} {'STARTED':<12} {'STATUS'}"
            )
            for s in status_data["sessions"]:
                started = _relative_time(s["started"])
                click.echo(
                    f"  {s['run_id']:<35} {s['branch']:<30} {started:<12} {s['container']}"
                )
    else:
        # Session listing mode (same as old session status)
        if show_all:
            all_runs = get_all_sessions()
            if not all_runs:
                click.echo("[scad] No sessions found.")
            else:
                click.echo(
                    f"{'RUN ID':<30} {'CONFIG':<12} {'BRANCH':<25} "
                    f"{'STARTED':<12} {'CONTAINER':<12} {'CLONES'}"
                )
                for run in all_runs:
                    started = _relative_time(run["started"]) if run["started"] else "?"
                    click.echo(
                        f"{run['run_id']:<30} {run['config']:<12} {run['branch']:<25} "
                        f"{started:<12} {run['container']:<12} {run['clones']}"
                    )
        else:
            running = list_scad_containers()
            if not running:
                click.echo("[scad] No running runs.")
            else:
                click.echo(
                    f"{'RUN ID':<30} {'CONFIG':<12} {'BRANCH':<25} "
                    f"{'STARTED':<12} {'CONTAINER':<12} {'CLONES'}"
                )
                for i, run in enumerate(running):
                    started = _relative_time(run["started"]) if run["started"] else "?"
                    clone_dir = SCAD_DIR / "runs" / run["run_id"] / "workspace"
                    clones = "yes" if clone_dir.exists() else "-"
                    click.echo(
                        f"{run['run_id']:<30} {run['config']:<12} {run['branch']:<25} "
                        f"{started:<12} {'running':<12} {clones}"
                    )
                    jobs = list_jobs(run["run_id"])
                    if jobs:
                        for job in jobs:
                            mode = job.get("mode", "?")
                            branch = job.get("branch") or ""
                            job_started = _relative_time(job.get("started", ""))
                            click.echo(
                                f"  └ {job['job_id']:<27} {mode:<12} {branch:<25} {job_started}"
                            )
                    if i < len(running) - 1:
                        click.echo()

            crashed = get_recently_crashed()
            if crashed:
                click.echo()
                click.echo("[scad] Recently crashed:")
                for c in crashed:
                    click.echo(f"  {c['run_id']} (exit code {c['exit_code']})")


def run_agent(
    config, branch: str, tag: str, prompt: str = None, headless: bool = False, rebuild: bool = False
) -> str:
    """Orchestrate the full agent lifecycle: resolve branch, build, create clones, run."""
    # Pre-flight: platform capability, then the VM, then Claude auth.
    # Capability first so an unsupported config fails before any slow work.
    ensure_gpu_supported(config)
    ensure_vm_running()
    reconcile_vm_mounts(config)

    valid, hours = check_claude_auth()
    if not valid:
        raise click.ClickException(
            "Claude auth expired or missing. Run: claude /login"
        )
    if hours < 1.0:
        click.echo(
            f"[scad] Warning: Claude auth expires in {hours * 60:.0f} minutes. "
            f"Consider running: claude /login"
        )

    run_id = generate_run_id(config.name, tag)

    # Build image if needed
    if rebuild or not image_exists(config):
        tag = f"scad-{config.name}"
        click.echo(f"[scad] Building image {tag}...")
        with tempfile.TemporaryDirectory() as build_dir:
            for line in build_image(config, Path(build_dir)):
                if line.startswith("Step "):
                    click.echo(f"[scad] {line}")
        click.echo(f"[scad] Image built: {tag}")
    else:
        click.echo(f"[scad] Using cached image scad-{config.name}")

    # Create host-side local clones
    click.echo(f"[scad] Creating clones on branch: {branch}")
    worktree_paths = create_clones(config, branch, run_id)

    # Run the container (always detached)
    click.echo(f"[scad] Dispatching agent: {run_id}")
    container_id = run_container(config, branch, run_id, worktree_paths)
    click.echo(f"[scad] Container started: {container_id[:12]}")

    if prompt:
        # Let entrypoint finish setup before injecting
        time.sleep(1)

        # Build add_dirs from config repos with add_dir=True
        add_dirs = [key for key, repo in config.repos.items() if repo.add_dir]

        job_id = inject_job(
            run_id=run_id,
            prompt=prompt,
            headless=headless,
            workdir_key=config.workdir_key,
            add_dirs=add_dirs,
            dangerously_skip_permissions=config.claude.dangerously_skip_permissions,
            additional_flags=config.claude.additional_flags,
        )
        mode = "headless" if headless else "interactive"
        click.echo(f"[scad] Injected {mode}: {job_id}")
        if headless:
            click.echo(f"[scad]   Setup log:    scad run logs {run_id}")
            click.echo(f"[scad]   Claude stream: scad run logs {run_id} --stream")
            click.echo(f"[scad]   Live follow:   scad run logs {run_id} -sf")
        else:
            click.echo(f"[scad]   Attach: scad run attach {run_id}")
    else:
        click.echo(f"[scad] Session ready. Run: scad run attach {run_id}")

    return run_id


@run.command("start")
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("--tag", required=True, help="Session tag (e.g., plan07, bugfix-auth). Use 'notag' to opt out.")
@click.option("--branch", default=None, help="Branch name (auto-generated if not specified).")
@click.option("--prompt", default=None, help="Prompt to kick off Claude (interactive by default, headless with --headless).")
@click.option("--headless", is_flag=True, help="Fire-and-forget mode (requires --prompt). Uses claude -p.")
@click.option("--rebuild", is_flag=True, help="Force rebuild the Docker image.")
def session_start(config_name: str, tag: str, branch: str, prompt: str, headless: bool, rebuild: bool):
    """Start a run — a container with the config's repos, ready for jobs."""
    if headless and not prompt:
        raise click.ClickException("--headless requires --prompt.")

    # Determine mode for events.log
    if headless:
        mode = "headless"
    elif prompt:
        mode = "interactive-prompt"
    else:
        mode = "interactive"

    try:
        config = load_config(config_name)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"[scad] Config validation error: {e}", err=True)
        sys.exit(2)

    try:
        branch = resolve_branch(config, branch, tag)
        run_id = run_agent(
            config, branch=branch, tag=tag, prompt=prompt, headless=headless, rebuild=rebuild
        )
        log_event(run_id, "start", f"config={config.name} branch={branch} mode={mode}")
        if headless:
            click.echo(f"[scad] Dispatched headless: {run_id}")
    except click.ClickException as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(3)


@main.group()
def config():
    """Manage project configs."""
    pass


@config.command("list")
def config_list():
    """List available project configs."""
    names = list_configs()
    if not names:
        click.echo("[scad] No configs found in ~/.scad/configs/")
        return

    click.echo(f"{'CONFIG':<20} {'IMAGE':<25} {'BUILT'}")
    for name in names:
        info = get_image_info(name)
        if info:
            built = _relative_time(info["created"])
            image = info["tag"]
        else:
            built = "never (not built)"
            image = f"scad-{name}"
        click.echo(f"{name:<20} {image:<25} {built}")


@config.command()
@click.argument("config_name", shell_complete=_complete_config_names)
def view(config_name: str):
    """Display a project config."""
    path = CONFIG_DIR / f"{config_name}.yml"
    if not path.exists():
        click.echo(f"[scad] Config not found: {config_name}", err=True)
        sys.exit(2)
    click.echo(path.read_text())


@config.command()
@click.argument("config_name", shell_complete=_complete_config_names)
def edit(config_name: str):
    """Open a project config in $EDITOR."""
    path = CONFIG_DIR / f"{config_name}.yml"
    if not path.exists():
        click.echo(f"[scad] Config not found: {config_name}", err=True)
        sys.exit(2)
    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([editor, str(path)])


@config.command("add")
@click.argument("config_path", type=click.Path(exists=True))
def config_add(config_path: str):
    """Register an external config (symlink into ~/.scad/configs/)."""
    path = Path(config_path).resolve()
    try:
        raw = yaml.safe_load(path.read_text())
        cfg = ScadConfig(**raw)
    except Exception as e:
        click.echo(f"[scad] Invalid config: {e}", err=True)
        sys.exit(2)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    link = CONFIG_DIR / f"{cfg.name}.yml"

    if link.exists():
        if link.is_symlink() and link.resolve() == path:
            click.echo(f"[scad] Already registered: {cfg.name}")
            return
        click.echo(f"[scad] Config '{cfg.name}' already exists at {link}", err=True)
        sys.exit(1)

    link.symlink_to(path)
    click.echo(f"[scad] Registered: {cfg.name} → {path}")


@config.command("remove")
@click.argument("config_name", shell_complete=_complete_config_names)
def config_remove(config_name: str):
    """Unregister a config (removes link, does not delete the source file)."""
    link = CONFIG_DIR / f"{config_name}.yml"
    if not link.exists():
        click.echo(f"[scad] Config not found: {config_name}", err=True)
        sys.exit(1)

    if link.is_symlink():
        target = link.resolve()
        link.unlink()
        click.echo(f"[scad] Removed: {config_name} (was → {target})")
    else:
        link.unlink()
        click.echo(f"[scad] Removed: {config_name}")


@config.command("new")
@click.argument("config_name")
@click.option("--edit", is_flag=True, help="Open in $EDITOR after creating.")
def config_new(config_name: str, edit: bool):
    """Create a new config from a commented template."""
    from scad.config import CONFIG_TEMPLATE

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / f"{config_name}.yml"

    if path.exists():
        click.echo(f"[scad] Config '{config_name}' already exists. Run: scad config edit {config_name}", err=True)
        sys.exit(1)

    path.write_text(CONFIG_TEMPLATE.format(name=config_name))
    click.echo(f"[scad] Created: {path}")

    if edit:
        editor = os.environ.get("EDITOR", "vim")
        subprocess.run([editor, str(path)])


@config.command("info")
@click.argument("config_name", shell_complete=_complete_config_names)
def config_info(config_name: str):
    """Show structured environment summary for a config."""
    try:
        cfg = load_config(config_name)
    except FileNotFoundError:
        click.echo(f"[scad] Config not found: {config_name}", err=True)
        sys.exit(1)

    click.echo(f"Config: {cfg.name}")
    click.echo(f"Image: scad-{cfg.name}")
    click.echo()

    click.echo("Repos:")
    for key, repo in cfg.repos.items():
        flags = []
        if repo.workdir:
            flags.append("workdir")
        if repo.add_dir:
            flags.append("add-dir")
        mode = "rw" if repo.worktree else "ro"
        flags.append(mode)
        click.echo(f"  {key}: {repo.path} → /workspace/{key} ({', '.join(flags)})")
    click.echo()

    if cfg.mounts:
        click.echo("Mounts:")
        for mount in cfg.mounts:
            click.echo(f"  {mount.host} → {mount.container} (rw)")
        click.echo()

    click.echo(f"Python: {cfg.python.version} (venv: /opt/venv)")
    if cfg.python.requirements:
        click.echo(f"Requirements: {cfg.python.requirements} (relative to /workspace/{cfg.workdir_key})")
    click.echo()

    click.echo("Claude:")
    if cfg.claude.dangerously_skip_permissions:
        click.echo("  dangerously_skip_permissions: true")
    if cfg.claude.plugins:
        click.echo(f"  plugins: {', '.join(p.split('@')[0] for p in cfg.claude.plugins)}")
    if cfg.claude.claude_md is not None and cfg.claude.claude_md is not False:
        click.echo(f"  claude_md: {cfg.claude.claude_md}")
    if cfg.claude.additional_flags:
        click.echo(f"  additional_flags: {cfg.claude.additional_flags}")


@main.command()
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("-v", "--verbose", is_flag=True, help="Show full Docker build output.")
@click.option("--no-cache", is_flag=True, help="Rebuild without Docker layer cache.")
def build(config_name: str, verbose: bool, no_cache: bool):
    """Build or rebuild the Docker image for a config."""
    try:
        config = load_config(config_name)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"[scad] Config validation error: {e}", err=True)
        sys.exit(2)

    try:
        ensure_vm_running()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)

    tag = f"scad-{config.name}"
    click.echo(f"[scad] Building image {tag}...")
    try:
        with tempfile.TemporaryDirectory() as build_dir:
            for line in build_image(config, Path(build_dir), no_cache=no_cache):
                if verbose:
                    click.echo(f"  {line}")
                elif line.startswith("Step "):
                    click.echo(f"[scad] {line}")
        click.echo(f"[scad] Built: {tag}")
        # After successful build, prune old images
        try:
            client = get_docker_client()
            new_image = client.images.get(tag)
            prune_old_images(client, config.name, new_image.id)
        except Exception:
            pass  # don't fail build over prune
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(3)


@main.group()
def vm():
    """Manage the scad Docker VM (macOS only)."""
    pass


@vm.command("start")
def vm_start_cmd():
    """Start the scad VM, creating it on first run."""
    try:
        vm_start()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    click.echo(f"[scad] VM '{SCAD_PROFILE}' running — socket: {colima_socket_path()}")


@vm.command("stop")
def vm_stop_cmd():
    """Stop the scad VM. Containers are preserved but not running."""
    try:
        vm_stop()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    click.echo(f"[scad] VM '{SCAD_PROFILE}' stopped")


@vm.command("status")
def vm_status_cmd():
    """Show whether scad's Docker daemon is reachable."""
    if not is_macos():
        try:
            get_docker_client()
            click.echo("[scad] linux — native Docker daemon: reachable")
        except docker.errors.DockerException as e:
            click.echo(f"[scad] linux — native Docker daemon: unreachable\n{e}")
        return
    state = vm_state()
    click.echo(f"[scad] VM '{SCAD_PROFILE}': {state}")
    if state == "absent":
        click.echo("[scad] Create it with: scad vm start")
    elif state == "stopped":
        click.echo("[scad] Start it with: scad vm start")


@vm.command("info")
def vm_info_cmd():
    """Show the scad VM's sizing, socket, and mounts."""
    if not is_macos():
        click.echo("[scad] linux — native Docker daemon, no scad VM")
        return
    try:
        info = vm_info()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    click.echo(f"[scad] Profile:    {info['profile']}")
    click.echo(f"[scad] State:      {info['state']}")
    click.echo(f"[scad] Socket:     {info['socket']}")
    click.echo(f"[scad] CPU:        {info['cpu']}")
    click.echo(f"[scad] Memory:     {info['memory_gib']} GiB")
    click.echo(f"[scad] Disk:       {info['disk_gib']} GiB")
    click.echo(f"[scad] VM type:    {info['vm_type']}")
    click.echo(f"[scad] Mount type: {info['mount_type']}")
    if info["mounts"]:
        click.echo("[scad] Extra mounts (beyond $HOME):")
        for m in info["mounts"]:
            click.echo(f"[scad]   {m}")
    else:
        click.echo("[scad] Extra mounts (beyond $HOME): none")


@vm.command("delete")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def vm_delete_cmd(yes: bool):
    """Delete the scad VM, including every image and container inside it."""
    if not yes:
        click.confirm(
            f"[scad] Delete VM '{SCAD_PROFILE}'? All scad images and containers "
            "inside it are destroyed.",
            abort=True,
        )
    try:
        vm_delete()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    click.echo(f"[scad] VM '{SCAD_PROFILE}' deleted")


@run.command("info")
@click.argument("run_id", shell_complete=_complete_run_ids)
def session_info(run_id: str):
    """Show a run's dashboard: clones, jobs, agent sessions, usage."""
    validate_run_id(run_id)
    try:
        info = get_session_info(run_id)
    except FileNotFoundError as e:
        click.echo(f"[scad] {e}", err=True)
        sys.exit(1)

    click.echo(f"Run ID:      {info['run_id']}")
    click.echo(f"Config:      {info.get('config', '?')}")
    click.echo(f"Branch:      {info.get('branch', '?')}")
    click.echo(f"Container:   {info.get('container', '?')}")

    if info.get("clones_path"):
        click.echo(f"Clones:      {info['clones_path']}")
        if info["clones"]:
            click.echo(f"             {', '.join(info['clones'])}")
    else:
        click.echo("Clones:      (cleaned)")

    click.echo()
    if info.get("claude_sessions"):
        click.echo("Claude sessions:")
        for s in info["claude_sessions"]:
            click.echo(f"  {s['id']} ({s['modified']})")
        if info.get("subagent_count", 0) > 0:
            click.echo(f"  ({info['subagent_count']} subagent session(s))")
    else:
        click.echo("Claude sessions: (none)")

    click.echo()
    if info.get("events"):
        click.echo("Events:")
        for e in info["events"]:
            click.echo(f"  {e}")
    else:
        click.echo("Events: (none)")

    # Usage (tokens primary, cost only if > 0)
    usage = get_session_usage(run_id)
    if usage:
        inp = usage.get("total_input_tokens", 0)
        out = usage.get("total_output_tokens", 0)
        turns = usage.get("total_turns", 0)
        cost = usage.get("total_cost", 0)
        cache_create = usage.get("cache_creation_tokens", 0)
        cache_read = usage.get("cache_read_tokens", 0)
        usage_str = f"{inp:,} input / {out:,} output tokens, {turns} turns"
        if cache_create or cache_read:
            usage_str += f" (cache: {cache_create:,} create, {cache_read:,} read)"
        if cost > 0:
            usage_str += f" (${cost:.2f})"
        click.echo()
        click.echo(f"Usage:       {usage_str}")


@run.command("logs")
@click.argument("run_id", shell_complete=_complete_run_ids)
@click.option("--follow", "-f", is_flag=True, help="Stream logs as they are written.")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100).")
@click.option("--stream", "-s", is_flag=True, help="Show Claude stream (tool calls, edits) instead of entrypoint log.")
@click.option("--job", default=None, help="Show logs for a specific job ID.")
def session_logs(run_id: str, follow: bool, lines: int, stream: bool, job: str):
    """Read agent log output."""
    validate_run_id(run_id)
    logs_dir = SCAD_DIR / "logs"
    if job:
        log_path = logs_dir / f"{job}.stream.jsonl"
        not_found_msg = f"No stream log found for job {job}"
    elif stream:
        log_path = logs_dir / f"{run_id}.stream.jsonl"
        not_found_msg = f"No stream log found for {run_id}"
    else:
        log_path = logs_dir / f"{run_id}.log"
        not_found_msg = f"No log file found for {run_id}"

    if not log_path.exists():
        click.echo(f"[scad] {not_found_msg}", err=True)
        sys.exit(1)

    # --job without --stream: human-readable activity summary
    if job and not stream:
        import json as _json
        lines_out = []
        result_text = None
        is_error = False
        for line in log_path.read_text().splitlines():
            try:
                record = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            # Tool activity
            for tl in _format_tool_line(record):
                lines_out.append(tl)
            # Final result
            if record.get("type") == "result":
                is_error = record.get("is_error", False)
                result_text = record.get("result", "")

        if lines_out:
            for ln in lines_out:
                click.echo(ln)
            click.echo()

        if result_text is not None:
            if is_error:
                click.echo(f"[scad] Job failed: {result_text}", err=True)
            else:
                click.echo(f"[scad] Result: {result_text}")
        else:
            click.echo("[scad] Job still running (no result yet).", err=True)
        return

    if follow:
        import subprocess
        try:
            subprocess.run(["tail", "-f", str(log_path)])
        except KeyboardInterrupt:
            pass
    else:
        text = log_path.read_text()
        output_lines = text.splitlines()
        for line in output_lines[-lines:]:
            click.echo(line)


@run.command("stop")
@click.argument("run_id", required=False, shell_complete=_complete_running_sessions)
@click.option("--all", "stop_all", is_flag=True, help="Stop all running sessions.")
@click.option("--config", "config_name", help="Stop all sessions for this config.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def session_stop(run_id, stop_all, config_name, yes):
    """Stop a run's container, preserving clones and state."""
    if run_id and (stop_all or config_name):
        raise click.ClickException("Cannot use run_id with --all or --config.")
    if not run_id and not stop_all and not config_name:
        raise click.ClickException("Provide a run_id, --all, or --config.")

    if run_id:
        validate_run_id(run_id)
        if stop_container(run_id):
            log_event(run_id, "stop")
            click.echo(f"[scad] Stopped: {run_id}")
        else:
            click.echo(f"[scad] No running container found for {run_id}", err=True)
            sys.exit(1)
    else:
        sessions = get_all_sessions()
        targets = [s for s in sessions if s["container"] == "running"]
        if config_name:
            targets = [s for s in targets if s["config"] == config_name]
        if not targets:
            click.echo("[scad] No running sessions to stop.")
            return
        if not yes:
            click.echo(f"[scad] Will stop {len(targets)} session(s):")
            for t in targets:
                click.echo(f"  {t['run_id']}")
            if not click.confirm("Proceed?"):
                return
        for t in targets:
            stop_container(t["run_id"])
            log_event(t["run_id"], "stop")
            click.echo(f"[scad] Stopped: {t['run_id']}")


@run.command("attach")
@click.argument("run_id", shell_complete=_complete_running_sessions)
def session_attach(run_id: str):
    """Attach to a run's tmux session."""
    validate_run_id(run_id)
    container_name = f"scad-{run_id}"
    try:
        client = get_docker_client()
        container = client.containers.get(container_name)
    except docker.errors.NotFound:
        click.echo(f"[scad] No container found for {run_id}", err=True)
        sys.exit(1)
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(1)

    if container.status != "running":
        click.echo(f"[scad] Container not running: {run_id}", err=True)
        sys.exit(1)

    check = container.exec_run("tmux has-session -t scad")
    if check.exit_code != 0:
        click.echo(
            f"[scad] No tmux session in '{run_id}'. "
            f"Inject work first: scad run inject {run_id} --prompt \"...\"",
            err=True,
        )
        sys.exit(1)

    log_event(run_id, "attach")
    result = _subprocess.run(
        ["docker", "exec", "-it", container_name, "tmux", "attach", "-t", "scad"],
        env=docker_cli_env(),
    )
    sys.exit(result.returncode)


@run.command("inject")
@click.argument("run_id", shell_complete=_complete_running_sessions)
@click.option("--prompt", required=True, help="Prompt to send to Claude.")
@click.option("--headless", is_flag=True, help="Fire-and-forget mode (claude -p).")
@click.option("--branch", default=None, help="Create/checkout branch before running.")
@click.option("--wait", is_flag=True, help="Block until Claude finishes (headless only).")
@click.option("--tail", is_flag=True, help="Stream Claude's activity during --wait.")
def session_inject(run_id: str, prompt: str, headless: bool, branch: str, wait: bool, tail: bool):
    """Inject a job — one Claude process — into a running run."""
    validate_run_id(run_id)
    config = _config_for_run(run_id)

    # --tail requires --wait
    if tail and not wait:
        click.echo("[scad] Error: --tail requires --wait", err=True)
        sys.exit(1)

    # --wait implies headless
    if wait and not headless:
        headless = True

    # Build add_dirs list from config
    add_dirs = [key for key, repo in config.repos.items() if repo.add_dir]

    # Common kwargs for inject_job
    inject_kwargs = dict(
        run_id=run_id,
        prompt=prompt,
        headless=headless,
        workdir_key=config.workdir_key,
        branch=branch,
        add_dirs=add_dirs,
        dangerously_skip_permissions=config.claude.dangerously_skip_permissions,
        additional_flags=config.claude.additional_flags,
        wait=wait,
    )

    # inject_job blocks when wait=True, so we run it in a background thread
    # and tail the stream file on the main thread.
    stop_event = None

    if tail:
        stop_event = threading.Event()
        inject_result = [None, None]  # [result, exception]

        def _run_inject():
            try:
                inject_result[0] = inject_job(**inject_kwargs)
            except (RuntimeError, ValueError) as e:
                inject_result[1] = e
            finally:
                stop_event.set()

        inject_thread = threading.Thread(target=_run_inject, daemon=True)
        inject_thread.start()

        # Wait for inject_job to create the job and start claude
        inject_thread.join(timeout=2.0)

        # Find the newest stream.jsonl for this run
        logs_dir = SCAD_DIR / "logs"
        stream_path = None
        if logs_dir.exists():
            candidates = sorted(logs_dir.glob(f"{run_id}-job-*.stream.jsonl"))
            if candidates:
                stream_path = candidates[-1]

        if stream_path:
            _tail_stream(stream_path, stop_event)
        else:
            click.echo("[scad] Waiting for job to complete...", err=True)
            stop_event.wait()

        inject_thread.join()

        if inject_result[1]:
            click.echo(f"[scad] Error: {inject_result[1]}", err=True)
            sys.exit(1)

        result = inject_result[0]
    else:
        try:
            result = inject_job(**inject_kwargs)
        except (RuntimeError, ValueError) as e:
            click.echo(f"[scad] Error: {e}", err=True)
            sys.exit(1)

    if wait:
        job_id, exit_code = result
        click.echo(f"[scad] Completed: {job_id} (exit code {exit_code})")

        # Parse stream.jsonl for final result (skip if --tail already showed activity)
        logs_dir = SCAD_DIR / "logs"
        stream_path = logs_dir / f"{job_id}.stream.jsonl"
        if stream_path.exists():
            for line in stream_path.read_text().splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "result":
                    if record.get("is_error"):
                        click.echo(f"[scad] Error: {record.get('result', 'unknown error')}", err=True)
                    else:
                        click.echo(record.get("result", ""))
                    break

        sys.exit(exit_code)
    else:
        job_id = result
        mode = "headless" if headless else "interactive"
        click.echo(f"[scad] Injected {mode}: {job_id}")
        if headless:
            click.echo(f"[scad]   Stream log: scad run logs {run_id} --stream --job {job_id}")
        else:
            click.echo(f"[scad]   Attach: scad run attach {run_id}")


@run.command("jobs")
@click.argument("run_id", shell_complete=_complete_running_sessions)
def session_jobs(run_id: str):
    """List a run's jobs."""
    validate_run_id(run_id)
    jobs = list_jobs(run_id)

    if not jobs:
        click.echo("[scad] No jobs found.")
        return

    # Header
    click.echo(f"{'JOB ID':<35} {'MODE':<12} {'BRANCH':<25} {'STARTED'}")
    for job in jobs:
        branch = job.get("branch") or "—"
        started = _relative_time(job.get("started", ""))
        click.echo(f"{job['job_id']:<35} {job['mode']:<12} {branch:<25} {started}")


@run.command("send")
@click.argument("run_id", shell_complete=_complete_running_sessions)
@click.argument("text")
@click.option("--job", "job_id", default=None, help="Target a specific job (required if multiple interactive jobs).")
def session_send(run_id: str, text: str, job_id: str):
    """Send input to a running interactive Claude."""
    validate_run_id(run_id)
    try:
        send_to_job(run_id, text, job_id=job_id)
        click.echo(f"[scad] Sent to {run_id}" + (f" (job {job_id})" if job_id else ""))
    except RuntimeError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)


@run.command("clean")
@click.argument("run_id", required=False, shell_complete=_complete_cleanable_sessions)
@click.option("--all", "clean_all", is_flag=True, help="Clean all sessions.")
@click.option("--config", "config_name", help="Clean all sessions for this config.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@click.option("--force", is_flag=True, help="Include running sessions (dangerous).")
def session_clean(run_id, clean_all, config_name, yes, force):
    """Remove container, clones, and run data for a completed run."""
    if run_id and (clean_all or config_name):
        raise click.ClickException("Cannot use run_id with --all or --config.")
    if not run_id and not clean_all and not config_name:
        raise click.ClickException("Provide a run_id, --all, or --config.")

    if run_id:
        validate_run_id(run_id)
        clean_run(run_id)
        click.echo(f"[scad] Cleaned: {run_id}")
    else:
        sessions = get_all_sessions()
        if config_name:
            sessions = [s for s in sessions if s["config"] == config_name]
        if not force:
            sessions = [s for s in sessions if s["container"] != "running"]
        if not sessions:
            click.echo("[scad] No sessions to clean.")
            return
        if not yes:
            click.echo(f"[scad] Will clean {len(sessions)} session(s):")
            for s in sessions:
                click.echo(f"  {s['run_id']} ({s['container']})")
            if not click.confirm("Proceed?"):
                return
        for s in sessions:
            clean_run(s["run_id"])
            click.echo(f"[scad] Cleaned: {s['run_id']}")


def _config_for_run(run_id: str) -> "ScadConfig":
    """Load the config associated with a run ID."""
    config_name = config_name_for_run(run_id)
    if not config_name:
        raise click.ClickException(f"Cannot determine config from run ID: {run_id}")
    return load_config(config_name)


@code.command("fetch")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
def code_fetch(run_id: str):
    """Fetch branches from clones back to source repos."""
    validate_run_id(run_id)
    try:
        config = _config_for_run(run_id)
        results = fetch_to_host(run_id, config)
        if not results:
            click.echo(f"[scad] Nothing to fetch for {run_id}")
        else:
            for r in results:
                click.echo(f"[scad] Fetched {r['repo']}: {r['branch']} → {r['source']}")
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)


@code.command("sync")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.option("--checkout", default=None, help="Checkout this branch after sync.")
@click.option("--no-update-main", is_flag=True, help="Skip fast-forwarding clone's main.")
def code_sync(run_id: str, checkout: str, no_update_main: bool):
    """Sync host repo changes into clones. Fast-forwards main by default."""
    validate_run_id(run_id)
    try:
        config = _config_for_run(run_id)
        results = sync_from_host(run_id, config, update_main=not no_update_main, checkout=checkout)
        if not results:
            click.echo(f"[scad] Nothing to sync for {run_id}")
        else:
            for r in results:
                msg = f"[scad] Synced {r['repo']} from {r['source']}"
                if r.get("main_updated") is True:
                    msg += " (main fast-forwarded)"
                elif r.get("main_updated") is False:
                    msg += " (main diverged \u2014 skipped)"
                click.echo(msg)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)


@code.command("diff")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
def code_diff(run_id: str):
    """Show diff between session clones and source repos."""
    validate_run_id(run_id)
    config = _config_for_run(run_id)

    try:
        diffs = diff_from_source(run_id, config)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)

    if not diffs:
        click.echo("[scad] No differences.")
        return

    for repo, diff_text in diffs.items():
        click.echo(f"\n--- {repo} ---")
        click.echo(diff_text)


@code.command("branch")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.argument("branch_name")
def code_branch(run_id: str, branch_name: str):
    """Create and switch to a branch in all clone repos."""
    validate_run_id(run_id)
    try:
        created = create_branch(run_id, branch_name)
        if created:
            for repo in created:
                click.echo(f"[scad] Branch '{branch_name}' in {repo}")
        else:
            click.echo("[scad] No clone repos found in workspace.")
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        click.echo(f"[scad] Git error: {e.stderr.decode().strip()}", err=True)
        sys.exit(1)


@run.command("refresh")
@click.argument("run_id", shell_complete=_complete_running_sessions)
def session_refresh(run_id: str):
    """Push fresh credentials into a running container."""
    validate_run_id(run_id)
    try:
        hours = refresh_credentials(run_id)
        h = int(hours)
        m = int((hours - h) * 60)
        click.echo(f"[scad] Credentials refreshed. Time remaining: {h}h {m:02d}m")
    except click.ClickException as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(1)


@code.command("add")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.option("--path", required=True, help="Host path to add.")
@click.option("--name", required=True, help="Name in workspace/.")
@click.option("--clone", is_flag=True, help="Git clone instead of symlink.")
@click.option(
    "--restart-vm",
    is_flag=True,
    help="macOS: add the path to the scad VM and restart it (stops running sessions).",
)
def code_add(run_id: str, path: str, name: str, clone: bool, restart_vm: bool):
    """Add a directory to a session's workspace."""
    validate_run_id(run_id)

    # Validate cheaply before doing anything destructive: a name collision is
    # a plain FileExistsError from workspace_add() below, but the VM restart
    # further down stops *every* running scad session. Checking first means a
    # doomed `--name` that already exists never pays that cost.
    if workspace_name_taken(run_id, name):
        click.echo(f"[scad] Error: '{name}' already exists in workspace", err=True)
        sys.exit(1)

    # A VM mount can only be added at (re)start, so a non-$HOME path on macOS
    # cannot be hot-added — without this check the container would see an empty
    # directory rather than the data.
    if not path_visible_in_vm(Path(path).expanduser()):
        target = str(mount_root(Path(path).expanduser()))
        click.echo(
            f"[scad] '{target}' is not visible inside the scad VM.\n"
            "[scad] It lives outside $HOME, and VM mounts can only be added at "
            "restart.\n"
            "[scad] Restarting the VM stops every running scad session; scad will "
            f"restart this one ({run_id}) afterwards.",
            err=True,
        )
        if not restart_vm:
            click.confirm("[scad] Add the mount and restart the VM now?", abort=True)

        mounts = sorted(set(read_vm_mounts()) | {target})
        try:
            if vm_state() == "running":
                vm_stop()
            vm_start(mounts=mounts)
        except VMUnsupported as e:
            click.echo(f"[scad] {e.message}", err=True)
            sys.exit(2)
        click.echo(f"[scad] VM restarted with {target} mounted")

        try:
            get_docker_client().containers.get(f"scad-{run_id}").start()
            click.echo(f"[scad] Restarted session container: {run_id}")
        except docker.errors.DockerException as e:
            click.echo(
                f"[scad] Could not restart the session container: {e}\n"
                f"[scad] Check it with: scad run ls",
                err=True,
            )

    try:
        workspace_add(run_id, path, name, clone=clone)
        mode = "cloned" if clone else "symlinked"
        click.echo(f"[scad] Added {name} ({mode}): {path}")
    except FileExistsError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)


@code.command("remove")
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.option("--name", required=True, help="Name to remove from workspace/.")
def code_remove(run_id: str, name: str):
    """Remove a directory from a session's workspace."""
    validate_run_id(run_id)
    try:
        workspace_remove(run_id, name)
        click.echo(f"[scad] Removed {name} from workspace")
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)


@main.command("gc")
@click.option("--force", is_flag=True, help="Actually clean (default is dry-run).")
def gc_cmd(force: bool):
    """Find and clean orphaned containers, run dirs, and images."""
    findings = gc(force=force)

    mode = "Cleaning" if force else "Garbage collection (dry run)"
    click.echo(f"[scad] {mode}")

    containers = findings["orphaned_containers"]
    dirs = findings["dead_run_dirs"]
    images = findings["unused_images"]

    if containers:
        click.echo(f"  Orphaned containers: {len(containers)}")
        for c in containers:
            click.echo(f"    {c['name']} ({c['status']})")
    if dirs:
        click.echo(f"  Dead run dirs: {len(dirs)}")
        for d in dirs:
            click.echo(f"    {d}")
    if images:
        click.echo(f"  Unused images: {len(images)}")
        for img in images:
            click.echo(f"    {', '.join(img['tags'])} ({img['id']})")

    if not containers and not dirs and not images:
        click.echo("  Nothing to clean.")
    elif not force:
        click.echo("\nRun with --force to clean up.")


@main.command()
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("--tag", required=True, help="Session tag.")
@click.option("--prompt-file", required=True, type=click.Path(exists=False), help="Path to ---delimited prompt file.")
@click.option("--parallel", default=3, type=int, help="Max concurrent jobs (default: 3).")
@click.option("--fail-fast", is_flag=True, help="Stop queuing on first failure.")
@click.option("--no-build", is_flag=True, help="Skip image build check.")
def batch(config_name, tag, prompt_file, parallel, fail_fast, no_build):
    """Run parallel headless jobs from a prompt file."""
    # --- Parse prompts ---
    try:
        prompts = parse_prompt_file(Path(prompt_file))
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)

    if not prompts:
        click.echo("[scad] Error: prompt file is empty", err=True)
        sys.exit(2)

    # --- Load config ---
    try:
        config = load_config(config_name)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)

    # --- Pre-flight: platform capability, then VM (mirrors run_agent order) ---
    try:
        ensure_gpu_supported(config)
        ensure_vm_running()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)

    # --- Auth check ---
    valid, hours = check_claude_auth()
    if not valid:
        raise click.ClickException("Claude auth expired or missing. Run: claude /login")

    # --- Build if needed ---
    if not no_build and not image_exists(config):
        image_tag = f"scad-{config.name}"
        click.echo(f"[scad] Building image {image_tag}...")
        with tempfile.TemporaryDirectory() as build_dir:
            for line in build_image(config, Path(build_dir)):
                if line.startswith("Step "):
                    click.echo(f"[scad] {line}")
        click.echo(f"[scad] Image built: {image_tag}")

    # --- Start session ---
    try:
        branch = resolve_branch(config, None, tag)
        run_id = run_agent(config, branch=branch, tag=tag)
        log_event(run_id, "start", f"config={config.name} branch={branch} mode=batch")
    except Exception as e:
        click.echo(f"[scad] Error starting session: {e}", err=True)
        sys.exit(2)

    time.sleep(1)
    click.echo(f"[scad] Batch: {len(prompts)} jobs, parallel={parallel}")

    # --- Common inject kwargs ---
    add_dirs = [key for key, repo in config.repos.items() if repo.add_dir]

    # --- Run jobs in parallel ---
    results = []  # list of (prompt_idx, job_id, exit_code)
    failed = False

    def _run_one(idx, prompt_text):
        job_id, exit_code = inject_job(
            run_id=run_id,
            prompt=prompt_text,
            headless=True,
            workdir_key=config.workdir_key,
            add_dirs=add_dirs,
            dangerously_skip_permissions=config.claude.dangerously_skip_permissions,
            additional_flags=config.claude.additional_flags,
            wait=True,
        )
        return (idx, job_id, exit_code)

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {}
        for idx, prompt_text in enumerate(prompts):
            if fail_fast and failed:
                break
            future = executor.submit(_run_one, idx, prompt_text)
            futures[future] = idx

        done_count = 0
        for future in as_completed(futures):
            idx, job_id, exit_code = future.result()
            done_count += 1
            status_icon = "pass" if exit_code == 0 else "FAIL"
            click.echo(
                f"[scad] [{done_count}/{len(prompts)} complete] "
                f"{job_id} {status_icon} (exit {exit_code})"
            )
            results.append((idx, job_id, exit_code))
            if exit_code != 0 and fail_fast:
                failed = True
                executor.shutdown(wait=False, cancel_futures=True)
                break

    # --- Summary ---
    passed = sum(1 for _, _, ec in results if ec == 0)
    failed_count = sum(1 for _, _, ec in results if ec != 0)
    click.echo()
    click.echo(f"[scad] Results: {passed} passed, {failed_count} failed")
    if failed_count:
        for idx, job_id, ec in results:
            if ec != 0:
                click.echo(f"[scad] Failed: {job_id} (exit {ec})")
    click.echo(f"[scad] Session: {run_id}")


@main.command()
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("--tag", required=True, help="Session tag.")
@click.option("--prompt", default=None, help="Prompt to send to Claude.")
@click.option("--plan", "plan_path", default=None, type=click.Path(exists=True), help="Plan file — auto-generates execution prompt.")
@click.option("--no-wait", is_flag=True, help="Fire-and-forget (don't block).")
@click.option("--headless", is_flag=True, help="Headless mode (claude -p). Default is interactive.")
@click.option("--attach", is_flag=True, help="Attach after interactive inject.")
@click.option("--fetch", is_flag=True, help="Auto-fetch results after completion (implies --headless --wait).")
@click.option("--no-build", is_flag=True, help="Skip image build check.")
@click.option("--tail", is_flag=True, help="Stream Claude activity during wait.")
def dispatch(config_name, tag, prompt, plan_path, no_wait, headless, attach, fetch, no_build, tail):
    """Start a run and dispatch work. Composites: build -> run start -> inject."""
    # --- Plan/prompt validation ---
    if plan_path and prompt:
        raise click.ClickException("--plan and --prompt are mutually exclusive.")
    if not plan_path and not prompt:
        raise click.ClickException("Either --prompt or --plan is required.")

    # --- Flag validation ---
    if fetch and no_wait:
        raise click.ClickException("--fetch and --no-wait are mutually exclusive.")
    if attach and headless:
        raise click.ClickException("--attach and --headless are mutually exclusive.")
    if attach and no_wait:
        raise click.ClickException("--attach and --no-wait are mutually exclusive.")
    if no_wait and not headless:
        raise click.ClickException("--no-wait requires --headless.")

    # --- Determine wait ---
    if fetch:
        headless = True
        wait = True
    else:
        wait = headless and not no_wait

    # --- Load config ---
    try:
        config = load_config(config_name)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"[scad] Config validation error: {e}", err=True)
        sys.exit(2)

    # --- Pre-flight: platform capability, then VM (mirrors run_agent order) ---
    try:
        ensure_gpu_supported(config)
        ensure_vm_running()
    except VMUnsupported as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)

    # --- Pre-flight: auth check ---
    valid, hours = check_claude_auth()
    if not valid:
        raise click.ClickException(
            "Claude auth expired or missing. Run: claude /login"
        )
    if hours < 1.0:
        click.echo(
            f"[scad] Warning: Claude auth expires in {hours * 60:.0f} minutes. "
            f"Consider running: claude /login"
        )

    # --- Build image if needed ---
    if not no_build and not image_exists(config):
        image_tag = f"scad-{config.name}"
        click.echo(f"[scad] Building image {image_tag}...")
        with tempfile.TemporaryDirectory() as build_dir:
            for line in build_image(config, Path(build_dir)):
                if line.startswith("Step "):
                    click.echo(f"[scad] {line}")
        click.echo(f"[scad] Image built: {image_tag}")

    # --- Start session (container + clones) ---
    try:
        branch = resolve_branch(config, None, tag)
        run_id = run_agent(config, branch=branch, tag=tag)
        log_event(run_id, "start", f"config={config.name} branch={branch} mode=dispatch")
    except click.ClickException as e:
        click.echo(f"[scad] {e.message}", err=True)
        sys.exit(2)
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(3)

    # --- Wait for entrypoint setup ---
    time.sleep(1)

    # --- Plan file resolution (after workspace exists) ---
    if plan_path:
        plan_host = Path(plan_path).resolve()
        container_path = None
        for key, repo in config.repos.items():
            try:
                relative = plan_host.relative_to(repo.resolved_path)
                container_path = f"/workspace/{key}/{relative}"
                break
            except ValueError:
                continue
        if not container_path:
            raise click.ClickException(
                f"Plan file {plan_host} is not inside any configured repo. "
                f"Add the repo containing this plan to your config."
            )
        prompt = (
            f"Load the executing-plans skill. Execute the plan at {container_path}. "
            f"Use subagent-driven development. Get through all tasks without waiting for feedback. "
            f"If you have any questions, ask upfront. Otherwise, continue and execute till the end."
        )

    # --- Inject work ---
    add_dirs = [key for key, repo in config.repos.items() if repo.add_dir]

    inject_kwargs = dict(
        run_id=run_id,
        prompt=prompt,
        headless=headless,
        workdir_key=config.workdir_key,
        add_dirs=add_dirs,
        dangerously_skip_permissions=config.claude.dangerously_skip_permissions,
        additional_flags=config.claude.additional_flags,
        wait=wait,
    )

    try:
        result = inject_job(**inject_kwargs)
    except (RuntimeError, ValueError) as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(1)

    # --- Handle results ---
    if wait:
        job_id, exit_code = result
        click.echo(f"[scad] Completed: {job_id} (exit code {exit_code})")

        # Parse stream.jsonl for final result
        logs_dir = SCAD_DIR / "logs"
        stream_path = logs_dir / f"{job_id}.stream.jsonl"
        if stream_path.exists():
            for line in stream_path.read_text().splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "result":
                    if record.get("is_error"):
                        click.echo(f"[scad] Error: {record.get('result', 'unknown error')}", err=True)
                    else:
                        click.echo(record.get("result", ""))
                    break

        # Auto-fetch if requested
        if fetch:
            try:
                results = fetch_to_host(run_id, config)
                if results:
                    for r in results:
                        click.echo(f"[scad] Fetched {r['repo']}: {r['branch']} -> {r['source']}")
                else:
                    click.echo(f"[scad] Nothing to fetch for {run_id}")
            except Exception as e:
                click.echo(f"[scad] Fetch error: {e}", err=True)

        if not fetch:
            click.echo(f"[scad] Harvest results: scad code fetch {run_id}")
    elif not headless and attach:
        click.echo(f"[scad] Attaching to session {run_id}...")
        container_name = f"scad-{run_id}"
        attach_result = _subprocess.run(
            ["docker", "exec", "-it", container_name, "tmux", "attach", "-t", "scad"],
            env=docker_cli_env(),
        )
        sys.exit(attach_result.returncode)
    else:
        job_id = result
        mode = "headless" if headless else "interactive"
        click.echo(f"[scad] Dispatched {mode}: {job_id}")
        click.echo(f"[scad]   Session: {run_id}")
        if headless:
            click.echo(f"[scad]   Stream log: scad run logs {run_id} --stream --job {job_id}")
        else:
            click.echo(f"[scad]   Attach: scad run attach {run_id}")


@main.command()
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.option("--diff", is_flag=True, help="Show full diff instead of git log.")
@click.option("--merge", is_flag=True, help="Merge fetched branches into source (ff-only).")
def harvest(run_id: str, diff: bool, merge: bool):
    """Fetch branches + show summary. The 'what did Claude produce?' command."""
    validate_run_id(run_id)
    config = _config_for_run(run_id)

    # Fetch
    results = fetch_to_host(run_id, config)
    if results:
        for r in results:
            if r.get("failed"):
                click.echo(f"[scad] Fetch FAILED {r['repo']}: {r['error']}", err=True)
            else:
                click.echo(f"[scad] Fetched {r['repo']}: {r['branch']} \u2192 {r['source']}")
    else:
        click.echo(f"[scad] Nothing to fetch for {run_id}")

    # Merge (ff-only) — only on successful fetches
    successful = [r for r in results if not r.get("failed")]
    if merge and successful:
        merge_results = merge_fetched_branches(successful, run_id)
        for m in merge_results:
            if m["status"] == "merged":
                click.echo(f"[scad] Merged {m['repo']}: {m['detail']}")
            else:
                click.echo(f"[scad] Skipped {m['repo']}: {m['detail']}")

    # Summary
    try:
        if diff:
            diffs = diff_from_source(run_id, config)
            if diffs:
                click.echo()
                for repo, diff_text in diffs.items():
                    click.echo(f"--- {repo} ---")
                    click.echo(diff_text)
        else:
            logs = log_from_source(run_id, config)
            if logs:
                click.echo()
                for repo, log_text in logs.items():
                    click.echo(f"--- {repo} ---")
                    click.echo(log_text)
    except FileNotFoundError:
        pass  # Clones already cleaned — skip


@main.command()
@click.argument("run_id", shell_complete=_complete_sessions_with_clones)
@click.option("--no-fetch", is_flag=True, help="Skip fetching (risk losing unfetched branches).")
@click.option("--merge", is_flag=True, help="Merge fetched branches into source (ff-only).")
@click.option("--keep-session", is_flag=True, help="Keep the session after fetching.")
@click.option("--force", is_flag=True, help="Clean even if session is running.")
def finish(run_id: str, no_fetch: bool, merge: bool, keep_session: bool, force: bool):
    """Fetch branches + clean the run. The 'I'm done' command."""
    validate_run_id(run_id)
    config = _config_for_run(run_id)

    # Fetch (unless skipped)
    if not no_fetch:
        results = fetch_to_host(run_id, config)
        if results:
            for r in results:
                if r.get("failed"):
                    click.echo(f"[scad] Fetch FAILED {r['repo']}: {r['error']}", err=True)
                else:
                    click.echo(f"[scad] Fetched {r['repo']}: {r['branch']} \u2192 {r['source']}")
        successful = [r for r in results if not r.get("failed")]
        if merge and successful:
            merge_results = merge_fetched_branches(successful, run_id)
            for m in merge_results:
                if m["status"] == "merged":
                    click.echo(f"[scad] Merged {m['repo']}: {m['detail']}")
                else:
                    click.echo(f"[scad] Skipped {m['repo']}: {m['detail']}")
        try:
            diffs = diff_from_source(run_id, config)
            if diffs:
                click.echo()
                click.echo("[scad] Changes saved:")
                for repo, diff_text in diffs.items():
                    added = diff_text.count("\n+") - diff_text.count("\n+++")
                    removed = diff_text.count("\n-") - diff_text.count("\n---")
                    click.echo(f"  {repo}: +{added} -{removed}")
        except FileNotFoundError:
            pass

    # Clean (unless --keep-session)
    if not keep_session:
        clean_run(run_id)
        click.echo(f"[scad] Cleaned: {run_id}")
    else:
        click.echo(f"[scad] Session kept: {run_id}")


@main.command()
@click.option("--run", "run_id", default=None, help="Archive only this run's traces.")
@click.option("--json", "as_json", is_flag=True, help="Emit counts as JSON on stdout.")
def archive(run_id, as_json):
    """Copy agent traces into the append-only archive.

    Agents prune their own transcripts and `scad run clean` destroys a run's
    traces outright. This copies them somewhere nothing deletes them. Safe to run
    repeatedly: unchanged files are skipped and nothing is ever overwritten.
    """
    results = archive_run(run_id) if run_id else archive_all()
    counts = summarize(results)

    if as_json:
        click.echo(json.dumps({"archive_root": str(archive_root()), "counts": counts}))
        return

    click.echo(f"[scad] Archive: {archive_root()}")
    if not counts:
        click.echo("[scad] Nothing to archive.")
        return
    for action in ("created", "appended", "skipped", "forked"):
        if action in counts:
            click.echo(f"[scad]   {action}: {counts[action]}")
    if "forked" in counts:
        click.echo("[scad] Forked files were rewritten at source; both copies kept.")


@main.command()
@click.option("--rebuild", is_flag=True, help="Drop and rebuild from the archive.")
@click.option("--force", is_flag=True, help="Allow --rebuild when raw is missing (destructive).")
@click.option("--no-archive", is_flag=True, help="Skip the archive sweep and index what is already archived.")
def reindex(rebuild, force, no_archive):
    """Archive new traces, then rebuild the session index from the archive."""
    stats = run_reindex(rebuild=rebuild, force=force, archive_first=not no_archive)
    if not stats:
        click.echo("[scad] Nothing indexed — is the archive empty? Run: scad archive")
        return
    for key in ("files", "sessions", "turns", "notes", "named",
                "skipped_lines", "skipped_files"):
        if stats.get(key):
            click.echo(f"[scad]   {key}: {stats[key]}")


@session.command("ls")
@click.option("--project", default=None, help="Filter by resolved project.")
@click.option("--agent", default=None, help="Filter by agent (claude, codex, kimi).")
@click.option("--kind", default=None, help="Filter by kind (main, subagent, workflow-agent).")
@click.option("--machine", default=None, help="Filter by machine.")
@click.option("--grade", default=None, type=click.Choice(["full", "skeleton"]),
              help="full = has turns; skeleton = known only from history.jsonl.")
@click.option("--outcome", default=None,
              type=click.Choice(["awaiting-question", "awaiting-user", "interrupted",
                                 "in-flight", "tool-result-last", "user-last"]),
              help="Terminal state — e.g. --outcome awaiting-question for sessions asking you something.")
@click.option("--since", default=None, help="Only sessions started on/after YYYY-MM-DD.")
@click.option("--until", default=None, help="Only sessions started before YYYY-MM-DD.")
@click.option("--limit", default=40, help="Rows to show.")
@click.option("--json", "as_json", is_flag=True, help="Emit rows as JSON.")
def session_ls(project, agent, kind, machine, grade, outcome, since, until, limit, as_json):
    """List indexed sessions, newest first."""
    conn = index_connect()
    where, params = [], []
    for column, value in (("project", project), ("agent", agent), ("kind", kind),
                          ("machine", machine), ("grade", grade), ("outcome", outcome)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    for column, value, op in (("started", since, ">="), ("started", until, "<")):
        if value:
            where.append(f"{column} {op} ?")
            params.append(_day_ms(value))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT id, name, harness_state, kind, agent, project, title, n_turns, "
        f"grade, outcome, started FROM sessions {clause} ORDER BY started DESC LIMIT ?",
        (*params, limit),
    ).fetchall()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], default=str))
        return
    if not rows:
        click.echo("[scad] No sessions. Run: scad archive && scad reindex")
        return
    for r in rows:
        when = datetime.fromtimestamp(r["started"] / 1000).strftime("%Y-%m-%d %H:%M") \
            if r["started"] else "?"
        title = (r["title"] or "")[:40]
        # Two columns, never one. The id is always shown because it is what
        # `--resume` takes. `name` is the human's own label — from `/rename` or
        # from the harness — and stays BLANK when nobody chose one: `title` is
        # derived (an agent's summary, or a first message verbatim) and printing
        # it here would dress a guess up as a name.
        name = (r["name"] or "")[:26]     # the longest real one is 26 characters
        click.echo(f"{r['id'][:12]:<14} {name:<27} {when}  {r['agent']:<7} "
                   f"{r['kind']:<14} {(r['project'] or '?'):<24} {r['n_turns']:>5}t  {title}")


@session.command("show")
@click.argument("session_id")
def session_show(session_id):
    """Show one session's metadata and turn breakdown."""
    conn = index_connect()
    row = session_row(conn, session_id)
    if row is None:
        raise click.ClickException(f"No session {session_id} in the index.")
    for field in ("id", "name", "kind", "agent", "machine", "project", "cwd", "title",
                  "git_branch", "grade", "source", "harness_state", "scad_run_id",
                  "parent_session_id", "workflow_id", "archive_path"):
        if row[field] is not None:
            click.echo(f"{field:<18} {row[field]}")
    # Model-written prose from the harness, not derived from the trace. Printed
    # last and labelled so it is not mistaken for structural evidence.
    for field in ("needs", "needs_detail"):
        if row[field] is not None:
            click.echo(f"{field:<18} {row[field]}")
    click.echo(f"{'turns':<18} {row['n_turns']}")
    kinds = conn.execute(
        "SELECT kind, count(*) n FROM turns WHERE session_id = ? GROUP BY kind ORDER BY n DESC",
        (session_id,),
    ).fetchall()
    for k in kinds:
        click.echo(f"  {k['kind']:<16} {k['n']}")
    kids = conn.execute(
        "SELECT count(*) n FROM sessions WHERE parent_session_id = ?", (session_id,)
    ).fetchone()["n"]
    if kids:
        click.echo(f"{'subagents':<18} {kids}")

    # The authored tier. Listed by topic rather than counted alone, because the
    # question a note answers is "what did I decide here", and a bare count
    # answers nothing. `scad session notes <id>` prints the text.
    notes = index_session_notes(conn, session_id)
    click.echo(f"{'notes':<18} {len(notes)}")
    for n in notes:
        click.echo(f"  [{n['idx']:>3}] {n['topic'] or '?':<24} {(n['title'] or '')[:48]}")


def _exec(argv: list[str]) -> None:
    """Replace this process with `argv`. Never returns.

    Its own function so the one call a test can never make is the one thing a
    test replaces. `execvp` rather than a subprocess: the caller lands directly
    in the agent, with no scad process left behind holding its stdio open.
    """
    os.execvp(argv[0], argv)


@session.command("resume")
@click.argument("session_id")
@click.option("--print", "print_only", is_flag=True,
              help="Emit the command instead of running it.")
def session_resume(session_id, print_only):
    """Go back into a session — attach if it is open, resume if it is closed.

    Works for every session the index has seen, not only the ones scad
    launched: the index already holds the agent, the cwd and the id, which is
    everything a resume command needs. A launch record adds the one thing the
    index cannot know — which pane the session is sitting in — and is otherwise
    optional.

    \b
    Three behaviours:
      open in a recorded pane   attach to it; never a second process on one id
      closed                    exec the agent, cwd set to where it ran
      --print                   emit the command; this is what the viewer copies
    """
    record = read_record(session_id) or {}
    row = session_row(index_connect(), session_id)
    if row is None and not record:
        raise click.ClickException(
            f"No session {session_id} in the index and no launch record. "
            f"Try: scad reindex")

    def field(name):
        value = row[name] if row is not None else None
        return value or record.get(name)

    kind = (row["kind"] if row is not None else None) or "main"
    if kind != "main":
        raise click.ClickException(
            f"{session_id} is a {kind} — it has no independent session to resume. "
            f"Resume the session that spawned it.")

    target = {"id": session_id, "kind": "main",
              "agent": field("agent"), "cwd": field("cwd")}
    argv = resume_argv(target)
    command = resume_command(target)

    if print_only:
        click.echo(command)
        return

    # The launch record is the only thing that can name the pane a specific
    # session id is in — a pane matched by cwd is "something is running here",
    # which is not the same session and would attach you to a stranger.
    pane_target = record.get("tmux")
    if pane_target and find_pane(pane_target, tmux_panes()) is not None:
        click.echo(f"[scad] {session_id} is open in {pane_target} — attaching.")
        _exec(attach_argv(pane_target))
        return

    # Proven live, nowhere to attach. The registry names the session exactly but
    # cannot name its window, and resuming would put a second writer on it.
    live = next((s for s in claude_live_sessions() if s.session_id == session_id), None)
    if live is not None:
        raise click.ClickException(
            f"{session_id} is running right now (pid {live.pid}) and scad cannot "
            f"tell which pane holds it, so resuming would start a second process "
            f"against a live session. Go to the window, or get the command with: "
            f"scad session resume {session_id} --print")

    cwd = target["cwd"]
    if cwd and Path(cwd).is_dir():
        os.chdir(cwd)
    elif cwd:
        # A recorded cwd outlives its directory. The conversation is still there.
        click.echo(f"[scad] {cwd} is gone — resuming from here instead.")
    click.echo(f"[scad] {command}")
    _exec(argv)


@session.command("note")
@click.option("--session", "session_id", default=None,
              help="Append to this session's note file.")
@click.option("--current", is_flag=True,
              help="Append to the session whose trace is being written in this cwd.")
@click.option("--agent", default="claude", help="Which agent's shard (claude, codex).")
def session_note(session_id, current, agent):
    """Append one /remember capture, read as JSON on stdin.

    The record is composed in-session, where the context already is — this only
    decides where it lands and appends it. That split is the point: judgment
    belongs to the agent holding the conversation, and store resolution belongs
    here, so codex and pi get the same store without reimplementing it in prose.
    """
    if bool(session_id) == bool(current):
        raise click.ClickException("Pass exactly one of --session <id> or --current.")

    if current:
        try:
            session_id = current_session_id(agent=agent)
        except NoteTargetError as exc:
            raise click.ClickException(str(exc)) from exc

    raw = sys.stdin.read()
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise click.ClickException(f"stdin is not valid JSON: {exc}") from exc

    try:
        path = append_note(record, session_id=session_id, agent=agent)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    # Confirm, do not echo: the caller just wrote the record and printing it back
    # into the transcript would double its cost in context for no information.
    rel = record.get("relation") or "?"
    if record.get("parent"):
        rel += f" <- {record['parent']}"
    click.echo(f"[scad] noted {session_id}  {record.get('topic') or '?'}  "
               f"{rel}  {len(record.get('tags') or [])} tags")
    click.echo(f"[scad] {path}")


@session.command("notes")
@click.argument("session_id")
@click.option("--agent", default="claude", help="Which agent's shard to read.")
@click.option("--json", "as_json", is_flag=True, help="Emit the records as written.")
def session_notes_cmd(session_id, agent, as_json):
    """Print a session's notes, oldest first.

    Reads the FILE, not the index. The file is truth, and a note must be
    readable before anything has been indexed and after a --rebuild has dropped
    every row.
    """
    path = note_path(session_id, agent)
    records = read_note_file(path)

    if as_json:
        click.echo(json.dumps(records, ensure_ascii=False, default=str))
        return
    if not records:
        click.echo(f"[scad] No notes for {session_id}.")
        return

    click.echo(f"[scad] {path}")
    for i, r in enumerate(records):
        head = f"[{i:>3}] {r.get('ts') or '?'}  {r.get('topic') or '?'}"
        if r.get("relation"):
            head += f"  ({r['relation']}" + (f" <- {r['parent']}" if r.get("parent") else "") + ")"
        click.echo(head)
        click.echo(f"      {r.get('title') or ''}")
        if r.get("text"):
            click.echo()
            for line in str(r["text"]).splitlines():
                click.echo(f"      {line}")
        for field in ("tags", "entities"):
            if r.get(field):
                click.echo(f"      {field}: {', '.join(str(v) for v in r[field])}")
        if r.get("invalidation"):
            click.echo(f"      invalidation: {r['invalidation']}")
        click.echo()


@main.group()
def notes():
    """Find and read notes — how a fresh session picks up where one left off.

    Deliberately two verbs. A session writes a note when it stops; the next one
    runs `notes ls --project <p>` to find it and `notes read <session-id>` to
    read it. That replaces a handoff document with something written by the
    same command every time, into a store that is already indexed and
    searchable, rather than a file whose name and location must be remembered.
    """
    pass


@notes.command("ls")
@click.option("--project", "project_name", default=None,
              help="Only notes from sessions in this project.")
@click.option("--session", "session_id", default=None, help="Only this session's notes.")
@click.option("--limit", default=20, help="How many, newest first.")
@click.option("--json", "as_json", is_flag=True, help="Emit as JSON.")
def notes_ls(project_name, session_id, limit, as_json):
    """List notes, newest first.

    Metadata only — the body is not in the index at all, so this can say what
    exists and never what it says. `notes read` is the second half.
    """
    conn = index_connect()
    where, params = [], []
    if project_name:
        where.append("s.project = ?")
        params.append(project_name)
    if session_id:
        where.append("n.session_id = ?")
        params.append(session_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = [dict(r) for r in conn.execute(
        "SELECT n.session_id, n.idx, n.ts, n.topic, n.relation, n.title, n.tags, "
        "       s.project, s.name, s.agent "
        "FROM notes n LEFT JOIN sessions s ON s.id = n.session_id "
        f"{clause} ORDER BY n.ts DESC LIMIT ?", (*params, limit))]

    if as_json:
        click.echo(json.dumps(rows, ensure_ascii=False, default=str))
        return
    if not rows:
        scope = f" for project '{project_name}'" if project_name else ""
        click.echo(f"[scad] No notes{scope}.")
        return

    for r in rows:
        # `ts` is epoch MILLISECONDS here, not the ISO string _relative_time
        # takes — the notes table stores what the CLI stamped, not what a
        # transcript recorded.
        when = (datetime.fromtimestamp(r["ts"] / 1000).strftime("%m-%d %H:%M")
                if r.get("ts") else "?")
        # The session id leads because it is the argument to the next command.
        click.echo(f'{r["session_id"]}  [{r["idx"]}]  {when:>14}  '
                   f'{(r.get("project") or "-"):22}  {(r.get("topic") or "-"):20}  '
                   f'{r.get("title") or ""}')
    click.echo(f"\n[scad] {len(rows)} note(s). Read one: scad notes read <session-id>")


@notes.command("read")
@click.argument("session_id")
@click.option("--last", is_flag=True, help="Only the newest note.")
@click.option("--idx", "idx", type=int, default=None, help="Only this note's index.")
@click.option("--agent", default="claude", help="Which agent's shard (claude, codex).")
@click.option("--json", "as_json", is_flag=True, help="Emit as JSON.")
@click.pass_context
def notes_read(ctx, session_id, last, idx, agent, as_json):
    """Print a session's notes — all of them, or one.

    Reads the FILE, not the index: the index stores no body text, so the file is
    the only place the narrative exists.

    `--last` / `--idx` are what make recall progressive. Catching up should cost
    what you actually need, not the whole length of the session that came
    before — read the newest, and go back only while `notes ls` says the thread
    continues.
    """
    if not last and idx is None:
        ctx.invoke(session_notes_cmd, session_id=session_id, agent=agent, as_json=as_json)
        return

    records = read_note_file(note_path(session_id, agent))
    if not records:
        click.echo(f"[scad] No notes for {session_id}.")
        return

    want = len(records) - 1 if last else idx
    if not 0 <= want < len(records):
        click.echo(f"[scad] There is no note [{want}] for {session_id} "
                   f"— it has {len(records)} (0..{len(records) - 1}).")
        return

    record = records[want]
    if as_json:
        click.echo(json.dumps(record, ensure_ascii=False, default=str))
        return

    click.echo(f"[scad] {note_path(session_id, agent)}  [{want}] of {len(records)}")
    head = f"[{want:>3}] {record.get('ts') or '?'}  {record.get('topic') or '?'}"
    if record.get("relation"):
        head += (f"  ({record['relation']}"
                 + (f" <- {record['parent']}" if record.get("parent") else "") + ")")
    click.echo(head)
    click.echo(f"      {record.get('title') or ''}")
    if record.get("text"):
        click.echo()
        for line in str(record["text"]).splitlines():
            click.echo(f"      {line}")
    for field in ("tags", "entities"):
        if record.get(field):
            click.echo(f"      {field}: {', '.join(str(v) for v in record[field])}")
    if record.get("invalidation"):
        click.echo(f"      invalidation: {record['invalidation']}")


@main.group()
def project():
    """Query sessions by resolved project."""
    pass


@project.command("ls")
def project_ls():
    """List projects with session counts."""
    conn = index_connect()
    rows = conn.execute(
        "SELECT project, count(*) n, sum(n_turns) t, max(ended) last "
        "FROM sessions GROUP BY project ORDER BY n DESC"
    ).fetchall()
    if not rows:
        click.echo("[scad] No sessions. Run: scad archive && scad reindex")
        return
    for r in rows:
        when = datetime.fromtimestamp(r["last"] / 1000).strftime("%Y-%m-%d") if r["last"] else "?"
        click.echo(f"{(r['project'] or '?'):<28} {r['n']:>5} sessions  "
                   f"{(r['t'] or 0):>7} turns  last {when}")


@project.command("show")
@click.argument("name")
@click.option("--limit", default=40, help="Rows to show.")
def project_show(name, limit):
    """List a project's sessions."""
    conn = index_connect()
    # Only sessions you started — kind = 'main', the same rule `scad view` uses.
    # A subagent is triggered BY an agent, has no independent existence and
    # cannot be resumed; listing them here while the page hid them made the two
    # disagree about the same project (1309 of 1462 rows are subagents).
    rows = conn.execute(
        "SELECT id, name, kind, agent, title, n_turns, started FROM sessions "
        "WHERE project = ? AND kind = 'main' ORDER BY started DESC LIMIT ?",
        (name, limit),
    ).fetchall()
    if not rows:
        raise click.ClickException(f"No sessions you started for project {name}.")
    for r in rows:
        when = datetime.fromtimestamp(r["started"] / 1000).strftime("%Y-%m-%d %H:%M") \
            if r["started"] else "?"
        # Same rule as `session ls`: the human's own label, and BLANK when
        # nobody chose one. `title` is derived and never stands in for a name.
        label = (r["name"] or "")[:26]
        click.echo(f"{r['id'][:12]:<14} {label:<27} {when}  {r['agent']:<7} "
                   f"{r['kind']:<14} {r['n_turns']:>5}t  {(r['title'] or '')[:52]}")


@session.command("read")
@click.argument("session_id")
@click.option("--kind", default=None,
              type=click.Choice(["text", "thinking", "tool_use", "tool_result"]),
              help="Only this kind of turn — e.g. --kind text to skip tool noise.")
@click.option("--role", default=None, help="Only this role (user, assistant, tool).")
@click.option("--limit", default=None, type=int, help="Stop after N turns.")
def session_read(session_id, kind, role, limit):
    """Print a session's turns in order."""
    conn = index_connect()
    if session_row(conn, session_id) is None:
        raise click.ClickException(f"No session {session_id} in the index.")
    rows = session_turns(conn, session_id, kind=kind, role=role, limit=limit)
    if not rows:
        click.echo("[scad] No turns — this session may be a skeleton (no transcript).")
        return
    for r in rows:
        head = f"[{r['idx']:>4}] {r['role'] or '?':<9} {r['kind']}"
        if r["tool_name"]:
            head += f" ({r['tool_name']})"
        if r["truncated"]:
            head += "  …truncated"
        click.echo(head)
        click.echo(r["text"])
        click.echo()


@main.command()
@click.argument("query")
@click.option("--project", default=None, help="Restrict to one project.")
@click.option("--kind", default=None,
              type=click.Choice(["text", "thinking", "tool_use", "tool_result"]),
              help="Search only this kind — e.g. --kind thinking for reasoning.")
@click.option("--limit", default=20, help="Hits to show.")
@click.option("--notes", "notes_only", is_flag=True, help="Search notes instead of turns.")
@click.option("--json", "as_json", is_flag=True, help="Emit hits as JSON.")
def search(query, project, kind, limit, notes_only, as_json):
    """Full-text search across every indexed turn, or across notes with --notes."""
    conn = index_connect()
    if notes_only:
        hits = search_notes(conn, query, limit=limit)
        if as_json:
            click.echo(json.dumps(hits, default=str))
            return
        if not hits:
            click.echo(f"[scad] No note matches {query!r}.")
            return
        for h in hits:
            when = datetime.fromtimestamp(h["ts"] / 1000).strftime("%Y-%m-%d %H:%M") if h["ts"] else "?"
            click.echo(f"{(h.get('name') or h['session_id'][:12]):<20} {when}  "
                       f"{(h.get('topic') or ''):<24} {(h.get('title') or '')[:60]}")
        return
    hits = search_turns(conn, query, project=project, kind=kind, limit=limit)

    if as_json:
        click.echo(json.dumps([dict(h) for h in hits], default=str))
        return
    if not hits:
        click.echo(f"[scad] No match for {query!r}.")
        return
    for h in hits:
        when = datetime.fromtimestamp(h["ts"] / 1000).strftime("%Y-%m-%d %H:%M") \
            if h["ts"] else "?"
        snippet = " ".join(h["text"].split())[:140]
        click.echo(f"{h['session_id'][:12]:<14} {when}  {h['kind']:<12} "
                   f"{(h['project'] or '?'):<20} {snippet}")


# --- v2.1 rename: the old paths, kept working but off the help ------------------
#
# `scad run start|stop|…` and `scad run ls` moved to `scad run …` when the
# run / job / session split landed. They stay registered so muscle memory does
# not break mid-flow, and `hidden=True` so `--help` stops teaching the old shape.
#
# Deliberately NOT a deprecation cycle. This is a single-user tool with no
# external consumers, so "deprecate over a release" is a meaningless unit — and
# an ADVERTISED alias would preserve exactly the ambiguity the rename removes:
# `scad run info <run-id>` and `scad session show <session-uuid>` taking
# incompatible identifiers side by side. Delete these whenever, no coordination.
#
# A shallow copy, not the same object: `hidden` lives on the Command, so sharing
# one instance would hide it from `scad run --help` too. The callback is shared,
# which is the part that has to stay identical.
def _hidden_alias(group, command, name=None):
    alias = copy.copy(command)
    alias.hidden = True
    group.add_command(alias, name or command.name)
    return alias


for _verb in ("start", "stop", "clean", "attach", "info",
              "inject", "jobs", "logs", "send", "refresh"):
    _hidden_alias(session, run.commands[_verb])

_hidden_alias(main, run.commands["ls"], "status")
del _verb


@main.command()
@click.option("--days", default=14, help="How far back the waiting list looks.")
@click.option("--output", default=None, type=click.Path(), help="Write the page here.")
@click.option("--no-open", is_flag=True, help="Write the page without opening a browser.")
@click.option("--no-refresh", is_flag=True,
              help="Render the existing index without archiving or indexing first.")
# Kept working, undocumented: refreshing is what happens anyway now, so the
# flag is a no-op that spares muscle memory and any script already passing it.
# Two documented flags for one decision would be one too many.
@click.option("--refresh", is_flag=True, hidden=True)
def view(days, output, no_open, refresh, no_refresh):
    """Render the session index to a page and open it.

    Archives and indexes first, then renders. This inverts what the viewer spec
    calls a non-goal ("the viewer never writes to the index"), and deliberately:
    nothing else refreshes the index — there is no timer — so an opt-in refresh
    meant a stale page every time it was forgotten. Worse, live tmux, container
    and registry state IS gathered at render time, so an unrefreshed page was
    half fresh while looking authoritative: a session started minutes earlier
    was simply missing, with nothing to say so.

    `--no-refresh` keeps the pure reader for anyone who wants the old contract.
    """
    from pathlib import Path as _Path

    from scad.config import get_scad_home

    del refresh                                  # accepted, deliberately inert

    if not no_refresh:
        # Incremental, and archiving first. The index reads the archive rather
        # than the live trace dirs, so a refresh that skipped the sweep would
        # report success and render exactly the same stale page.
        #
        # Never rebuild: that is for derivation-rule changes, and on a viewer
        # command it would be 11s of pointless work — and destructive if raw had
        # been pruned.
        try:
            stats = run_reindex(archive_first=True)
            click.echo(f"[scad] refreshed: {stats.get('sessions', 0)} new session(s), "
                       f"{stats.get('turns', 0)} new turn(s)")
        except Exception as exc:
            # A refresh is a convenience wrapped around the thing actually asked
            # for. Failing the render because the sweep hit a full disk would
            # withhold the page over a problem it does not have; stale beats
            # absent, as long as it is said out loud.
            click.echo(f"[scad] Warning: refresh failed, rendering existing index: {exc}")

    conn = index_connect()
    data = gather(conn, tmux_panes(), running_run_ids(), days=days)
    target = _Path(output) if output else get_scad_home() / "view.html"
    write_view(target, render(data))

    click.echo(f"[scad] {target}")
    click.echo(f"[scad]   open now: {len(data.get('open_now') or [])}  "
               f"waiting: {len(data['waiting'])}  "
               f"open panes: {len(data.get('panes') or [])}  total: {len(data['all'])}")
    if not no_open:
        webbrowser.open(target.as_uri())
