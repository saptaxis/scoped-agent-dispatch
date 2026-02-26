"""Docker container management."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import docker
from jinja2 import Environment, PackageLoader

from scad.config import ScadConfig


def _get_jinja_env() -> Environment:
    return Environment(loader=PackageLoader("scad", "templates"))


def generate_run_id(branch: str) -> str:
    """Generate a unique run ID from branch name and current timestamp."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%b%d")  # e.g., Feb26
    time_str = now.strftime("%H%M")  # e.g., 1430
    return f"{branch}-{date_str}-{time_str}"


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
    client = docker.from_env()
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
    client = docker.from_env()
    try:
        image = client.images.get(tag)
        created = image.attrs.get("Created", "")
        return {"tag": tag, "created": created}
    except docker.errors.ImageNotFound:
        return None


def build_image(config: ScadConfig, build_dir: Path) -> str:
    """Build a Docker image for the given config. Returns the image tag."""
    tag = f"scad-{config.name}"
    render_build_context(config, build_dir)

    client = docker.from_env()
    client.images.build(path=str(build_dir), tag=tag, rm=True)
    return tag


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

    # Claude auth — mount .claude dir and .claude.json config
    claude_dir = Path.home() / ".claude"
    claude_json = Path.home() / ".claude.json"
    if claude_dir.exists():
        volumes[str(claude_dir)] = {"bind": "/home/scad/.claude", "mode": "rw"}
    if claude_json.exists():
        volumes[str(claude_json)] = {"bind": "/home/scad/.claude.json", "mode": "rw"}

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


def fetch_bundles(config: ScadConfig, run_id: str, branch: str) -> dict[str, bool]:
    """Fetch git bundles from a completed run into host repos."""
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
