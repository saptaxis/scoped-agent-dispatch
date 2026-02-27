"""Docker container management."""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import docker
from docker.errors import DockerException, NotFound as DockerNotFound
from jinja2 import Environment, PackageLoader

from scad.config import ScadConfig

SCAD_DIR = Path.home() / ".scad"
WORKTREE_DIR = SCAD_DIR / "worktrees"
RUNS_DIR = SCAD_DIR / "runs"


def _get_jinja_env() -> Environment:
    return Environment(loader=PackageLoader("scad", "templates"))


def generate_run_id(config_name: str) -> str:
    """Generate a unique run ID from config name and current timestamp."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%b%d")  # e.g., Feb26
    time_str = now.strftime("%H%M")  # e.g., 1430
    return f"{config_name}-{date_str}-{time_str}"


def check_claude_auth() -> tuple[bool, float]:
    """Check if Claude credentials exist and are valid.

    Returns (valid, hours_remaining). valid is False if credentials
    are missing or expired. hours_remaining is 0 if invalid.
    """
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        return False, 0.0
    try:
        data = json.loads(creds_path.read_text())
        expires_at = data["claudeAiOauth"]["expiresAt"] / 1000  # ms → sec
        remaining = (expires_at - time.time()) / 3600  # seconds → hours
        return remaining > 0, max(remaining, 0.0)
    except (json.JSONDecodeError, KeyError):
        return False, 0.0


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


def create_clones(
    config: ScadConfig, branch: str, run_id: str
) -> dict[str, Path]:
    """Create host-side local clones for each repo with worktree=True.

    Uses git clone --local (hardlinks, near-instant) instead of git worktree
    because worktrees' .git file references the main repo's .git directory,
    which isn't accessible inside Docker containers.

    Returns dict of repo_key -> clone_path (or direct path for non-worktree repos).
    """
    clone_base = WORKTREE_DIR / run_id
    clone_base.mkdir(parents=True, exist_ok=True)

    paths = {}
    for key, repo in config.repos.items():
        if repo.worktree:
            clone_path = clone_base / key
            subprocess.run(
                ["git", "clone", "--local",
                 str(repo.resolved_path), str(clone_path)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(clone_path),
                 "checkout", "-b", branch],
                check=True,
            )
            paths[key] = clone_path
        else:
            paths[key] = repo.resolved_path

    # Create persistent run directory for Claude session data
    run_dir = RUNS_DIR / run_id / "claude"
    run_dir.mkdir(parents=True, exist_ok=True)

    return paths


def cleanup_clones(run_id: str) -> None:
    """Remove clones for a completed run.

    Does NOT fetch branches back — user does that separately.
    Just deletes the clone directory.
    """
    clone_base = WORKTREE_DIR / run_id
    if clone_base.exists():
        shutil.rmtree(clone_base)


def clean_run(run_id: str) -> None:
    """Remove container, clones, and run directory for a run. Point of no return."""
    # Stop + remove container if it exists
    try:
        client = docker.from_env()
        container_name = f"scad-{run_id}"
        container = client.containers.get(container_name)
        container.stop(timeout=10)
        container.remove()
    except (DockerNotFound, DockerException):
        pass

    # Remove clones
    cleanup_clones(run_id)

    # Remove run directory
    run_dir = RUNS_DIR / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


def render_build_context(config: ScadConfig, build_dir: Path) -> None:
    """Render Dockerfile and entrypoint into a build context directory."""
    env = _get_jinja_env()

    workdir_key = config.workdir_key
    workdir_repo = config.repos[workdir_key]

    # Check if requirements.txt exists in the workdir repo
    requirements_content = False
    if config.python.requirements:
        req_path = workdir_repo.resolved_path / config.python.requirements
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

    # Build context prompt from focus fields
    context_parts = []
    for key, repo in config.repos.items():
        if repo.focus:
            context_parts.append(
                f"Read /workspace/{key}/{repo.focus}/overview.md for project context"
            )
    context_prompt = ". ".join(context_parts) if context_parts else None

    # Render entrypoint
    entrypoint_template = env.get_template("entrypoint.sh.j2")
    repos_dict = {
        k: {"add_dir": v.add_dir}
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
        context_prompt=context_prompt,
    )
    (build_dir / "entrypoint.sh").write_text(entrypoint_content)

    # Render bootstrap config (plugins list from config)
    bootstrap_conf_template = env.get_template("bootstrap-claude.conf.j2")
    bootstrap_conf_content = bootstrap_conf_template.render(
        plugins=config.claude.plugins,
    )
    (build_dir / "bootstrap-claude.conf").write_text(bootstrap_conf_content)

    # Copy static bootstrap script
    bootstrap_script = Path(__file__).parent / "templates" / "bootstrap-claude.sh"
    shutil.copy2(bootstrap_script, build_dir / "bootstrap-claude.sh")


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
    """Stop a scad container by run ID. Does NOT remove — use clean for that."""
    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        return False
    container_name = f"scad-{run_id}"
    try:
        container = client.containers.get(container_name)
        container.stop(timeout=10)
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
    worktree_paths: dict[str, Path],
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

    # Repos — clone (rw) or direct mount (ro) at /workspace/<key>
    for key, repo in config.repos.items():
        host_path = str(worktree_paths[key])
        mode = "rw" if repo.worktree else "ro"
        volumes[host_path] = {"bind": f"/workspace/{key}", "mode": mode}

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

    # Claude auth — mount to staging path (same pattern as gitconfig)
    # Direct-mount to final path breaks on credential refresh (/login writes
    # a new file → new inode → container still sees stale mount).
    # Entrypoint copies from staging path on startup.
    claude_creds = Path.home() / ".claude" / ".credentials.json"
    if claude_creds.exists():
        volumes[str(claude_creds)] = {
            "bind": "/mnt/host-claude-credentials.json",
            "mode": "ro",
        }

    # Persistent run directory — Claude session data
    run_claude_dir = RUNS_DIR / run_id / "claude"
    if run_claude_dir.exists():
        volumes[str(run_claude_dir)] = {"bind": "/home/scad/.claude", "mode": "rw"}

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
    # Pass host timezone so git commits, logs, and branch names match host time
    import time as _time
    tz = _time.tzname[0] if _time.daylight == 0 else _time.tzname[_time.daylight]
    environment = {"RUN_ID": run_id, "TZ": tz}
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


