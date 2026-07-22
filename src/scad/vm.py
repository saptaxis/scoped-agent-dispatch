"""OS-specific Docker provider resolution.

The only OS-specific surface in scad is "where the Docker daemon comes from".

On Linux that is the native daemon reached through the ambient Docker context.
macOS has no native Docker, so scad owns a dedicated Colima profile named
`scad` — isolated from the user's other Docker — whose daemon socket lives at
``~/.colima/scad/docker.sock``. Everything downstream of this module (container
lifecycle, workspace, injection, code flow) is identical on both platforms.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
import docker
import yaml
from docker.errors import DockerException

if TYPE_CHECKING:
    from scad.config import ScadConfig

SCAD_PROFILE = "scad"

# macOS Keychain service name Claude Code stores its OAuth credentials under.
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"

# Keychain access can pop a GUI prompt on a locked/unconfigured keychain --
# bound it so a hung prompt can never hang scad indefinitely.
KEYCHAIN_TIMEOUT = 10


class DockerUnavailable(DockerException):
    """No usable Docker daemon could be reached.

    Subclasses DockerException so that every existing ``except DockerException``
    handler keeps working and surfaces the actionable message below.
    """


class VMUnsupported(click.ClickException):
    """The requested operation is not supported on this platform."""


def is_macos() -> bool:
    """True when running on macOS (Darwin)."""
    return platform.system() == "Darwin"


def colima_profile_dir() -> Path:
    """Directory Colima keeps the scad profile's state in."""
    return Path.home() / ".colima" / SCAD_PROFILE


def colima_socket_path() -> Path:
    """Docker socket exposed by the scad Colima profile."""
    return colima_profile_dir() / "docker.sock"


def docker_base_url() -> str | None:
    """Base URL for the Docker client, or None to use the ambient environment."""
    if is_macos():
        return f"unix://{colima_socket_path()}"
    return None


def _unavailable_message() -> str:
    """Actionable guidance for an unreachable daemon, per platform."""
    if is_macos():
        return (
            f"Cannot reach the scad Docker daemon at {colima_socket_path()}.\n"
            "  Start it with:  scad vm start\n"
            "  Not installed?  re-run scad's install.sh (or: brew install colima docker)"
        )
    return (
        "Cannot reach the Docker daemon — is dockerd running?\n"
        "  Try:  systemctl status docker   (or: sudo systemctl start docker)"
    )


def get_docker_client() -> docker.DockerClient:
    """Return a Docker client for the daemon scad owns on this platform.

    Linux: the native daemon via the ambient context. macOS: the scad Colima
    profile socket — never the user's default Docker context.

    Raises DockerUnavailable (a DockerException) with actionable guidance if the
    daemon cannot be reached.
    """
    base_url = docker_base_url()
    try:
        if base_url is None:
            client = docker.from_env()
        else:
            client = docker.DockerClient(base_url=base_url)
        client.ping()
        return client
    except (DockerException, OSError) as exc:
        # docker-py leaks connection failures rather than raising DockerException:
        # requests.exceptions.RequestException subclasses OSError, so this pair
        # covers a dead or missing socket without depending on requests directly.
        raise DockerUnavailable(_unavailable_message()) from exc


def docker_cli_env() -> dict[str, str]:
    """Environment for shelling out to the `docker` CLI.

    On macOS, scopes DOCKER_HOST to the scad profile so `docker exec` reaches the
    same daemon as the Python client. On Linux, the ambient environment is used
    unchanged.
    """
    env = dict(os.environ)
    if is_macos():
        env["DOCKER_HOST"] = f"unix://{colima_socket_path()}"
    return env


def read_claude_credentials() -> str | None:
    """Raw Claude credentials JSON from this platform's credential store.

    Linux keeps them in ~/.claude/.credentials.json; macOS keeps them in the
    login Keychain. Returns None when no credentials are available.
    """
    if is_macos():
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", CLAUDE_KEYCHAIN_SERVICE, "-w"],
                capture_output=True,
                text=True,
                timeout=KEYCHAIN_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        return output or None

    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        return None
    try:
        return creds_path.read_text()
    except OSError:
        return None


def stage_claude_credentials(run_id: str) -> Path | None:
    """Materialise this platform's Claude credentials into a run-scoped file.

    Docker cannot bind-mount a Keychain entry, so on macOS the credentials are
    written out to disk first at a run-scoped path and that file is mounted
    instead. Created with mode 0600 (before the contents are written, where
    possible) since it holds a live OAuth token. Returns the staged path, or
    None when no credentials are available on the host.

    Used by both `claude_config.get_volume_mounts()` (initial mount) and
    `container.refresh_credentials()` (re-materialising a refreshed token) so
    the two never drift on the path, the mode, or the write.
    """
    raw = read_claude_credentials()
    if raw is None:
        return None

    from scad.config import get_scad_home

    path = get_scad_home() / "runs" / run_id / "claude-credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)  # guarantee 0600 even if the file pre-existed
    with os.fdopen(fd, "w") as f:
        f.write(raw)
    return path


# Creating a Colima VM pulls an image and formats a disk — allow generous time.
COLIMA_TIMEOUT = 900


def colima_installed() -> bool:
    """True when the colima binary is on PATH."""
    return shutil.which("colima") is not None


def _require_macos(action: str) -> None:
    """Raise unless we are on macOS — VM management is macOS-only."""
    if not is_macos():
        raise VMUnsupported(
            f"'{action}' is macOS-only. On Linux scad uses the native Docker "
            "daemon directly — there is no scad VM to manage."
        )


def _require_colima() -> None:
    """Raise unless the colima binary is available."""
    if not colima_installed():
        raise VMUnsupported(
            "colima is not installed.\n"
            "  Install it with:  brew install colima docker\n"
            "  Or re-run scad's install.sh, which does it for you."
        )


def _colima(*args: str) -> subprocess.CompletedProcess:
    """Run a colima subcommand, raising VMUnsupported with its stderr on failure."""
    try:
        result = subprocess.run(
            ["colima", *args],
            capture_output=True,
            text=True,
            timeout=COLIMA_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise VMUnsupported(
            f"colima {' '.join(args)} timed out after {COLIMA_TIMEOUT}s.\n"
            f"  Check on it with:  colima status {SCAD_PROFILE}\n"
            f"  Or run it by hand: colima start {SCAD_PROFILE}"
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise VMUnsupported(
            f"colima {' '.join(args)} failed:\n{detail}\n"
            "  If this machine predates macOS 13, set qemu/sshfs in "
            "~/.scad/settings.yml:\n"
            "    colima:\n      vm_type: qemu\n      mount_type: sshfs"
        )
    return result


def vm_state() -> str:
    """State of the scad VM: 'running', 'stopped', or 'absent'.

    Derived from the profile directory plus a daemon ping rather than colima's
    output format, so a stale socket left behind by a crash reads as 'stopped'.
    """
    if not colima_profile_dir().exists():
        return "absent"
    if not colima_socket_path().exists():
        return "stopped"
    try:
        get_docker_client()
        return "running"
    except DockerException:
        return "stopped"


def read_vm_config() -> dict:
    """Parsed ~/.colima/scad/colima.yaml, or {} when the profile does not exist."""
    path = colima_profile_dir() / "colima.yaml"
    if not path.exists():
        return {}
    parsed = yaml.safe_load(path.read_text())
    return parsed if isinstance(parsed, dict) else {}


def read_vm_mounts() -> list[str]:
    """Host paths currently mounted into the scad VM beyond the $HOME default.

    Normalised with expanduser + resolve to match the fully-resolved strings
    `reconcile_vm_mounts()` builds via `mount_root()` — otherwise a symlink or
    `~` segment in colima.yaml would make the two sides compare unequal and
    force a VM restart on every session.
    """
    mounts = []
    for entry in read_vm_config().get("mounts") or []:
        location = entry.get("location") if isinstance(entry, dict) else str(entry)
        if location:
            mounts.append(str(Path(location).expanduser().resolve()))
    return mounts


def vm_start(mounts: list[str] | None = None) -> None:
    """Start the scad Colima VM, creating the profile on first run.

    Sizing flags are passed only at creation — on an existing profile colima
    reuses its persisted config. When `mounts` is given it must be the COMPLETE
    desired set: colima replaces the configured mount list rather than merging.
    """
    _require_macos("scad vm start")
    _require_colima()

    args = ["start", SCAD_PROFILE]
    if vm_state() == "absent":
        from scad.config import load_settings

        colima = load_settings().colima
        args += [
            "--cpu", str(colima.cpu),
            "--memory", str(colima.memory),
            "--disk", str(colima.disk),
            "--vm-type", colima.vm_type,
            "--mount-type", colima.mount_type,
        ]
    for mount in mounts or []:
        args += ["--mount", f"{mount}:w"]

    _colima(*args)


def vm_stop() -> None:
    """Stop the scad Colima VM. Containers survive; the daemon goes away."""
    _require_macos("scad vm stop")
    _require_colima()
    _colima("stop", SCAD_PROFILE)


def vm_delete() -> None:
    """Delete the scad Colima VM and everything inside it (images, containers)."""
    _require_macos("scad vm delete")
    _require_colima()
    _colima("delete", "--force", SCAD_PROFILE)


def vm_info() -> dict:
    """Descriptive state of the scad VM for `scad vm info`."""
    _require_macos("scad vm info")
    config = read_vm_config()
    return {
        "profile": SCAD_PROFILE,
        "state": vm_state(),
        "socket": str(colima_socket_path()),
        "cpu": config.get("cpu"),
        "memory_gib": config.get("memory"),
        "disk_gib": config.get("disk"),
        "vm_type": config.get("vmType"),
        "mount_type": config.get("mountType"),
        "mounts": read_vm_mounts(),
    }


def ensure_vm_running() -> None:
    """Bring the scad VM up if it is down. No-op on Linux."""
    if not is_macos():
        return
    if vm_state() == "running":
        return
    _require_colima()
    click.echo(f"[scad] Starting scad VM (colima profile '{SCAD_PROFILE}')...")
    vm_start()
    click.echo("[scad] VM ready")


def ensure_gpu_supported(config: "ScadConfig") -> None:
    """Raise if the config asks for GPU passthrough on a platform without it.

    There is no NVIDIA runtime inside a Lima VM on Apple Silicon, so GPU
    passthrough stays Linux + nvidia-container-toolkit only.
    """
    if config.gpu and is_macos():
        raise VMUnsupported(
            "gpu: true is not supported on macOS — there is no GPU passthrough "
            "into the scad Linux VM.\n"
            f"  Remove 'gpu: true' from the '{config.name}' config, or run this "
            "config on a Linux host with nvidia-container-toolkit."
        )


def required_host_paths(config: "ScadConfig") -> list[Path]:
    """Host paths a session for this config needs visible inside the VM.

    Repo sources, declared data mounts, and scad's own state directories. On
    Linux nothing consumes this — the daemon already sees the whole filesystem.
    """
    from scad.config import get_scad_home

    home = Path.home()
    paths: list[Path] = [get_scad_home()]

    candidates = [home / ".claude", home / ".ssh", home / ".gitconfig"]
    if config.claude.claude_md is None:
        # Matches claude_config.get_volume_mounts()'s fallback when claude_md
        # is unset: it mounts home / "CLAUDE.md" implicitly, so that implicit
        # path needs the same existence guard as the other candidates.
        candidates.append(home / "CLAUDE.md")
    for candidate in candidates:
        if candidate.exists():
            paths.append(candidate)

    for repo in config.repos.values():
        paths.append(repo.resolved_path)

    for mount in config.mounts:
        paths.append(Path(mount.host).expanduser().resolve())

    if isinstance(config.claude.claude_md, str):
        claude_md_path = Path(config.claude.claude_md).expanduser().resolve()
        if claude_md_path.exists():
            paths.append(claude_md_path)

    return paths


def partition_paths(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split paths into (inside $HOME, outside $HOME).

    Colima mounts $HOME into the VM by default, so only the second group needs
    to be added to the VM's mount list.
    """
    home = Path.home().resolve()
    inside: list[Path] = []
    outside: list[Path] = []
    for path in paths:
        try:
            path.resolve().relative_to(home)
            inside.append(path)
        except ValueError:
            outside.append(path)
    return inside, outside


def mount_root(path: Path) -> Path:
    """Directory to mount for a path — the path itself, or its parent for a file.

    A path that does not exist on the host is not a directory either, so it
    resolves to its parent — a typo'd `mounts:` host path silently mounts the
    parent directory rather than raising.
    """
    resolved = path.resolve()
    return resolved if resolved.is_dir() else resolved.parent


def path_visible_in_vm(path: Path) -> bool:
    """True when a host path is already reachable inside the scad VM.

    Always True off macOS — there is no VM hop.
    """
    if not is_macos():
        return True
    inside, _ = partition_paths([path])
    if inside:
        return True
    return str(mount_root(path)) in read_vm_mounts()


def reconcile_vm_mounts(config: "ScadConfig") -> bool:
    """Ensure every non-$HOME host path this config needs is mounted in the VM.

    Restarts the VM only when the required mount set is not already covered, so
    repeat sessions on an unchanged config never pay a restart. Non-$HOME data
    paths are mounted writable — scad supports bidirectional data mounts.

    Returns True if the VM was restarted. No-op on Linux.
    """
    if not is_macos():
        return False

    _, outside = partition_paths(required_host_paths(config))
    required = {str(mount_root(p)) for p in outside}
    current = set(read_vm_mounts())
    missing = sorted(required - current)
    if not missing:
        return False

    click.echo("[scad] VM mount set changed — restarting scad VM to expose:")
    for path in missing:
        click.echo(f"[scad]   {path}")
    if vm_state() == "running":
        vm_stop()
    vm_start(mounts=sorted(current | required))
    click.echo("[scad] VM restarted with updated mounts")
    return True
