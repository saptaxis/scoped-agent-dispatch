"""Tests for session injection — docker exec into running containers."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from scad.container import inject_job, list_jobs, send_to_job, create_branch, RUNS_DIR


class TestInjectJob:
    """Tests for inject_job() — docker exec into running container."""

    @patch("scad.container.docker.from_env")
    def test_headless_injection_runs_docker_exec(self, mock_docker, tmp_path):
        """Headless inject runs claude -p via docker exec."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Summarize the code",
                headless=True,
                workdir_key="code",
            )

        assert job_id.startswith("test-run-job-")
        # Two exec_run calls: prompt file write + claude command
        assert mock_container.exec_run.call_count == 2
        exec_cmd = mock_container.exec_run.call_args_list[1][0][0]
        assert "claude -p" in str(exec_cmd)

    @patch("scad.container.docker.from_env")
    def test_interactive_injection_runs_tmux(self, mock_docker, tmp_path):
        """Interactive inject creates tmux session via docker exec."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Fix the bug",
                headless=False,
                workdir_key="code",
            )

        exec_cmd = mock_container.exec_run.call_args[0][0]
        assert "tmux" in str(exec_cmd)

    @patch("scad.container.docker.from_env")
    def test_writes_job_metadata(self, mock_docker, tmp_path):
        """Inject creates job metadata JSON file."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Do the thing",
                headless=True,
                workdir_key="code",
            )

        job_file = tmp_path / "test-run" / "jobs" / f"{job_id}.json"
        assert job_file.exists()
        meta = json.loads(job_file.read_text())
        assert meta["job_id"] == job_id
        assert meta["prompt"] == "Do the thing"
        assert meta["mode"] == "headless"
        assert "started" in meta

    @patch("scad.container.docker.from_env")
    def test_logs_inject_event(self, mock_docker, tmp_path):
        """Inject logs to events.log."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
            )

        events_log = tmp_path / "test-run" / "events.log"
        assert events_log.exists()
        content = events_log.read_text()
        assert "inject" in content
        assert job_id in content

    @patch("scad.container.docker.from_env")
    def test_container_not_running_raises(self, mock_docker, tmp_path):
        """Inject raises if container is not running."""
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker.return_value.containers.get.return_value = mock_container

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            with pytest.raises(RuntimeError, match="not running"):
                inject_job(
                    run_id="test-run",
                    prompt="Task",
                    headless=True,
                    workdir_key="code",
                )

    @patch("scad.container.docker.from_env")
    def test_job_id_increments(self, mock_docker, tmp_path):
        """Sequential injects get incrementing job IDs."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job1 = inject_job("test-run", "Task 1", True, "code")
            job2 = inject_job("test-run", "Task 2", True, "code")

        assert job1 == "test-run-job-001"
        assert job2 == "test-run-job-002"

    @patch("scad.container.docker.from_env")
    def test_headless_uses_add_dir_flags(self, mock_docker, tmp_path):
        """Headless inject includes --add-dir for repos with add_dir=True."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                add_dirs=["docs"],
            )

        exec_cmd = mock_container.exec_run.call_args[0][0]
        assert "--add-dir /workspace/docs" in str(exec_cmd)

    @patch("scad.container.docker.from_env")
    def test_interactive_uses_add_dir_flags(self, mock_docker, tmp_path):
        """Interactive inject includes --add-dir for repos with add_dir=True."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            inject_job(
                run_id="test-run",
                prompt="Task",
                headless=False,
                workdir_key="code",
                add_dirs=["docs"],
            )

        # add_dir flag is in the launcher script (second exec_run call)
        all_calls = [str(c) for c in mock_container.exec_run.call_args_list]
        assert any("--add-dir /workspace/docs" in c for c in all_calls)

    @patch("scad.container.docker.from_env")
    def test_headless_uses_skip_permissions(self, mock_docker, tmp_path):
        """Headless inject includes --dangerously-skip-permissions if configured."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                dangerously_skip_permissions=True,
            )

        exec_cmd = mock_container.exec_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in str(exec_cmd)


from click.testing import CliRunner
from scad.cli import main


class TestSessionInjectCLI:
    """Tests for the session inject CLI command."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    @patch("scad.cli._config_for_run")
    def test_inject_headless(self, mock_config_for_run, mock_load, mock_inject, mock_validate):
        """session inject --headless runs headless injection."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path="/tmp/code", workdir=True)},
        )
        mock_config_for_run.return_value = config
        mock_load.return_value = config
        mock_inject.return_value = "test-run-job-001"

        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "inject", "test-run",
            "--prompt", "Do the thing",
            "--headless",
        ])
        assert result.exit_code == 0
        assert "job-001" in result.output
        mock_inject.assert_called_once()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_inject_requires_prompt(self, mock_config_for_run, mock_inject, mock_validate):
        """session inject without --prompt should fail."""
        runner = CliRunner()
        result = runner.invoke(main, ["session", "inject", "test-run"])
        assert result.exit_code != 0

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_inject_interactive_default(self, mock_config_for_run, mock_inject, mock_validate):
        """session inject without --headless defaults to interactive."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test",
            repos={"code": RepoConfig(path="/tmp/code", workdir=True)},
        )
        mock_config_for_run.return_value = config
        mock_inject.return_value = "test-run-job-001"

        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "inject", "test-run",
            "--prompt", "Fix bug",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_inject.call_args
        assert kwargs.get("headless") is False or not kwargs.get("headless")


class TestListJobs:
    """Tests for list_jobs() — read job metadata from run dir."""

    def test_lists_all_jobs(self, tmp_path):
        """list_jobs returns all job metadata from the jobs/ dir."""
        jobs_dir = tmp_path / "test-run" / "jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / "test-run-job-001.json").write_text(json.dumps({
            "job_id": "test-run-job-001", "prompt": "Task A",
            "mode": "headless", "started": "2026-03-02T15:00:00Z",
        }))
        (jobs_dir / "test-run-job-002.json").write_text(json.dumps({
            "job_id": "test-run-job-002", "prompt": "Task B",
            "mode": "interactive", "started": "2026-03-02T15:01:00Z",
        }))
        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs = list_jobs("test-run")
        assert len(jobs) == 2
        assert jobs[0]["job_id"] == "test-run-job-001"
        assert jobs[1]["job_id"] == "test-run-job-002"

    def test_empty_when_no_jobs(self, tmp_path):
        """list_jobs returns empty list if no jobs dir."""
        (tmp_path / "test-run").mkdir(parents=True)
        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs = list_jobs("test-run")
        assert jobs == []


class TestSessionJobsCLI:

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.list_jobs")
    def test_jobs_shows_table(self, mock_list, mock_validate):
        mock_list.return_value = [
            {"job_id": "run-job-001", "mode": "headless", "branch": None, "started": "2026-03-02T15:00:00+00:00"},
            {"job_id": "run-job-002", "mode": "interactive", "branch": "feat-x", "started": "2026-03-02T15:01:00+00:00"},
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["session", "jobs", "test-run"])
        assert result.exit_code == 0
        assert "job-001" in result.output
        assert "job-002" in result.output
        assert "headless" in result.output
        assert "interactive" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.list_jobs")
    def test_jobs_empty(self, mock_list, mock_validate):
        mock_list.return_value = []
        runner = CliRunner()
        result = runner.invoke(main, ["session", "jobs", "test-run"])
        assert result.exit_code == 0
        assert "No jobs" in result.output


class TestBranchPerJob:

    @patch("scad.container.docker.from_env")
    def test_inject_with_branch_includes_checkout(self, mock_docker, tmp_path):
        """Inject with --branch includes git checkout in exec command."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                branch="feature-x",
            )

        exec_cmd = mock_container.exec_run.call_args[0][0]
        # The bash -c command is the third element (index 2)
        bash_cmd = exec_cmd[2] if isinstance(exec_cmd, list) else str(exec_cmd)
        assert "git checkout" in bash_cmd
        assert "feature-x" in bash_cmd

    @patch("scad.container.docker.from_env")
    def test_inject_branch_stored_in_metadata(self, mock_docker, tmp_path):
        """Branch is recorded in job metadata."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                branch="feature-x",
            )

        meta = json.loads((tmp_path / "test-run" / "jobs" / f"{job_id}.json").read_text())
        assert meta["branch"] == "feature-x"


class TestWorkspaceAdd:

    def test_add_creates_symlink(self, tmp_path):
        """workspace_add creates a symlink in workspace/."""
        from scad.container import workspace_add
        workspace = tmp_path / "test-run" / "workspace"
        workspace.mkdir(parents=True)
        source = tmp_path / "data"
        source.mkdir()

        with patch("scad.container.RUNS_DIR", tmp_path):
            workspace_add("test-run", str(source), "experiments")

        link = workspace / "experiments"
        assert link.is_symlink()
        assert link.resolve() == source.resolve()

    def test_add_with_clone_flag(self, tmp_path):
        """workspace_add --clone does git clone instead of symlink."""
        from scad.container import workspace_add
        workspace = tmp_path / "test-run" / "workspace"
        workspace.mkdir(parents=True)
        source = tmp_path / "repo"
        source.mkdir()

        with patch("scad.container.RUNS_DIR", tmp_path), \
             patch("scad.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            workspace_add("test-run", str(source), "repo", clone=True)

        mock_run.assert_called()
        clone_call = mock_run.call_args_list[0][0][0]
        assert "clone" in clone_call

    def test_add_rejects_duplicate_name(self, tmp_path):
        """workspace_add raises if name already exists in workspace."""
        from scad.container import workspace_add
        workspace = tmp_path / "test-run" / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "existing").mkdir()
        source = tmp_path / "data"
        source.mkdir()

        with patch("scad.container.RUNS_DIR", tmp_path):
            with pytest.raises(FileExistsError):
                workspace_add("test-run", str(source), "existing")


class TestWorkspaceRemove:

    def test_remove_deletes_symlink(self, tmp_path):
        """workspace_remove removes a symlink from workspace/."""
        from scad.container import workspace_remove
        workspace = tmp_path / "test-run" / "workspace"
        workspace.mkdir(parents=True)
        source = tmp_path / "data"
        source.mkdir()
        (workspace / "experiments").symlink_to(source)

        with patch("scad.container.RUNS_DIR", tmp_path):
            workspace_remove("test-run", "experiments")

        assert not (workspace / "experiments").exists()
        assert source.exists()  # Original not deleted

    def test_remove_nonexistent_raises(self, tmp_path):
        """workspace_remove raises if name doesn't exist."""
        from scad.container import workspace_remove
        workspace = tmp_path / "test-run" / "workspace"
        workspace.mkdir(parents=True)

        with patch("scad.container.RUNS_DIR", tmp_path):
            with pytest.raises(FileNotFoundError):
                workspace_remove("test-run", "nope")


class TestCodeDiff:

    @patch("scad.container.subprocess.run")
    def test_diff_returns_output(self, mock_run, tmp_path):
        """diff_from_source returns git diff output."""
        from scad.container import diff_from_source
        workspace = tmp_path / "test-run" / "workspace" / "code"
        workspace.mkdir(parents=True)
        (workspace / ".git").mkdir()  # Looks like a git repo

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="diff --git a/file.py b/file.py\n+new line\n",
        )

        with patch("scad.container.RUNS_DIR", tmp_path):
            from scad.config import ScadConfig, RepoConfig
            config = ScadConfig(
                name="test",
                repos={"code": RepoConfig(path=str(tmp_path / "source"), workdir=True)},
            )
            results = diff_from_source("test-run", config)

        assert len(results) == 1
        assert "code" in results
        assert "+new line" in results["code"]


class TestInjectWait:
    """Tests for --wait blocking behavior."""

    @patch("scad.container.docker.from_env")
    def test_wait_does_not_detach(self, mock_docker, tmp_path):
        """When wait=True, docker exec is NOT detached."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        # exec_run returns (exit_code, output) when not detached
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            job_id = inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                wait=True,
            )

        # The claude exec call (second call) should NOT have detach=True
        claude_call = mock_container.exec_run.call_args_list[1]
        assert claude_call[1].get("detach") is not True

    @patch("scad.container.docker.from_env")
    def test_wait_returns_exit_code(self, mock_docker, tmp_path):
        """Wait mode returns the exit code from docker exec."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            result = inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
                wait=True,
            )

        # When wait=True, returns (job_id, exit_code) instead of just job_id
        assert isinstance(result, tuple)
        assert result[1] == 0

    @patch("scad.container.docker.from_env")
    def test_no_wait_still_detaches(self, mock_docker, tmp_path):
        """Without wait, inject still detaches (existing behavior)."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            result = inject_job(
                run_id="test-run",
                prompt="Task",
                headless=True,
                workdir_key="code",
            )

        # Without wait, returns just job_id (string)
        assert isinstance(result, str)
        # The claude exec call should have detach=True
        claude_call = mock_container.exec_run.call_args_list[1]
        assert claude_call[1].get("detach") is True

    @patch("scad.container.docker.from_env")
    def test_wait_with_interactive_raises(self, mock_docker, tmp_path):
        """wait=True with headless=False raises ValueError."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "workspace").mkdir(parents=True)
            with pytest.raises(ValueError, match="wait.*headless"):
                inject_job(
                    run_id="test-run",
                    prompt="Task",
                    headless=False,
                    workdir_key="code",
                    wait=True,
                )


class TestInjectTail:
    """Tests for --tail streaming during --wait."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_tail_requires_wait(self, mock_config, mock_inject, mock_validate):
        """--tail without --wait should error."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "inject", "test-run",
            "--prompt", "Task",
            "--headless",
            "--tail",
        ])
        assert result.exit_code != 0 or "requires --wait" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_wait_tail_accepted(self, mock_config, mock_inject, mock_validate):
        """--wait --tail is a valid combination."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_inject.return_value = ("test-job-001", 0)
        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "inject", "test-run",
            "--prompt", "Task",
            "--wait",
            "--tail",
        ])
        assert result.exit_code == 0

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_tail_thread_starts_and_stops(self, mock_config, mock_inject, mock_validate, tmp_path):
        """--wait --tail starts a tailing thread that stops after inject_job returns."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_inject.return_value = ("test-run-job-001", 0)

        # Create a stream.jsonl at the expected SCAD_HOME/logs/ path
        scad_dir = tmp_path / ".scad"
        logs_dir = scad_dir / "logs"
        logs_dir.mkdir(parents=True)
        stream_file = logs_dir / "test-run-job-001.stream.jsonl"
        stream_file.write_text(
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/x"}}]}}\n'
        )

        runner = CliRunner()
        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, [
                "session", "inject", "test-run",
                "--prompt", "Task",
                "--wait",
                "--tail",
            ])
        assert result.exit_code == 0

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.inject_job")
    @patch("scad.cli._config_for_run")
    def test_tail_displays_tool_activity(self, mock_config, mock_inject, mock_validate, tmp_path):
        """--tail should display condensed tool activity from stream.jsonl."""
        import json as _json
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_inject.return_value = ("test-run-job-001", 0)

        # Pre-create the stream.jsonl at the expected SCAD_HOME/logs/ path
        scad_dir = tmp_path / ".scad"
        logs_dir = scad_dir / "logs"
        logs_dir.mkdir(parents=True)
        stream_file = logs_dir / "test-run-job-001.stream.jsonl"
        lines = [
            _json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/workspace/code/main.py"}}
            ]}}),
            _json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/ -v"}}
            ]}}),
            _json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/workspace/code/utils.py"}}
            ]}}),
            _json.dumps({"type": "result", "result": "Done"}),
        ]
        stream_file.write_text("\n".join(lines) + "\n")

        runner = CliRunner()
        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, [
                "session", "inject", "test-run",
                "--prompt", "Task",
                "--wait",
                "--tail",
            ])

        assert result.exit_code == 0
        # The tail output should mention the tools used
        assert "Reading" in result.output or "Read" in result.output
        assert "Running" in result.output or "Bash" in result.output or "pytest" in result.output
        assert "Editing" in result.output or "Edit" in result.output


class TestSendToJob:
    """Tests for send_to_job() — send input to running interactive Claude."""

    @patch("scad.container.docker.from_env")
    def test_send_keys_to_tmux(self, mock_docker, tmp_path):
        """send_to_job sends text via tmux send-keys."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs_dir = tmp_path / "test-run" / "jobs"
            jobs_dir.mkdir(parents=True)
            (jobs_dir / "test-run-job-001.json").write_text(json.dumps({
                "job_id": "test-run-job-001",
                "mode": "interactive",
                "started": "2026-03-03T12:00:00Z",
            }))
            send_to_job("test-run", "summarize what you did")

        # Should call exec_run with tmux send-keys
        calls = [str(c) for c in mock_container.exec_run.call_args_list]
        assert any("send-keys" in c for c in calls)

    @patch("scad.container.docker.from_env")
    def test_send_errors_if_no_interactive_jobs(self, mock_docker, tmp_path):
        """send_to_job raises if no interactive jobs exist."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container

        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs_dir = tmp_path / "test-run" / "jobs"
            jobs_dir.mkdir(parents=True)
            (jobs_dir / "test-run-job-001.json").write_text(json.dumps({
                "job_id": "test-run-job-001",
                "mode": "headless",
                "started": "2026-03-03T12:00:00Z",
            }))
            with pytest.raises(RuntimeError, match="No interactive"):
                send_to_job("test-run", "hello")

    @patch("scad.container.docker.from_env")
    def test_send_errors_if_multiple_interactive_no_job_id(self, mock_docker, tmp_path):
        """send_to_job raises if multiple interactive jobs and no job_id specified."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container

        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs_dir = tmp_path / "test-run" / "jobs"
            jobs_dir.mkdir(parents=True)
            for i in range(1, 3):
                (jobs_dir / f"test-run-job-00{i}.json").write_text(json.dumps({
                    "job_id": f"test-run-job-00{i}",
                    "mode": "interactive",
                    "started": "2026-03-03T12:00:00Z",
                }))
            with pytest.raises(RuntimeError, match="Multiple interactive"):
                send_to_job("test-run", "hello")

    @patch("scad.container.docker.from_env")
    def test_send_with_explicit_job_id(self, mock_docker, tmp_path):
        """send_to_job with explicit job_id targets that job."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker.return_value.containers.get.return_value = mock_container
        mock_container.exec_run.return_value = (0, b"")

        with patch("scad.container.RUNS_DIR", tmp_path):
            jobs_dir = tmp_path / "test-run" / "jobs"
            jobs_dir.mkdir(parents=True)
            for i in range(1, 3):
                (jobs_dir / f"test-run-job-00{i}.json").write_text(json.dumps({
                    "job_id": f"test-run-job-00{i}",
                    "mode": "interactive",
                    "started": "2026-03-03T12:00:00Z",
                }))
            send_to_job("test-run", "hello", job_id="test-run-job-002")

        calls = [str(c) for c in mock_container.exec_run.call_args_list]
        assert any("send-keys" in c and "job-002" in c for c in calls)

    @patch("scad.container.docker.from_env")
    def test_send_container_not_running_raises(self, mock_docker, tmp_path):
        """send_to_job raises if container is not running."""
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker.return_value.containers.get.return_value = mock_container

        with patch("scad.container.RUNS_DIR", tmp_path):
            (tmp_path / "test-run" / "jobs").mkdir(parents=True)
            with pytest.raises(RuntimeError, match="not running"):
                send_to_job("test-run", "hello")


class TestSessionSendCLI:
    """Tests for session send CLI command."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.send_to_job")
    def test_send_basic(self, mock_send, mock_validate):
        """session send passes text to send_to_job."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "send", "test-run", "summarize what you did",
        ])
        assert result.exit_code == 0
        mock_send.assert_called_once_with("test-run", "summarize what you did", job_id=None)

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.send_to_job")
    def test_send_with_job_flag(self, mock_send, mock_validate):
        """session send --job targets specific job."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "send", "test-run",
            "--job", "test-run-job-002",
            "do something",
        ])
        assert result.exit_code == 0
        mock_send.assert_called_once_with("test-run", "do something", job_id="test-run-job-002")

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.send_to_job")
    def test_send_error_displayed(self, mock_send, mock_validate):
        """session send shows error if send_to_job fails."""
        mock_send.side_effect = RuntimeError("No interactive jobs")
        runner = CliRunner()
        result = runner.invoke(main, [
            "session", "send", "test-run", "hello",
        ])
        assert result.exit_code != 0
        assert "No interactive" in result.output


class TestCreateBranch:
    """Tests for create_branch() — create/switch branch in clones."""

    def test_creates_branch_in_clone(self, tmp_path):
        """create_branch runs git checkout -b in workspace clone."""
        workspace = tmp_path / "test-run" / "workspace" / "code"
        workspace.mkdir(parents=True)
        (workspace / ".git").mkdir()  # fake git repo

        with patch("scad.container.RUNS_DIR", tmp_path), \
             patch("scad.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            create_branch("test-run", "feature-x")

        mock_run.assert_called()
        cmd = mock_run.call_args_list[0][0][0]
        assert "checkout" in cmd
        assert "feature-x" in cmd

    def test_creates_branch_in_all_clones(self, tmp_path):
        """create_branch creates the branch in every git repo in workspace."""
        workspace = tmp_path / "test-run" / "workspace"
        for name in ["code", "docs"]:
            d = workspace / name
            d.mkdir(parents=True)
            (d / ".git").mkdir()

        with patch("scad.container.RUNS_DIR", tmp_path), \
             patch("scad.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            create_branch("test-run", "feature-x")

        # Should be called for each git repo
        assert mock_run.call_count >= 2

    def test_skips_symlinks(self, tmp_path):
        """create_branch skips symlinked directories (non-clone repos)."""
        workspace = tmp_path / "test-run" / "workspace"
        clone = workspace / "code"
        clone.mkdir(parents=True)
        (clone / ".git").mkdir()

        # Create a symlink (data mount)
        data_source = tmp_path / "data"
        data_source.mkdir()
        (workspace / "data").symlink_to(data_source)

        with patch("scad.container.RUNS_DIR", tmp_path), \
             patch("scad.container.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            create_branch("test-run", "feature-x")

        # Only called once — for the clone, not the symlink
        assert mock_run.call_count == 1


class TestCodeBranchCLI:

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.create_branch")
    def test_branch_command(self, mock_branch, mock_validate):
        """code branch creates branch in clones."""
        mock_branch.return_value = ["code"]
        runner = CliRunner()
        result = runner.invoke(main, ["code", "branch", "test-run", "feature-x"])
        assert result.exit_code == 0
        mock_branch.assert_called_once_with("test-run", "feature-x")
