"""Docker container management."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import docker
from jinja2 import Environment, PackageLoader

from scad.config import ScadConfig, load_config


def _get_jinja_env() -> Environment:
    return Environment(loader=PackageLoader("scad", "templates"))


def generate_run_id(branch: str) -> str:
    """Generate a unique run ID from branch name and current timestamp."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%b%d")  # e.g., Feb26
    time_str = now.strftime("%H%M")  # e.g., 1430
    return f"{branch}-{date_str}-{time_str}"


def generate_branch_name() -> str:
    """Auto-generate branch name: scad-MonDD-HHMM."""
    now = datetime.now(timezone.utc)
    return f"scad-{now.strftime('%b%d')}-{now.strftime('%H%M')}"


def check_branch_exists(repo_path: Path, branch: str) -> bool:
    """Check if a branch exists in a git repo."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--list", branch],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def resolve_branch(config: ScadConfig, branch: Optional[str]) -> str:
    """Resolve branch name: validate user-specified or auto-generate.

    User-specified branch that already exists raises ClickException.
    Auto-generated branches get -2, -3 suffix on collision.
    """
    if branch is None:
        branch = generate_branch_name()
        base = branch
        suffix = 2
        while any(
            check_branch_exists(repo.resolved_path, branch)
            for repo in config.repos.values()
            if repo.worktree
        ):
            branch = f"{base}-{suffix}"
            suffix += 1
        return branch
    else:
        for key, repo in config.repos.items():
            if repo.worktree and check_branch_exists(repo.resolved_path, branch):
                raise click.ClickException(
                    f"Branch '{branch}' already exists in repo '{key}'. "
                    "Use a different name or delete the existing branch."
                )
        return branch


def render_build_context(config: ScadConfig, build_dir: Path) -> None:
    """Render Dockerfile and entrypoint into a build context directory."""
    env = _get_jinja_env()

    workdir_key = config.workdir_key
    workdir_repo = config.repos[workdir_key]

    # Check if requirements.txt exists in the workdir repo
    requirements_content = False
    if config.python.requirements:
        req_path = Path(workdir_repo.path).expanduser() / config.python.requirements
        if req_path.exists():
            shutil.copy2(req_path, build_dir / "requirements.txt")
            requirements_content = True

    # Determine requirements file path inside container for entrypoint pip sync
    requirements_file = None
    if config.python.requirements:
        requirements_file = config.python.requirements

    # Render Dockerfile
    dockerfile_template = env.get_template("Dockerfile.j2")
    dockerfile_content = dockerfile_template.render(
        base_image=config.base_image,
        apt_packages=config.apt_packages,
        requirements_content=requirements_content,
    )
    (build_dir / "Dockerfile").write_text(dockerfile_content)

    # Render entrypoint
    entrypoint_template = env.get_template("entrypoint.sh.j2")
    repos_dict = {
        k: {
            "branch_from": v.branch_from,
            "add_dir": v.add_dir,
        }
        for k, v in config.repos.items()
    }
    entrypoint_content = entrypoint_template.render(
        repos=repos_dict,
        workdir_key=workdir_key,
        requirements_file=requirements_file,
        claude={
            "dangerously_skip_permissions": config.claude.dangerously_skip_permissions,
            "additional_flags": config.claude.additional_flags,
        },
        config_name=config.name,
    )
    (build_dir / "entrypoint.sh").write_text(entrypoint_content)


def list_scad_containers() -> list[dict]:
    """List running scad containers from Docker."""
    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        return []
    containers = client.containers.list(filters={"label": "scad.managed=true"})
    results = []
    for c in containers:
        labels = c.labels
        results.append({
            "run_id": labels.get("scad.run_id", "?"),
            "config": labels.get("scad.config", "?"),
            "branch": labels.get("scad.branch", "?"),
            "started": labels.get("scad.started", ""),
            "status": "running",
        })
    return results


def list_completed_runs(logs_dir: Optional[Path] = None) -> list[dict]:
    """List completed runs from status JSON files."""
    if logs_dir is None:
        logs_dir = Path.home() / ".scad" / "logs"
    if not logs_dir.exists():
        return []
    results = []
    for status_file in sorted(logs_dir.glob("*.status.json")):
        try:
            data = json.loads(status_file.read_text())
            results.append({
                "run_id": data.get("run_id", status_file.stem.replace(".status", "")),
                "config": data.get("config", "?"),
                "branch": data.get("branch", "?"),
                "started": data.get("started", ""),
                "status": f"exited({data.get('exit_code', '?')})",
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def stop_container(run_id: str) -> bool:
    """Stop and remove a scad container by run ID."""
    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        return False
    container_name = f"scad-{run_id}"
    try:
        container = client.containers.get(container_name)
        container.stop(timeout=10)
        container.remove()
        return True
    except docker.errors.NotFound:
        return False


def get_image_info(config_name: str) -> Optional[dict]:
    """Get Docker image info for a config. Returns None if not built."""
    tag = f"scad-{config_name}"
    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        return None
    try:
        image = client.images.get(tag)
        created = image.attrs.get("Created", "")
        return {"tag": tag, "created": created}
    except docker.errors.ImageNotFound:
        return None


def build_image(config: ScadConfig, build_dir: Path):
    """Build a Docker image for the given config. Yields build log lines."""
    tag = f"scad-{config.name}"
    render_build_context(config, build_dir)

    client = docker.from_env()
    for chunk in client.api.build(path=str(build_dir), tag=tag, rm=True, decode=True):
        if "stream" in chunk:
            line = chunk["stream"].rstrip()
            if line:
                yield line
        elif "error" in chunk:
            raise docker.errors.BuildError(chunk["error"], [])


def image_exists(config: ScadConfig) -> bool:
    """Check if the Docker image for this config already exists."""
    tag = f"scad-{config.name}"
    client = docker.from_env()
    try:
        client.images.get(tag)
        return True
    except docker.errors.ImageNotFound:
        return False


def run_container(
    config: ScadConfig,
    branch: str,
    run_id: str,
    prompt: Optional[str] = None,
    image_tag: Optional[str] = None,
) -> str:
    """Run a container for the given config. Returns the container ID."""
    if image_tag is None:
        image_tag = f"scad-{config.name}"

    client = docker.from_env()
    logs_dir = Path.home() / ".scad" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Build volume mounts
    volumes = {}

    # Repos — read-only
    for key, repo in config.repos.items():
        host_path = str(Path(repo.path).expanduser().resolve())
        volumes[host_path] = {"bind": f"/mnt/repos/{key}", "mode": "ro"}

    # Data mounts — read-write
    for mount in config.mounts:
        host_path = str(Path(mount.host).expanduser().resolve())
        volumes[host_path] = {"bind": mount.container, "mode": "rw"}

    # Logs directory — read-write
    volumes[str(logs_dir)] = {"bind": "/scad-logs", "mode": "rw"}

    # Git config — mount as read-only source, entrypoint copies to writable location
    gitconfig = Path.home() / ".gitconfig"
    if gitconfig.exists():
        volumes[str(gitconfig)] = {"bind": "/mnt/host-gitconfig", "mode": "ro"}

    # Claude auth — mount ONLY credentials file, not the whole ~/.claude dir.
    # Mounting the full dir brings host plugins (with host-specific installPaths
    # that don't resolve in the container) and risks host file deletion.
    # The entrypoint generates a minimal ~/.claude.json stub for onboarding.
    claude_creds = Path.home() / ".claude" / ".credentials.json"
    if claude_creds.exists():
        volumes[str(claude_creds)] = {
            "bind": "/home/scad/.claude/.credentials.json",
            "mode": "ro",
        }

    # CLAUDE.md — global instructions for Claude Code
    if config.claude.claude_md is False:
        pass  # explicitly disabled
    elif config.claude.claude_md is not None:
        # Custom path specified
        claude_md_path = Path(config.claude.claude_md).expanduser().resolve()
        if claude_md_path.exists():
            volumes[str(claude_md_path)] = {"bind": "/home/scad/CLAUDE.md", "mode": "ro"}
    else:
        # Auto-detect ~/CLAUDE.md
        claude_md_path = Path.home() / "CLAUDE.md"
        if claude_md_path.exists():
            volumes[str(claude_md_path)] = {"bind": "/home/scad/CLAUDE.md", "mode": "ro"}

    # Environment variables
    environment = {
        "BRANCH_NAME": branch,
        "RUN_ID": run_id,
    }
    if prompt:
        environment["AGENT_PROMPT"] = prompt

    # Pass through API key if set
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        environment["ANTHROPIC_API_KEY"] = api_key

    container_name = f"scad-{run_id}"

    container = client.containers.run(
        image_tag,
        detach=True,
        name=container_name,
        volumes=volumes,
        environment=environment,
        labels={
            "scad.managed": "true",
            "scad.config": config.name,
            "scad.branch": branch,
            "scad.run_id": run_id,
            "scad.started": datetime.now(timezone.utc).isoformat(),
        },
    )
    return container.id


def fetch_bundles(config: ScadConfig, run_id: str, branch: str, logs_dir: Optional[Path] = None) -> dict[str, bool]:
    """Fetch git bundles from a completed run into host repos."""
    if logs_dir is None:
        logs_dir = Path.home() / ".scad" / "logs"
    results = {}

    for key, repo in config.repos.items():
        bundle_path = logs_dir / f"{run_id}-{key}.bundle"
        if not bundle_path.exists():
            continue

        repo_path = Path(repo.path).expanduser().resolve()

        # Verify the bundle
        verify = subprocess.run(
            ["git", "bundle", "verify", str(bundle_path)],
            cwd=repo_path,
            capture_output=True,
        )
        if verify.returncode != 0:
            print(f"[scad] Warning: bundle verification failed for {key}")
            results[key] = False
            continue

        # Fetch the bundle
        fetch = subprocess.run(
            [
                "git",
                "fetch",
                str(bundle_path),
                f"{branch}:{branch}",
            ],
            cwd=repo_path,
            capture_output=True,
        )
        if fetch.returncode != 0:
            print(
                f"[scad] Warning: bundle fetch failed for {key}: "
                f"{fetch.stderr.decode()}"
            )
            results[key] = False
        else:
            print(f"[scad] Fetched branch '{branch}' into {repo_path}")
            results[key] = True

    return results


def fetch_pending_bundles(logs_dir: Optional[Path] = None) -> list[dict]:
    """Auto-fetch bundles for completed runs that haven't been fetched yet.

    Returns a list of dicts with run_id and fetch results for each run processed.
    """
    if logs_dir is None:
        logs_dir = Path.home() / ".scad" / "logs"
    if not logs_dir.exists():
        return []

    results = []
    for status_file in sorted(logs_dir.glob("*.status.json")):
        run_id = status_file.name.replace(".status.json", "")

        # Skip if already fetched
        fetched_marker = logs_dir / f"{run_id}.fetched"
        if fetched_marker.exists():
            continue

        # Check if any bundle files exist for this run
        bundles = list(logs_dir.glob(f"{run_id}-*.bundle"))
        if not bundles:
            continue

        # Read status to get config name and branch
        try:
            data = json.loads(status_file.read_text())
        except (json.JSONDecodeError, KeyError):
            continue

        config_name = data.get("config")
        branch = data.get("branch")
        if not config_name or not branch:
            continue

        # Load config and fetch
        try:
            config = load_config(config_name)
        except Exception:
            continue

        fetch_results = fetch_bundles(config, run_id, branch, logs_dir=logs_dir)
        if fetch_results:
            fetched_marker.write_text("")
            results.append({"run_id": run_id, "fetched": fetch_results})

    return results
