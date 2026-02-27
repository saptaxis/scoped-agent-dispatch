"""Container management tests."""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import click
from scad.config import ScadConfig
import docker
import time as _time

from scad.container import (
    render_build_context,
    generate_run_id,
    generate_branch_name,
    check_branch_exists,
    check_claude_auth,
    resolve_branch,
    create_clones,
    cleanup_clones,
    build_image,
    run_container,
    list_scad_containers,
    list_completed_runs,
    stop_container,
)


@pytest.fixture
def sample_config():
    return ScadConfig(
        name="test",
        repos={"code": {"path": "/tmp/fake", "workdir": True}},
        python={"version": "3.11", "requirements": "requirements.txt"},
        apt_packages=["build-essential"],
    )


class TestRenderBuildContext:
    def test_creates_dockerfile(self, sample_config, tmp_path):
        render_build_context(sample_config, tmp_path)
        dockerfile = tmp_path / "Dockerfile"
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert "FROM python:3.11-slim" in content

    def test_creates_entrypoint(self, sample_config, tmp_path):
        render_build_context(sample_config, tmp_path)
        entrypoint = tmp_path / "entrypoint.sh"
        assert entrypoint.exists()
        content = entrypoint.read_text()
        assert "cd /workspace/code" in content
        assert "git clone" not in content

    def test_creates_bootstrap_files(self, sample_config, tmp_path):
        render_build_context(sample_config, tmp_path)
        assert (tmp_path / "bootstrap-claude.sh").exists()
        assert (tmp_path / "bootstrap-claude.conf").exists()
        conf = (tmp_path / "bootstrap-claude.conf").read_text()
        assert "superpowers@claude-plugins-official" in conf

    def test_copies_requirements(self, sample_config, tmp_path):
        # Create a fake requirements.txt in a fake repo
        fake_repo = Path(sample_config.repos["code"].path)
        fake_repo.mkdir(parents=True, exist_ok=True)
        (fake_repo / "requirements.txt").write_text("numpy\n")

        render_build_context(sample_config, tmp_path)
        req_file = tmp_path / "requirements.txt"
        assert req_file.exists()
        assert "numpy" in req_file.read_text()

    def test_no_requirements_file(self, tmp_path):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake2", "workdir": True}},
        )
        render_build_context(config, tmp_path)
        assert not (tmp_path / "requirements.txt").exists()


class TestGenerateRunId:
    def test_format(self):
        run_id = generate_run_id("lwg")
        assert run_id.startswith("lwg-")
        parts = run_id.split("-")
        assert len(parts) == 3
        assert len(parts[-1]) == 4  # HHMM
        assert len(parts[-2]) == 5  # MonDD

    def test_contains_config_name(self):
        run_id = generate_run_id("my-project")
        assert run_id.startswith("my-project-")


class TestBranchManagement:
    def test_generate_branch_name_format(self):
        name = generate_branch_name()
        assert name.startswith("scad-")
        # Format: scad-MonDD-HHMM
        parts = name.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 5  # MonDD like Feb27
        assert len(parts[2]) == 4  # HHMM like 1430

    @patch("scad.container.subprocess.run")
    def test_check_branch_exists_true(self, mock_run):
        mock_run.return_value = MagicMock(stdout="  plan-22\n")
        assert check_branch_exists(Path("/tmp/repo"), "plan-22") is True

    @patch("scad.container.subprocess.run")
    def test_check_branch_exists_false(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        assert check_branch_exists(Path("/tmp/repo"), "plan-22") is False

    @patch("scad.container.check_branch_exists", return_value=None)
    def test_resolve_branch_auto_generates(self, mock_check):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True, "worktree": True}},
        )
        branch = resolve_branch(config, None)
        assert branch.startswith("scad-")

    @patch("scad.container.check_branch_exists")
    def test_resolve_branch_user_collision_raises(self, mock_check):
        mock_check.return_value = True
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True, "worktree": True}},
        )
        with pytest.raises(click.ClickException, match="already exists"):
            resolve_branch(config, "plan-22")

    @patch("scad.container.check_branch_exists")
    def test_resolve_branch_auto_collision_adds_suffix(self, mock_check):
        # First call: collision. Second call: no collision.
        mock_check.side_effect = [True, False]
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True, "worktree": True}},
        )
        branch = resolve_branch(config, None)
        assert branch.endswith("-2")


class TestCloneLifecycle:
    @patch("scad.container.subprocess.run")
    def test_create_clones_calls_git_clone(self, mock_run, tmp_path):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": str(tmp_path / "repo"), "workdir": True, "worktree": True}},
        )
        with patch("scad.container.Path.home", return_value=tmp_path):
            paths = create_clones(config, "plan-22", "test-run-id")

        # First call: git clone --local, second call: git checkout -b
        assert mock_run.call_count == 2
        clone_args = mock_run.call_args_list[0][0][0]
        assert "clone" in clone_args
        assert "--local" in clone_args
        checkout_args = mock_run.call_args_list[1][0][0]
        assert "checkout" in checkout_args
        assert "-b" in checkout_args
        assert "plan-22" in checkout_args

    @patch("scad.container.subprocess.run")
    def test_create_clones_returns_paths(self, mock_run, tmp_path):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": str(tmp_path / "repo"), "workdir": True, "worktree": True}},
        )
        with patch("scad.container.Path.home", return_value=tmp_path):
            paths = create_clones(config, "plan-22", "test-run-id")

        assert "code" in paths
        expected = tmp_path / ".scad" / "worktrees" / "test-run-id" / "code"
        assert paths["code"] == expected

    @patch("scad.container.subprocess.run")
    def test_create_clones_skips_non_worktree(self, mock_run, tmp_path):
        config = ScadConfig(
            name="test",
            repos={
                "code": {"path": str(tmp_path / "code"), "workdir": True, "worktree": True},
                "ref": {"path": str(tmp_path / "ref"), "worktree": False},
            },
        )
        with patch("scad.container.Path.home", return_value=tmp_path):
            paths = create_clones(config, "plan-22", "test-run-id")

        # ref repo should return its original path, not a clone
        assert paths["ref"] == (tmp_path / "ref").resolve()
        # Two subprocess calls for code (clone + checkout), zero for ref
        assert mock_run.call_count == 2

    @patch("scad.container.shutil.rmtree")
    def test_cleanup_clones_removes_directory(self, mock_rmtree, tmp_path):
        clone_base = tmp_path / ".scad" / "worktrees" / "test-run-id"
        clone_base.mkdir(parents=True)

        with patch("scad.container.Path.home", return_value=tmp_path):
            cleanup_clones("test-run-id")

        mock_rmtree.assert_called_once_with(clone_base)

    def test_cleanup_clones_noop_if_missing(self, tmp_path):
        with patch("scad.container.Path.home", return_value=tmp_path):
            cleanup_clones("nonexistent")  # should not raise


class TestBuildImage:
    @patch("scad.container.docker.from_env")
    def test_build_streams_output(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_client.api.build.return_value = iter([
            {"stream": "Step 1/5 : FROM python:3.11-slim\n"},
            {"stream": "Step 2/5 : RUN apt-get update\n"},
        ])

        lines = list(build_image(sample_config, tmp_path))
        assert len(lines) == 2
        assert "Step 1/5" in lines[0]
        mock_client.api.build.assert_called_once()

    @patch("scad.container.docker.from_env")
    def test_build_raises_on_error(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_client.api.build.return_value = iter([
            {"stream": "Step 1/5 : FROM python:3.11-slim\n"},
            {"error": "something went wrong"},
        ])

        with pytest.raises(docker.errors.BuildError):
            list(build_image(sample_config, tmp_path))

    @patch("scad.container.docker.from_env")
    def test_build_skips_empty_lines(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_client.api.build.return_value = iter([
            {"stream": "Step 1/5\n"},
            {"stream": "\n"},
            {"stream": "Step 2/5\n"},
        ])

        lines = list(build_image(sample_config, tmp_path))
        assert len(lines) == 2


class TestListScadContainers:
    @patch("scad.container.docker.from_env")
    def test_lists_running_containers(self, mock_docker):
        mock_container = MagicMock()
        mock_container.labels = {
            "scad.managed": "true",
            "scad.run_id": "test-Feb26-1430",
            "scad.config": "myconfig",
            "scad.branch": "test",
            "scad.started": "2026-02-26T14:30:00Z",
        }
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_docker.return_value = mock_client

        result = list_scad_containers()
        assert len(result) == 1
        assert result[0]["run_id"] == "test-Feb26-1430"
        assert result[0]["status"] == "running"

    @patch("scad.container.docker.from_env")
    def test_empty_when_none_running(self, mock_docker):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_docker.return_value = mock_client

        result = list_scad_containers()
        assert result == []


class TestListCompletedRuns:
    def test_reads_status_files(self, tmp_path):
        status = {
            "run_id": "test-Feb26-1430",
            "config": "myconfig",
            "branch": "test",
            "exit_code": 0,
            "started": "2026-02-26T14:30:00Z",
            "finished": "2026-02-26T15:00:00Z",
        }
        (tmp_path / "test-Feb26-1430.status.json").write_text(json.dumps(status))

        result = list_completed_runs(logs_dir=tmp_path)
        assert len(result) == 1
        assert result[0]["run_id"] == "test-Feb26-1430"
        assert result[0]["status"] == "exited(0)"

    def test_empty_logs_dir(self, tmp_path):
        result = list_completed_runs(logs_dir=tmp_path)
        assert result == []

    def test_skips_malformed_json(self, tmp_path):
        (tmp_path / "bad.status.json").write_text("not json{{{")
        result = list_completed_runs(logs_dir=tmp_path)
        assert result == []


class TestStopContainer:
    @patch("scad.container.docker.from_env")
    def test_stops_existing_container(self, mock_docker):
        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = stop_container("test-Feb26-1430")
        assert result is True
        mock_client.containers.get.assert_called_with("scad-test-Feb26-1430")
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once()

    @patch("scad.container.docker.from_env")
    def test_returns_false_for_missing(self, mock_docker):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        result = stop_container("nonexistent")
        assert result is False


class TestRunContainerClaudeMd:
    @patch("scad.container.docker.from_env")
    def test_auto_mounts_claude_md(self, mock_docker, sample_config, tmp_path):
        """Default: auto-mount ~/CLAUDE.md if it exists."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Instructions")

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        claude_md_mount = volumes.get(str(claude_md))
        assert claude_md_mount is not None
        assert claude_md_mount["mode"] == "ro"
        assert claude_md_mount["bind"] == "/home/scad/CLAUDE.md"

    @patch("scad.container.docker.from_env")
    def test_skips_claude_md_if_missing(self, mock_docker, sample_config, tmp_path):
        """Default: no mount if ~/CLAUDE.md doesn't exist."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for path in volumes:
            assert "CLAUDE.md" not in path

    @patch("scad.container.docker.from_env")
    def test_claude_md_disabled(self, mock_docker, tmp_path):
        """claude_md: false disables auto-mount even if file exists."""
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"claude_md": False},
        )
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Instructions")

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for path in volumes:
            assert "CLAUDE.md" not in path

    @patch("scad.container.docker.from_env")
    def test_claude_md_custom_path(self, mock_docker, tmp_path):
        """claude_md: ~/custom/file.md mounts that file instead."""
        custom_md = tmp_path / "custom" / "instructions.md"
        custom_md.parent.mkdir(parents=True)
        custom_md.write_text("# Custom")

        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"claude_md": str(custom_md)},
        )
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        custom_mount = volumes.get(str(custom_md))
        assert custom_mount is not None
        assert custom_mount["mode"] == "ro"
        assert custom_mount["bind"] == "/home/scad/CLAUDE.md"


class TestRunContainerAuth:
    @patch("scad.container.docker.from_env")
    def test_mounts_credentials_to_staging_path(self, mock_docker, sample_config, tmp_path):
        """Credentials mounted to staging path, not final ~/.claude location."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = claude_dir / ".credentials.json"
        creds.write_text('{"claudeAiOauth": {}}')

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        creds_mount = volumes.get(str(creds))
        assert creds_mount is not None
        assert creds_mount["mode"] == "ro"
        assert creds_mount["bind"] == "/mnt/host-claude-credentials.json"

    @patch("scad.container.docker.from_env")
    def test_no_auth_mount_when_no_credentials(self, mock_docker, sample_config, tmp_path):
        """No auth mount if credentials file doesn't exist."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": Path("/tmp/fake")}
        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for path in volumes:
            assert ".credentials" not in path


class TestRunContainerWorktreeMounts:
    @patch("scad.container.docker.from_env")
    def test_mounts_worktree_at_workspace(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": tmp_path / "worktrees" / "code"}

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "plan-22", "test-run", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        wt_mount = volumes.get(str(worktree_paths["code"]))
        assert wt_mount is not None
        assert wt_mount["bind"] == "/workspace/code"
        assert wt_mount["mode"] == "rw"

    @patch("scad.container.docker.from_env")
    def test_no_mnt_repos_mount(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": tmp_path / "worktrees" / "code"}

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "plan-22", "test-run", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for bind_info in volumes.values():
            assert not bind_info["bind"].startswith("/mnt/repos")

    @patch("scad.container.docker.from_env")
    def test_direct_mount_is_readonly(self, mock_docker, tmp_path):
        config = ScadConfig(
            name="test",
            repos={
                "code": {"path": str(tmp_path / "code"), "workdir": True, "worktree": True},
                "ref": {"path": str(tmp_path / "ref"), "worktree": False},
            },
        )
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {
            "code": tmp_path / "worktrees" / "code",
            "ref": (tmp_path / "ref").resolve(),
        }

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(config, "plan-22", "test-run", worktree_paths)

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        ref_mount = volumes.get(str((tmp_path / "ref").resolve()))
        assert ref_mount is not None
        assert ref_mount["mode"] == "ro"

    @patch("scad.container.docker.from_env")
    def test_no_branch_name_env(self, mock_docker, sample_config, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        worktree_paths = {"code": tmp_path / "worktrees" / "code"}

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "plan-22", "test-run", worktree_paths)

        env = mock_client.containers.run.call_args[1]["environment"]
        assert "BRANCH_NAME" not in env
        assert "RUN_ID" in env


class TestCheckClaudeAuth:
    def test_missing_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        valid, hours = check_claude_auth()
        assert valid is False
        assert hours == 0.0

    def test_expired_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        expired_ms = (_time.time() - 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": expired_ms}})
        )
        valid, hours = check_claude_auth()
        assert valid is False
        assert hours == 0.0

    def test_valid_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        future_ms = (_time.time() + 4 * 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": future_ms}})
        )
        valid, hours = check_claude_auth()
        assert valid is True
        assert 3.9 < hours < 4.1

    def test_warns_under_one_hour(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        soon_ms = (_time.time() + 1800) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": soon_ms}})
        )
        valid, hours = check_claude_auth()
        assert valid is True
        assert hours < 1.0

    def test_malformed_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        (creds_dir / ".credentials.json").write_text("not json")
        valid, hours = check_claude_auth()
        assert valid is False
        assert hours == 0.0


