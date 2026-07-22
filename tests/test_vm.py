"""Tests for the OS-specific Docker provider layer."""

import os
from pathlib import Path
from unittest.mock import patch

import docker
import pytest

from scad.vm import (
    SCAD_PROFILE,
    DockerUnavailable,
    colima_socket_path,
    docker_base_url,
    docker_cli_env,
    get_docker_client,
    is_macos,
)


class TestPlatformDetection:
    @patch("scad.vm.platform.system", return_value="Darwin")
    def test_is_macos_true_on_darwin(self, _mock):
        assert is_macos() is True

    @patch("scad.vm.platform.system", return_value="Linux")
    def test_is_macos_false_on_linux(self, _mock):
        assert is_macos() is False


class TestSocketResolution:
    def test_profile_name_is_scad(self):
        assert SCAD_PROFILE == "scad"

    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    def test_socket_path_is_scad_profile(self, _mock):
        assert colima_socket_path() == Path("/Users/tester/.colima/scad/docker.sock")

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    def test_base_url_on_macos(self, _home, _mac):
        assert docker_base_url() == "unix:///Users/tester/.colima/scad/docker.sock"

    @patch("scad.vm.is_macos", return_value=False)
    def test_base_url_none_on_linux(self, _mac):
        assert docker_base_url() is None


class TestGetDockerClient:
    @patch("scad.vm.is_macos", return_value=False)
    @patch("scad.vm.docker.from_env")
    def test_linux_uses_from_env(self, mock_from_env, _mac):
        client = get_docker_client()
        mock_from_env.assert_called_once_with()
        assert client is mock_from_env.return_value
        client.ping.assert_called_once()

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    @patch("scad.vm.docker.DockerClient")
    def test_macos_uses_colima_socket(self, mock_cls, _home, _mac):
        client = get_docker_client()
        mock_cls.assert_called_once_with(
            base_url="unix:///Users/tester/.colima/scad/docker.sock"
        )
        assert client is mock_cls.return_value

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.docker.DockerClient", side_effect=OSError("no such file"))
    def test_macos_failure_says_scad_vm_start(self, _cls, _mac):
        with pytest.raises(DockerUnavailable) as exc:
            get_docker_client()
        assert "scad vm start" in str(exc.value)

    @patch("scad.vm.is_macos", return_value=False)
    @patch("scad.vm.docker.from_env", side_effect=OSError("no such file"))
    def test_linux_failure_mentions_dockerd(self, _from_env, _mac):
        with pytest.raises(DockerUnavailable) as exc:
            get_docker_client()
        assert "dockerd" in str(exc.value)

    def test_docker_unavailable_is_a_docker_exception(self):
        assert issubclass(DockerUnavailable, docker.errors.DockerException)


class TestDockerCliEnv:
    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    def test_macos_scopes_docker_host(self, _home, _mac):
        env = docker_cli_env()
        assert env["DOCKER_HOST"] == "unix:///Users/tester/.colima/scad/docker.sock"

    @patch("scad.vm.is_macos", return_value=False)
    def test_linux_leaves_env_untouched(self, _mac):
        env = docker_cli_env()
        assert env == dict(os.environ)


class TestNoBareFromEnv:
    """The provider resolver is the only way scad reaches a Docker daemon."""

    def test_no_bare_from_env_in_source(self):
        src = Path(__file__).parent.parent / "src" / "scad"
        offenders = [
            f"{p.name}:{i}"
            for p in sorted(src.glob("*.py"))
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if "docker.from_env()" in line and p.name != "vm.py"
        ]
        assert offenders == [], f"bare docker.from_env() outside vm.py: {offenders}"


class TestVMState:
    @patch("scad.vm.colima_profile_dir")
    def test_absent_when_profile_dir_missing(self, mock_dir, tmp_path):
        from scad.vm import vm_state
        mock_dir.return_value = tmp_path / "nope"
        assert vm_state() == "absent"

    @patch("scad.vm.colima_socket_path")
    @patch("scad.vm.colima_profile_dir")
    def test_stopped_when_socket_missing(self, mock_dir, mock_sock, tmp_path):
        from scad.vm import vm_state
        mock_dir.return_value = tmp_path
        mock_sock.return_value = tmp_path / "docker.sock"
        assert vm_state() == "stopped"

    @patch("scad.vm.get_docker_client")
    @patch("scad.vm.colima_socket_path")
    @patch("scad.vm.colima_profile_dir")
    def test_running_when_socket_pings(self, mock_dir, mock_sock, mock_client, tmp_path):
        from scad.vm import vm_state
        mock_dir.return_value = tmp_path
        sock = tmp_path / "docker.sock"
        sock.touch()
        mock_sock.return_value = sock
        assert vm_state() == "running"

    @patch("scad.vm.get_docker_client", side_effect=DockerUnavailable("down"))
    @patch("scad.vm.colima_socket_path")
    @patch("scad.vm.colima_profile_dir")
    def test_stopped_when_socket_is_stale(self, mock_dir, mock_sock, _client, tmp_path):
        from scad.vm import vm_state
        mock_dir.return_value = tmp_path
        sock = tmp_path / "docker.sock"
        sock.touch()
        mock_sock.return_value = sock
        assert vm_state() == "stopped"


class TestReadVMMounts:
    @patch("scad.vm.colima_profile_dir")
    def test_empty_when_no_config(self, mock_dir, tmp_path):
        from scad.vm import read_vm_mounts
        mock_dir.return_value = tmp_path
        assert read_vm_mounts() == []

    @patch("scad.vm.colima_profile_dir")
    def test_reads_mount_locations(self, mock_dir, tmp_path):
        from scad.vm import read_vm_mounts
        mock_dir.return_value = tmp_path
        (tmp_path / "colima.yaml").write_text(
            "cpu: 2\nmounts:\n"
            "  - location: /Volumes/data\n    writable: true\n"
            "  - location: /srv/models\n    writable: true\n"
        )
        assert read_vm_mounts() == ["/Volumes/data", "/srv/models"]


class TestVMStart:
    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="absent")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_creation_passes_sizing_flags(self, _m, _i, _s, mock_colima, tmp_path, monkeypatch):
        from scad.vm import vm_start
        monkeypatch.setenv("SCAD_HOME", str(tmp_path))
        vm_start()
        args = mock_colima.call_args[0]
        assert args[:2] == ("start", "scad")
        assert "--cpu" in args and "2" in args
        assert "--memory" in args and "4" in args
        assert "--disk" in args and "60" in args
        assert "--vm-type" in args and "vz" in args
        assert "--mount-type" in args and "virtiofs" in args

    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="stopped")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_existing_profile_no_sizing_flags(self, _m, _i, _s, mock_colima):
        from scad.vm import vm_start
        vm_start()
        args = mock_colima.call_args[0]
        assert args == ("start", "scad")

    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="stopped")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_mounts_passed_writable(self, _m, _i, _s, mock_colima):
        from scad.vm import vm_start
        vm_start(mounts=["/Volumes/data", "/srv/models"])
        args = mock_colima.call_args[0]
        assert "--mount" in args
        assert "/Volumes/data:w" in args
        assert "/srv/models:w" in args

    @patch("scad.vm.colima_installed", return_value=False)
    @patch("scad.vm.is_macos", return_value=True)
    def test_errors_when_colima_missing(self, _m, _i):
        from scad.vm import VMUnsupported, vm_start
        with pytest.raises(VMUnsupported) as exc:
            vm_start()
        assert "brew install colima" in str(exc.value)

    @patch("scad.vm.is_macos", return_value=False)
    def test_errors_on_linux(self, _m):
        from scad.vm import VMUnsupported, vm_start
        with pytest.raises(VMUnsupported) as exc:
            vm_start()
        assert "native Docker" in str(exc.value)


class TestEnsureVMRunning:
    @patch("scad.vm.vm_start")
    @patch("scad.vm.is_macos", return_value=False)
    def test_noop_on_linux(self, _m, mock_start):
        from scad.vm import ensure_vm_running
        ensure_vm_running()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_state", return_value="running")
    @patch("scad.vm.is_macos", return_value=True)
    def test_noop_when_already_running(self, _m, _s, mock_start):
        from scad.vm import ensure_vm_running
        ensure_vm_running()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_state", return_value="stopped")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_starts_when_stopped(self, _m, _i, _s, mock_start):
        from scad.vm import ensure_vm_running
        ensure_vm_running()
        mock_start.assert_called_once_with()
