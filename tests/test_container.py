"""Container management tests."""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import click
from scad.config import ScadConfig
import docker
from scad.container import (
    render_build_context,
    generate_run_id,
    generate_branch_name,
    check_branch_exists,
    resolve_branch,
    build_image,
    run_container,
    fetch_pending_bundles,
    list_scad_containers,
    list_completed_runs,
    stop_container,
)


@pytest.fixture
def sample_config():
    return ScadConfig(
        name="test",
        repos={"code": {"path": "/tmp/fake", "workdir": True, "branch_from": "main"}},
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
        assert "git clone" in content

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
        run_id = generate_run_id("plan-22")
        # Should be like plan-22-Feb26-1430
        assert run_id.startswith("plan-22-")
        parts = run_id.split("-")
        # branch-parts-MonDD-HHMM
        assert len(parts) >= 3
        # Last two parts are the date and time
        assert len(parts[-1]) == 4  # HHMM
        assert len(parts[-2]) == 5  # MonDD like Feb26

    def test_contains_branch(self):
        run_id = generate_run_id("my-feature")
        assert "my-feature" in run_id


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

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430")

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

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430")

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for path in volumes:
            assert "CLAUDE.md" not in path

    @patch("scad.container.docker.from_env")
    def test_claude_md_disabled(self, mock_docker, tmp_path):
        """claude_md: false disables auto-mount even if file exists."""
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True, "branch_from": "main"}},
            claude={"claude_md": False},
        )
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Instructions")

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(config, "test", "test-Feb27-1430")

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
            repos={"code": {"path": "/tmp/fake", "workdir": True, "branch_from": "main"}},
            claude={"claude_md": str(custom_md)},
        )
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(config, "test", "test-Feb27-1430")

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        custom_mount = volumes.get(str(custom_md))
        assert custom_mount is not None
        assert custom_mount["mode"] == "ro"
        assert custom_mount["bind"] == "/home/scad/CLAUDE.md"


class TestRunContainerAuth:
    @patch("scad.container.docker.from_env")
    def test_mounts_credentials_only(self, mock_docker, sample_config, tmp_path):
        """Only .credentials.json is mounted, not the whole ~/.claude dir."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        # Create ~/.claude/.credentials.json
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = claude_dir / ".credentials.json"
        creds.write_text('{"claudeAiOauth": {}}')
        # Also create files that should NOT be mounted
        (claude_dir / "settings.json").write_text("{}")
        plugins_dir = claude_dir / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "installed_plugins.json").write_text("{}")

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430")

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        # Credentials file should be mounted read-only
        creds_mount = volumes.get(str(creds))
        assert creds_mount is not None
        assert creds_mount["mode"] == "ro"
        assert creds_mount["bind"] == "/home/scad/.claude/.credentials.json"
        # Full .claude dir should NOT be mounted
        assert str(claude_dir) not in volumes
        # .claude.json from host should NOT be mounted
        for path in volumes:
            if path.endswith(".claude.json") and ".credentials" not in path:
                pytest.fail(f"Host .claude.json should not be mounted: {path}")

    @patch("scad.container.docker.from_env")
    def test_no_auth_mount_when_no_credentials(self, mock_docker, sample_config, tmp_path):
        """No auth mount if credentials file doesn't exist."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.id = "abc123"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        with patch("scad.container.Path.home", return_value=tmp_path):
            (tmp_path / ".scad" / "logs").mkdir(parents=True)
            run_container(sample_config, "test", "test-Feb27-1430")

        volumes = mock_client.containers.run.call_args[1]["volumes"]
        for path in volumes:
            assert ".credentials" not in path


class TestFetchPendingBundles:
    def test_finds_and_fetches_unfetched_bundles(self, tmp_path):
        status = {
            "run_id": "test-Feb26-1430",
            "config": "myconfig",
            "branch": "test-branch",
            "exit_code": 0,
            "bundles": {"code": True},
        }
        (tmp_path / "test-Feb26-1430.status.json").write_text(json.dumps(status))
        (tmp_path / "test-Feb26-1430-code.bundle").write_bytes(b"fake bundle")

        with patch("scad.container.fetch_bundles") as mock_fetch:
            mock_fetch.return_value = {"code": True}
            with patch("scad.container.load_config") as mock_load:
                mock_load.return_value = MagicMock()
                results = fetch_pending_bundles(logs_dir=tmp_path)

        assert len(results) == 1
        assert results[0]["run_id"] == "test-Feb26-1430"
        # Should create .fetched marker
        assert (tmp_path / "test-Feb26-1430.fetched").exists()

    def test_skips_already_fetched(self, tmp_path):
        status = {
            "run_id": "old-Feb25-1000",
            "config": "myconfig",
            "branch": "old",
            "exit_code": 0,
            "bundles": {"code": True},
        }
        (tmp_path / "old-Feb25-1000.status.json").write_text(json.dumps(status))
        (tmp_path / "old-Feb25-1000-code.bundle").write_bytes(b"fake")
        (tmp_path / "old-Feb25-1000.fetched").write_text("")

        with patch("scad.container.fetch_bundles") as mock_fetch:
            results = fetch_pending_bundles(logs_dir=tmp_path)

        mock_fetch.assert_not_called()
        assert results == []

    def test_skips_runs_without_bundles(self, tmp_path):
        status = {
            "run_id": "no-bundles-Feb26-1430",
            "config": "myconfig",
            "branch": "test",
            "exit_code": 0,
            "bundles": {},
        }
        (tmp_path / "no-bundles-Feb26-1430.status.json").write_text(json.dumps(status))

        with patch("scad.container.fetch_bundles") as mock_fetch:
            results = fetch_pending_bundles(logs_dir=tmp_path)

        mock_fetch.assert_not_called()
        assert results == []

    def test_returns_empty_for_missing_dir(self, tmp_path):
        results = fetch_pending_bundles(logs_dir=tmp_path / "nonexistent")
        assert results == []
