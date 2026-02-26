"""CLI entry point."""

import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import click
import docker

from scad.config import load_config, list_configs
from scad.container import (
    build_image,
    fetch_bundles,
    generate_run_id,
    get_image_info,
    image_exists,
    run_container,
)


def _relative_time(iso_str: str) -> str:
    """Format an ISO timestamp as relative time."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
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


@click.group()
def main():
    """scad — dispatch Claude Code agents in isolated Docker containers."""
    pass


def run_agent(config, branch: str, prompt: str = None, rebuild: bool = False) -> str:
    """Orchestrate the full agent lifecycle: build, run, wait, fetch bundles."""
    run_id = generate_run_id(branch)

    # Build image if needed
    if rebuild or not image_exists(config):
        click.echo(f"[scad] Building image scad-{config.name}...")
        with tempfile.TemporaryDirectory() as build_dir:
            tag = build_image(config, Path(build_dir))
        click.echo(f"[scad] Image built: {tag}")
    else:
        click.echo(f"[scad] Using cached image scad-{config.name}")

    # Run the container
    click.echo(f"[scad] Dispatching agent: {run_id}")
    container_id = run_container(config, branch, run_id, prompt)
    click.echo(f"[scad] Container started: {container_id[:12]}")

    if prompt:
        # Headless mode — detach and start background watcher
        click.echo(f"[scad] Running headless. Logs: scad logs {run_id}")
        click.echo(f"[scad] Status: scad status")

        def _wait_and_fetch():
            client = docker.from_env()
            container = client.containers.get(container_id)
            result = container.wait()
            exit_code = result.get("StatusCode", -1)

            # Fetch bundles
            bundle_results = fetch_bundles(config, run_id, branch)

            # Read status file
            status_path = Path.home() / ".scad" / "logs" / f"{run_id}.status.json"
            if status_path.exists():
                status = json.loads(status_path.read_text())
                click.echo(
                    f"\n[scad] Agent {run_id} finished "
                    f"(exit code {status.get('exit_code', '?')})"
                )
            else:
                click.echo(
                    f"\n[scad] Agent {run_id} exited (code {exit_code}), "
                    f"no status file found"
                )

            if bundle_results:
                for repo, success in bundle_results.items():
                    status_str = "fetched" if success else "FAILED"
                    click.echo(f"[scad]   {repo}: {status_str}")

            # Clean up container
            try:
                container.remove()
            except Exception:
                pass

        watcher = threading.Thread(target=_wait_and_fetch, daemon=True)
        watcher.start()
    else:
        # Interactive mode — attach
        click.echo("[scad] Attaching to interactive session...")
        client = docker.from_env()
        container = client.containers.get(container_id)
        try:
            for chunk in container.attach(stream=True):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        except KeyboardInterrupt:
            pass
        container.wait()
        fetch_bundles(config, run_id, branch)
        try:
            container.remove()
        except Exception:
            pass

    return run_id


@main.command()
@click.argument("config_name")
@click.option("--branch", required=True, help="Branch name for the agent's work.")
@click.option("--prompt", default=None, help="Prompt for headless mode.")
@click.option("--rebuild", is_flag=True, help="Force rebuild the Docker image.")
def run(config_name: str, branch: str, prompt: str, rebuild: bool):
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
        run_id = run_agent(
            config, branch=branch, prompt=prompt, rebuild=rebuild
        )
        if prompt:
            click.echo(f"[scad] Dispatched: {run_id}")
    except docker.errors.DockerException as e:
        click.echo(f"[scad] Docker error: {e}", err=True)
        sys.exit(3)


@main.command()
def configs():
    """List available project configs."""
    names = list_configs()
    if not names:
        click.echo("[scad] No configs found in ~/.scad/templates/")
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
