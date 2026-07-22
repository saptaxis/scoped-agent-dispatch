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
from pathlib import Path

import click
import docker
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
    except Exception as exc:  # docker-py leaks requests/OS errors on a dead socket
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
