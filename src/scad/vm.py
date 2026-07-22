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

import click
import docker
import yaml
from docker.errors import DockerException

SCAD_PROFILE = "scad"


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
    """Host paths currently mounted into the scad VM beyond the $HOME default."""
    mounts = []
    for entry in read_vm_config().get("mounts") or []:
        location = entry.get("location") if isinstance(entry, dict) else str(entry)
        if location:
            mounts.append(str(Path(location).expanduser()))
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
