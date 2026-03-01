"""Container management tests."""

import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import click
from scad.config import ScadConfig, RepoConfig, PythonConfig, ClaudeConfig
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
    clean_run,
    build_image,
    run_container,
    list_scad_containers,
    list_completed_runs,
    stop_container,
    fetch_to_host,
    sync_from_host,
    log_event,
    gc,
    get_all_sessions,
    get_session_info,
    get_session_cost,
    get_project_status,
    refresh_credentials,
    validate_run_id,
    _migrate_worktrees,
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
    def test_format_with_tag(self):
        run_id = generate_run_id("lwg", "plan07")
        assert run_id.startswith("lwg-plan07-")
        parts = run_id.split("-")
        assert len(parts) == 4  # config-tag-MonDD-HHMM
        assert len(parts[-1]) == 4  # HHMM
        assert len(parts[-2]) == 5  # MonDD

    def test_contains_config_and_tag(self):
        run_id = generate_run_id("my-project", "bugfix")
        assert "my-project-bugfix-" in run_id

    def test_notag_default(self):
        run_id = generate_run_id("demo", "notag")
        assert "demo-notag-" in run_id


class TestBranchManagement:
    def test_generate_branch_name_format(self):
        name = generate_branch_name("demo", "plan07")
        assert name.startswith("scad-demo-plan07-")
        # Format: scad-config-tag-MonDD-HHMM
        parts = name.split("-")
        assert len(parts) == 5
        assert parts[0] == "scad"
        assert parts[1] == "demo"
        assert parts[2] == "plan07"
        assert len(parts[3]) == 5  # MonDD like Feb27
        assert len(parts[4]) == 4  # HHMM like 1430

    def test_generate_branch_name_includes_config(self):
        """Branch name includes config name for shared-repo disambiguation."""
        branch = generate_branch_name("demo", "plan08")
        assert branch.startswith("scad-demo-plan08-")
        parts = branch.split("-")
        assert parts[0] == "scad"
        assert parts[1] == "demo"
        assert parts[2] == "plan08"

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
    def test_create_clones_returns_paths(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / ".scad" / "runs")
        config = ScadConfig(
            name="test",
            repos={"code": {"path": str(tmp_path / "repo"), "workdir": True, "worktree": True}},
        )
        paths = create_clones(config, "plan-22", "test-run-id")

        assert "code" in paths
        expected = tmp_path / ".scad" / "runs" / "test-run-id" / "worktrees" / "code"
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
    def test_cleanup_clones_removes_directory(self, mock_rmtree, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / ".scad" / "runs")
        clone_base = tmp_path / ".scad" / "runs" / "test-run-id" / "worktrees"
        clone_base.mkdir(parents=True)

        cleanup_clones("test-run-id")

        mock_rmtree.assert_called_once_with(clone_base)

    def test_cleanup_clones_noop_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / ".scad" / "runs")
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
    def test_stops_running_container(self, mock_from_env):
        mock_container = MagicMock()
        mock_from_env.return_value.containers.get.return_value = mock_container
        result = stop_container("test-run")
        assert result is True
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_not_called()  # Changed: no remove on stop

    @patch("scad.container.docker.from_env")
    def test_returns_false_for_missing_container(self, mock_from_env):
        mock_from_env.return_value.containers.get.side_effect = (
            docker.errors.NotFound("not found")
        )
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


class TestRunDirectory:
    def test_create_clones_creates_run_dir(self, tmp_path, monkeypatch):
        """create_clones also creates ~/.scad/runs/<run-id>/claude/."""
        monkeypatch.setattr("scad.container.SCAD_DIR", tmp_path / ".scad")
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / ".scad" / "runs")
        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(tmp_path / "repo"), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )
        # Create a real git repo to clone from
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo_dir), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)

        create_clones(config, "test-branch", "test-run-1234")

        run_dir = tmp_path / ".scad" / "runs" / "test-run-1234" / "claude"
        assert run_dir.exists()

    @patch("scad.container.docker")
    def test_run_container_mounts_run_dir(self, mock_docker, tmp_path, monkeypatch):
        """run_container mounts ~/.scad/runs/<run-id>/claude/ as /home/scad/.claude/."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / ".scad" / "runs")

        runs_dir = tmp_path / ".scad" / "runs" / "test-run" / "claude"
        runs_dir.mkdir(parents=True)

        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(tmp_path), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )
        worktree_paths = {"code": tmp_path / "clone"}
        (tmp_path / "clone").mkdir()

        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_docker.from_env.return_value.containers.run.return_value = mock_container

        run_container(config, "test-branch", "test-run", worktree_paths)

        call_kwargs = mock_docker.from_env.return_value.containers.run.call_args
        volumes = call_kwargs[1]["volumes"]
        claude_mount = volumes[str(runs_dir)]
        assert claude_mount["bind"] == "/home/scad/.claude"
        assert claude_mount["mode"] == "rw"


class TestCleanRun:
    @patch("scad.container.docker")
    def test_removes_container(self, mock_docker, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_container = MagicMock()
        mock_docker.from_env.return_value.containers.get.return_value = mock_container
        clean_run("test-run")
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

    @patch("scad.container.docker")
    def test_removes_clones(self, mock_docker, tmp_path, monkeypatch):
        run_dir = tmp_path / "runs" / "test-run"
        clone_dir = run_dir / "worktrees"
        clone_dir.mkdir(parents=True)
        (clone_dir / "somefile").touch()
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_docker.from_env.return_value.containers.get.side_effect = docker.errors.NotFound("x")
        clean_run("test-run")
        assert not clone_dir.exists()

    @patch("scad.container.docker")
    def test_removes_run_dir(self, mock_docker, tmp_path, monkeypatch):
        run_dir = tmp_path / "runs" / "test-run"
        run_dir.mkdir(parents=True)
        (run_dir / "claude").mkdir()
        (run_dir / "fetches.log").touch()
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_docker.from_env.return_value.containers.get.side_effect = docker.errors.NotFound("x")
        clean_run("test-run")
        assert not run_dir.exists()

    @patch("scad.container.docker")
    def test_succeeds_even_if_nothing_exists(self, mock_docker, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_docker.from_env.return_value.containers.get.side_effect = docker.errors.NotFound("x")
        clean_run("nonexistent")  # Should not raise


class TestFetchToHost:
    def test_fetches_branch_to_source(self, tmp_path, monkeypatch):
        """fetch_to_host copies branch from clone to source repo."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")

        # Create source repo
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)

        # Create clone with a branch and commit
        clone_dir = tmp_path / "runs" / "test-run" / "worktrees" / "code"
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--local", str(source), str(clone_dir)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone_dir), "checkout", "-b", "test-branch"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone_dir), "commit", "--allow-empty", "-m", "work"], check=True, capture_output=True)

        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(source), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )

        results = fetch_to_host("test-run", config)

        # Branch should now exist in source
        branches = subprocess.run(
            ["git", "-C", str(source), "branch", "--list", "test-branch"],
            capture_output=True, text=True
        )
        assert "test-branch" in branches.stdout
        assert len(results) == 1
        assert results[0]["repo"] == "code"

    def test_fetches_multiple_repos(self, tmp_path, monkeypatch):
        """fetch_to_host handles multiple repos."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")

        sources = {}
        worktrees_base = tmp_path / "runs" / "test-run" / "worktrees"
        worktrees_base.mkdir(parents=True)
        for name in ["code", "docs"]:
            src = tmp_path / f"source-{name}"
            src.mkdir()
            subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(src), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
            clone = worktrees_base / name
            subprocess.run(["git", "clone", "--local", str(src), str(clone)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "checkout", "-b", "feat"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(clone), "commit", "--allow-empty", "-m", "work"], check=True, capture_output=True)
            sources[name] = src

        config = ScadConfig(
            name="test",
            repos={
                "code": RepoConfig(path=str(sources["code"]), workdir=True),
                "docs": RepoConfig(path=str(sources["docs"]), add_dir=True),
            },
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )

        results = fetch_to_host("test-run", config)
        assert len(results) == 2

    def test_writes_events_log(self, tmp_path, monkeypatch):
        """fetch_to_host appends to events.log (not fetches.log)."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")

        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
        clone_dir = tmp_path / "runs" / "test-run" / "worktrees" / "code"
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--local", str(source), str(clone_dir)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone_dir), "checkout", "-b", "feat"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone_dir), "commit", "--allow-empty", "-m", "work"], check=True, capture_output=True)

        run_dir = tmp_path / "runs" / "test-run"

        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(source), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )

        fetch_to_host("test-run", config)

        events_log = (run_dir / "events.log").read_text()
        assert "fetch" in events_log
        assert "code" in events_log
        assert "feat" in events_log
        # Old fetches.log should NOT be created
        assert not (run_dir / "fetches.log").exists()

    def test_no_worktree_clones_raises(self, tmp_path, monkeypatch):
        """fetch_to_host raises if clone dir doesn't exist."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(tmp_path), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )
        with pytest.raises(FileNotFoundError):
            fetch_to_host("nonexistent-run", config)


class TestSyncFromHost:
    def test_syncs_new_branches_into_clone(self, tmp_path, monkeypatch):
        """sync_from_host fetches source repo refs into clone."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")

        # Create source repo with a branch
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)

        # Clone it
        clone = tmp_path / "runs" / "test-run" / "worktrees" / "code"
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--local", str(source), str(clone)], check=True, capture_output=True)

        # Add a new branch to source AFTER cloning
        subprocess.run(["git", "-C", str(source), "checkout", "-b", "new-feature"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "new work"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "checkout", "-"], check=True, capture_output=True)

        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(source), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )

        results = sync_from_host("test-run", config)

        # Clone should now know about new-feature
        branches = subprocess.run(
            ["git", "-C", str(clone), "branch", "-r"],
            capture_output=True, text=True
        )
        assert "new-feature" in branches.stdout
        assert len(results) == 1

    def test_sync_logs_events(self, tmp_path, monkeypatch):
        """sync_from_host logs sync events to events.log."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")

        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"], check=True, capture_output=True)
        clone = tmp_path / "runs" / "test-run" / "worktrees" / "code"
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--local", str(source), str(clone)], check=True, capture_output=True)

        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(source), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )

        sync_from_host("test-run", config)

        events_log = tmp_path / "runs" / "test-run" / "events.log"
        assert events_log.exists()
        assert "sync" in events_log.read_text()
        assert "code" in events_log.read_text()

    def test_no_clones_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path=str(tmp_path), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(dangerously_skip_permissions=True),
        )
        with pytest.raises(FileNotFoundError):
            sync_from_host("nonexistent", config)


class TestLogEvent:
    def test_creates_events_log(self, tmp_path, monkeypatch):
        """log_event creates events.log in run dir."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        log_event("test-run", "start", "config=demo branch=scad-Feb28-1400")
        log_file = tmp_path / "runs" / "test-run" / "events.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "start" in content
        assert "config=demo" in content

    def test_appends_multiple_events(self, tmp_path, monkeypatch):
        """log_event appends, doesn't overwrite."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        log_event("test-run", "start", "config=demo branch=feat")
        log_event("test-run", "fetch", "code feat → /source")
        content = (tmp_path / "runs" / "test-run" / "events.log").read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "start" in lines[0]
        assert "fetch" in lines[1]

    def test_event_without_details(self, tmp_path, monkeypatch):
        """log_event works with no details."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        log_event("test-run", "stop")
        content = (tmp_path / "runs" / "test-run" / "events.log").read_text()
        assert "stop" in content

    def test_event_has_iso_timestamp(self, tmp_path, monkeypatch):
        """Each event line starts with an ISO timestamp."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        log_event("test-run", "attach")
        content = (tmp_path / "runs" / "test-run" / "events.log").read_text()
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", content)


class TestGetAllSessions:
    @patch("scad.container.docker.from_env")
    def test_returns_running_containers(self, mock_docker, tmp_path, monkeypatch):
        """get_all_sessions includes running containers."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_container = MagicMock()
        mock_container.labels = {
            "scad.managed": "true",
            "scad.run_id": "demo-Feb28-1400",
            "scad.config": "demo",
            "scad.branch": "scad-Feb28-1400",
            "scad.started": "2026-02-28T14:00:00Z",
        }
        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_client.containers.get.return_value = mock_container
        mock_container.status = "running"
        mock_docker.return_value = mock_client

        results = get_all_sessions()
        assert len(results) >= 1
        running = [r for r in results if r["run_id"] == "demo-Feb28-1400"]
        assert running[0]["container"] == "running"

    @patch("scad.container.docker.from_env")
    def test_includes_stopped_sessions(self, mock_docker, tmp_path, monkeypatch):
        """get_all_sessions includes sessions with stopped containers."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []

        stopped_container = MagicMock()
        stopped_container.status = "exited"
        stopped_container.labels = {
            "scad.config": "demo",
            "scad.branch": "scad-Feb28-1400",
            "scad.started": "2026-02-28T14:00:00Z",
        }
        mock_client.containers.get.return_value = stopped_container
        mock_docker.return_value = mock_client

        run_dir = tmp_path / "runs" / "demo-Feb28-1400"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text(
            "2026-02-28T14:00 start config=demo branch=scad-Feb28-1400\n"
        )
        (run_dir / "worktrees").mkdir(parents=True)

        results = get_all_sessions()
        assert len(results) == 1
        assert results[0]["container"] == "stopped"

    @patch("scad.container.docker.from_env")
    def test_includes_removed_sessions(self, mock_docker, tmp_path, monkeypatch):
        """get_all_sessions shows removed when container gone but clones exist."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.containers.get.side_effect = docker.errors.NotFound("gone")
        mock_docker.return_value = mock_client

        run_dir = tmp_path / "runs" / "old-Feb27-0900"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text(
            "2026-02-27T09:00 start config=demo branch=scad-Feb27-0900\n"
        )
        (run_dir / "worktrees").mkdir(parents=True)

        results = get_all_sessions()
        assert len(results) == 1
        assert results[0]["container"] == "removed"
        assert results[0]["clones"] == "yes"

    @patch("scad.container.docker.from_env")
    def test_includes_cleaned_sessions(self, mock_docker, tmp_path, monkeypatch):
        """get_all_sessions shows cleaned when only events.log remains."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.containers.get.side_effect = docker.errors.NotFound("gone")
        mock_docker.return_value = mock_client

        run_dir = tmp_path / "runs" / "ancient-Feb26-1000"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text(
            "2026-02-26T10:00 start config=demo branch=scad-Feb26-1000\n"
            "2026-02-26T11:00 stop\n"
        )

        results = get_all_sessions()
        assert len(results) == 1
        assert results[0]["container"] == "cleaned"
        assert results[0]["clones"] == "-"


class TestGetSessionInfo:
    def test_basic_info_from_events_log(self, tmp_path, monkeypatch):
        """get_session_info parses config and branch from events.log."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "demo-Feb28-1400"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text(
            "2026-02-28T14:00 start config=demo branch=scad-Feb28-1400\n"
            "2026-02-28T14:30 fetch code scad-Feb28-1400 → /src\n"
        )

        with patch("scad.container.docker.from_env") as mock_docker:
            mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("x")
            info = get_session_info("demo-Feb28-1400")

        assert info["run_id"] == "demo-Feb28-1400"
        assert info["config"] == "demo"
        assert info["branch"] == "scad-Feb28-1400"
        assert len(info["events"]) == 2

    def test_container_state_running(self, tmp_path, monkeypatch):
        """get_session_info shows container as running."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "demo-Feb28-1400"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text("2026-02-28T14:00 start config=demo branch=feat\n")

        with patch("scad.container.docker.from_env") as mock_docker:
            mock_container = MagicMock()
            mock_container.status = "running"
            mock_docker.return_value.containers.get.return_value = mock_container
            info = get_session_info("demo-Feb28-1400")

        assert info["container"] == "running"

    def test_clone_paths(self, tmp_path, monkeypatch):
        """get_session_info lists clone directories."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "demo-Feb28-1400"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text("2026-02-28T14:00 start config=demo branch=feat\n")
        clone_dir = run_dir / "worktrees"
        (clone_dir / "demo-code").mkdir(parents=True)
        (clone_dir / "demo-docs").mkdir(parents=True)

        with patch("scad.container.docker.from_env") as mock_docker:
            mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("x")
            info = get_session_info("demo-Feb28-1400")

        assert "demo-code" in info["clones"]
        assert "demo-docs" in info["clones"]

    def test_claude_sessions(self, tmp_path, monkeypatch):
        """get_session_info finds Claude session .jsonl files."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "demo-Feb28-1400"
        run_dir.mkdir(parents=True)
        (run_dir / "events.log").write_text("2026-02-28T14:00 start config=demo branch=feat\n")
        projects_dir = run_dir / "claude" / "projects" / "encoded-path"
        projects_dir.mkdir(parents=True)
        (projects_dir / "abc12345.jsonl").write_text("{}\n")

        with patch("scad.container.docker.from_env") as mock_docker:
            mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("x")
            info = get_session_info("demo-Feb28-1400")

        assert len(info["claude_sessions"]) == 1
        assert info["claude_sessions"][0]["id"] == "abc12345"

    def test_nonexistent_run_raises(self, tmp_path, monkeypatch):
        """get_session_info raises for unknown run ID."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        with pytest.raises(FileNotFoundError, match="No session found"):
            get_session_info("nonexistent")


class TestRefreshCredentials:
    @patch("scad.container.docker.from_env")
    def test_copies_credentials_to_container(self, mock_docker, tmp_path, monkeypatch):
        """refresh_credentials copies host creds into container."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        future_ms = (_time.time() + 4 * 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": future_ms}})
        )
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = MagicMock(exit_code=0)

        hours = refresh_credentials("test-run")

        mock_container.exec_run.assert_called_once_with(
            "cp /mnt/host-claude-credentials.json /home/scad/.claude/.credentials.json"
        )
        assert hours > 3.0

    @patch("scad.container.docker.from_env")
    def test_logs_refresh_event(self, mock_docker, tmp_path, monkeypatch):
        """refresh_credentials logs to events.log."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        future_ms = (_time.time() + 4 * 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": future_ms}})
        )
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container

        refresh_credentials("test-run")

        events_log = tmp_path / "runs" / "test-run" / "events.log"
        assert events_log.exists()
        assert "refresh" in events_log.read_text()
        assert "credentials" in events_log.read_text()

    @patch("scad.container.docker.from_env")
    def test_raises_if_credentials_expired(self, mock_docker, tmp_path, monkeypatch):
        """refresh_credentials raises if host credentials expired."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        expired_ms = (_time.time() - 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": expired_ms}})
        )
        with pytest.raises(click.ClickException, match="expired"):
            refresh_credentials("test-run")

    @patch("scad.container.docker.from_env")
    def test_raises_if_container_not_running(self, mock_docker, tmp_path, monkeypatch):
        """refresh_credentials raises if container is not running."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        future_ms = (_time.time() + 4 * 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": future_ms}})
        )
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker.return_value.containers.get.return_value = mock_container

        with pytest.raises(click.ClickException, match="not running"):
            refresh_credentials("test-run")

    @patch("scad.container.docker.from_env")
    def test_raises_if_container_not_found(self, mock_docker, tmp_path, monkeypatch):
        """refresh_credentials raises if container doesn't exist."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        future_ms = (_time.time() + 4 * 3600) * 1000
        (creds_dir / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": future_ms}})
        )
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("gone")

        with pytest.raises(click.ClickException, match="not found"):
            refresh_credentials("test-run")


class TestRunContainerTelemetry:
    @patch("scad.container.docker.from_env")
    def test_disables_telemetry(self, mock_docker, sample_config, tmp_path, monkeypatch):
        """run_container sets telemetry disable env vars."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        # Create required dirs
        run_dir = tmp_path / "runs" / "test-run" / "claude"
        run_dir.mkdir(parents=True)

        worktree_paths = {"code": tmp_path / "worktrees" / "test-run" / "code"}

        run_container(sample_config, "feat", "test-run", worktree_paths)

        call_kwargs = mock_client.containers.run.call_args[1]
        env = call_kwargs["environment"]
        assert env["DISABLE_TELEMETRY"] == "1"
        assert env["DISABLE_ERROR_REPORTING"] == "1"
        assert env["CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY"] == "1"


class TestGetSessionCost:
    def test_returns_cost_from_ccusage(self, tmp_path, monkeypatch):
        """get_session_cost parses ccusage JSON output."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "test-run" / "claude"
        run_dir.mkdir(parents=True)

        ccusage_output = json.dumps([{
            "total_cost": 2.34,
            "total_input_tokens": 12450,
            "total_output_tokens": 8200,
            "total_turns": 47,
        }])

        with patch("scad.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=ccusage_output,
            )
            cost = get_session_cost("test-run")

        assert cost is not None
        assert cost["total_cost"] == 2.34
        assert cost["total_input_tokens"] == 12450

    def test_returns_none_on_failure(self, tmp_path, monkeypatch):
        """get_session_cost returns None if ccusage fails."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        run_dir = tmp_path / "runs" / "test-run" / "claude"
        run_dir.mkdir(parents=True)

        with patch("scad.container.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("npx not found")
            cost = get_session_cost("test-run")

        assert cost is None

    def test_fallback_to_stream_json(self, tmp_path, monkeypatch):
        """get_session_cost falls back to stream-json final record."""
        monkeypatch.setattr("scad.container.RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr("scad.container.SCAD_DIR", tmp_path)
        run_dir = tmp_path / "runs" / "test-run" / "claude"
        run_dir.mkdir(parents=True)

        # Create a stream log with cost in final line
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True)
        stream_log = logs_dir / "test-run.stream.jsonl"
        stream_log.write_text(
            '{"type":"tool_use"}\n'
            '{"type":"result","cost_usd":1.50,"num_turns":20,"duration_ms":120000}\n'
        )

        with patch("scad.container.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("npx not found")
            cost = get_session_cost("test-run")

        assert cost is not None
        assert cost["total_cost"] == 1.50


class TestGetProjectStatus:
    @patch("scad.container.get_all_sessions")
    @patch("scad.container.get_session_cost")
    def test_filters_by_config(self, mock_cost, mock_sessions):
        """get_project_status returns only sessions matching config name."""
        mock_sessions.return_value = [
            {"run_id": "demo-plan07-Mar01-1400", "config": "demo", "branch": "scad-plan07-Mar01-1400",
             "started": "2026-03-01T14:00", "container": "running", "clones": "yes"},
            {"run_id": "other-Mar01-1200", "config": "other", "branch": "scad-Mar01-1200",
             "started": "2026-03-01T12:00", "container": "stopped", "clones": "yes"},
            {"run_id": "demo-bugfix-Feb28-0900", "config": "demo", "branch": "scad-bugfix-Feb28-0900",
             "started": "2026-02-28T09:00", "container": "cleaned", "clones": "-"},
        ]
        mock_cost.return_value = None

        status = get_project_status("demo")
        assert status["config"] == "demo"
        assert status["total_sessions"] == 2
        assert len(status["sessions"]) == 2
        assert all(s["config"] == "demo" for s in status["sessions"])

    @patch("scad.container.get_all_sessions")
    @patch("scad.container.get_session_cost")
    def test_aggregates_cost(self, mock_cost, mock_sessions):
        """get_project_status sums cost across sessions."""
        mock_sessions.return_value = [
            {"run_id": "demo-a-Mar01-1400", "config": "demo", "branch": "b1",
             "started": "2026-03-01T14:00", "container": "running", "clones": "yes"},
            {"run_id": "demo-b-Mar01-0900", "config": "demo", "branch": "b2",
             "started": "2026-03-01T09:00", "container": "stopped", "clones": "yes"},
        ]
        mock_cost.side_effect = [
            {"total_cost": 2.34, "total_input_tokens": 1000, "total_output_tokens": 500, "total_turns": 10},
            {"total_cost": 1.50, "total_input_tokens": 800, "total_output_tokens": 400, "total_turns": 8},
        ]

        status = get_project_status("demo")
        assert abs(status["total_cost"] - 3.84) < 0.01


class TestConsolidatedPaths:
    """After consolidation, clones live under ~/.scad/runs/<run-id>/worktrees/."""

    def test_create_clones_uses_run_dir(self, tmp_path, monkeypatch):
        """create_clones() puts clones in RUNS_DIR/<run-id>/worktrees/."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        # Create a source repo
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"], capture_output=True, check=True)

        config = ScadConfig(
            name="demo",
            repos={"code": RepoConfig(path=str(source), workdir=True)},
            python=PythonConfig(),
            claude=ClaudeConfig(),
        )
        paths = create_clones(config, "test-branch", "demo-test-Mar01-1400")

        clone_path = runs_dir / "demo-test-Mar01-1400" / "worktrees" / "code"
        assert clone_path.exists()
        assert paths["code"] == clone_path

    def test_cleanup_clones_removes_worktrees_subdir(self, tmp_path, monkeypatch):
        """cleanup_clones() removes the worktrees subdir under run dir."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        worktrees = runs_dir / "demo-test-Mar01-1400" / "worktrees" / "code"
        worktrees.mkdir(parents=True)
        (worktrees / "file.txt").write_text("test")

        cleanup_clones("demo-test-Mar01-1400")
        assert not (runs_dir / "demo-test-Mar01-1400" / "worktrees").exists()
        # Run dir itself still exists (has events.log, claude data)
        assert (runs_dir / "demo-test-Mar01-1400").exists()

    def test_clean_run_removes_entire_run_dir(self, tmp_path, monkeypatch):
        """clean_run() removes the entire run dir in one shot."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        run_dir = runs_dir / "demo-test-Mar01-1400"
        (run_dir / "worktrees" / "code").mkdir(parents=True)
        (run_dir / "claude").mkdir(parents=True)
        (run_dir / "events.log").write_text("test")

        monkeypatch.setattr("scad.container.docker.from_env", lambda: MagicMock(
            containers=MagicMock(get=MagicMock(side_effect=docker.errors.NotFound("not found")))
        ))

        clean_run("demo-test-Mar01-1400")
        assert not run_dir.exists()

    def test_no_worktree_dir_constant(self):
        """WORKTREE_DIR constant should not exist after consolidation."""
        import scad.container
        assert not hasattr(scad.container, "WORKTREE_DIR")


class TestWorktreeMigration:
    """Auto-migrate old ~/.scad/worktrees/ to ~/.scad/runs/<run-id>/worktrees/."""

    def test_migrates_worktree_with_matching_run_dir(self, tmp_path, monkeypatch):
        """Worktree moves under existing run dir."""
        scad_dir = tmp_path / ".scad"
        runs_dir = scad_dir / "runs"
        old_worktrees = scad_dir / "worktrees"
        monkeypatch.setattr("scad.container.SCAD_DIR", scad_dir)
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        # Old layout: worktree exists, run dir exists
        (old_worktrees / "demo-Mar01-1400" / "code").mkdir(parents=True)
        (old_worktrees / "demo-Mar01-1400" / "code" / "file.txt").write_text("test")
        (runs_dir / "demo-Mar01-1400" / "claude").mkdir(parents=True)

        _migrate_worktrees()

        # New layout: worktree under run dir
        assert (runs_dir / "demo-Mar01-1400" / "worktrees" / "code" / "file.txt").exists()
        assert not old_worktrees.exists()

    def test_migrates_worktree_without_run_dir(self, tmp_path, monkeypatch):
        """Orphaned worktree gets a new run dir created."""
        scad_dir = tmp_path / ".scad"
        runs_dir = scad_dir / "runs"
        old_worktrees = scad_dir / "worktrees"
        monkeypatch.setattr("scad.container.SCAD_DIR", scad_dir)
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        (old_worktrees / "orphan-Mar01-1400" / "code").mkdir(parents=True)

        _migrate_worktrees()

        assert (runs_dir / "orphan-Mar01-1400" / "worktrees" / "code").exists()
        assert not old_worktrees.exists()

    def test_noop_when_no_old_worktrees(self, tmp_path, monkeypatch):
        """No error when ~/.scad/worktrees/ doesn't exist."""
        scad_dir = tmp_path / ".scad"
        monkeypatch.setattr("scad.container.SCAD_DIR", scad_dir)
        _migrate_worktrees()  # should not raise


class TestValidateRunId:
    """validate_run_id() raises ClickException for unknown run-ids."""

    def test_valid_run_id_with_run_dir(self, tmp_path, monkeypatch):
        """No error when run dir exists."""
        runs_dir = tmp_path / "runs"
        (runs_dir / "demo-test-Mar01-1400").mkdir(parents=True)
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)

        validate_run_id("demo-test-Mar01-1400")  # should not raise

    def test_valid_run_id_with_container_only(self, tmp_path, monkeypatch):
        """No error when container exists but run dir doesn't."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: True)

        validate_run_id("demo-test-Mar01-1400")  # should not raise

    def test_invalid_run_id_raises(self, tmp_path, monkeypatch):
        """ClickException when neither run dir nor container exists."""
        runs_dir = tmp_path / "runs"
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)

        with pytest.raises(click.ClickException, match="No session found"):
            validate_run_id("nonexistent-run")


class TestGarbageCollection:
    """gc() finds orphaned state and optionally cleans it."""

    def test_finds_orphaned_container(self, tmp_path, monkeypatch):
        """Container with scad.managed label but no run dir."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        mock_container = MagicMock()
        mock_container.name = "scad-orphan-Mar01-1400"
        mock_container.status = "exited"
        mock_container.labels = {"scad.managed": "true"}

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_client.images.list.return_value = []
        monkeypatch.setattr("scad.container.docker.from_env", lambda: mock_client)

        findings = gc(force=False)
        assert len(findings["orphaned_containers"]) == 1
        assert findings["orphaned_containers"][0]["name"] == "scad-orphan-Mar01-1400"

    def test_finds_dead_run_dir(self, tmp_path, monkeypatch):
        """Run dir with no container and no worktrees."""
        runs_dir = tmp_path / "runs"
        dead_dir = runs_dir / "dead-Mar01-1400"
        dead_dir.mkdir(parents=True)
        (dead_dir / "events.log").write_text("old")
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.images.list.return_value = []
        monkeypatch.setattr("scad.container.docker.from_env", lambda: mock_client)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)

        findings = gc(force=False)
        assert len(findings["dead_run_dirs"]) == 1

    def test_finds_unused_images(self, tmp_path, monkeypatch):
        """Image tagged scad-* with no containers using it."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        mock_image = MagicMock()
        mock_image.tags = ["scad-demo:latest"]
        mock_image.id = "sha256:abc123"
        mock_image.attrs = {"Created": "2026-02-28T00:00:00Z"}

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.images.list.return_value = [mock_image]
        monkeypatch.setattr("scad.container.docker.from_env", lambda: mock_client)

        findings = gc(force=False)
        assert len(findings["unused_images"]) == 1

    def test_dry_run_does_not_delete(self, tmp_path, monkeypatch):
        """gc(force=False) reports but doesn't clean."""
        runs_dir = tmp_path / "runs"
        dead_dir = runs_dir / "dead-Mar01-1400"
        dead_dir.mkdir(parents=True)
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.images.list.return_value = []
        monkeypatch.setattr("scad.container.docker.from_env", lambda: mock_client)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)

        gc(force=False)
        assert dead_dir.exists()  # still there

    def test_force_deletes(self, tmp_path, monkeypatch):
        """gc(force=True) actually cleans."""
        runs_dir = tmp_path / "runs"
        dead_dir = runs_dir / "dead-Mar01-1400"
        dead_dir.mkdir(parents=True)
        monkeypatch.setattr("scad.container.RUNS_DIR", runs_dir)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_client.images.list.return_value = []
        monkeypatch.setattr("scad.container.docker.from_env", lambda: mock_client)
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)

        gc(force=True)
        assert not dead_dir.exists()
