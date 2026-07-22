"""Tests for the OS-specific Docker provider layer."""

import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import docker
import pytest

from scad.vm import (
    CLAUDE_KEYCHAIN_SERVICE,
    SCAD_PROFILE,
    DockerUnavailable,
    colima_socket_path,
    docker_base_url,
    docker_cli_env,
    get_docker_client,
    is_macos,
    read_claude_credentials,
    stage_claude_credentials,
)


class TestPlatformDetection:
    @patch("scad.vm.platform.system", return_value="Darwin")
    def test_is_macos_true_on_darwin(self, _mock):
        assert is_macos() is True

    @patch("scad.vm.platform.system", return_value="Linux")
    def test_is_macos_false_on_linux(self, _mock):
        assert is_macos() is False


class TestColimaDefaultMountsDocstring:
    """colima_default_mounts()'s docstring must not claim /tmp/colima is a
    real Colima default -- live profile inspection (2026-07-22) shows
    Colima's only actual implicit mount is $HOME."""

    def test_does_not_claim_tmp_colima_is_a_real_colima_default(self):
        import scad.vm as vm
        doc = vm.colima_default_mounts.__doc__ or ""
        assert "is not one of Colima's defaults" in doc or "not a real colima default" in doc.lower()

    def test_still_returns_both_entries(self):
        """Behavior is unchanged by the docstring fix -- both entries still
        get unioned in defensively."""
        from scad.vm import colima_default_mounts
        result = colima_default_mounts()
        assert str(Path("/tmp/colima").resolve()) in result
        assert str(Path.home().resolve()) in result


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

    @patch("scad.vm.is_macos", return_value=False)
    @patch(
        "scad.vm.docker.from_env",
        side_effect=PermissionError(13, "Permission denied"),
    )
    def test_linux_failure_includes_underlying_error_detail(self, _from_env, _mac):
        """`raise ... from exc` alone only sets __cause__, which no caller
        prints -- a Linux user not in the docker group used to see the real
        PermissionError(13); after DockerUnavailable this collapsed to the
        generic "is dockerd running?" guidance, sending them to check
        `systemctl status docker` (which reports it active) instead of the
        actual fix. The underlying exception text must be in the message
        itself, not just __cause__."""
        with pytest.raises(DockerUnavailable) as exc:
            get_docker_client()
        assert "Permission denied" in str(exc.value)
        assert "13" in str(exc.value)

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    @patch(
        "scad.vm.docker.DockerClient",
        side_effect=OSError("No such file or directory: '/Users/tester/.colima/scad/docker.sock'"),
    )
    def test_macos_failure_includes_underlying_error_detail(self, _cls, _home, _mac):
        with pytest.raises(DockerUnavailable) as exc:
            get_docker_client()
        assert "No such file or directory" in str(exc.value)

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


class TestReadClaudeCredentials:
    @patch("scad.vm.is_macos", return_value=False)
    def test_linux_reads_the_file(self, _mac, tmp_path):
        home = tmp_path
        creds_dir = home / ".claude"
        creds_dir.mkdir()
        (creds_dir / ".credentials.json").write_text('{"claudeAiOauth": {}}')
        with patch("scad.vm.Path.home", return_value=home):
            assert read_claude_credentials() == '{"claudeAiOauth": {}}'

    @patch("scad.vm.is_macos", return_value=False)
    def test_linux_missing_file_returns_none(self, _mac, tmp_path):
        with patch("scad.vm.Path.home", return_value=tmp_path):
            assert read_claude_credentials() is None

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.subprocess.run")
    def test_macos_shells_out_to_security(self, mock_run, _mac):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"claudeAiOauth": {}}\n')
        result = read_claude_credentials()
        assert result == '{"claudeAiOauth": {}}'
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "security", "find-generic-password", "-s", CLAUDE_KEYCHAIN_SERVICE, "-w",
        ]
        assert kwargs["timeout"] > 0

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.subprocess.run")
    def test_macos_nonzero_exit_returns_none(self, mock_run, _mac):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert read_claude_credentials() is None

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.subprocess.run")
    def test_macos_empty_stdout_returns_none(self, mock_run, _mac):
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        assert read_claude_credentials() is None

    @patch("scad.vm.is_macos", return_value=True)
    @patch("scad.vm.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="security", timeout=10))
    def test_macos_timeout_returns_none(self, _run, _mac):
        assert read_claude_credentials() is None


class TestStageClaudeCredentials:
    @patch("scad.vm.read_claude_credentials", return_value=None)
    def test_returns_none_when_no_credentials(self, _creds, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path))
        assert stage_claude_credentials("run-1") is None

    @patch("scad.vm.read_claude_credentials", return_value='{"claudeAiOauth": {}}')
    def test_writes_run_scoped_file_mode_0600(self, _creds, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path))
        staged = stage_claude_credentials("run-1")
        assert staged == tmp_path / "runs" / "run-1" / "claude-credentials.json"
        assert staged.read_text() == '{"claudeAiOauth": {}}'
        assert oct(staged.stat().st_mode)[-3:] == "600"

    @patch("scad.vm.read_claude_credentials", return_value='{"fresh": true}')
    def test_overwrites_existing_staged_file(self, _creds, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path))
        staged_dir = tmp_path / "runs" / "run-1"
        staged_dir.mkdir(parents=True)
        staged_path = staged_dir / "claude-credentials.json"
        staged_path.write_text('{"stale": true}')
        staged_path.chmod(0o644)

        result = stage_claude_credentials("run-1")

        assert result == staged_path
        assert staged_path.read_text() == '{"fresh": true}'
        assert oct(staged_path.stat().st_mode)[-3:] == "600"


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

    @patch("scad.vm.colima_profile_dir")
    def test_symlinked_mount_matches_reconcile_normalisation(self, mock_dir, tmp_path):
        """A colima.yaml mount written through a symlink must normalise to the
        same string reconcile_vm_mounts() computes via mount_root() — otherwise
        the two sides never agree and the VM restarts on every session."""
        from scad.vm import mount_root, read_vm_mounts
        mock_dir.return_value = tmp_path

        real_dir = tmp_path / "real" / "data"
        real_dir.mkdir(parents=True)
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        (tmp_path / "colima.yaml").write_text(
            f"mounts:\n  - location: {link_dir}\n    writable: true\n"
        )

        current = read_vm_mounts()
        required = str(mount_root(link_dir))
        assert current == [required]


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
        assert args == (
            "start", "scad",
            "--cpu", "2",
            "--memory", "4",
            "--disk", "60",
            "--vm-type", "vz",
            "--mount-type", "virtiofs",
        )

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
        from scad.vm import colima_default_mounts, vm_start
        vm_start(mounts=["/Volumes/data", "/srv/models"])
        args = mock_colima.call_args[0]
        expected_mounts = sorted(
            {"/Volumes/data", "/srv/models", *colima_default_mounts()}
        )
        expected_args = ["start", "scad"]
        for mount in expected_mounts:
            expected_args += ["--mount", f"{mount}:w"]
        assert args == tuple(expected_args)
        assert args.count("--mount") == len(expected_mounts)

    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="stopped")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_mounts_include_colima_defaults(self, _m, _i, _s, mock_colima):
        """Explicit --mount flags replace Colima's own defaults rather than
        extending them, so vm_start() must union $HOME and /tmp/colima back
        in whenever it passes any mounts at all -- otherwise a session with
        one extra data mount silently loses $HOME (and everything under it,
        including /workspace's git checkouts) inside the VM.

        Regression test for the live macOS bug confirmed 2026-07-22: adding
        a single non-$HOME `--mount` dropped $HOME from the running VM,
        `ls -A ~` inside the VM returned only Docker's empty auto-created
        stub directories, and the container failed to start.
        """
        from scad.vm import vm_start
        vm_start(mounts=["/ext"])
        args = mock_colima.call_args[0]
        mount_flags = [
            args[i + 1] for i in range(len(args)) if args[i] == "--mount"
        ]
        assert f"{Path.home().resolve()}:w" in mount_flags
        assert f"{Path('/tmp/colima').resolve()}:w" in mount_flags
        assert "/ext:w" in mount_flags

    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="stopped")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_no_mounts_emits_no_mount_flags(self, _m, _i, _s, mock_colima):
        """`colima start scad` with no --mount at all reuses the profile's
        persisted mount config -- passing defaults unconditionally would
        silently override whatever the user last configured."""
        from scad.vm import vm_start
        vm_start()
        args = mock_colima.call_args[0]
        assert args == ("start", "scad")
        assert "--mount" not in args

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

    @patch("scad.vm._colima")
    @patch("scad.vm.vm_state", return_value="absent")
    @patch("scad.vm.colima_installed", return_value=True)
    @patch("scad.vm.is_macos", return_value=True)
    def test_malformed_settings_raises_vm_unsupported_not_raw_error(
        self, _m, _i, _s, mock_colima, tmp_path, monkeypatch
    ):
        """A typo'd ~/.scad/settings.yml key must surface as a clean
        VMUnsupported (`[scad] ...`) out of vm_start(), not a bare pydantic
        ValidationError traceback."""
        from scad.vm import VMUnsupported, vm_start
        monkeypatch.setenv("SCAD_HOME", str(tmp_path))
        settings_path = tmp_path / "settings.yml"
        settings_path.write_text("colima:\n  cpus: 6\n")
        with pytest.raises(VMUnsupported) as exc:
            vm_start()
        assert str(settings_path) in str(exc.value)
        mock_colima.assert_not_called()


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


class TestGpuGuard:
    @patch("scad.vm.is_macos", return_value=True)
    def test_gpu_true_errors_on_macos(self, _mac):
        from scad.config import ScadConfig
        from scad.vm import VMUnsupported, ensure_gpu_supported
        config = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}, gpu=True
        )
        with pytest.raises(VMUnsupported) as exc:
            ensure_gpu_supported(config)
        assert "gpu" in str(exc.value).lower()

    @patch("scad.vm.is_macos", return_value=True)
    def test_gpu_false_ok_on_macos(self, _mac):
        from scad.config import ScadConfig
        from scad.vm import ensure_gpu_supported
        config = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}
        )
        ensure_gpu_supported(config)  # must not raise

    @patch("scad.vm.is_macos", return_value=False)
    def test_gpu_true_ok_on_linux(self, _mac):
        from scad.config import ScadConfig
        from scad.vm import ensure_gpu_supported
        config = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}, gpu=True
        )
        ensure_gpu_supported(config)  # must not raise


class TestRequiredHostPaths:
    def test_includes_repos_mounts_and_scad_home(self, tmp_path, monkeypatch):
        from scad.config import ScadConfig
        from scad.vm import required_host_paths
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / "scadhome"))
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(tmp_path / "repo"), "workdir": True}},
            mounts=[{"host": str(tmp_path / "data"), "container": "/data"}],
        )
        paths = [str(p) for p in required_host_paths(config)]
        assert str(tmp_path / "scadhome") in paths
        assert str((tmp_path / "repo").resolve()) in paths
        assert str((tmp_path / "data").resolve()) in paths


class TestPartitionPaths:
    @patch("scad.vm.Path.home", return_value=Path("/Users/tester"))
    def test_splits_on_home(self, _home):
        from scad.vm import partition_paths
        inside, outside = partition_paths(
            [Path("/Users/tester/code"), Path("/Volumes/data"), Path("/Users/tester")]
        )
        assert Path("/Users/tester/code") in inside
        assert Path("/Users/tester") in inside
        assert outside == [Path("/Volumes/data")]


class TestMountRoot:
    def test_directory_maps_to_itself(self, tmp_path):
        from scad.vm import mount_root
        d = tmp_path / "d"
        d.mkdir()
        assert mount_root(d) == d.resolve()

    def test_file_maps_to_parent(self, tmp_path):
        from scad.vm import mount_root
        f = tmp_path / "d" / "CLAUDE.md"
        f.parent.mkdir()
        f.touch()
        assert mount_root(f) == (tmp_path / "d").resolve()


class TestPathVisibleInVM:
    @patch("scad.vm.is_macos", return_value=False)
    def test_linux_always_visible_even_outside_home(self, _mac, tmp_path):
        from scad.vm import path_visible_in_vm
        outside = tmp_path / "outside"
        outside.mkdir()
        assert path_visible_in_vm(outside) is True

    @patch("scad.vm.is_macos", return_value=True)
    def test_macos_home_path_visible_without_consulting_mounts(
        self, _mac, tmp_path, monkeypatch
    ):
        from scad.vm import path_visible_in_vm
        home = tmp_path / "home"
        home.mkdir()
        inside = home / "code"
        inside.mkdir()
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)

        def _boom():
            raise AssertionError(
                "read_vm_mounts should not be consulted for a $HOME path"
            )

        monkeypatch.setattr("scad.vm.read_vm_mounts", _boom)
        assert path_visible_in_vm(inside) is True

    @patch("scad.vm.is_macos", return_value=True)
    def test_macos_outside_home_path_in_mount_list_visible(
        self, _mac, tmp_path, monkeypatch
    ):
        from scad.vm import path_visible_in_vm
        home = tmp_path / "home"
        home.mkdir()
        outside = tmp_path / "volumes" / "data"
        outside.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.read_vm_mounts", lambda: [str(outside.resolve())]
        )
        assert path_visible_in_vm(outside) is True

    @patch("scad.vm.is_macos", return_value=True)
    def test_macos_outside_home_path_not_in_mount_list_not_visible(
        self, _mac, tmp_path, monkeypatch
    ):
        from scad.vm import path_visible_in_vm
        home = tmp_path / "home"
        home.mkdir()
        outside = tmp_path / "volumes" / "data"
        outside.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr("scad.vm.read_vm_mounts", lambda: ["/srv/other"])
        assert path_visible_in_vm(outside) is False


class TestReconcileVMMounts:
    @patch("scad.vm.is_macos", return_value=False)
    def test_noop_on_linux(self, _mac):
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        config = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}
        )
        assert reconcile_vm_mounts(config) is False

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.read_vm_mounts", return_value=[])
    @patch("scad.vm.is_macos", return_value=True)
    def test_all_home_paths_no_restart(self, _mac, _read, mock_stop, mock_start,
                                       tmp_path, monkeypatch):
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        monkeypatch.setattr("scad.vm.Path.home", lambda: tmp_path)
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        (tmp_path / "repo").mkdir()
        config = ScadConfig(
            name="t", repos={"code": {"path": str(tmp_path / "repo"), "workdir": True}}
        )
        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.vm_state", return_value="running")
    @patch("scad.vm.read_vm_mounts", return_value=["/srv/old"])
    @patch("scad.vm.is_macos", return_value=True)
    def test_new_outside_path_restarts_with_full_set(
        self, _mac, _read, _state, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is True
        mock_stop.assert_called_once_with()
        mock_start.assert_called_once_with(
            mounts=sorted(
                ["/srv/old", str(data.resolve()), str(home.resolve()), "/tmp/colima"]
            )
        )

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.is_macos", return_value=True)
    def test_already_mounted_no_restart(self, _mac, mock_stop, mock_start,
                                        tmp_path, monkeypatch):
        """A healthy VM -- one whose mount list already carries the required
        path *and* Colima's defaults -- must not be restarted."""
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setattr(
            "scad.vm.read_vm_mounts",
            lambda: [str(data.resolve()), str(home.resolve()), "/tmp/colima"],
        )
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.vm_state", return_value="running")
    @patch("scad.vm.is_macos", return_value=True)
    def test_existing_mounts_are_preserved(self, _mac, _state, mock_stop, mock_start,
                                           tmp_path, monkeypatch):
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setattr("scad.vm.read_vm_mounts", lambda: ["/srv/old"])
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        reconcile_vm_mounts(config)
        mock_start.assert_called_once_with(
            mounts=sorted(
                ["/srv/old", str(data.resolve()), str(home.resolve()), "/tmp/colima"]
            )
        )

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.is_macos", return_value=True)
    def test_converges_once_colima_yaml_holds_defaults_plus_extra(
        self, _mac, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Regression test for the convergence guarantee: once a restart has
        written $HOME, /tmp/colima, and the extra mount into colima.yaml,
        read_vm_mounts() reports all three back on the *next* reconcile.
        required_host_paths() only ever asks for the non-$HOME one (the
        $HOME-rooted ones are filtered out by partition_paths), so `missing`
        must be empty and reconcile must NOT restart the VM a second time.
        A regression here means the VM restarts on every single session.
        """
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        # Simulate colima.yaml as left behind by a prior restart: the
        # defaults vm_start() unioned in, plus the one extra mount.
        monkeypatch.setattr(
            "scad.vm.read_vm_mounts",
            lambda: sorted([str(home.resolve()), "/tmp/colima", str(data.resolve())]),
        )
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.colima_profile_dir")
    @patch("scad.vm.is_macos", return_value=True)
    def test_converges_on_real_colima_yaml_with_both_tmp_colima_spellings(
        self, _mac, mock_profile_dir, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Regression test for the live macOS restart loop confirmed
        2026-07-22: `/tmp` is a symlink to `/private/tmp` on macOS, and
        colima.yaml can legitimately hold BOTH spellings at once -- scad's
        own `/tmp/colima` default resolves and gets written back by colima
        as `/private/tmp/colima`, while colima also appends its own
        unresolved `/tmp/colima` entry (with no `writable` key) alongside
        it. This drives the real `read_vm_mounts()` over an actual
        colima.yaml fixture shaped exactly like the one pulled live off a
        VM stuck in the restart loop, rather than mocking its return value,
        so a regression in either `read_vm_mounts()`'s resolution or
        `colima_default_mounts()`'s resolution would be caught here.
        """
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts

        home = tmp_path / "home"
        home.mkdir()
        profile_dir = tmp_path / "colima-profile"
        profile_dir.mkdir()
        mock_profile_dir.return_value = profile_dir
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)

        shared_external = tmp_path / "Shared" / "scad-external"
        shared_external.mkdir(parents=True)

        (profile_dir / "colima.yaml").write_text(
            "mounts:\n"
            f"  - location: {shared_external}\n    writable: true\n"
            f"  - location: {home}\n    writable: true\n"
            "  - location: /private/tmp/colima\n    writable: true\n"
            "  - location: /tmp/colima\n"
        )

        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(shared_external), "container": "/data"}],
        )

        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    # -- Self-healing: once a VM has (or is about to get) an explicit mount
    # list, Colima's implicit defaults no longer apply, so reconcile must
    # carry them explicitly. The four cases below are the ones called out in
    # the hardening spec: a fresh VM needing nothing extra must not pay a
    # restart (unchanged from before), a fresh VM needing an extra path picks
    # up the defaults too, an already-healthy VM (defaults + extra already
    # present) is left alone, and a VM whose defaults were dropped -- the
    # live bug this reconcile exists to prevent -- gets repaired.

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.read_vm_mounts", return_value=[])
    @patch("scad.vm.is_macos", return_value=True)
    def test_fresh_vm_nothing_outside_home_no_restart(
        self, _mac, _read, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Fresh VM (current={}), config needs nothing outside $HOME
        (required={}): no defaults are added, missing={}, no restart --
        Colima's own implicit defaults are still in force. Must not regress."""
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        monkeypatch.setattr("scad.vm.Path.home", lambda: tmp_path)
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        (tmp_path / "repo").mkdir()
        config = ScadConfig(
            name="t", repos={"code": {"path": str(tmp_path / "repo"), "workdir": True}}
        )
        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.vm_state", return_value="absent")
    @patch("scad.vm.read_vm_mounts", return_value=[])
    @patch("scad.vm.is_macos", return_value=True)
    def test_fresh_vm_needs_outside_path_restarts_with_defaults(
        self, _mac, _read, _state, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Fresh VM (current={}), config needs /ext outside $HOME: required
        becomes {/ext, $HOME, /tmp/colima} -- the VM is about to get an
        explicit mount list for the first time, so the defaults must be
        carried into it or it comes up with $HOME unmounted."""
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is True
        mock_stop.assert_not_called()  # absent VM: nothing to stop
        mock_start.assert_called_once_with(
            mounts=sorted([str(data.resolve()), str(home.resolve()), "/tmp/colima"])
        )

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.is_macos", return_value=True)
    def test_healthy_vm_with_defaults_and_extra_no_restart(
        self, _mac, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Healthy VM already holding {$HOME, /tmp/colima, /ext}, config
        needs /ext: missing={}, no restart. The 'never pay a restart on an
        unchanged config' guarantee must hold."""
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setattr(
            "scad.vm.read_vm_mounts",
            lambda: [str(home.resolve()), "/tmp/colima", str(data.resolve())],
        )
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is False
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("scad.vm.vm_start")
    @patch("scad.vm.vm_stop")
    @patch("scad.vm.vm_state", return_value="running")
    @patch("scad.vm.is_macos", return_value=True)
    def test_damaged_vm_missing_defaults_restarts_with_repaired_union(
        self, _mac, _state, mock_stop, mock_start, tmp_path, monkeypatch
    ):
        """Damaged VM holding only {/ext} (this is the exact live state left
        behind by the pre-fix code: colima.yaml holds one external mount and
        $HOME is not mounted), config needs /ext: missing={$HOME, /tmp/colima}
        -- reconcile must restart and repair the VM even though the only
        thing required_host_paths() ever asks for (/ext) is already present."""
        from scad.config import ScadConfig
        from scad.vm import reconcile_vm_mounts
        home = tmp_path / "home"
        home.mkdir()
        data = tmp_path / "volumes" / "data"
        data.mkdir(parents=True)
        monkeypatch.setattr("scad.vm.Path.home", lambda: home)
        monkeypatch.setattr(
            "scad.vm.colima_default_mounts",
            lambda: {str(home.resolve()), "/tmp/colima"},
        )
        monkeypatch.setattr("scad.vm.read_vm_mounts", lambda: [str(data.resolve())])
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        (home / "repo").mkdir()
        config = ScadConfig(
            name="t",
            repos={"code": {"path": str(home / "repo"), "workdir": True}},
            mounts=[{"host": str(data), "container": "/data"}],
        )
        assert reconcile_vm_mounts(config) is True
        mock_stop.assert_called_once_with()
        mock_start.assert_called_once_with(
            mounts=sorted([str(data.resolve()), str(home.resolve()), "/tmp/colima"])
        )
