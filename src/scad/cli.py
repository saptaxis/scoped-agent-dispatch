"""CLI entry point."""

import os
import subprocess
import subprocess as _subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click
import docker
import yaml

from scad.config import load_config, list_configs, CONFIG_DIR, ScadConfig
from scad.container import (
    build_image,
    check_claude_auth,
    clean_run,
    cleanup_clones,
    config_name_for_run,
    create_clones,
    fetch_to_host,
    gc,
    generate_run_id,
    get_all_sessions,
    get_image_info,
    get_project_status,
    get_session_usage,
    get_session_info,
    image_exists,
    list_scad_containers,
    log_event,
    prune_old_images,
    refresh_credentials,
    resolve_branch,
    run_container,
    stop_container,
    sync_from_host,
    validate_run_id,
)


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


def _complete_run_ids(ctx, param, incomplete):
    """Shell completion for run IDs from ~/.scad/runs/."""
    runs_dir = Path.home() / ".scad" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        d.name for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(incomplete)
    )


def _complete_config_names(ctx, param, incomplete):
    """Shell completion for config names."""
    return sorted(n for n in list_configs() if n.startswith(incomplete))


@click.group()
def main():
    """scad — dispatch Claude Code agents in isolated Docker containers."""
    pass


@main.group()
def session():
    """Container + Claude session lifecycle."""
    pass


@main.group()
def code():
    """Git state between host and clones."""
    pass


@main.group()
def project():
    """Project-level views."""
    pass


@project.command("status")
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("--cost", is_flag=True, help="Include cost data (slow — runs ccusage).")
def project_status(config_name: str, cost: bool):
    """Show cross-session project overview."""
    status = get_project_status(config_name, include_cost=cost)

    if status["total_sessions"] == 0:
        click.echo(f"[scad] No sessions found for config: {config_name}")
        return

    # Summary
    parts = []
    if status["running"]:
        parts.append(f"{status['running']} running")
    if status["stopped"]:
        parts.append(f"{status['stopped']} stopped")
    if status["cleaned"]:
        parts.append(f"{status['cleaned']} cleaned")
    status_str = ", ".join(parts) if parts else "none"

    click.echo(f"Project:     {status['config']}")
    click.echo(f"Sessions:    {status['total_sessions']} ({status_str})")
    click.echo(f"Last active: {_relative_time(status['last_active'])}")
    if cost and status["total_cost"] > 0:
        click.echo(f"Total cost:  ${status['total_cost']:.2f}")

    # Session table
    click.echo()
    if cost:
        click.echo(
            f"  {'RUN ID':<35} {'BRANCH':<30} {'STARTED':<12} {'STATUS':<10} {'COST'}"
        )
        for s in status["sessions"]:
            started = _relative_time(s["started"])
            cost_str = f"${s['cost']:.2f}" if s["cost"] > 0 else "-"
            click.echo(
                f"  {s['run_id']:<35} {s['branch']:<30} {started:<12} {s['container']:<10} {cost_str}"
            )
    else:
        click.echo(
            f"  {'RUN ID':<35} {'BRANCH':<30} {'STARTED':<12} {'STATUS'}"
        )
        for s in status["sessions"]:
            started = _relative_time(s["started"])
            click.echo(
                f"  {s['run_id']:<35} {s['branch']:<30} {started:<12} {s['container']}"
            )


def run_agent(
    config, branch: str, tag: str, prompt: str = None, rebuild: bool = False
) -> str:
    """Orchestrate the full agent lifecycle: resolve branch, build, create clones, run."""
    # Pre-flight: check Claude auth
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
    container_id = run_container(config, branch, run_id, worktree_paths, prompt)
    click.echo(f"[scad] Container started: {container_id[:12]}")

    if prompt:
        click.echo(f"[scad] Running headless.")
        click.echo(f"[scad]   Setup log:    scad session logs {run_id}")
        click.echo(f"[scad]   Claude stream: scad session logs {run_id} --stream")
        click.echo(f"[scad]   Live follow:   scad session logs {run_id} -sf")
    else:
        click.echo(f"[scad] Session ready. Run: scad session attach {run_id}")

    return run_id


@session.command("start")
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("--tag", required=True, help="Session tag (e.g., plan07, bugfix-auth). Use 'notag' to opt out.")
@click.option("--branch", default=None, help="Branch name (auto-generated if not specified).")
@click.option("--prompt", default=None, help="Prompt for headless mode.")
@click.option("--rebuild", is_flag=True, help="Force rebuild the Docker image.")
def session_start(config_name: str, tag: str, branch: str, prompt: str, rebuild: bool):
    """Launch an agent in a new container."""
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
            config, branch=branch, tag=tag, prompt=prompt, rebuild=rebuild
        )
        log_event(run_id, "start", f"config={config.name} branch={branch}")
        if prompt:
            click.echo(f"[scad] Dispatched: {run_id}")
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


@main.command()
@click.argument("config_name", shell_complete=_complete_config_names)
@click.option("-v", "--verbose", is_flag=True, help="Show full Docker build output.")
def build(config_name: str, verbose: bool):
    """Build or rebuild the Docker image for a config."""
    try:
        config = load_config(config_name)
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"[scad] Config validation error: {e}", err=True)
        sys.exit(2)

    tag = f"scad-{config.name}"
    click.echo(f"[scad] Building image {tag}...")
    try:
        with tempfile.TemporaryDirectory() as build_dir:
            for line in build_image(config, Path(build_dir)):
                if verbose:
                    click.echo(f"  {line}")
                elif line.startswith("Step "):
                    click.echo(f"[scad] {line}")
        click.echo(f"[scad] Built: {tag}")
        # After successful build, prune old images
        try:
            client = docker.from_env()
            new_image = client.images.get(tag)
            prune_old_images(client, config.name, new_image.id)
        except Exception:
            pass  # don't fail build over prune
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(3)


@session.command("status")
@click.option("--all", "show_all", is_flag=True, help="Show full session history.")
def session_status(show_all: bool):
    """List sessions. Running only by default, --all for full history."""
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
            click.echo("[scad] No running sessions.")
        else:
            click.echo(
                f"{'RUN ID':<30} {'CONFIG':<12} {'BRANCH':<25} "
                f"{'STARTED':<12} {'CONTAINER':<12} {'CLONES'}"
            )
            for run in running:
                started = _relative_time(run["started"]) if run["started"] else "?"
                clone_dir = Path.home() / ".scad" / "runs" / run["run_id"] / "worktrees"
                clones = "yes" if clone_dir.exists() else "-"
                click.echo(
                    f"{run['run_id']:<30} {run['config']:<12} {run['branch']:<25} "
                    f"{started:<12} {'running':<12} {clones}"
                )

    # Credential expiry warning
    valid, hours = check_claude_auth()
    if not valid:
        click.echo("\n\u2717 Credentials expired \u2014 run: claude /login")
    elif hours < 2.0:
        click.echo(f"\n\u26a0 Credentials expire in {hours:.1f}h \u2014 run: scad code refresh <run-id>")


@session.command("info")
@click.argument("run_id", shell_complete=_complete_run_ids)
def session_info(run_id: str):
    """Show session dashboard."""
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


@session.command("logs")
@click.argument("run_id", shell_complete=_complete_run_ids)
@click.option("--follow", "-f", is_flag=True, help="Stream logs as they are written.")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default: 100).")
@click.option("--stream", "-s", is_flag=True, help="Show Claude stream (tool calls, edits) instead of entrypoint log.")
def session_logs(run_id: str, follow: bool, lines: int, stream: bool):
    """Read agent log output."""
    validate_run_id(run_id)
    logs_dir = Path.home() / ".scad" / "logs"
    if stream:
        log_path = logs_dir / f"{run_id}.stream.jsonl"
        not_found_msg = f"No stream log found for {run_id}"
    else:
        log_path = logs_dir / f"{run_id}.log"
        not_found_msg = f"No log file found for {run_id}"

    if not log_path.exists():
        click.echo(f"[scad] {not_found_msg}", err=True)
        sys.exit(1)

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


@session.command("stop")
@click.argument("run_id", required=False, shell_complete=_complete_run_ids)
@click.option("--all", "stop_all", is_flag=True, help="Stop all running sessions.")
@click.option("--config", "config_name", help="Stop all sessions for this config.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def session_stop(run_id, stop_all, config_name, yes):
    """Stop a running session."""
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


@session.command("attach")
@click.argument("run_id", shell_complete=_complete_run_ids)
def session_attach(run_id: str):
    """Attach to an interactive tmux session."""
    validate_run_id(run_id)
    container_name = f"scad-{run_id}"
    try:
        client = docker.from_env()
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
            f"[scad] Container '{run_id}' is running headless. "
            f"Use 'scad session logs {run_id}' to view output.",
            err=True,
        )
        sys.exit(1)

    log_event(run_id, "attach")
    result = _subprocess.run(
        ["docker", "exec", "-it", container_name, "tmux", "attach", "-t", "scad"]
    )
    sys.exit(result.returncode)


@session.command("clean")
@click.argument("run_id", required=False, shell_complete=_complete_run_ids)
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
@click.argument("run_id", shell_complete=_complete_run_ids)
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
@click.argument("run_id", shell_complete=_complete_run_ids)
def code_sync(run_id: str):
    """Sync host repo changes into clones (makes new branches available)."""
    validate_run_id(run_id)
    try:
        config = _config_for_run(run_id)
        results = sync_from_host(run_id, config)
        if not results:
            click.echo(f"[scad] Nothing to sync for {run_id}")
        else:
            for r in results:
                click.echo(f"[scad] Synced {r['repo']} from {r['source']}")
    except FileNotFoundError as e:
        click.echo(f"[scad] Error: {e}", err=True)
        sys.exit(2)


@code.command("refresh")
@click.argument("run_id", shell_complete=_complete_run_ids)
def code_refresh(run_id: str):
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
