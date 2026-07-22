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
