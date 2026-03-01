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


def log_event(run_id: str, verb: str, details: str = "") -> None:
    """Append an event to ~/.scad/runs/<run-id>/events.log.

    Format: <ISO-timestamp> <verb> <details>
    """
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "events.log"
    timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M")
    line = f"{timestamp} {verb}"
    if details:
        line += f" {details}"
    with open(log_file, "a") as f:
        f.write(line + "\n")


def _get_jinja_env() -> Environment:
    return Environment(loader=PackageLoader("scad", "templates"))


def generate_run_id(config_name: str, tag: str) -> str:
    """Generate a unique run ID: {config}-{tag}-{MonDD}-{HHMM}."""
    now = datetime.now()
    date_str = now.strftime("%b%d")
    time_str = now.strftime("%H%M")
    return f"{config_name}-{tag}-{date_str}-{time_str}"


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


def generate_branch_name(tag: str) -> str:
    """Auto-generate branch name: scad-{tag}-{MonDD}-{HHMM}."""
    now = datetime.now()
    return f"scad-{tag}-{now.strftime('%b%d')}-{now.strftime('%H%M')}"


def check_branch_exists(repo_path: Path, branch: str) -> bool:
    """Check if a branch exists in a git repo."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--list", branch],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def resolve_branch(config: ScadConfig, branch: Optional[str], tag: str = "notag") -> str:
    """Resolve branch name: validate user-specified or auto-generate.

    User-specified branch that already exists raises ClickException.
    Auto-generated branches get -2, -3 suffix on collision.
    """
    if branch is None:
        branch = generate_branch_name(tag)
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

    # Copy .tmux.conf template
    tmux_conf_src = Path(__file__).parent / "templates" / ".tmux.conf"
    shutil.copy2(tmux_conf_src, build_dir / ".tmux.conf")


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

    # Disable telemetry in containers — no reason for isolated sessions to phone home
    environment["DISABLE_TELEMETRY"] = "1"
    environment["DISABLE_ERROR_REPORTING"] = "1"
    environment["CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY"] = "1"

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


def fetch_to_host(run_id: str, config: ScadConfig) -> list[dict]:
    """Fetch branches from clones back to source repos.

    For each clone: detach HEAD, fetch branch to source, re-checkout branch.
    Appends to ~/.scad/runs/<run-id>/events.log.
    """
    clone_base = WORKTREE_DIR / run_id
    if not clone_base.exists():
        raise FileNotFoundError(f"No clones found for run: {run_id}")

    results = []
    for key, repo_cfg in config.repos.items():
        clone_path = clone_base / key
        if not clone_path.exists():
            continue

        source_path = repo_cfg.resolved_path

        # Get current branch name
        branch = subprocess.run(
            ["git", "-C", str(clone_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        if branch == "HEAD":
            continue

        # Detach HEAD so source repo can accept the fetch
        subprocess.run(
            ["git", "-C", str(clone_path), "checkout", "--detach"],
            capture_output=True, check=True,
        )

        # Fetch branch into source repo
        subprocess.run(
            ["git", "-C", str(source_path), "fetch", str(clone_path), f"{branch}:{branch}"],
            capture_output=True, text=True, check=True,
        )

        # Re-checkout the branch in the clone
        subprocess.run(
            ["git", "-C", str(clone_path), "checkout", branch],
            capture_output=True, check=True,
        )

        results.append({"repo": key, "branch": branch, "source": str(source_path)})

    # Log to events.log
    for r in results:
        log_event(run_id, "fetch", f"{r['repo']} {r['branch']} → {r['source']}")

    return results


def sync_from_host(run_id: str, config: ScadConfig) -> list[dict]:
    """Fetch source repo refs into clones. Makes new branches/commits available.

    Does NOT checkout or merge — just makes refs available.
    """
    clone_base = WORKTREE_DIR / run_id
    if not clone_base.exists():
        raise FileNotFoundError(f"No clones found for run: {run_id}")

    results = []
    for key, repo_cfg in config.repos.items():
        clone_path = clone_base / key
        if not clone_path.exists():
            continue

        source_path = repo_cfg.resolved_path

        subprocess.run(
            ["git", "-C", str(clone_path), "fetch", str(source_path),
             "+refs/heads/*:refs/remotes/origin/*"],
            capture_output=True, text=True, check=True,
        )

        results.append({"repo": key, "source": str(source_path)})

    # Log to events.log
    for r in results:
        log_event(run_id, "sync", f"{r['repo']} \u2190 {r['source']}")

    return results


def config_name_for_run(run_id: str) -> Optional[str]:
    """Extract config name from run ID. Checks events.log first, then heuristic."""
    info = _parse_events_log(run_id)
    if info.get("config") and info["config"] != "?":
        return info["config"]
    # Fallback: first segment of run_id (works for simple config names)
    parts = run_id.split("-")
    if len(parts) >= 4:
        return parts[0]
    return None


def _parse_events_log(run_id: str) -> dict:
    """Parse events.log for config, branch, start time."""
    events_log = RUNS_DIR / run_id / "events.log"
    info = {"run_id": run_id, "config": "?", "branch": "?", "started": ""}
    if not events_log.exists():
        return info
    for line in events_log.read_text().strip().split("\n"):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "start":
            info["started"] = parts[0]
            for p in parts[2:]:
                if p.startswith("config="):
                    info["config"] = p.split("=", 1)[1]
                elif p.startswith("branch="):
                    info["branch"] = p.split("=", 1)[1]
            break
    return info


def get_all_sessions() -> list[dict]:
    """Get all sessions with container state. Sorted most-recent-first."""
    sessions = {}

    # 1. Running containers (from Docker)
    for c in list_scad_containers():
        run_id = c["run_id"]
        clone_dir = WORKTREE_DIR / run_id
        sessions[run_id] = {
            "run_id": run_id,
            "config": c["config"],
            "branch": c["branch"],
            "started": c["started"],
            "container": "running",
            "clones": "yes" if clone_dir.exists() else "-",
        }

    # 2. Scan runs dir for all sessions
    if RUNS_DIR.exists():
        for d in RUNS_DIR.iterdir():
            if not d.is_dir() or d.name in sessions:
                continue
            run_id = d.name
            info = _parse_events_log(run_id)

            # Determine container state
            try:
                client = docker.from_env()
                container = client.containers.get(f"scad-{run_id}")
                container_state = "stopped" if container.status != "running" else "running"
            except (DockerNotFound, DockerException):
                container_state = "removed"

            clone_dir = WORKTREE_DIR / run_id
            has_clones = clone_dir.exists()

            if container_state == "removed" and not has_clones:
                container_state = "cleaned"

            sessions[run_id] = {
                "run_id": run_id,
                "config": info["config"],
                "branch": info["branch"],
                "started": info["started"],
                "container": container_state,
                "clones": "yes" if has_clones else "-",
            }

    # Sort most-recent-first by start time
    return sorted(sessions.values(), key=lambda x: x.get("started", ""), reverse=True)


def get_session_info(run_id: str) -> dict:
    """Assemble session dashboard from multiple sources."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"No session found for {run_id}")

    info = _parse_events_log(run_id)

    # Events list
    events_log = run_dir / "events.log"
    info["events"] = []
    if events_log.exists():
        info["events"] = [
            line for line in events_log.read_text().strip().split("\n") if line
        ]

    # Container state
    try:
        client = docker.from_env()
        container = client.containers.get(f"scad-{run_id}")
        info["container"] = container.status
    except (DockerNotFound, DockerException):
        clone_dir = WORKTREE_DIR / run_id
        info["container"] = "removed" if clone_dir.exists() else "cleaned"

    # Clone paths
    clone_dir = WORKTREE_DIR / run_id
    if clone_dir.exists():
        info["clones"] = sorted(d.name for d in clone_dir.iterdir() if d.is_dir())
        info["clones_path"] = str(clone_dir)
    else:
        info["clones"] = []
        info["clones_path"] = None

    # Claude sessions — glob for .jsonl files in run_dir/claude/projects/
    claude_projects = run_dir / "claude" / "projects"
    info["claude_sessions"] = []
    if claude_projects.exists():
        for jsonl in claude_projects.rglob("*.jsonl"):
            stat = jsonl.stat()
            info["claude_sessions"].append({
                "id": jsonl.stem,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })

    return info


def refresh_credentials(run_id: str) -> float:
    """Push fresh credentials into a running container.

    Returns hours remaining on the credentials.
    Raises ClickException if credentials expired or container not running.
    """
    valid, hours = check_claude_auth()
    if not valid:
        raise click.ClickException("Credentials expired. Run: claude /login")

    container_name = f"scad-{run_id}"
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
    except DockerNotFound:
        raise click.ClickException(f"Container scad-{run_id} not found")

    if container.status != "running":
        raise click.ClickException(f"Container scad-{run_id} is not running")

    container.exec_run(
        "cp /mnt/host-claude-credentials.json /home/scad/.claude/.credentials.json"
    )

    log_event(run_id, "refresh", "credentials")
    return hours


